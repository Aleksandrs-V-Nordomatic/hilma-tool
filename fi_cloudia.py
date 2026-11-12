"""The shut 80%: a tender's documents from Tarjouspalvelu, read under our supplier account.

`tarjouspalvelu.fi` -- Cloudia, now Mercell -- holds 79.6% of Finland's tender documents, and it
serves them to a signed-in supplier as one archive per tender:

    GET https://tarjouspalvelu.fi/Zip/TarjousPyynnonLiitteet/{tpId}

`tpId` is the `id=` of the notice's `procurementDocumentsUrl`. So this module signs in ONCE per
run, takes the session's cookies out of the browser, and from then on asks with plain HTTP through
`net.get`, like every other request in this tool.

THE BROWSER IS ONLY FOR THE DOOR. The sign-in goes through Cloudia's single sign-on, a Vaadin
page that a plain form POST cannot drive (the portal's own `InternalLogin` works only for local
accounts). Playwright is imported inside `sign_in` and nowhere else, so a run without an account
-- and every test -- never needs it.

A TENDER'S PAGE IS NEVER OPENED. Opening one creates a draft response under the company's name,
visible to the buyer. The browser visits the portal's front page and the sign-in pages, and the
archive is asked for by its own address. Only tenders that passed the recall gate, or that a
person is already watching, get here at all: `fi_day` never fetches a gated notice.

THE ARCHIVE IS BUILT FRESH ON EVERY REQUEST, so its own digest changes while its contents do not.
Each member is therefore its own record in `manifest.json`, keyed by its path inside the archive,
with the sha256 of its bytes -- which is exactly what `changes` compares. The archive's digest is
not recorded at all, because a fingerprint built on it would report a change every night.

THE RUN'S LOG IS PUBLIC, so this module prints counts and nothing else: no tpId, no title, no
account. A reason for a failure goes into the tender's `index.json`, which is delivered privately.

    python3 fi_cloudia.py --check      # sign in, say whether it worked, download nothing
"""
import argparse
import hashlib
import io
import os
import re
import sys
import time
import urllib.parse
import zipfile

import net

HOST = "tarjouspalvelu.fi"
BASE = "https://tarjouspalvelu.fi"
ZIP_URL = BASE + "/Zip/TarjousPyynnonLiitteet/%s"

# The largest specification archive proved on Hilma's CDN was nine megabytes; a building project
# on Cloudia can carry drawings. Ten minutes is long enough for that and short enough that one
# stalled archive does not hold a night.
ZIP_TIMEOUT = 600

# What one tender may unpack to. A real pack is tens of megabytes; an archive that claims more
# than this is either a mistake or a bomb, and in neither case worth a night's disk.
MAX_UNPACKED = 1024 * 1024 * 1024

HTML_HEADS = (b"<!DOCTYPE", b"<!doctype", b"<html", b"<HTML")
ZIP_HEAD = b"PK\x03\x04"
EMPTY_ZIP_HEAD = b"PK\x05\x06"

# What a second factor or a captcha says on the page it stops us at, in Finnish and English.
SECOND_FACTOR = re.compile(r"vahvistuskoodi|tunnistautu|kaksivaihe|authenticator|verification code|"
                           r"one.time|captcha|recaptcha|turnstile", re.I)


# A browser that broke or a page that was slow is tried once more after this pause. The night of
# 2026-11-11 lost all fourteen of its portal tenders to one browser error at sign-in.
RETRY_PAUSE = 30


class SignInFailed(Exception):
    """The account did not get us in. The message names the step and never the account.

    `transient` is True when the browser broke or a page did not load in time -- a second attempt
    can cure that. An answer about the account itself is never retried.
    """

    def __init__(self, message, transient=False):
        super().__init__(message)
        self.transient = transient


# ONE SIGN-IN PER RUN, AND ONE FAILED SIGN-IN PER RUN. A night fetches dozens of tenders; a
# browser per tender would be slow, and a refused account retried dozens of times looks like an
# attack on it.
_SESSION = {"cookie": None, "agent": None, "failed": None}
COUNTS = {"read": 0, "failed": 0, "skipped": 0}


def tp_id(url):
    """The tender's number on Tarjouspalvelu, from the address Hilma carries, or None."""
    parts = urllib.parse.urlparse((url or "").strip())
    host = parts.netloc.casefold()
    if not (host == HOST or host.endswith("." + HOST)):
        return None
    for key, values in urllib.parse.parse_qs(parts.query).items():
        if key.casefold() == "id":
            value = values[0].strip()
            return value if value.isdigit() else None
    return None


