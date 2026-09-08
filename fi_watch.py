"""The registration queue: the tenders whose documents are worth asking for, and their state.

WHY FINLAND NEEDS A QUEUE AND NO OTHER COUNTRY IN THIS FAMILY DOES. Latvia's and Estonia's
documents are free: a run decides a tender is interesting and downloads it in the same second, so
one judgement on one night is the whole story. Measured over the 11 815 Finnish contract notices
of 1 Sep 2025 - 31 Aug 2026, 90% of tenders keep their documents on a platform that answers
nobody who has not registered as a bidder on that specific tender -- 79.6% on tarjouspalvelu.fi
alone, the rest across ten smaller portals, every one of which was asked anonymously and refused.

So a Finnish judgement is two judgements with a human act between them:

    seen        the notice is in the day, everything Hilma knows about it is held
    candidate   judged on the notice alone as worth the documents -- a person must now register
    registered  a person has registered; the platform should start answering
    ready       the documents are here, and the real judgement can be made
    judged      the reading step has decided; a card exists or the reason it does not
    expired     the deadline passed while this sat in the queue        <- the failure this file exists for
    dropped     a person took it out

THE FAILURE MODE IS NOT A CRASH, IT IS SILENCE. Between `candidate` and `ready` stands a person
with a diary, and a tender whose deadline passes while it waits is lost with every step having
succeeded: the fetch worked, the judgement worked, the queue is valid, and nobody bid. Nothing
else in this pipeline has a human on the critical path, so nothing else can fail this way.
`due()` is therefore not a convenience -- it is the only thing standing between the design and a
quiet loss, and the day prints it whether or not anything else happened.

WHAT THIS FILE DOES NOT DO. It does not register. Registering is an act in a procurement, made by
a person, under their name, and it announces Echoo as an interested party to that buyer. A queue
that registered by itself would be a script deciding to enter competitions.

    python3 fi_watch.py --out work/FI                  # what is waiting, and what is overdue
    python3 fi_watch.py --out work/FI --mark 36616 registered
"""
import argparse
import datetime
import json
import os
import sys

import fi_page

FILE = "registrations.json"
SCHEMA = "registrations/1"

# The states, in the order a tender moves through them. `expired` and `dropped` are terminal.
SEEN, CANDIDATE, REGISTERED, READY, JUDGED, EXPIRED, DROPPED = (
    "seen", "candidate", "registered", "ready", "judged", "expired", "dropped")
STATES = (SEEN, CANDIDATE, REGISTERED, READY, JUDGED, EXPIRED, DROPPED)
TERMINAL = (JUDGED, EXPIRED, DROPPED)
# The states a nightly run must look at again: something outside this tool may have changed.
OPEN = (CANDIDATE, REGISTERED, READY)

# How close a deadline has to be before a tender waiting on a person is shouted about rather than
# listed. A Finnish contract notice runs three to five weeks; a week is enough to register, read
# and write, and not so much that every entry is urgent on the day it arrives.
URGENT_DAYS = 7


def _now():
    return datetime.datetime.now(datetime.timezone.utc)


def _stamp():
    return _now().strftime("%Y-%m-%dT%H:%M:%SZ")


def _when(value):
    """A deadline as an aware datetime, or None when the index did not state one."""
    if not value:
        return None
    try:
        parsed = datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=datetime.timezone.utc)


def path(out_root):
    return os.path.join(out_root, FILE)


def load(out_root):
    """The queue, or an empty one. A queue that will not parse is never silently replaced.

    An unreadable file here would lose the record of every tender a person has already
    registered on -- work that cannot be redone from the portal, because the portal will not say
    which tenders somebody expressed interest in. So a parse failure raises.
    """
    where = path(out_root)
    if not os.path.exists(where):
        return {"schema": SCHEMA, "written_at": None, "tenders": {}}
    with open(where, encoding="utf-8") as fh:
        queue = json.load(fh)
    queue.setdefault("tenders", {})
    return queue


