"""The small procurements Hilma never sees, from Tarjouspalvelu's own public list.

Hilma carries every notice the law makes a buyer publish. A procurement under the national
threshold -- a `pienhankinta`, a lightened competition, a below-threshold notice -- is not one of
them, and many buyers put it on Tarjouspalvelu alone. Measured on 11 Nov 2026: one page of the
portal's list held 500 publications over seven days, 434 of them eForms notices that Hilma also
carries and about 64 competitions that exist nowhere else.

THE LIST IS PUBLIC AND NEEDS NO ACCOUNT. It is the portal's front-page search:

    POST https://tarjouspalvelu.fi/Default/Hae   sivunumero, ilmoituksiaPerSivu=500, sortingKey

sorted newest first, so a one-day window is the first page and a catch-up walks back until the
publication dates pass the window's start. Each row carries the buyer, the title, the notice
type, the whole description, the publication time and the deadline -- enough for the recall gate
to judge it without a second request.

A ROW WITH AN EFORMS CODE IS ALREADY IN HILMA. The type label says so: `Kilpailu [E3]`,
`Tulokset [TED eF[29]]`. Those are left to Hilma, which proves its window and links the notice;
taking them here as well would put one tender on the board twice. What is left is the label
without a code, and of that only the competitions: a direct-award notice or a sale has nothing to
bid for.

A tender found here lives at `tenders/tp-<tpId>/`, its `ref` is `TP-<tpId>`, and its documents
come through `fi_cloudia` exactly as a Hilma tender's on the same portal do. The row's link goes
through the portal's own redirect; this module never follows it, because opening a tender under
an account creates a draft response.

    python3 fi_tp_list.py --date 2026-11-10        # print the window's rows, fetch nothing
"""
import argparse
import datetime
import html
import re
import sys
import urllib.parse
import urllib.request

import net

URL = "https://tarjouspalvelu.fi/Default/Hae"
LINK = "https://tarjouspalvelu.fi/UX/TP/SiirryTarjouspyyntoon/?tpId=%s"
PER_PAGE = 500
# A week of the portal fits on one page, so twenty pages is several months -- more than any
# catch-up asks for, and a bound on a list that stopped sorting.
MAX_PAGES = 20
UA = "Mozilla/5.0 (compatible; eis-tool)"

# Labels without an eForms code that are not competitions. Matched as folded substrings.
NOT_COMPETITIONS = ("suorahankin", "ulosmyynti", "tulokset", "sopimusmuutos")


def _text(fragment):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment or ""))).strip()


def _when(block):
    """`10.11.2026 16.17 (UTC+02:00)` as an ISO time in UTC, or None."""
    m = re.search(r"<span>\s*(\d{1,2})\.(\d{1,2})\.(\d{4})\s*</span>\s*<span>\s*(\d{1,2})\.(\d{2})"
                  r"\s*</span>(?:\s*<span[^>]*>\s*\(UTC([+-])(\d{2}):(\d{2})\)\s*</span>)?", block)
    if not m:
        return None
    day, month, year, hour, minute = (int(x) for x in m.groups()[:5])
    sign, oh, om = m.group(6), int(m.group(7) or 0), int(m.group(8) or 0)
    offset = datetime.timedelta(hours=oh, minutes=om) * (-1 if sign == "-" else 1)
    local = datetime.datetime(year, month, day, hour, minute, tzinfo=datetime.timezone(offset))
    return local.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _column(row, label):
    """The column whose screen-reader label is `label`, up to the next column."""
    m = re.search(r'label-xl-aria">\s*%s\s*</div>(.*?)(?=<div class="tp-list__column|</a>)'
                  % label, row, re.S)
    return m.group(1) if m else ""


def parse(page):
    """Every row of one list page, as the portal states it."""
    out = []
    for row in page.split('class="tp-list__row"')[1:]:
        tp = re.search(r"SiirryTarjouspyyntoon/\?tpId=(\d+)", row)
        if not tp:
            continue
        title = re.search(r'<span class="text-bold">(.*?)</span>', row, re.S)
        label = re.search(r'<div class="font-075-rem">(.*?)</div>', row, re.S)
        described = re.search(r'Kuvaus\s*</div>\s*<span title="([^"]*)"', row, re.S)
        buyer = re.search(r'tp-list__column--hy">.*?Organisaatio\s*</div>(.*?)</div>', row, re.S)
        out.append({
            "tp_id": tp.group(1),
            "buyer": _text(buyer.group(1)) if buyer else None,
            "title": _text(title.group(1)) if title else None,
            "label": _text(label.group(1)) if label else "",
            "description": html.unescape(described.group(1)).strip() if described else None,
            "published": _when(_column(row, "Julkaisuaika")),
            "deadline": _when(_column(row, "M&#228;&#228;r&#228;aika") or _column(row, "Määräaika")),
        })
    return out