def applies(row):
    """Whether this tender's documents are on Tarjouspalvelu and can be asked for by number."""
    return row.get("documents_host") == HOST and _tender(row) is not None


def _tender(row):
    """The tpId a row carries itself (a tender found on the portal's own list), or the one in
    the documents address Hilma gives."""
    own = str(row.get("tp_id") or "").strip()
    return own if own.isdigit() else tp_id(row.get("documents_url"))


def _account():
    user = (os.environ.get("CLOUDIA_USERNAME") or "").strip()
    password = os.environ.get("CLOUDIA_PASSWORD") or ""
    return (user, password) if user and password else None


def sign_in(user, password):
    """Sign in through the portal's own pages. Returns `(cookie_header, user_agent)`.

    The steps are the ones the portal's own script takes, proved from a runner on 2026-11-09:
    ask which sign-in the address uses, then either Cloudia's single sign-on form or the local
    one. Each failure is raised with the step it happened at and no page text that could carry
    the account; a browser error keeps its first line, with the account masked by `_said`.
    """
    from playwright.sync_api import Error as PwError
    from playwright.sync_api import TimeoutError as PwTimeout
    from playwright.sync_api import sync_playwright

    step = "starting the browser"
    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch(headless=True)
        except PwError as exc:
            raise SignInFailed("the browser failed at %s: %s" % (step, _said(exc, user, password)),
                               transient=True)
        try:
            step = "opening the front page"
            ctx = browser.new_context(locale="fi-FI")
            page = ctx.new_page()
            # `domcontentloaded`, never `networkidle`: the portal keeps a connection open and
            # an idle network never comes.
            page.goto(BASE + "/Default/Index", wait_until="domcontentloaded", timeout=60000)
            step = "declining the cookie banner"
            for label in ("Deny", "Kiellä", "Hylkää"):       # cookie banner: decline the extras
                button = page.locator("button:has-text('%s')" % label)
                if button.count():
                    button.first.click()
                    break

            step = "asking how the account signs in"
            try:
                method = ctx.request.get(BASE + "/Authentication/TarkistaOnkoSSO",
                                         params={"userName": user}).json().get("loginMethod")
            except Exception as exc:
                raise SignInFailed("the portal did not say how this account signs in (%s)"
                                   % type(exc).__name__, transient=True)

            if method == "SSO":
                step = "opening single sign-on"
                page.goto(BASE + "/Default/LoginSSO?userName=" + urllib.parse.quote(user),
                          wait_until="domcontentloaded", timeout=60000)
                try:
                    page.wait_for_url(lambda u: "login.cloudia.net" in u, timeout=30000,
                                      wait_until="domcontentloaded")
                except PwTimeout:
                    pass
                # Checked before anything is typed: a password goes into Cloudia's form or into
                # nothing.
                if "login.cloudia.net" not in page.url:
                    raise SignInFailed("single sign-on did not reach the Cloudia form")
                step = "filling the Cloudia form"
                page.wait_for_selector("#username", timeout=30000)
                # A Tab after each field, because the Vaadin form only takes a value it has
                # seen the field lose focus over.
                page.click("#username")
                page.fill("#username", user)
                page.keyboard.press("Tab")
                secret = page.locator("input[type=password]").first
                secret.click()
                secret.fill(password)
                page.keyboard.press("Tab")
                page.click("#login-btn")
            elif method == "LOCAL":
                step = "filling the local form"
                page.fill("#user", user)
                page.click("#continue")
                page.wait_for_selector("#password", timeout=30000)
                page.fill("#password", password)
                page.click("#oldlogin")
            else:
                # ERROR is an address the portal does not know, NO_ACCESS one without a supplier
                # role. Both are about the account, and retrying will not change them.
                raise SignInFailed("the portal refused the account: loginMethod=%s" % method)

            step = "waiting to be sent back to the portal"
            try:
                page.wait_for_url(re.compile(r"^https://(www\.)?tarjouspalvelu\.fi/"),
                                  timeout=45000, wait_until="domcontentloaded")
            except PwTimeout:
                pass
            if urllib.parse.urlparse(page.url).netloc.casefold() not in (HOST, "www." + HOST):
                if SECOND_FACTOR.search(page.inner_text("body")[:2000]):
                    raise SignInFailed("a second factor or a captcha stood in the way")
                raise SignInFailed("the password did not lead back to the portal")
            page.wait_for_timeout(2000)

            step = "taking the session's cookies"
            cookies = ctx.cookies(BASE)
            agent = page.evaluate("navigator.userAgent")
        except PwTimeout as exc:
            raise SignInFailed("a sign-in page did not load in time at %s (%s)"
                               % (step, type(exc).__name__), transient=True)
        except PwError as exc:                 # crashed, closed, refused a click or a navigation
            raise SignInFailed("the browser failed at %s: %s" % (step, _said(exc, user, password)),
                               transient=True)
        finally:
            try:
                browser.close()
            except PwError:
                pass

    if not cookies:
        raise SignInFailed("signed in, but the portal set no cookie")
    return "; ".join("%s=%s" % (c["name"], c["value"]) for c in cookies), agent


