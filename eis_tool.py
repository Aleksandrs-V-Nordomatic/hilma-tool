#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
One Finnish tender, end to end: find it, download what can be downloaded, read it, say what
could not be read.

    python3 eis_tool.py day 2026-09-02 --country FI --out work   # the published window
    python3 eis_tool.py probe --country FI                       # will this address be told the truth
    python3 eis_tool.py discover --date 2026-09-02 --country FI  # the day, and its proof
    python3 eis_tool.py fetch 2026-056114 --country FI           # one notice into a home
    python3 eis_tool.py reconcile --date 2026-09-02 --country FI # the coverage controls alone
    python3 eis_tool.py scans --date 2026-09-02 --country FI     # read what no decoder could
    python3 eis_tool.py extract --pack out                       # deterministic text

WHY A SINGLE ENTRY POINT, AND WHY IT IS STILL CALLED `eis_tool.py` IN A FINNISH REPOSITORY. The
steps could live as separate scripts glued together inside a workflow file, which would put the
sequence in YAML, where it cannot be tested and cannot be run anywhere else. A VPS, a laptop and
a runner now execute the identical thing.

The name is the family's, not the portal's, and all three country tools already carry it --
Latvia's, Lithuania's and Estonia's, the last of which reads a register called RHR. A person who
has driven one has driven this, and `hilma_tool.py` would have been the fourth spelling of one
command. The portal's name is in the repository, which is where it belongs.

WHY THERE IS A `probe` HERE WHEN ESTONIA HAS NONE. Estonia's tool says, correctly, that a gate
which cannot fail teaches a reader that the failure it names does not happen. Finland's can fail,
and it fails quietly: the search index answers a second question with the first question's rows
rather than refusing, and whether it does that depends on which address is asking. Eight GitHub
runners were told the truth on 2026-09-07 and the workstation that had been asking all week was
not. So `probe` is a real question here with a real answer, and it is the first thing to run when
a night comes back strange.