def in_hilma(item):
    """An eForms notice -- a code in brackets after its type -- is published in Hilma too."""
    return "[" in (item.get("label") or "")


def competition(item):
    label = (item.get("label") or "").casefold()
    return bool(label) and not any(word in label for word in NOT_COMPETITIONS)


def notice(item):
    """One row in the shape `fi_page.notice` gives a Hilma tender, so the day, the gate and the
    fetch take it unchanged. Everything Hilma would add and this list does not state is empty,
    not guessed."""
    tp = item["tp_id"]
    return {
        "pid": "tp-%s" % tp, "ref": "TP-%s" % tp, "tp_id": tp,
        "source": "Tarjouspalvelu", "notice_type": item.get("label"),
        "procedure_id": None, "notice_id": None, "row_id": None, "shape": "tarjouspalvelu",
        "kind": "tender", "main_type": None, "type": None,
        "title": item.get("title"), "titles": item.get("title"),
        "description": item.get("description"),
        "buyer": item.get("buyer"), "buyer_id": None, "buyer_type": None,
        "published": item.get("published"), "deadline": item.get("deadline"),
        "expires": None, "cpv": [], "cpv_main": None, "cpv_name": None, "nuts": None,
        "procedure": None, "value": None, "framework": False, "dps": False,
        "eu": False, "national": True, "corrigendum": False, "cancelled": False,
        "supersedes": [], "ted_id": None,
        "documents_url": LINK % tp, "documents_available": "external",
        "documents_host": "tarjouspalvelu.fi", "tendering_in_hilma": False,
        "link": LINK % tp, "record": None,
    }


def page(number, timeout=120, opener=None):
    body = urllib.parse.urlencode({"sivunumero": number, "ilmoituksiaPerSivu": PER_PAGE,
                                   "sortingKey": "PublishDateSortDescending",
                                   "hakusana": "", "nappihaku": "true"}).encode()
    request = urllib.request.Request(URL, data=body, headers={
        "User-Agent": UA, "Content-Type": "application/x-www-form-urlencoded"})
    data, _ = net.open_url(request, timeout=timeout, opener=opener)
    return data.decode("utf-8", "replace")


def window(date_from, date_to, fetch=None, max_pages=MAX_PAGES):
    """The window's competitions that only Tarjouspalvelu publishes.

    Returns `(report, rows)`. The report says how many rows were read and why the rest were left,
    so a quiet day and a list that stopped answering do not look alike.
    """
    fetch = fetch or page
    seen, kept, report = set(), [], {"pages": 0, "rows": 0, "in_window": 0, "in_hilma": 0,
                                      "not_competition": 0, "kept": 0, "reached_start": False}
    for number in range(1, max_pages + 1):
        items = parse(fetch(number))
        report["pages"] += 1
        if not items:
            break
        oldest = None
        for item in items:
            if item["tp_id"] in seen:
                continue
            seen.add(item["tp_id"])
            report["rows"] += 1
            day = (item.get("published") or "")[:10]
            oldest = day if oldest is None or (day and day < oldest) else oldest
            if not day or not (date_from <= day <= date_to):
                continue
            report["in_window"] += 1
            if in_hilma(item):
                report["in_hilma"] += 1
            elif not competition(item):
                report["not_competition"] += 1
            else:
                kept.append(notice(item))
        if oldest and oldest < date_from:
            report["reached_start"] = True
            break
    report["kept"] = len(kept)
    return report, kept


def main(argv=None):
    ap = argparse.ArgumentParser(description="Tarjouspalvelu's own competitions for a window.")
    ap.add_argument("--date", required=True, help="YYYY-MM-DD, UTC; the first day")
    ap.add_argument("--to", default=None)
    args = ap.parse_args(argv)
    report, rows = window(args.date, args.to or args.date)
    print("tarjouspalvelu list %s..%s: %s" % (args.date, args.to or args.date, report))
    for row in rows:
        print("  %-10s %-20s %s" % (row["ref"], (row["deadline"] or "")[:16], row["title"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