def save(out_root, queue):
    queue["schema"] = SCHEMA
    queue["written_at"] = _stamp()
    os.makedirs(out_root, exist_ok=True)
    where = path(out_root)
    # Written beside and moved, because this file is the only record of what a person has
    # already done and a half-written one would read as an empty queue.
    temporary = where + ".writing"
    with open(temporary, "w", encoding="utf-8") as fh:
        json.dump(queue, fh, ensure_ascii=False, indent=2)
    os.replace(temporary, where)
    return where


def _entry(record, state, why=None, run_id=None):
    return {
        "pid": record["pid"],
        "ref": record["ref"],
        "title": record.get("title"),
        "buyer": record.get("buyer"),
        "deadline": record.get("deadline"),
        "link": record.get("link"),
        # Where a person has to go to register. `external` alone sends them nowhere, and 79.6%
        # of the time this is one host they can hold a single account for.
        "portal": record.get("documents_host"),
        "documents_url": record.get("documents_url"),
        "documents_available": record.get("documents_available"),
        "state": state,
        "state_since": _stamp(),
        "why": why,
        "first_seen_run": run_id,
        "history": [{"at": _stamp(), "state": state, "why": why, "run": run_id}],
    }


def note(entry, state, why=None, run_id=None):
    """Move one tender to a new state, keeping what it was and when.

    The history is kept rather than overwritten because the interesting question about a lost
    tender is never what state it ended in -- it is how long it sat in the one before.
    """
    if entry["state"] != state:
        entry["state"] = state
        entry["state_since"] = _stamp()
    entry["history"].append({"at": _stamp(), "state": state, "why": why, "run": run_id})
    return entry


def observe(queue, records, run_id=None):
    """Record every notice the day saw, and refresh what is already in the queue.

    Everything is recorded, including what the gate dropped: the queue is the picture, and the
    gate governs only what is downloaded. A tender already in the queue keeps its state and gets
    the day's fresh facts -- a deadline the buyer moved, documents that have appeared.
    """
    fresh, moved = [], []
    for record in records:
        pid = str(record["pid"])
        entry = queue["tenders"].get(pid)
        if entry is None:
            queue["tenders"][pid] = _entry(record, SEEN, run_id=run_id)
            fresh.append(pid)
            continue
        for field in ("title", "buyer", "link", "documents_url", "documents_available"):
            if record.get(field) is not None:
                entry[field] = record.get(field)
        if record.get("documents_host"):
            entry["portal"] = record["documents_host"]
        # A MOVED DEADLINE IS NEWS, AND IT IS THE ONE FACT THIS QUEUE MUST NOT MISS. A
        # corrigendum that shortens a deadline can take a tender from comfortable to overdue
        # between two nights, and the person waiting to register would never be told.
        if record.get("deadline") and record["deadline"] != entry.get("deadline"):
            entry["deadline_was"] = entry.get("deadline")
            entry["deadline"] = record["deadline"]
            note(entry, entry["state"], why="the buyer moved the deadline", run_id=run_id)
            moved.append(pid)
    return fresh, moved


def refresh(queue, run_id=None, available=None):
    """Ask, for everything still open, whether the documents have arrived and whether time has.

    `available` decides whether a tender's documents can now be read; it is injected so this
    file needs no network and the test suite needs no portal. The default asks Hilma, which is
    the only source that answers without a person having registered.
    """
    available = available if available is not None else _hilma_has_documents
    became_ready, expired = [], []
    for pid, entry in queue["tenders"].items():
        if entry["state"] in TERMINAL:
            continue
        due = _when(entry.get("deadline"))
        if due is not None and due <= _now():
            # A DEADLINE THAT PASSED IN THE QUEUE IS NOT HOUSEKEEPING. Everything worked and
            # nobody bid, so it is recorded as its own state and named in the report rather
            # than quietly dropped.
            if entry["state"] in (CANDIDATE, REGISTERED, READY):
                note(entry, EXPIRED, why="the deadline passed while this was waiting",
                     run_id=run_id)
                expired.append(pid)
            else:
                note(entry, EXPIRED, why="the deadline passed", run_id=run_id)
            continue
        if entry["state"] in (CANDIDATE, REGISTERED) and available(entry):
            note(entry, READY, why="the documents can now be read", run_id=run_id)
            became_ready.append(pid)
    return became_ready, expired