ONE COUNTRY, AND IT STILL HAS TO BE NAMED. `--country FI` is not decoration and there is no
default: the destination folder is derived from it, and a tool that assumes its own country is a
tool that cannot say when it was pointed at the wrong drive. See country.py.
"""

import argparse
import country
import json
import os
import re
import sys

from console import utf8_streams


def extract(pack, with_images=False, keep_unpacked=False):
    """Deterministic text. Imported rather than shelled out, so a failure is a traceback."""
    import normalize
    argv = ["--in", pack, "--out", os.path.join(pack, "normalized")]
    if with_images:
        argv.append("--with-images")
    # The scan lane runs after this and can only read what still exists: a file that came out of
    # an archive has no downloaded original to fall back on.
    if keep_unpacked:
        argv.append("--keep-unpacked")
    return normalize.main(argv)


def read_scans(pack, model=None, limit=None, provider=None):
    """The fallback lane over files no decoder could read. Never fails the run.

    Defaults to local OCR, which needs no account and no key -- so this step works out of the box
    on any machine that has Tesseract, and degrades to a printed note rather than an error on one
    that does not. A pack whose scans stay unread is exactly as complete as it was before this
    lane existed.
    """
    import assist as assist_mod
    provider = provider or os.environ.get("ASSIST_PROVIDER", assist_mod.DEFAULT_PROVIDER)
    _, needs_key, _, _ = assist_mod.PROVIDERS.get(
        provider, assist_mod.PROVIDERS[assist_mod.DEFAULT_PROVIDER])
    api_key = (os.environ.get("%s_API_KEY" % provider.upper())
               or os.environ.get("ASSIST_API_KEY"))
    if needs_key and not api_key:
        print("scan lane skipped -- %s_API_KEY not set (the pack is complete without it)"
              % provider.upper())
        return 0
    # EVERY exception, not one class of them. This lane reads files the deterministic extractor
    # already listed as unreadable, and a pack whose scans stay unread is exactly as complete as
    # it was before the lane existed -- so nothing it does may fail a tender.
    try:
        doc = assist_mod.run(pack, model=model, api_key=api_key, provider=provider, limit=limit)
    except Exception as exc:
        print("scan lane skipped -- %s" % str(exc)[:200])
        return 0
    print("%s lane - %d read - %d deferred - %d skipped"
          % (doc["provider"], doc["read"], doc["deferred"], doc["skipped"]))
    return 0


def read_targets(source):
    """Notice numbers from a file or from the argument itself, in the order they were given.

    Both spellings because both callers are real: a workflow writes its multi-line input to a
    file, and a person on a terminal types two references separated by a comma. Deduplicated
    while keeping order, because a list naming the same tender twice would fetch it twice and
    count the day wrong.
    """
    if not source:
        return []
    raw = source
    if os.path.exists(source):
        with open(source, encoding="utf-8") as fh:
            raw = fh.read()
    out, seen = [], set()
    for token in re.split(r"[\s,]+", raw.strip()):
        # `FI:2026-056114` is how a card spells a key; the number is what the index answers to.
        token = token.split(":")[-1].strip()
        if token and token not in seen:
            seen.add(token)
            out.append(token)
    return out


def probe():
    """Will this address be told the truth? Three questions, and the middle one is the point.

    Asks the search index for two different days in quick succession. An address that is being
    rate-limited answers the second with the first day's rows, under a 200, with a matching
    `@odata.count` -- so the check is which day the rows are FROM, never the status code.
    """
    import fi_page
    import fi_targets

    ok = True
    try:
        count, rows = fi_targets.rows("2026-09-02")
        print("search   : %d rows, index count %s" % (len(rows), count))
    except Exception as exc:
        print("search   : FAILED -- %s" % str(exc)[:300])
        return 1

    try:
        # Deliberately the very next request, with no extra pause beyond the configured pace.
        count2, rows2 = fi_targets.rows("2026-09-03")
        stale = {(r.get("datePublished") or "")[:10] for r in rows2} - {"2026-09-03"}
        print("second   : %d rows, index count %s%s"
              % (len(rows2), count2, "" if not stale else "  <- carried %s" % sorted(stale)))
    except Exception as exc:
        ok = False
        print("second   : FAILED -- %s" % str(exc)[:300])
        print("           this address is being served stale answers at the configured pace of "
              "%.0fs. Raise HILMA_SEARCH_PACE, or run from a runner." % fi_page.SEARCH_PACE)

    document = fi_page.detail(36616, 56114)
    print("detail   : %s" % ("2026-056114" if document and
                             document.get("noticeNumber") == "2026-056114" else "FAILED"))
    ok = ok and bool(document)

    import net
    try:
        data, _ = net.get(fi_page.ATTACHMENT % "1c2baa08-ce40-422a-00cb-08df08cea9e0",
                          headers={"User-Agent": fi_page.UA, "Range": "bytes=0-3"}, timeout=40)
        print("cdn      : %s" % ("ZIP" if data.startswith(b"PK\x03\x04") else "NOT AN ARCHIVE"))
        ok = ok and data.startswith(b"PK\x03\x04")
    except Exception as exc:
        ok = False
        print("cdn      : FAILED -- %s" % str(exc)[:200])
    return 0 if ok else 1


def main(argv=None):
    utf8_streams()

    ap = argparse.ArgumentParser(description=__doc__.strip().split("\n")[0])
    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser("extract", help="turn a downloaded pack into text")
    p.add_argument("--pack", required=True)
    p.add_argument("--with-images", action="store_true")

    # ONE COMMAND, ONE WINDOW. It names a country and a window and gets the delivery shape every
    # country tool produces -- the same `day.json` beside the same folders, so a reader that
    # knows one country's layout knows them all. That shape is the contract between the tools,
    # and it is the reason each country can be its own repository without the reader having to
    # learn a second one.
    p = sub.add_parser("day", help="fetch one country's published window into the delivery shape")
    p.add_argument("date", help="YYYY-MM-DD in UTC, the first day of the window")
    p.add_argument("--to", default=None,
                   help="the last day. Absent means a single day. A range exists because a "
                        "night that did not run is a day nobody reads, and the index does not "
                        "publish it again")
    p.add_argument("--out", default="work")
    p.add_argument("--limit", type=int, default=None, help="stop after this many, for a trial")
    p.add_argument("--policy", default=None,
                   help="recall policy: JSON, a path to one, or FI_POLICY from the environment. "
                        "Absent means fetch everything.")
    # THE WATCH LIST TRAVELS WITH THE WINDOW, never in a run of its own. Two runs are two draws
    # at one index for one date, and two answers about what that date contained. These are
    # notices somebody is still deciding about, so the recall gate does not apply: it decides
    # what is worth fetching for the FIRST time, and these already have a card.
    p.add_argument("--targets", default=None,
                   help="notice numbers to re-read whatever the gate would say -- a file of "
                        "them, one per line, or the numbers themselves separated by commas")
    p.add_argument("--no-reconcile", action="store_true",
                   help="skip the coverage controls. For a trial run, never for a night")
    country.add_argument(p)

    p = sub.add_parser("probe", help="will this address be told the truth by the search index")
    country.add_argument(p)

    p = sub.add_parser("discover", help="one window's notices, without fetching any of them")
    p.add_argument("--date", required=True, help="YYYY-MM-DD, UTC")
    p.add_argument("--to", default=None)
    p.add_argument("--json", action="store_true")
    country.add_argument(p)

    p = sub.add_parser("fetch", help="one notice into a home, by the number a person quotes")
    p.add_argument("reference")
    p.add_argument("--out", default="work")
    country.add_argument(p)

    p = sub.add_parser("reconcile", help="the coverage controls alone, without a delivery")
    p.add_argument("--date", required=True, help="YYYY-MM-DD, UTC")
    p.add_argument("--to", default=None)
    p.add_argument("--no-ted", action="store_true")
    p.add_argument("--json", action="store_true")
    country.add_argument(p)

    # THE FALLBACK LANE, AS ITS OWN COMMAND AND NOT A STEP INSIDE THE DAY. It works only on the
    # queue the deterministic extractor already produced and named, and it must be able to fail
    # without touching the day.
    p = sub.add_parser("scans", help="read the files no decoder could, over one day's homes")
    p.add_argument("--out", default="work")
    p.add_argument("--date", required=True, help="the day whose homes to walk")
    p.add_argument("--model", default=None,
                   help="OCR language string, or a model name when --provider is a hosted one")
    p.add_argument("--provider", default=None)
    p.add_argument("--limit", type=int, default=None)
    country.add_argument(p)

    args = ap.parse_args(argv)

    try:
        code = country.resolve(args.country, os.environ) if args.command != "extract" else None
    except country.Mismatch as exc:
        # A stack trace here would be the tool blaming the caller for a question it simply has to
        # be asked.
        print("%s: %s" % (args.command, exc), file=sys.stderr)
        return 2

    if args.command == "extract":
        return extract(os.path.abspath(args.pack), args.with_images)

    if args.command == "probe":
        return probe()

    if args.command == "discover":
        import fi_targets
        found = fi_targets.window(args.date, args.to or args.date)
        if args.json:
            json.dump([{k: v for k, v in r.items() if k != "record"} for r in found],
                      sys.stdout, ensure_ascii=False, indent=2)
            print()
            return 0
        import fi_page
        kinds, docs = {}, {}
        for row in found:
            kinds[row["kind"]] = kinds.get(row["kind"], 0) + 1
            docs[row["documents_available"]] = docs.get(row["documents_available"], 0) + 1
        live = sum(1 for r in found if fi_page.is_live(r))
        print("%s..%s: %d notices, %d live  %s  documents %s"
              % (args.date, args.to or args.date, len(found), live, kinds, docs))
        return 0

    if args.command == "reconcile":
        import fi_recon
        report = fi_recon.run(args.date, args.to, ted=not args.no_ted)
        if args.json:
            json.dump(report, sys.stdout, ensure_ascii=False, indent=2)
            print()
        else:
            print(fi_recon.line(report))
        return 0 if report["ok"] else 1

    if args.command == "fetch":
        import fi_fetch
        import fi_targets
        row = fi_targets.one(args.reference)
        if row is None:
            print("fetch: no live notice numbered %s. A corrigendum supersedes and DELETES the "
                  "notice it replaces, so this is usually not a withdrawal -- look for the "
                  "successor by procedureId." % args.reference, file=sys.stderr)
            return 1
        out = os.path.join(args.out, code)
        done = fi_fetch.fetch(row, out)
        # A DAY FILE FOR ONE TENDER, so the same delivery can carry it. `deliver_fi` delivers
        # what a day names -- rightly, because `tenders/` accumulates months of them -- and a
        # fetch with no day would deliver nothing. The folder is suffixed so it can never be
        # mistaken for, or overwrite, a window's.
        import fi_day
        _, folder = fi_day.one_tender(row, done, out)
        print("%s (%s): %d of %d document(s), %d withheld, %.1f MB, documents %s -> %s"
              % (done["ref"], done["pid"], done["documents"], done["catalogued"],
                 done["withheld"], done["bytes"] / 1048576.0, done["documents_available"],
                 done["home"]))
        print("  deliver it with: python3 deliver_fi.py --out %s --date %s" % (args.out, folder))
        return 0

    if args.command == "scans":
        root = os.path.join(args.out, code)
        day_file = os.path.join(root, args.date, "day.json")
        if not os.path.exists(day_file):
            # THE DAY NAMES THE HOMES, NOT THE DIRECTORY. `tenders/` accumulates every tender
            # ever fetched onto this disk; only what this day delivered is this day's business.
            print("scans: no day at %s -- nothing to walk" % args.date, file=sys.stderr)
            return 0
        with open(day_file, encoding="utf-8") as fh:
            day = json.load(fh)
        for row in day.get("tenders", []):
            home = os.path.join(root, "tenders", str(row["pid"]))
            # A TENDER WITH NO READABLE DOCUMENTS HAS NO QUEUE, AND IS NOT A FAILURE. Eighty-nine
            # per cent of Finnish notices keep their documents behind a supplier account, so this
            # is the normal case here rather than the exception, and handing such a home to the
            # lane would print a line per tender that reads exactly like a real failure.
            if not os.path.exists(os.path.join(home, "normalized",
                                               "manifest_normalized.json")):
                continue
            read_scans(home, model=args.model, limit=args.limit, provider=args.provider)
        return 0

    if args.command == "day":
        import fi_day
        import fi_recon
        out = os.path.join(args.out, code)
        day, _ = fi_day.run(args.date, out, args.limit, policy=args.policy,
                            watch=read_targets(args.targets), date_to=args.to,
                            reconcile=not args.no_reconcile)
        print("%s %s..%s: %d/%d delivered, %d gated, %d closed, %d document(s) -> %s"
              % (code, day["window"]["from"], day["window"]["to"],
                 day["coverage"]["delivered"], day["coverage"]["targets"],
                 day["coverage"]["gated"], day["coverage"]["closed"],
                 day["counts"]["documents"], out))
        if day.get("reconciliation"):
            print("  %s" % fi_recon.line(day["reconciliation"]))
        # NAMED, NOT COUNTED. "5 of 41" is also what a heavily gated day looks like, so a short
        # day that only reported a number would be indistinguishable from a normal one.
        for row in day.get("lost", []):
            print("  lost %s (%s): %s" % (row.get("ref") or row.get("pid"),
                                          row.get("kind") or "?", row.get("reason")),
                  file=sys.stderr)
        for ref in day.get("unverified_links", []):
            print("  the detail API does not agree that %s is at the link built for it" % ref,
                  file=sys.stderr)
        return 0 if day["complete"] else 1

    raise AssertionError("unreachable: argparse rejects any other command")


if __name__ == "__main__":
    sys.exit(main())
