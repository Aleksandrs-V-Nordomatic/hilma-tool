"""Did the run see the whole day? Three controls, and two of them may fail it.

A country tool that cannot answer this question delivers a green run and an empty morning, and
the two look identical from every side. Finland is unusually generous here: the index states its
own total, and the EU half of every day is independently published by somebody else.

    1  HARD   rows held == the index's own `@odata.count` for the window
    2  HARD   every TED notice for the day is found in Hilma by its TED publication id
    3  WARN   the portal's own live-procurement counter is within a few percent of ours

WHY THE THIRD ONLY WARNS. A hard assertion has to be worth stopping a night for. This one
compares two numbers that are close but not defined identically -- 1262 against 1238 when it was
last measured, 1.9% apart -- so making it hard would fail perfectly good nights on a definition
mismatch.

AND WHY THERE IS NO FOURTH. The research handed over a candidate control: `noticeNumber` is a
monotonically increasing serial, so the union of a window's numbers should have no unexplained
gap. It was marked UNVERIFIED, pending somebody finding out whether a draft consumes a number.
It was tried here and it does not work, and the reason is more interesting than the control: the
serial is allocated when a notice is CREATED, not when it is published. The hundred notices
published on 2026-09-02 carry numbers from `2026-053358` to `2026-056175` -- a range holding 2 819
serials, of which 2 706 were published on other days or not at all. A control that fires 2 706
times on a clean day is not a weak control, it is a control that teaches a reader to ignore
controls. So it is not shipped, and this paragraph is here so the next person does not build it
again.

NEVER COMPARE DAILY TOTALS WITH TED, AND THIS IS THE TRAP. Hilma leads TED across weekends: on
2026-08-30, a Sunday, Hilma published 72 notices of which 65 were EU, and TED published them on
the Monday -- 39 against 74 the other way. A same-day count comparison generates a false alarm
every single week. The equality is per notice, matched on `tedPublicationId`, over a window wide
enough to absorb the shift. And it is one-directional by nature: Hilma also carries the national
tier, which never reaches TED at all, so `hilma >= ted` always.

    python3 fi_recon.py 2026-09-02
"""
import argparse
import json
import sys
import urllib.request

import fi_page
import fi_targets
import net

TED_SEARCH = "https://api.ted.europa.eu/v3/notices/search"
TED_PAGE = 100
# Days either side of the day being proved, on the Hilma side, when looking for TED's notices.
# Two behind covers the Sunday-to-Monday shift; one ahead covers the reverse.
TED_SPREAD = (-2, 1)
# How many publication ids fit in one `or` filter. Twenty was exercised during the route
# measurement; the limit is the URL, not the index.
TED_BATCH = 20

STATISTICS = fi_page.BASE + "/statistics/NoticeStatistics"
# How far the portal's own counter may sit from ours before the run says so. The two are not
# defined identically, so this is a band, not an assertion.
OPEN_TOLERANCE = 0.05


def ted_notices(date, timeout=60):
    """Every TED notice published on `date` for a Finnish buyer. Returns publication numbers.

    `totalNoticeCount` is TED's own total and is collected against, for the same reason
    `@odata.count` is: a paging bug that returns fewer rows is invisible without it.

    `buyer-country=FIN` matches a notice with a Finnish buyer ANYWHERE among its buyers, and the
    EU institutions buy jointly: 778712-2026 named 48 of them, the Helsinki agency among them,
    and was sent to TED by the Commission. Hilma never carries such a notice, and nothing obliges
    it to. So the notices whose LEAD buyer -- the first listed -- is not Finnish are returned
    apart, and the control does not ask Hilma for them.
    """
    wanted = date.replace("-", "")
    numbers, led_abroad, page, total = [], [], 1, None
    while True:
        payload = json.dumps({
            "query": "publication-date=%s AND buyer-country=FIN" % wanted,
            "limit": TED_PAGE, "page": page,
            "fields": ["publication-number", "publication-date", "notice-type", "buyer-country"],
        }).encode("utf-8")
        request = urllib.request.Request(
            TED_SEARCH, data=payload,
            headers={"Content-Type": "application/json", "User-Agent": fi_page.UA})
        body, _ = net.open_url(request, timeout=timeout, log=fi_page.log,
                               parse=lambda raw: json.loads(raw.decode("utf-8")))
        total = body.get("totalNoticeCount") if total is None else total
        rows = body.get("notices") or []
        for r in rows:
            if r.get("publication-number"):
                numbers.append(str(r["publication-number"]))
                countries = r.get("buyer-country") or []
                if countries and countries[0] != "FIN":
                    led_abroad.append(str(r["publication-number"]))
        if len(numbers) >= (total or 0) or not rows:
            break
        page += 1
    return total, numbers, led_abroad


