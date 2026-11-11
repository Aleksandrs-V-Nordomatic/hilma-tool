#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deliver a Finnish day to a Graph drive, and give the day a memory.

    python3 deliver_fi.py --out work --date 2026-09-02

WHY THIS IS NOT `deliver_graph`. The two deliveries agree about almost everything — the same
Graph client, the same home layout, the same rule that an index is written after the files it
names — and they are separate because a delivery written for another country's register does
specific, quiet damage to this one's tenders:

  * it REBUILDS `index.json` from `procurement.json` and the normalized manifest. This
    country's index is not derivable from those. It carries `documents_available` -- whether
    the specification was read, is behind a supplier account, or does not exist -- and it
    carries the buyer's own description, which for the 89% of Finnish tenders whose documents
    are shut is the whole of what a judgement can be made on. A rebuilt index is a
    correct-looking index with both of those missing, and a card that then implies it read
    something it did not.
  * its download address is a literal belonging to another register. Pointed here it does not
    fail; it stamps a working foreign URL shape onto a Finnish tender, and the card carries a
    link to a document that is not the one it names.
  * a shard is in its path, because four runners draw four addresses at a register that
    refuses a third of them. Hilma refused none of eight, so there is one runner, no shard
    index to reconcile, and no collection step.

So this file delivers WHAT THE FETCH ALREADY BUILT rather than reassembling it. `fi_fetch`
writes the index, and the index it wrote is the one that ships.

WHERE THE MEMORY LIVES, AND WHY IT CANNOT BE THE DISK. `fi_day` compares each procurement
against `state.json` in its own home — correct on a workstation that keeps `work/` between
runs, and worthless on a runner, whose disk is new every night. Every tender would come
back `new`, `changes.json` would be a copy of the day for ever, and the half of the night that
reports what moved would have nothing true to say.

The drive is the only durable thing in the arrangement, so the comparison is made against the
drive, here, at delivery. The local `changes.json` is overwritten with the result before it is
uploaded, so the file a reader finds is the one that was compared against what the reader can
actually see.

