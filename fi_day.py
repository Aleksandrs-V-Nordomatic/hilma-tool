"""One Finnish window, delivered: `day.json`, `changes.json`, and a home per tender.

WHAT A DAY IS. The two files a reader already knows from every country tool in this family:
`changes.json` says what moved, `day.json` says what the window contains and is written LAST, so
a reader that lists folders instead of reading it reads the wrong day. Neither holds tender
bytes; both point at `tenders/<pid>/`, which is permanent and shared across days.

A WINDOW, NOT A DATE. A missed night is a day of notices nobody reads, and the index does not
publish them again. So the run takes a range and the caller decides what it covers -- usually
yesterday, and after an outage the days that were lost. One request answers a fortnight as easily
as a day, and the day folder is named for the end of the range so the delivery keeps its shape.

THE WINDOW IS IN UTC AND FINLAND IS NOT, and this is not a detail. `datePublished` is UTC,
Finland runs at UTC+2 or +3, and notices are published as late as 18:36 UTC. A caller that means
"yesterday in Helsinki" and passes it to a UTC filter loses or duplicates the evening every time.
This tool's dates are UTC dates, and a scheduler that wants Helsinki days must say so itself.

THE GATE COSTS NOTHING HERE. In a country whose search returns only ids, deciding what is worth
downloading means fetching a card per notice first. This index returns the title in four
languages, the buyer, the CPV codes, the flags AND the buyer's own description -- a median of 964
characters -- in the search itself. So the whole window is gated on what discovery already
returned, and the first extra request of the run is made for a tender we have already decided to
keep.

AWARDS ARE NOT FETCHED AND ARE NOT DISCARDED EITHER. An award notice looks exactly like a tender
and they are 35% of the volume, so a run that treated them as opportunities would publish a month
of decided procurements on its first night. But an award is the answer to a card already on the
board -- the competition it closes may be one somebody is still waiting on -- so it is recorded
in `changes.json` as a close, keyed on the same `procedureId`, and it costs no request at all to
do that. The same is true of a contract modification.

    python3 fi_day.py 2026-09-02 --out work/FI --limit 5
"""
import argparse
import datetime
import json
import os
import time

import fi_fetch
import fi_page
import fi_recon
import fi_targets

try:
    import changes as changes_mod
except Exception:
    changes_mod = None

try:
    import policy as policy_mod
except Exception:
    policy_mod = None


def _read(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _write(path, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)


def _future(when):
    """Is this deadline still ahead? A card with a past one is not an opportunity."""
    if not when:
        return None
    try:
        stamp = datetime.datetime.fromisoformat(str(when).replace("Z", "+00:00"))
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=datetime.timezone.utc)
    return stamp > datetime.datetime.now(datetime.timezone.utc)