def session(fresh=False):
    """The run's cookies, signing in on first use. Raises `SignInFailed`, at most once per run."""
    if _SESSION["failed"]:
        raise SignInFailed(_SESSION["failed"])
    if _SESSION["cookie"] and not fresh:
        return _SESSION["cookie"], _SESSION["agent"]
    account = _account()
    if account is None:
        raise SignInFailed("no supplier account is configured for this run")
    for attempt in (1, 2):
        try:
            cookie, agent = sign_in(*account)
            break
        except SignInFailed as exc:
            failure = exc
        except Exception as exc:               # the browser itself: missing, or not Playwright's
            failure = SignInFailed("the browser could not sign in (%s)" % type(exc).__name__,
                                   transient=True)
        if not failure.transient or attempt == 2:
            _SESSION["failed"] = str(failure) + (" (on the second attempt)" if attempt == 2
                                                 else "")
            raise SignInFailed(_SESSION["failed"])
        time.sleep(RETRY_PAUSE)
    _SESSION.update(cookie=cookie, agent=agent)
    return cookie, agent


def _said(exc, user, password):
    """The first line of what the browser said, with the account masked.

    Playwright's message names the call and often the address it was on, and the single sign-on
    address carries the user name. So both spellings of it, and the password, are masked before
    the line goes anywhere -- it ends up in the tender's private `index.json`, never the log.
    """
    line = (str(exc).strip().splitlines() or [type(exc).__name__])[0]
    for secret in (password, user, urllib.parse.quote(user), urllib.parse.quote_plus(user)):
        if secret:
            line = line.replace(secret, "<account>")
    return line[:200]


def download(tp, cookie, agent, get=None):
    """The archive's bytes, or why there are none. Returns `(data, reason, signed_out)`.

    THE STATUS CODE PROVES NOTHING HERE EITHER. A session that has ended is answered with the
    portal's front page under a 200, so the bytes are checked for what they are: an archive
    begins `PK`, and a page begins with HTML.
    """
    get = get or net.get
    try:
        data, _ = get(ZIP_URL % tp, headers={"Cookie": cookie, "User-Agent": agent},
                      timeout=ZIP_TIMEOUT)
    except net.Unreachable as exc:
        return None, "the archive could not be downloaded: %s" % str(exc)[:160], False
    if not data:
        return None, "the portal served an empty body", False
    head = data.lstrip()[:16]
    if any(head.startswith(h) for h in HTML_HEADS):
        return None, "the portal served a page rather than the archive (%d bytes)" % len(data), True
    if data.startswith(EMPTY_ZIP_HEAD):
        return None, "the portal's archive holds no files", False
    if not data.startswith(ZIP_HEAD):
        return None, "the portal served something that is not an archive (%d bytes)" % len(data), False
    return data, None, False


def _member_name(info):
    """The member's real name. An archive without the UTF-8 flag is decoded as cp437 by Python,
    which turns a Finnish `ä` written by a Windows zipper into box-drawing characters."""
    name = info.filename
    if not info.flag_bits & 0x800:
        try:
            name = name.encode("cp437").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            try:
                name = name.encode("cp437").decode("cp1252")
            except (UnicodeEncodeError, UnicodeDecodeError):
                pass
    return name.replace("\\", "/")