WHAT IT KNOWS ABOUT THE DESTINATION: NOTHING. Tenant, client, drive and root arrive in the
environment; the country picks the folder under the root; and this file prints counts rather
than paths.
"""

import argparse
import json
import os
import sys
import time

import changes
import country
# The Graph client, the upload session, the retry set and the archive builder are shared
# rather than copied. Two clients would drift, and the first sign of the drift would be one
# country retrying a code the other had learned to retry.
import deliver_graph as graph


# WHAT TRAVELS, AND WHAT STAYS BEHIND. The same principle as `deliver_graph.KEEP_NAMES`:
# everything a reader opens and nothing it does not. `originals/` is the published files
# themselves — the overwhelming majority of the bytes — and no reader opens them, because
# every document is already delivered as Markdown and the index carries the register's own
# address for anyone who wants the original itself. The raw archive stays behind for the same
# reason, and its name is reused for the archive of what did travel: a reader that takes
# `<pid>.zip` wants the procurement readable in one request, not a folder of .docx.
# `notice.json` travels with the other two, and only Finland's does. It is the eForms notice
# exactly as the portal served it, and on a route where 89% of specifications are shut it is
# the deepest thing a later question can be answered from -- a question asked after the
# runner's disk has gone, which is every question.
# `evidence.json` travels because `index.json` points at it: the located quotes are what the
# reading step is told to prefer, and a pointer to a file that stayed on the runner is worse
# than no pointer. `ted.json` travels because the reading step is told to open it for the EU
# tier, and the award criteria's weights are only summarised in the index.
FLAT = ("procurement.json", "manifest.json", "notice.json", "evidence.json", "ted.json")
NORMALIZED = "normalized/manifest_normalized.json"


def _read(path):
    with open(path, "rb") as fh:
        return fh.read()


def delivered_index(home, pid, date, run_id):
    """The index that ships: the one `fi_fetch` wrote, addressed for the drive.

    Two edits, both because the drive holds less than the pack did. `original` pointed at
    `originals/<file>`, which is not delivered, and a pointer to something absent is worse
    than no pointer — the index already carries the address of the tender's own notice
    page for anybody who wants the file itself. The delivery fields are added so that a reader
    who has learned one country's home has learned this one.
    """
    with open(os.path.join(home, "index.json"), encoding="utf-8") as fh:
        index = json.load(fh)
    documents = []
    for entry in index.get("documents", []):
        entry = dict(entry)
        entry.pop("original", None)
        documents.append(entry)
    index["documents"] = documents
    index.update({"home": "tenders/%s" % pid, "archive": "%s.zip" % pid,
                  "index_file": "index.json", "run_file": "runs/%s.json" % date,
                  "date": date, "run_id": run_id})
    return index


def members(home, pid, date, run_id, index=None):
    """Everything one procurement publishes, in the order it is published.

    ONE LIST, TWO RENDERINGS: the home somebody can open without downloading anything, and
    the archive somebody can take whole, are built from this same list, so a file that reaches
    one always reaches the other.

    `index.json` is last because it is the reader's proof that the rest arrived.
    """
    out = []
    for name in FLAT:
        path = os.path.join(home, name)
        if os.path.exists(path):
            out.append((name, _read(path)))
    normalized = os.path.join(home, "normalized", "manifest_normalized.json")
    if os.path.exists(normalized):
        out.append((NORMALIZED, _read(normalized)))
    doc_dir = os.path.join(home, "doc")
    if os.path.isdir(doc_dir):
        for name in sorted(os.listdir(doc_dir)):
            if name.endswith(".md"):
                out.append(("doc/%s" % name, _read(os.path.join(doc_dir, name))))
    index = index if index is not None else delivered_index(home, pid, date, run_id)
    out.append(("index.json",
                json.dumps(index, ensure_ascii=False).encode("utf-8")))
    return out


def deliver(out_root, date, run_id, drive, base, tok):
    """One day's homes and one day's two files. Returns the counts worth printing."""
    day_dir = os.path.join(out_root, date)
    with open(os.path.join(day_dir, "day.json"), encoding="utf-8") as fh:
        day = json.load(fh)
    with open(os.path.join(day_dir, "changes.json"), encoding="utf-8") as fh:
        day_changes = json.load(fh)

    # THE DAY NAMES WHAT IS DELIVERED, NOT THE DIRECTORY. `work/FI/tenders/` accumulates
    # every procurement ever fetched onto this disk, and on a workstation that is months of
    # them. Only what this day actually delivered is this day's business.
    pids = [str(t["pid"]) for t in day.get("tenders", [])]

    records = {}
    undelivered = []
    files = sent = carried = 0
    bytes_sent = 0
    tally = {"new": 0, "changed": 0, "unchanged": 0}

    for pid in pids:
        home = os.path.join(out_root, "tenders", pid)
        state_path = os.path.join(home, "state.json")
        if not os.path.exists(state_path):
            # A procurement the day names and the disk does not hold is a delivery that did
            # not finish. It is taken OUT of the day rather than merely mentioned: left in,
            # `day.json` would name a home that is not on the drive, carry `fi_day`'s local
            # guess of `new` for it, and still call itself complete — so a reader would ask
            # the drive for an archive that was never uploaded and get a 404 in the middle
            # of a night that reported success. A day may be short; it may not be wrong.
            print("  no state for %s — not delivered" % pid, file=sys.stderr)
            undelivered.append(pid)
            continue
        with open(state_path, encoding="utf-8") as fh:
            current = json.load(fh)

        home_root = "%s/tenders/%s" % (base, pid)
        # WHAT THIS PROCUREMENT LOOKED LIKE LAST TIME, ASKED OF THE DRIVE. Absent for one
        # nobody has fetched before, which is the answer for every one of them on the first
        # night this runs and for none of them afterwards.
        was_seen = graph.json_at(drive, "%s/seen.json" % home_root, tok)
        record = changes.diff(graph.json_at(drive, "%s/state.json" % home_root, tok),
                              current, date=date, run_id=run_id, seen=was_seen)
        record["home"] = "tenders/%s" % pid
        records[pid] = record
        tally[record["status"]] = tally.get(record["status"], 0) + 1

        index = delivered_index(home, pid, date, run_id)
        contents = members(home, pid, date, run_id, index=index)

        # ONLY WHAT IS NOT ALREADY THERE. A document's name is its digest, so the bytes at
        # that name cannot have become different bytes, and a document uploaded the day it
        # appeared is never uploaded again.
        wanted = {"doc/%s.md" % k for k in changes.documents_to_send(record, current)}
        to_send = [(rel, data) for rel, data in contents
                   if not rel.startswith("doc/") or rel in wanted]
        sent += len(wanted)
        carried += 0 if record.get("reextracted") else record.get("carried_over", 0)

        # A PROCUREMENT THAT DID NOT MOVE WRITES ONLY THE TWO SMALL PER-RUN FILES.
        # Everything else on the drive already describes it correctly, and rewriting it
        # would cost megabytes a night to advance a timestamp.
        #
        # INDEX AFTER THE DOCUMENTS, STATE AFTER THE INDEX: `index.json` is the reader's
        # proof the home is whole, `state.json` is tomorrow's proof of what it may skip, and
        # a state file that landed before the documents it vouches for would let tomorrow
        # carry over text that never arrived. `contents` ends with the index, so the loop
        # below is already in the order of the rule.
        if changes.refreshed(record):
            archive = graph.tender_archive(contents)
            graph.upload(drive, "%s/%s.zip" % (home_root, pid), archive, tok)
            files += 1
            bytes_sent += len(archive)
            for rel, data in to_send:
                graph.upload(drive, "%s/%s" % (home_root, rel), data, tok)
                files += 1
                bytes_sent += len(data)
            state_bytes = json.dumps(current, ensure_ascii=False).encode("utf-8")
            graph.upload(drive, "%s/state.json" % home_root, state_bytes, tok)
            files += 1
            bytes_sent += len(state_bytes)

        for dest, payload in (("seen.json", changes.seen(pid, was_seen, record, date)),
                              ("runs/%s.json" % date, record)):
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            graph.upload(drive, "%s/%s" % (home_root, dest), data, tok)
            files += 1
            bytes_sent += len(data)

    # THE DAY'S TWO FILES, WITH THE VERDICT THE DRIVE GAVE RATHER THAN THE ONE THE DISK
    # GUESSED. `fi_day` wrote `status` against a local `state.json` that a runner never has;
    # every one of them says `new`. Replacing them here is the whole point of the exercise,
    # and the file that lands is therefore the one a reader can act on.
    for row in day_changes.get("tenders", []):
        record = records.get(str(row["pid"]))
        if record is None:
            continue
        row["status"] = record["status"]
        row["moved"] = record.get("moves") or []
        row["change"] = changes.summary(record)
    for row in day.get("tenders", []):
        record = records.get(str(row["pid"]))
        if record is not None:
            row["status"] = record["status"]
    # A procurement that never reached the drive is not in the day the drive publishes.
    if undelivered:
        gone = set(undelivered)
        for doc in (day, day_changes):
            doc["tenders"] = [r for r in doc.get("tenders", [])
                              if str(r.get("pid")) not in gone]
        day["lost"] = list(day.get("lost") or []) + [
            {"pid": pid, "kind": None,
             "reason": "delivery did not finish — no state on the runner"}
            for pid in undelivered]
        day["complete"] = False
        day_changes["complete"] = False
        day["coverage"] = dict(day.get("coverage") or {},
                               delivered=len(records), undelivered=len(undelivered))

    day_changes["counts"] = dict(day_changes.get("counts") or {}, **tally)
    day_changes["compared_against"] = "drive"

    root = "%s/%s" % (base, date)
    # CHANGES FIRST, DAY LAST. `day.json` is the proof there is a day to read, so it is
    # written after everything it vouches for — the same rule as the index, one level up.
    for name, payload in (("changes.json", day_changes), ("day.json", day)):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        graph.upload(drive, "%s/%s" % (root, name), data, tok)
        files += 1
        bytes_sent += len(data)

    return {"files": files, "bytes": bytes_sent, "tally": tally,
            "sent": sent, "carried": carried, "tenders": len(records)}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Deliver a Finnish day to a Graph drive.")
    ap.add_argument("--out", default="work",
                    help="the runtime root the fetch wrote into; the country folder is "
                         "under it, exactly as the fetch derived it")
    ap.add_argument("--date", help="YYYY-MM-DD, the day being delivered")
    # ASK BEFORE THE NIGHT, NOT AFTER IT. The delivery is the LAST step of a run, so a
    # credential that has expired or a destination that has moved is discovered after the
    # portal has been walked and a day's archives downloaded — the most expensive possible
    # moment to learn something that costs one request to check. A client secret expires on
    # a date nobody has in their calendar, and when it does the night dies whole.
    ap.add_argument("--check", action="store_true",
                    help="prove the credentials reach the destination, deliver nothing, "
                         "and say so in counts rather than paths")
    ap.add_argument("--run-id", default=os.environ.get("GITHUB_RUN_ID", ""),
                    help="the workflow run this delivery came from")
    country.add_argument(ap)
    args = ap.parse_args(argv)

    try:
        code = country.resolve(args.country, os.environ)
    except country.Mismatch as exc:
        print("deliver_fi: %s" % exc, file=sys.stderr)
        return 2
    if code != "FI":
        # Unreachable while this repository declares one country — `country.resolve` has
        # already refused anything else — and kept because the two guards answer different
        # questions. That one asks whether a source exists; this one asks whether THIS lane
        # is the right one for it. A second country added to `country.SOURCES` without a
        # delivery of its own would otherwise arrive here and be handed the Hilma shape.
        print("deliver_fi: this lane is Finland's, and %s is not delivered by this tool"
              % code, file=sys.stderr)
        return 2

    drive = graph.env("GRAPH_DRIVE_ID")
    base = country.destination(graph.env("GRAPH_DEST_ROOT"), code)

    if args.check:
        # Silent about where it writes, like the delivery it stands in front of: the
        # destination is a secret and this prints what it found, never where.
        try:
            tok = graph.graph_token()
        except Exception as exc:                      # any transport or auth failure
            print("deliver_fi --check: could not get a token for %s — the tenant, the "
                  "client id or the client secret is wrong or expired (%s)"
                  % (code, type(exc).__name__), file=sys.stderr)
            return 2
        # WHAT THIS CHECK IS FOR, AND WHAT IT IS NOT FOR. It stands in front of a night of
        # downloads to catch the two failures that make the whole night worthless: a
        # credential that has expired, and a drive or root that does not answer. Both are
        # facts about the DESTINATION ROOT.
        #
        # It used to ask for the COUNTRY folder instead, and that conflated two states that
        # need different answers. `work/LV` has existed for months, so a missing one there
        # really does mean the root is misconfigured. For a country nobody has delivered yet
        # the same signal means "first run" — and the check answered it by refusing, with a
        # message blaming `GRAPH_DEST_ROOT`, which was correct. Every new country would have
        # paid for that once, at the first step of its first night, and the folder is not
        # even a precondition: Graph creates intermediate folders on an upload addressed by
        # path, so the delivery makes it on the way past.
        root = graph.env("GRAPH_DEST_ROOT").strip("/")
        try:
            item = graph.item_at(drive, root, tok)
        except SystemExit as exc:
            print("deliver_fi --check: the token worked but the drive did not answer — "
                  "GRAPH_DRIVE_ID is wrong, or the app has no access to it (%s)"
                  % exc, file=sys.stderr)
            return 2
        if item is None:
            print("deliver_fi --check: reached the drive, but the destination root is not "
                  "there. GRAPH_DEST_ROOT names the folder that CONTAINS the country "
                  "folders; the country is appended by the tool.", file=sys.stderr)
            return 2
        # Said out loud rather than left for somebody to infer from a count. A first
        # delivery and a hundredth look identical in every other line this prints.
        country_folder = graph.item_at(drive, base, tok)
        where = ("holds %d item(s)" % (country_folder.get("folder") or {}).get("childCount", 0)
                 if country_folder is not None
                 else "has no folder for %s yet — this would be its first delivery" % code)
        print("delivery credentials OK for %s: the destination answers and %s" % (code, where))
        return 0

    if not args.date:
        ap.error("--date is required unless --check is given")
    tok = graph.graph_token()

    out_root = os.path.join(args.out, code)
    result = deliver(out_root, args.date, args.run_id or None, drive, base, tok)

    print("delivered %s for %s: %d procurement(s), %d SharePoint files, %.1f MB"
          % (code, args.date, result["tenders"], result["files"], result["bytes"] / 1e6))
    print("  %d new, %d changed, %d unchanged · %d document(s) sent, %d carried over"
          % (result["tally"]["new"], result["tally"]["changed"],
             result["tally"]["unchanged"], result["sent"], result["carried"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
