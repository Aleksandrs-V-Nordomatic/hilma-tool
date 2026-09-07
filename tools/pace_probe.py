#!/usr/bin/env python3
"""How fast may the search index be asked before it starts repeating itself?

Not part of the tool. A measurement, kept in the repository because the number it produces is
the one that decides how long a catch-up window takes, and because the failure it looks for is
invisible from every other angle: the endpoint answers a second question with the FIRST
question's rows, under a 200, with a matching `@odata.count`, and nothing in the body says the
filter was not applied.

    python3 tools/pace_probe.py --gap 20

Prints one line per request saying which day the answer actually contained. Every line OK means
the gap is safe. A STALE line names a gap that is not, and the day it echoed instead.
"""
import argparse
import collections
import datetime
import json
import sys
import time
import urllib.parse
import urllib.request

SEARCH = "https://www.hankintailmoitukset.fi/search/eformnotices"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; eis-tool)", "Accept": "application/json"}

# Six published days with different, known totals. Different totals are the point: two days that
# happened to hold the same number of notices could not tell a stale answer from a fresh one on
# the count alone, which is why the check below compares the DATES in the rows.
DAYS = ("2026-09-01", "2026-09-02", "2026-09-03", "2026-09-04", "2026-09-05", "2026-09-08")


def ask(day, timeout=40):
    nxt = (datetime.date.fromisoformat(day) + datetime.timedelta(days=1)).isoformat()
    query = urllib.parse.urlencode({
        "search": "*", "$count": "true", "$top": "1000",
        "$filter": "datePublished ge %sT00:00:00Z and datePublished lt %sT00:00:00Z"
                   % (day, nxt)})
    request = urllib.request.Request("%s?%s" % (SEARCH, query), headers=HEADERS)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = json.loads(response.read().decode("utf-8"))
    rows = body.get("value") or []
    days = collections.Counter((r.get("datePublished") or "")[:10] for r in rows)
    return body.get("@odata.count"), rows, dict(sorted(days.items()))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.strip().split("\n")[0])
    ap.add_argument("--gap", type=float, default=20.0)
    ap.add_argument("--settle", type=float, default=70.0,
                    help="idle seconds before the first request, so it is known to be honest")
    args = ap.parse_args(argv)

    print("settling for %.0fs so the first answer is a real one" % args.settle)
    time.sleep(args.settle)

    stale = 0
    for index, day in enumerate(DAYS):
        if index:
            time.sleep(args.gap)
        count, rows, days = ask(day)
        # An empty answer is honest when the index agrees it is empty -- a future date, or a day
        # this country did not publish on. It is only a lie when rows arrive from another day.
        honest = (set(days) == {day}) or (not rows and count == 0)
        stale += 0 if honest else 1
        print("  gap %-4.0fs asked %s -> %-5s count=%-5s rows=%-4d got=%s"
              % (args.gap if index else 0, day, "OK" if honest else "STALE",
                 count, len(rows), days))
        sys.stdout.flush()

    print("%d of %d answers were the wrong day at a %.0fs gap" % (stale, len(DAYS), args.gap))
    return 1 if stale else 0


if __name__ == "__main__":
    sys.exit(main())