def _hilma_has_documents(entry):
    """True when Hilma itself will serve this tender's files, which needs nobody's account.

    Only asked for tenders Hilma runs. On the other 90% the answer is a person's to give by
    registering, and a request made here would be answered with a login page under a 200.
    """
    if entry.get("documents_available") != "hilma":
        return False
    if not (entry.get("pid") or "").isdigit():
        return False
    try:
        document = fi_page.detail(entry["pid"], str(entry["ref"]).split("-")[-1].lstrip("0"))
    except Exception:
        return False
    if document is None:
        return False
    public, _ = fi_page.catalogue(document)
    return bool(public)


def due(queue, days=URGENT_DAYS):
    """What a person has to act on, soonest first. The report the day always prints.

    Two lists, because they need different people. `to_register` is somebody deciding to enter a
    competition; `overdue` is a post-mortem, and it is printed so that a design with a human on
    the critical path cannot fail without saying so.
    """
    now, edge = _now(), _now() + datetime.timedelta(days=days)
    waiting, urgent, overdue = [], [], []
    for entry in queue["tenders"].values():
        if entry["state"] not in (CANDIDATE, REGISTERED):
            if entry["state"] == EXPIRED and entry.get("history"):
                previous = [h["state"] for h in entry["history"][:-1]]
                if CANDIDATE in previous or REGISTERED in previous:
                    overdue.append(entry)
            continue
        when = _when(entry.get("deadline"))
        (urgent if (when is not None and when <= edge) else waiting).append(entry)
    key = lambda e: _when(e.get("deadline")) or datetime.datetime.max.replace(
        tzinfo=datetime.timezone.utc)
    return {"urgent": sorted(urgent, key=key), "waiting": sorted(waiting, key=key),
            "overdue": overdue}


def counts(queue):
    tally = {state: 0 for state in STATES}
    for entry in queue["tenders"].values():
        tally[entry["state"]] = tally.get(entry["state"], 0) + 1
    return tally


def report(queue, days=URGENT_DAYS):
    """The queue in the shape a person reads at the top of a morning."""
    lines, work = [], due(queue, days)
    tally = counts(queue)
    lines.append("queue: " + ", ".join("%d %s" % (n, s) for s, n in tally.items() if n))
    for entry in work["urgent"]:
        lines.append("  REGISTER NOW  %-13s %-10s %s  %s"
                     % (entry["ref"], (entry.get("deadline") or "")[:10],
                        (entry.get("portal") or "-")[:22], (entry.get("title") or "")[:40]))
    for entry in work["waiting"]:
        lines.append("  to register   %-13s %-10s %-22s %s"
                     % (entry["ref"], (entry.get("deadline") or "no deadline")[:10],
                        (entry.get("portal") or "-")[:22], (entry.get("title") or "")[:40]))
    for entry in work["overdue"]:
        lines.append("  LOST          %-13s deadline %s passed while it waited to be registered"
                     % (entry["ref"], (entry.get("deadline") or "")[:10]))
    return lines


def main(argv=None):
    ap = argparse.ArgumentParser(description="The Finnish registration queue.")
    ap.add_argument("--out", default="work/FI")
    ap.add_argument("--mark", nargs=2, metavar=("PID", "STATE"),
                    help="move one tender to a state: %s" % "|".join(STATES))
    ap.add_argument("--why", default=None)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    queue = load(args.out)
    if args.mark:
        pid, state = args.mark
        if state not in STATES:
            print("mark: %s is not a state (%s)" % (state, "|".join(STATES)), file=sys.stderr)
            return 2
        entry = queue["tenders"].get(str(pid))
        if entry is None:
            print("mark: no tender %s in the queue" % pid, file=sys.stderr)
            return 2
        note(entry, state, why=args.why or "set by hand")
        save(args.out, queue)
        print("%s -> %s" % (pid, state))
        return 0

    if args.json:
        json.dump(due(queue), sys.stdout, ensure_ascii=False, indent=2, default=str)
        print()
        return 0
    for line in report(queue):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
