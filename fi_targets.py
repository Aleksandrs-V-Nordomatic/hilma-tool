"""Discovery for Finland: which notices a window contains, and the proof that it is all of them.

One request answers a whole window, and the rows come back already carrying the title in four
languages, the buyer, the CPV codes, the description, the deadline, the value and every flag the
gate could want. There is no pager to walk for an ordinary day and no register to resolve
against: the search IS the register, and it states its own total in `@odata.count`.

That total is the coverage proof, and it comes free. It is also the only thing standing between
this tool and four ways of getting a wrong answer under a 200.

FIRST, AND WORST: THE INDEX ANSWERS A SECOND QUESTION WITH THE FIRST QUESTION'S ROWS. Not a 429,
not an error -- the previous response, whole, with its own `@odata.count`, and the filter that
was just sent nowhere in it. Measured 2026-09-07: six different days asked two seconds apart from
a workstation returned the first day six times over. At twenty seconds, honest six times out of
six; from a GitHub runner, honest at five. So the limiter is per address and the pace is a matter
of which address is asking -- which means a pace can never be the guard. The guard is that every
row is checked against the window it was asked for, and an answer carrying somebody else's day is
thrown away and asked for again. See `_ask`.

SECOND: A DOLLAR LOST IN TRANSIT DROPS THE FILTER SILENTLY, and the endpoint then answers with
the default page of the whole 157 981-document index. Same shape, same status, wrong century.
`fi_page.search` builds every query through `urlencode`, which percent-encodes it; the row check
below is what would catch it if that ever stopped being true.

THIRD: `$top` IS A PAGE, NOT A PROMISE. A published day runs to about a hundred notices and a
fortnight's catch-up to about fourteen hundred, so a window wider than a few days needs paging.
`@odata.count` says how many there are and the run keeps asking until it holds that many -- and
stops if a page repeats itself, because a repeated page is the first failure wearing a hat.

FOURTH: THE INDEX IS IN UTC AND FINLAND IS NOT. `datePublished` is UTC, Finland runs at UTC+2 or
+3, and notices are published as late as 18:36 UTC. A window built from a Helsinki-local
"yesterday" and applied to a UTC field silently drops or duplicates the evening. Every date in
this file is UTC and says so.

    python3 fi_targets.py 2026-09-02                 # one published day
    python3 fi_targets.py 2026-08-25 2026-09-04      # a range, inclusive of both ends
    python3 fi_targets.py --ref 2026-056114          # one notice, by the number a person quotes
"""
import argparse
import datetime
import json
import sys
import time

import fi_page

# What one request will carry. Azure Cognitive Search caps a page at a thousand rows; a Finnish
# publication day has never exceeded 101 in anything measured, so this pages only on a catch-up.
PAGE = 1000

# How many times an answer that is plainly not ours is asked for again before the run stops. The
# waits are long because the thing being waited out is a rate limiter with a slow refill, and a
# run that gave up after two tries on a throttled address would report a broken register.
STALE_TRIES = 5
STALE_BACKOFF = (10.0, 25.0, 45.0, 75.0)

# SATURDAY IS THE ONLY QUIET DAY, AND SUNDAY IS NOT ONE. The batch that forwards notices to TED
# runs on Sundays -- 2026-08-30 carried 72 notices, 65 of them EU -- while Saturday carried 5.
# A weekday-only expectation would call a Sunday's whole batch a broken crawl, and a
# weekday-only SCHEDULE would lose it. Saturday is therefore the one day an empty answer is not
# evidence of anything.
QUIET_WEEKDAY = 5


class Truncated(RuntimeError):
    """The index says the window holds more rows than the run managed to hold.

    Raised rather than returned. A window that came back short cannot say what it missed --
    unlike a target that failed, which the day names in `lost` -- so there is nothing honest to
    deliver and no way for a reader to know the day is incomplete.
    """


class FilterIgnored(RuntimeError):
    """The index answered, and the answer is not the question that was asked.

    The one failure this route produces that looks exactly like success, and the reason every
    row is checked rather than counted. See the module docstring.
    """