def run(date, out_root, limit=None, run_id=None, policy=None, watch=None, date_to=None,
        reconcile=True, with_text=True):
    """Fetch the window's live notices into homes and write the day's two files.

    `watch` is the notice numbers somebody is still deciding about -- the cards whose decision has
    not been settled. They ride in the SAME pass as the window, deduplicated against it: two
    passes are two draws at one index for one date, and two answers about what that date held.

    THE GATE DOES NOT APPLY TO THEM. The gate decides what is worth fetching for the first time;
    a watched tender was already judged worth a card by a person, and dropping it here would
    silently stop answering the question the card is open for.
    """
    run_id = run_id or time.strftime("%Y%m%dT%H%M%S", time.gmtime())
    date_from = date
    date_to = date_to or date
    folder = date_to                       # named for the end of the range

    index_count, raw = fi_targets.rows(date_from, date_to)
    targets = [fi_page.notice(record) for record in raw]

    # AN EMPTY WINDOW OVER DAYS FINLAND PUBLISHES ON IS A BROKEN CRAWL, AND NOTHING ELSE WOULD
    # SAY SO. Saturday is the one quiet day -- 5 notices measured against 72 on the Sunday, which
    # is when the batch that forwards to TED runs. Every way discovery can break returns an empty
    # list rather than an error, and left unsaid that becomes a green run, a complete day, an
    # empty morning, and nothing to tell it from a public holiday.
    discovery_failed = bool(fi_targets.publishing_days(date_from, date_to)) and not targets

    live = [t for t in targets if fi_page.is_live(t)]
    # Recorded without a request. See the module docstring.
    closed = [{"pid": t["pid"], "ref": t["ref"], "kind": t["kind"], "title": t["title"],
               "buyer": t["buyer"], "published": t["published"], "link": t["link"]}
              for t in targets if not fi_page.is_live(t)]

    if limit:
        live = live[:limit]

    # THE GATE FIRES BEFORE A BYTE MOVES, and here it fires before a REQUEST moves: the search
    # row carries everything it reads. It is `policy.outside_scope` unchanged -- a CPV code means
    # the same thing everywhere and the terms are a file, so the gate never needed a country of
    # its own.
    rules = policy_mod.load_policy(policy) if policy_mod is not None else None

    moves, delivered, failed, gated = [], [], [], []

    known = {t["pid"] for t in live}
    for reference in (watch or []):
        try:
            row = fi_targets.one(reference)
        except Exception as exc:
            failed.append({"ref": str(reference), "pid": None, "kind": None, "watched": True,
                           "reason": "the index did not answer: %s" % str(exc)[:150]})
            continue
        if row is None:
            # A WATCHED NOTICE THAT IS GONE IS USUALLY NOT A WITHDRAWAL. A corrigendum supersedes
            # its predecessor and the index then deletes the predecessor -- four for four in the
            # measured sample, and corrigenda are 6.6% of rows. So the successor is looked for
            # through the procedure before this is called a hole.
            failed.append({"ref": str(reference), "pid": None, "kind": None, "watched": True,
                           "reason": "no live notice with this number -- a corrigendum "
                                     "supersedes and deletes the notice it replaces, so look "
                                     "for the successor by procedureId"})
            continue
        if row["pid"] in known:
            for target in live:
                if target["pid"] == row["pid"]:
                    target["watched"] = True
            continue
        row["watched"] = True
        live.append(row)
        known.add(row["pid"])

    for target in live:
        pid = target["pid"]
        home = os.path.join(out_root, "tenders", pid)
        previous = _read(os.path.join(home, "state.json"))

        if rules is not None and not target.get("watched"):
            if policy_mod.outside_scope(target, rules):
                # Named, never merely dropped. A tender nobody fetched and nobody mentioned reads
                # exactly like a tender that does not exist -- and the list of what was cut is
                # the fastest way to see a badly tuned word list.
                gated.append({"pid": pid, "ref": target["ref"], "kind": target["kind"],
                              "title": target["title"], "buyer": target["buyer"],
                              "cpv": target["cpv"], "link": target["link"]})
                continue

        try:
            done = fi_fetch.fetch(target, out_root, with_text=with_text)
        except Exception as exc:                      # one tender must not lose the day
            failed.append({"pid": pid, "ref": target["ref"], "kind": target["kind"],
                           "watched": bool(target.get("watched")),
                           "reason": str(exc)[:200]})
            continue

        record = None
        if changes_mod is not None and done.get("state") is not None:
            record = changes_mod.diff(previous, done["state"], date=folder, run_id=run_id)
        status = (record or {}).get("status") or ("new" if previous is None else "changed")
        moves.append({
            "pid": pid, "ref": target["ref"], "kind": target["kind"], "status": status,
            # Which population this came from. A reader answers two different questions of the
            # two -- "is this worth a card" and "has what I am waiting on moved" -- and a day
            # that did not say which was which would make them guess from the date.
            "watched": bool(target.get("watched")),
            "title": target["title"], "buyer": target["buyer"],
            "published": target["published"], "deadline": done.get("deadline"),
            "deadline_future": _future(done.get("deadline")),
            "value": done.get("value"), "cpv_main": done.get("cpv_main"),
            "documents_available": done.get("documents_available"),
            "link_verified": done.get("link_verified"),
            # A corrigendum names every notice it supersedes, and those rows are already gone
            # from the index. A reader following one of them needs to be told where it went.
            "supersedes": target.get("supersedes") or [],
            "documents": done["documents"],
            "withheld": done["withheld"],
            "uncatalogued": done["uncatalogued"],
            "moved": (record or {}).get("moves") or [],
        })
        delivered.append({
            "pid": pid, "ref": target["ref"], "kind": target["kind"], "status": status,
            "watched": bool(target.get("watched")),
            "home": "tenders/%s" % pid,
            "documents": done["documents"], "bytes": done["bytes"],
            "documents_available": done.get("documents_available"),
            "link_verified": done.get("link_verified"),
            "title": target["title"],
        })

    by_status = {}
    for row in moves:
        by_status[row["status"]] = by_status.get(row["status"], 0) + 1
    watched_count = sum(1 for row in moves if row["watched"])

    # WHOSE FAILURE MAKES A DAY SHORT. The day is the window; a watched card is a standing
    # question somebody asked of it. A watched notice the index will not serve is a hole in the
    # watch and is reported as one -- but it is not the window arriving short, and letting it say
    # so would mark every night incomplete until a person edited the board, which teaches a
    # reader to ignore the flag exactly when it starts meaning something.
    lost_window = [f for f in failed if not f.get("watched")]
    lost_watch = [f for f in failed if f.get("watched")]

    proof = None
    if reconcile:
        proof = fi_recon.run(date_from, date_to, records=targets, count=index_count)

    # A LINK THIS RUN COULD NOT VERIFY IS NOT PUBLISHED AS VERIFIED. Every rendered page on this
    # portal answers 200, so `False` here means the detail API disagreed with the row -- which is
    # a wrong link, not a slow one.
    bad_links = [d["ref"] for d in delivered if d.get("link_verified") is False]
    # Measured: a deadline is present on 50 of 50 contract notices and null on 128 of 130 awards.
    # A live tender with no deadline is therefore not normal, and is worth a reader's eye.
    no_deadline = [m["ref"] for m in moves if m["kind"] == "tender" and not m["deadline"]]

    complete = (not lost_window and not discovery_failed and not bad_links
                and (proof is None or proof["ok"]))

    common = {"date": folder, "window": {"from": date_from, "to": date_to},
              "country": "FI", "run_id": run_id,
              "written_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
              "complete": complete, "discovery_failed": discovery_failed}

    changes = dict(common, **{
        "schema": "day-changes/1",
        "counts": dict(by_status, tenders=len(moves), gated=len(gated),
                       watched=watched_count, closed=len(closed)),
        "gated": gated,
        # Awards and contract modifications, which cost no request and answer a card already on
        # the board.
        "closed": closed,
        "tenders": moves,
    })
    _write(os.path.join(out_root, folder, "changes.json"), changes)

    day = dict(common, **{
        "schema": "day/1", "source": "Hilma",
        "changes_path": "%s/changes.json" % folder,
        "tenders_path": "tenders",
        "coverage": {"index_count": index_count, "rows_held": len(targets),
                     "live": len(live), "targets": len(live), "delivered": len(delivered),
                     "gated": len(gated), "failed": len(lost_window),
                     "watch_holes": len(lost_watch), "closed": len(closed)},
        "counts": dict(by_status, tenders=len(delivered), gated=len(gated),
                       watched=watched_count, closed=len(closed),
                       documents=sum(t["documents"] for t in delivered),
                       bytes=sum(t["bytes"] for t in delivered)),
        "reconciliation": proof,
        "unverified_links": bad_links,
        "tenders_without_deadline": no_deadline,
        "lost": failed,
        "tenders": delivered,
        # KEPT IN THE SHAPE THE OTHER COUNTRIES DELIVER, with exactly one shard, always present.
        # Latvia's day is four shards because its register refuses a third of the runner
        # addresses it is offered; Hilma refused none of eight, so Finland runs on one runner --
        # and a reader of `day.json` should not have to know which country it is holding.
        "shards": ["hilma-day-shard-1"],
    })
    _write(os.path.join(out_root, folder, "day.json"), day)   # last, as the contract says
    return day, changes