def in_hilma(numbers, date):
    """Which of those TED publication ids Hilma also holds, looked for either side of the day.

    Batched, because one request per notice would be fifty-four requests against an index that
    answers a second question with the first question's rows. `tedPublicationId` is the join.
    """
    if not numbers:
        return set()
    since = fi_targets.days(
        _shift(date, TED_SPREAD[0]), _shift(date, TED_SPREAD[0]))[0]
    until = _shift(date, TED_SPREAD[1])
    window = fi_targets._window_filter(since, until)
    inside = set(fi_targets.days(since, until))
    found = set()
    for start in range(0, len(numbers), TED_BATCH):
        batch = numbers[start:start + TED_BATCH]
        # A CORRIGENDUM DELETES THE NOTICE IT SUPERSEDES, and the TED id that notice was sent
        # under then survives only in the successor's `previousTedPublicationId`. 778153-2026
        # was exactly that on 2026-11-09: published to TED, superseded in Hilma the next morning
        # by 2026-061483, and reported missing by a control that looked at one field.
        clause = " or ".join(
            "tedPublicationId eq '{0}' or previousTedPublicationId/any(p: p eq '{0}')".format(
                n.replace("'", "''")) for n in batch)
        _, rows = fi_targets._ask("(%s) and %s" % (clause, window), top=TED_BATCH * 4,
                                  order=None, inside=inside)
        wanted = set(batch)
        for r in rows:
            held = [r.get("tedPublicationId")] + list(r.get("previousTedPublicationId") or [])
            found.update(str(n) for n in held if n and str(n) in wanted)
    return found


def _shift(date, days):
    import datetime
    return (datetime.date.fromisoformat(date) + datetime.timedelta(days=days)).isoformat()


def statistics(timeout=30):
    """The portal's own counters, computed by a different service from the search index.

    `newNotices` is today's publication count and `openProcurements` the live population. Only
    meaningful about the moment they are read, which is why they are a band and not a proof.
    """
    try:
        body, _ = net.get_json(STATISTICS, headers={"User-Agent": fi_page.UA}, timeout=timeout,
                               tries=2, log=fi_page.log)
        return body
    except Exception as exc:
        # A control that only warns must never be the thing that stops a night.
        return {"error": str(exc)[:160]}


def live_stock(timeout=60):
    """How many contract notices are open right now, counted by us rather than by the portal.

    One request, and it carries no rows at all -- `$top=0` still returns `@odata.count`, which is
    the only part of the answer this control reads. Asking for the rows would be a megabyte of
    tenders nobody is going to look at.
    """
    import datetime
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    body = fi_page.search({"search": "*", "count": "true", "top": 0,
                           "filter": "expirationDate gt %s and mainType eq 'ContractNotices'"
                                     % now})
    return body.get("@odata.count")


