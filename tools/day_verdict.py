#!/usr/bin/env python3
"""Read a delivered day and say, in one screen, whether it can prove itself.

    python3 tools/day_verdict.py work/FI/2026-09-02/day.json

Runs LAST in the night and after the delivery, deliberately. A day that failed a control is
still a day: it is delivered, it is readable, and it carries `complete: false`, which is the
honest state. What this step adds is that the run itself goes red, so the failure is somewhere a
person looks rather than somewhere they would have to go and read.

Exit 1 when the day cannot prove itself. Exit 2 when there is no day at all, which is a
different sentence -- the fetch never got far enough to write one.
"""
import json
import os
import sys


def verdict(day):
    """Every line of the report, and whether the day stands. Returns (ok, lines)."""
    lines, ok = [], True

    window = day.get("window") or {}
    counts = day.get("counts") or {}
    coverage = day.get("coverage") or {}
    lines.append("FI %s..%s  %d of %d live delivered, %d gated, %d closed, %d document(s)"
                 % (window.get("from"), window.get("to"), coverage.get("delivered", 0),
                    coverage.get("targets", 0), coverage.get("gated", 0),
                    coverage.get("closed", 0), counts.get("documents", 0)))

    if day.get("discovery_failed"):
        ok = False
        lines.append("  DISCOVERY FAILED -- the window covers a day Finland publishes on and "
                     "the index returned nothing. Every way discovery breaks looks like this.")

    for row in day.get("lost") or []:
        if row.get("watched"):
            lines.append("  watch hole %s: %s" % (row.get("ref"), row.get("reason")))
        else:
            ok = False
            lines.append("  LOST %s: %s" % (row.get("ref") or row.get("pid"),
                                            row.get("reason")))

    for ref in day.get("unverified_links") or []:
        ok = False
        lines.append("  UNVERIFIED LINK %s -- the detail API does not agree that this notice is "
                     "where the card would send somebody" % ref)

    without = day.get("tenders_without_deadline") or []
    if without:
        lines.append("  no deadline on %d live tender(s): %s -- present on 50 of 50 in the "
                     "measured window, so this is worth an eye" % (len(without),
                                                                   ", ".join(without[:5])))

    proof = day.get("reconciliation")
    if proof is None:
        lines.append("  reconciliation: not run")
    else:
        for control in proof.get("controls") or []:
            state = control.get("ok")
            mark = "ok" if state else ("warn" if state is None else "FAILED")
            if state is False and control.get("hard"):
                ok = False
            detail = {k: v for k, v in control.items()
                      if k not in ("control", "hard", "ok") and v not in (None, [], "")}
            lines.append("  %-20s %-6s %s" % (control.get("control"), mark, detail))

    if not day.get("complete") and ok:
        # The day marked itself short for a reason this reader does not know about. Trust the
        # day: it wrote the flag with more in front of it than a JSON file has.
        ok = False
        lines.append("  the day marked itself incomplete")
    return ok, lines


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print("day_verdict: name a day.json", file=sys.stderr)
        return 2
    path = argv[0]
    if not os.path.exists(path):
        print("no day at %s -- the fetch did not get far enough to write one" % path,
              file=sys.stderr)
        return 2
    with open(path, encoding="utf-8") as fh:
        day = json.load(fh)

    ok, lines = verdict(day)
    for line in lines:
        print(line)
    if not ok:
        print("::error::the Finnish day cannot prove it arrived whole")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