def one_tender(record, done, out_root, run_id=None):
    """The delivery shape for a single hand-asked tender, so the same delivery can carry it.

    WHY IT GETS A DAY FILE AT ALL. `deliver_fi` delivers what a day names, which is right: the
    `tenders/` folder accumulates every tender ever fetched onto a disk and only what a run
    produced is that run's business. A single fetch with no day would therefore deliver nothing.

    AND WHY THE FOLDER IS NOT NAMED FOR A DATE. A window's folder is its last day. If a hand
    fetch wrote `2026-09-08/day.json` naming one tender, the night that later delivers the whole
    of 2026-09-08 would replace it -- or, worse, arrive first and be replaced BY it, leaving a
    date whose `day.json` claims Finland published one notice. The suffix makes the two kinds of
    folder impossible to confuse and impossible to collide, and `single_fetch` says in the file
    what the folder name says on the outside.
    """
    run_id = run_id or time.strftime("%Y%m%dT%H%M%S", time.gmtime())
    today = time.strftime("%Y-%m-%d", time.gmtime())
    folder = "%s-fetch-%s" % (today, record["ref"])

    row = {"pid": record["pid"], "ref": record["ref"], "kind": record["kind"],
           "status": "fetched", "watched": True, "home": "tenders/%s" % record["pid"],
           "documents": done["documents"], "bytes": done["bytes"],
           "documents_available": done.get("documents_available"),
           "link_verified": done.get("link_verified"), "title": record["title"]}

    common = {"date": folder, "window": {"from": today, "to": today},
              "country": "FI", "run_id": run_id,
              "written_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
              # A single fetch proves nothing about a window and does not pretend to. It is
              # complete as what it is: one tender somebody asked for, and it arrived.
              "complete": True, "discovery_failed": False, "single_fetch": True}

    changes = dict(common, **{
        "schema": "day-changes/1",
        "counts": {"tenders": 1, "gated": 0, "watched": 1, "closed": 0},
        "gated": [], "closed": [],
        "tenders": [dict(row, deadline=done.get("deadline"), value=done.get("value"),
                         cpv_main=done.get("cpv_main"), published=record["published"],
                         buyer=record["buyer"], supersedes=record.get("supersedes") or [],
                         deadline_future=_future(done.get("deadline")),
                         withheld=done["withheld"], uncatalogued=done["uncatalogued"],
                         moved=[])],
    })
    _write(os.path.join(out_root, folder, "changes.json"), changes)

    day = dict(common, **{
        "schema": "day/1", "source": "Hilma",
        "changes_path": "%s/changes.json" % folder,
        "tenders_path": "tenders",
        "coverage": {"index_count": None, "rows_held": 1, "live": 1, "targets": 1,
                     "delivered": 1, "gated": 0, "failed": 0, "watch_holes": 0, "closed": 0},
        "counts": {"tenders": 1, "gated": 0, "watched": 1, "closed": 0,
                   "documents": done["documents"], "bytes": done["bytes"]},
        "reconciliation": None,
        "unverified_links": [record["ref"]] if done.get("link_verified") is False else [],
        "tenders_without_deadline": ([record["ref"]]
                                     if record["kind"] == "tender" and not done.get("deadline")
                                     else []),
        "lost": [], "tenders": [row], "shards": ["hilma-day-shard-1"],
    })
    _write(os.path.join(out_root, folder, "day.json"), day)
    return day, folder