def _utc_day(day):
    """The half-open UTC interval one calendar day occupies, as the index spells it."""
    start = datetime.date.fromisoformat(day)
    return ("%sT00:00:00Z" % start.isoformat(),
            "%sT00:00:00Z" % (start + datetime.timedelta(days=1)).isoformat())


def days(date_from, date_to):
    """Every calendar day the caller asked about, inclusive of both ends, in UTC.

    Public because the day driver asks the same question of a window -- whether any day in it is
    one this country publishes on, which is what makes an empty answer a broken crawl rather than
    a quiet weekend. Two modules deriving that list separately is two places for the arithmetic
    to differ.
    """
    start = datetime.date.fromisoformat(date_from)
    end = datetime.date.fromisoformat(date_to)
    if end < start:
        raise ValueError("the window ends before it begins: %s .. %s" % (date_from, date_to))
    out, day = [], start
    while day <= end:
        out.append(day.isoformat())
        day = day + datetime.timedelta(days=1)
    return out


def publishing_days(date_from, date_to):
    """The days in the window on which Finland would be expected to publish something."""
    return [d for d in days(date_from, date_to)
            if datetime.date.fromisoformat(d).weekday() != QUIET_WEEKDAY]


def _window_filter(date_from, date_to):
    since, _ = _utc_day(date_from)
    _, until = _utc_day(date_to)
    return "datePublished ge %s and datePublished lt %s" % (since, until)


def _ask(criteria, skip=0, top=PAGE, order="datePublished asc,id asc", inside=None):
    """One page, re-asked until the index answers the question it was given.

    `inside` is the set of UTC dates the rows must fall inside. An answer carrying a date from
    outside it is the stale-response failure, and the only honest thing to do with it is throw it
    away and wait longer -- returning it would deliver another day's notices under this day's
    name, and counting it would agree with `@odata.count` while doing so.
    """
    last = None
    for attempt in range(STALE_TRIES):
        body = fi_page.search({"search": "*", "count": "true", "top": top, "skip": skip or None,
                               "orderby": order, "filter": criteria})
        rows = body.get("value") or []
        count = body.get("@odata.count")
        if count is None:
            raise FilterIgnored("the index answered without @odata.count, so it cannot be "
                                "asked to prove the window: %s" % str(body)[:200])
        outside = ([r for r in rows if (r.get("datePublished") or "")[:10] not in inside]
                   if inside is not None else [])
        if not outside:
            return count, rows
        last = "%d of %d rows fall outside the window" % (len(outside), len(rows))
        if attempt == STALE_TRIES - 1:
            break
        wait = STALE_BACKOFF[min(attempt, len(STALE_BACKOFF) - 1)]
        fi_page.log("  the index answered with another window's rows (%s); waiting %.0fs"
                    % (last, wait))
        time.sleep(wait)
    raise FilterIgnored(
        "the index would not answer the window it was asked for after %d attempts -- %s. It "
        "serves the PREVIOUS request's rows rather than refusing, so this is a rate limit "
        "wearing a 200. Raise HILMA_SEARCH_PACE, or run from another address."
        % (STALE_TRIES, last))