def run(date, date_to=None, records=None, count=None, ted=True):
    """Every control, as a report. `ok` is false only when a HARD control failed."""
    date_to = date_to or date
    if records is None:
        count, raw = fi_targets.rows(date, date_to)
        records = [fi_page.notice(r) for r in raw]

    held = len(records)
    coverage = {"control": "index count", "hard": True,
                "index_count": count, "rows_held": held, "ok": count == held}

    ted_report = {"control": "TED", "hard": True, "ok": True, "skipped": not ted}
    if ted:
        # Proved for the LAST day of the window only. A catch-up window would cost one TED
        # request per day for a control whose failure mode is the same on every one of them,
        # and the day that matters is the one a person is about to read.
        total, collected, led_abroad = ted_notices(date_to)
        numbers = [n for n in collected if n not in set(led_abroad)]
        found = in_hilma(numbers, date_to)
        missing = sorted(set(numbers) - found)
        ted_report.update({"day": date_to, "ted_total": total, "collected": len(collected),
                           "led_abroad": led_abroad, "asked": len(numbers),
                           "matched": len(numbers) - len(missing), "missing": missing,
                           "ok": not missing})

    # THE COMPARISON IS BETWEEN TWO STOCKS, NOT A STOCK AND A DAY. `openProcurements` is every
    # procurement standing open on the portal right now; the window's own live count is a day's
    # worth of them. Comparing those two would report a 95% discrepancy every night and mean
    # nothing by it, so this asks the index for the same stock the portal is describing.
    stats = statistics()
    ours = None
    if stats.get("openProcurements") is not None:
        try:
            ours = live_stock()
        except Exception as exc:                   # a warn control never stops a night
            stats = dict(stats, error=str(exc)[:160])
    open_report = {"control": "open procurements", "hard": False,
                   "portal": stats.get("openProcurements"),
                   "new_today": stats.get("newNotices"),
                   "ours": ours,
                   "live_in_window": sum(1 for r in records if fi_page.is_live(r)),
                   "ok": True}
    if stats.get("error") or ours is None:
        open_report["ok"] = None
        open_report["note"] = stats.get("error") or "the portal served no counter"
    else:
        drift = abs(stats["openProcurements"] - ours) / float(max(ours, 1))
        open_report["drift"] = round(drift, 4)
        open_report["ok"] = drift < OPEN_TOLERANCE

    controls = [coverage, ted_report, open_report]
    return {"window": {"from": date, "to": date_to},
            "ok": all(c.get("ok") for c in controls if c.get("hard")),
            "controls": controls}


def line(report):
    """The report in one sentence a person reads in three seconds."""
    by = {c["control"]: c for c in report["controls"]}
    cover, ted, open_ = by["index count"], by["TED"], by["open procurements"]
    parts = ["pulled %d = count %s %s" % (cover["rows_held"], cover["index_count"],
                                          "OK" if cover["ok"] else "FAILED")]
    if ted.get("skipped"):
        parts.append("TED skipped")
    else:
        abroad = len(ted.get("led_abroad") or [])
        parts.append("TED %s/%s%s %s" % (ted.get("matched"), ted.get("asked", ted.get("collected")),
                                         " (+%d led abroad)" % abroad if abroad else "",
                                         "OK" if ted["ok"] else "MISSING %d" % len(ted["missing"])))
    if open_.get("ours") is not None:
        parts.append("open %s vs %s (%+.1f%%) %s"
                     % (open_["portal"], open_["ours"],
                        100.0 * (open_["portal"] - open_["ours"]) / max(open_["ours"], 1),
                        "OK" if open_["ok"] else "WIDE"))
    return "FI %s..%s -- %s" % (report["window"]["from"], report["window"]["to"],
                                "; ".join(parts))


def main(argv=None):
    ap = argparse.ArgumentParser(description="Prove a Finnish window arrived whole.")
    ap.add_argument("date", help="YYYY-MM-DD, UTC")
    ap.add_argument("--to", default=None)
    ap.add_argument("--no-ted", action="store_true", help="skip the TED cross-check")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    report = run(args.date, args.to, ted=not args.no_ted)
    if args.json:
        json.dump(report, sys.stdout, ensure_ascii=False, indent=2)
        print()
    else:
        print(line(report))
        for control in report["controls"]:
            print("  %-20s %s %s" % (control["control"],
                                     "HARD" if control["hard"] else "warn",
                                     {k: v for k, v in control.items()
                                      if k not in ("control", "hard")}))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