def main(argv=None):
    ap = argparse.ArgumentParser(description="One Finnish window into the delivery shape.")
    ap.add_argument("date", help="YYYY-MM-DD, UTC; the first day of the window")
    ap.add_argument("--to", default=None, help="the last day, if the window is wider than one")
    ap.add_argument("--out", default="work/FI")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--policy", default=None,
                    help="recall policy: JSON, a path to one, or FI_POLICY from the "
                         "environment. Absent means fetch everything.")
    ap.add_argument("--no-reconcile", action="store_true",
                    help="skip the coverage controls -- for a trial run, never for a night")
    ap.add_argument("--no-text", action="store_true", help="download but do not extract")
    args = ap.parse_args(argv)

    day, changes = run(args.date, args.out, args.limit, policy=args.policy, date_to=args.to,
                       reconcile=not args.no_reconcile, with_text=not args.no_text)
    print("%s..%s: %d/%d delivered, %d gated, %d closed, %d document(s), %.1f MB -- %s"
          % (day["window"]["from"], day["window"]["to"],
             day["coverage"]["delivered"], day["coverage"]["targets"],
             day["coverage"]["gated"], day["coverage"]["closed"],
             day["counts"]["documents"], day["counts"]["bytes"] / 1048576.0,
             "complete" if day["complete"] else "SHORT"))
    if day.get("reconciliation"):
        print("  %s" % fi_recon.line(day["reconciliation"]))
    for row in changes["tenders"][:25]:
        print("  %-13s %-8s %-10s %-8s %s"
              % (row["ref"], row["status"], row["kind"], row["documents_available"],
                 (row["title"] or "")[:44]))
    return day


if __name__ == "__main__":
    main()