def rows(date_from, date_to=None):
    """Every index row published in the window, both ends inclusive, and the index's own total.

    Returns `(count, rows)`. The count is what the index says the window holds; the rows are what
    this run actually holds. The day is not delivered unless they agree, and that comparison is
    the whole coverage proof -- it is not derived from anything this tool did, which is what
    makes it worth something.
    """
    date_to = date_to or date_from
    inside = set(days(date_from, date_to))
    criteria = _window_filter(date_from, date_to)

    count, page = _ask(criteria, inside=inside)
    held, seen = [], set()
    while True:
        fresh = 0
        for record in page:
            identity = str(record.get("id") or "")
            if identity and identity in seen:
                continue
            seen.add(identity)
            held.append(record)
            fresh += 1
        if len(held) >= count or not page:
            break
        if not fresh:
            # A page that added nothing is the stale answer again, arriving as a repeat rather
            # than as a foreign date -- the one shape the row check above cannot see.
            raise FilterIgnored(
                "page %d repeated the previous page's rows, so the index is not paging. "
                "%d of %d rows held." % (len(held) // PAGE + 1, len(held), count))
        count, page = _ask(criteria, skip=len(held), inside=inside)

    if len(held) != count:
        raise Truncated(
            "the index says %s notices were published in %s .. %s and this run holds %d. The "
            "rows it did not serve are named nowhere, so there is nothing honest to deliver."
            % (count, date_from, date_to, len(held)))
    return count, held


def window(date_from, date_to=None):
    """The window, in the small shape the day and the gate work with."""
    _, held = rows(date_from, date_to)
    return [fi_page.notice(record) for record in held]


def one(reference):
    """The row for a single notice, found by the number a person quotes.

    `noticeNumber` and not `noticeId`: the index holds two id namespaces that overlap, and
    `noticeId 55293` resolves to a legacy notice from 2020 while `2026-055293` was a separate
    eForms notice. Keyed on the wrong one, a watched card reports somebody else's tender.
    """
    token = str(reference).strip().split(":")[-1].strip()
    if not token:
        return None
    _, page = _ask("noticeNumber eq '%s'" % token.replace("'", "''"), top=10, order=None)
    exact = [r for r in page if str(r.get("noticeNumber") or "") == token]
    if not exact:
        # A NOTICE THAT IS GONE IS NORMAL HERE, AND IT IS NOT A WITHDRAWAL. A corrigendum
        # supersedes its predecessor and the index then DELETES the predecessor -- four for four
        # in the measured sample. A run that stored a notice number and re-checks it will find
        # its own row missing, and the successor is found through `procedureId`, not by
        # concluding the tender was cancelled.
        return None
    return fi_page.notice(exact[0])


def successor(procedure_id):
    """The notices currently live for one procedure, newest first.

    This is how a superseded notice number is followed. `procedureId` is the tender's identity
    and survives every corrigendum; the notice number does not.
    """
    _, page = _ask("procedureId eq %d" % int(procedure_id), top=50,
                   order="datePublished desc")
    return [fi_page.notice(r) for r in page]


def verify(row):
    """Does the link on this card open the notice it claims to? The only check that can fail.

    EVERY RENDERED PAGE ON THIS PORTAL RETURNS 200 WITH THE SAME 9 684-BYTE SHELL, including for
    ids that were invented. A link checker reading the status would pass every URL it ever built.
    The detail API discriminates -- it 404s honestly -- so the check is: call it for the same id
    pair, require a notice back, and require its number to match the row's.

    Returns None when the pair is not one this check applies to (a plan has no notice, a legacy
    row is not served by this API), so a caller can tell "verified" from "not applicable".
    """
    if row.get("shape") != "eforms" or not (row.get("procedure_id") and row.get("notice_id")):
        return None
    document = fi_page.detail(row["procedure_id"], row["notice_id"])
    if document is None:
        return False
    return str(document.get("noticeNumber") or "") == str(row.get("ref") or "")


def main(argv=None):
    ap = argparse.ArgumentParser(description="What Hilma published in a window, in UTC.")
    ap.add_argument("date_from", nargs="?", help="YYYY-MM-DD, UTC")
    ap.add_argument("date_to", nargs="?")
    ap.add_argument("--ref", default=None, help="one notice, by its number")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if args.ref:
        found = [r for r in [one(args.ref)] if r]
    elif args.date_from:
        found = window(args.date_from, args.date_to or args.date_from)
    else:
        ap.error("name a date, or --ref")

    if args.json:
        json.dump([{k: v for k, v in r.items() if k != "record"} for r in found],
                  sys.stdout, ensure_ascii=False, indent=2)
        print()
        return found

    by_kind = {}
    for row in found:
        by_kind[row["kind"]] = by_kind.get(row["kind"], 0) + 1
    print("%d row(s) %s" % (len(found), by_kind))
    for row in found[:25]:
        print("  %-13s %-12s %-8s %s" % (row["ref"], row["kind"], row["documents_available"],
                                         (row["title"] or "")[:52]))
    return found


if __name__ == "__main__":
    main()