def unpack(home, data, source):
    """Every file in the archive, written under `originals/` as `fi_fetch.store` writes them.

    Returns `(records, bytes, reason)`. Directories inside the archive are flattened -- the
    extractor reads a flat folder -- but each record keeps its full path as `member` and as its
    id, so two files of the same name in different folders stay two documents.
    """
    import fi_fetch                            # imported here: fi_fetch imports this module

    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        return [], 0, "the archive could not be opened: %s" % str(exc)[:120]
    members = [i for i in archive.infolist() if not i.is_dir()]
    claimed = sum(i.file_size for i in members)
    if claimed > MAX_UNPACKED:
        return [], 0, ("the archive claims %.0f MB unpacked, over the %d MB a tender may take"
                       % (claimed / 1048576.0, MAX_UNPACKED // 1048576))

    originals = os.path.join(home, "originals")
    os.makedirs(originals, exist_ok=True)
    records, seen, total = [], set(), 0
    for info in sorted(members, key=lambda i: i.filename):
        member = _member_name(info)
        try:
            payload = archive.read(info)
        except Exception as exc:               # one broken member must not lose the rest
            return [], 0, "a member could not be read: %s" % type(exc).__name__
        total += len(payload)
        if total > MAX_UNPACKED:
            return [], 0, "the archive unpacked past the %d MB a tender may take" % (
                MAX_UNPACKED // 1048576)
        filename = fi_fetch._safe(member)
        stem, dot, ext = filename.rpartition(".")
        n = 1
        while filename.casefold() in seen:
            n += 1
            filename = "%s (%d)%s%s" % (stem or filename, n, dot, ext)
        seen.add(filename.casefold())
        with open(os.path.join(originals, filename), "wb") as fh:
            fh.write(payload)
        records.append({
            "id": "zip:%s" % member,
            "title": member,
            "type_code": None,
            "subtype": None,
            "section": "current",
            # The archive dates its members by the moment it was built, not by when the buyer
            # published them, so there is nothing honest to put here.
            "publish_date": None,
            "catalogued": True,
            "member": member,
            "download": source,
            "files": [{"filename": filename,
                       "path": "originals/%s" % filename,
                       "original_name": member.rsplit("/", 1)[-1],
                       "sha256": hashlib.sha256(payload).hexdigest(),
                       "bytes": len(payload)}],
        })
    return records, total, None


def fetch_into(home, row, get=None):
    """Read one tender's documents from the portal into its home. Never raises.

    Returns None when the tender is not on Tarjouspalvelu, otherwise
    `{"status": "read"|"failed"|"skipped", "reason", "records", "bytes", "files", "host"}`.
    A tender that fails stays `external`: the card is then judged on the notice, as it was
    before this module existed, and the reason travels with it.
    """
    if not applies(row):
        return None
    out = {"host": HOST, "status": "failed", "reason": None, "records": [], "bytes": 0,
           "files": 0}
    if _account() is None:
        COUNTS["skipped"] += 1
        return dict(out, status="skipped", reason="no supplier account is configured for this run")
    tp = _tender(row)
    try:
        cookie, agent = session()
        data, reason, signed_out = download(tp, cookie, agent, get=get)
        if signed_out:                         # the session ended mid-run: one fresh sign-in
            cookie, agent = session(fresh=True)
            data, reason, _ = download(tp, cookie, agent, get=get)
        if data is not None:
            records, total, reason = unpack(home, data, ZIP_URL % tp)
            if reason is None and not records:
                reason = "the portal's archive holds no files"
            if reason is None:
                COUNTS["read"] += 1
                return dict(out, status="read", records=records, bytes=total,
                            files=len(records))
    except SignInFailed as exc:
        reason = "sign-in failed: %s" % exc
    except Exception as exc:                   # a tender must not lose the night
        reason = "the portal step failed: %s" % type(exc).__name__
    COUNTS["failed"] += 1
    return dict(out, reason=reason)


def summary():
    """One line for the public log: counts only, or None when no tender was on the portal."""
    if not any(COUNTS.values()):
        return None
    return ("tarjouspalvelu: %(read)d read, %(failed)d failed, %(skipped)d skipped "
            "for want of an account" % COUNTS)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Sign in to Tarjouspalvelu, download nothing.")
    ap.add_argument("--check", action="store_true", required=True)
    ap.parse_args(argv)
    try:
        session()
    except SignInFailed as exc:
        print("tarjouspalvelu: sign-in failed: %s" % exc)
        return 1
    print("tarjouspalvelu: signed in")
    return 0


if __name__ == "__main__":
    sys.exit(main())
