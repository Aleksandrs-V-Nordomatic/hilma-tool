"""What the day does with a window, without touching the network or the disk's real portal.

The three judgements under test are the ones that decide whether a morning is worth reading:
which notices are opportunities, what happens to the ones that are not, and when an empty answer
is a quiet country rather than a broken crawl.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fi_day
import fi_fetch
import fi_page
import fi_targets


def row(identity, main="ContractNotices", day="2026-09-02", **extra):
    serial = int(identity.split("-")[-1])
    out = {"id": identity, "noticeId": serial, "procedureId": serial + 1000,
           "noticeNumber": "2026-%06d" % serial, "mainType": main,
           "datePublished": "%sT08:00:00.0000000Z" % day,
           "titleFi": "Tender %s" % serial, "organisationNameFi": "Kaupunki",
           "cpvCodes": "45331000", "deadline": "2099-01-01T12:00:00Z"}
    out.update(extra)
    return out


class DayHarness(unittest.TestCase):
    def setUp(self):
        self.out = tempfile.mkdtemp(prefix="fi-day-")
        self.addCleanup(shutil.rmtree, self.out, ignore_errors=True)
        self.fetched = []

        self._rows, self._fetch, self._one = (fi_targets.rows, fi_fetch.fetch, fi_targets.one)
        fi_fetch.fetch = self.fake_fetch
        self.served = []
        # The watch list resolves through `one`, which is a network call. Served from the same
        # rows the window was served from, so a test never reaches the portal.
        fi_targets.one = self.fake_one
        self.addCleanup(self.restore)

    def restore(self):
        fi_targets.rows, fi_fetch.fetch, fi_targets.one = (self._rows, self._fetch, self._one)

    def serve(self, rows):
        self.served = list(rows)
        fi_targets.rows = lambda a, b=None: (len(rows), list(rows))

    def fake_one(self, reference):
        token = str(reference).split(":")[-1]
        for record in self.served:
            if record.get("noticeNumber") == token:
                return fi_page.notice(record)
        return None

    def fake_fetch(self, record, out_root, with_text=True):
        self.fetched.append(record["ref"])
        home = os.path.join(out_root, "tenders", record["pid"])
        os.makedirs(home, exist_ok=True)
        return {"pid": record["pid"], "ref": record["ref"], "kind": record["kind"],
                "home": home, "documents": 0, "catalogued": 0, "withheld": 0,
                "uncatalogued": 0, "bytes": 0, "index": {}, "state": None,
                "documents_available": record["documents_available"],
                "link_verified": True, "title": record["title"], "buyer": record["buyer"],
                "deadline": record["deadline"], "value": None, "cpv_main": record["cpv_main"]}

    def run_day(self, date="2026-09-02", **kw):
        kw.setdefault("reconcile", False)
        return fi_day.run(date, self.out, **kw)


class AwardsAreRecordedAndNotFetched(DayHarness):
    def test_an_award_never_becomes_a_card(self):
        """Awards are 35% of the volume and carry a title, a buyer, a CPV and a description
        exactly like a live tender. A run that fetched them would publish a month of decided
        procurements on its first night."""
        self.serve([row("EF-1"), row("EF-2", main="ContractAwardNotices")])
        day, changes = self.run_day()
        self.assertEqual(self.fetched, ["2026-000001"])
        self.assertEqual([c["ref"] for c in changes["closed"]], ["2026-000002"])

    def test_a_closed_procurement_is_still_reported_because_it_costs_no_request(self):
        """An award answers a card already on the board -- the competition it closes may be one
        somebody is still waiting on -- and the row that says so was already in hand."""
        self.serve([row("EF-2", main="ContractAwardNotices")])
        day, changes = self.run_day()
        self.assertEqual(day["coverage"]["closed"], 1)
        self.assertEqual(changes["closed"][0]["kind"], "award")
        self.assertEqual(changes["closed"][0]["pid"], "1002",
                         "a close must carry the tender's own key, or it lands on nothing")

    def test_a_contract_modification_is_closed_too(self):
        self.serve([row("EF-3", main="Modification")])
        day, changes = self.run_day()
        self.assertEqual(self.fetched, [])
        self.assertEqual(day["coverage"]["closed"], 1)

    def test_a_corrigendum_is_fetched_because_it_is_the_live_notice_now(self):
        self.serve([row("EF-4", isCorrigendum=True,
                        previousNoticeNumber=["2026-000003"])])
        day, changes = self.run_day()
        self.assertEqual(self.fetched, ["2026-000004"])
        self.assertEqual(changes["tenders"][0]["kind"], "amendment")
        self.assertEqual(changes["tenders"][0]["supersedes"], ["2026-000003"],
                         "a reader following the superseded number needs to be told where it "
                         "went -- the index has already deleted that row")


class AnEmptyWindow(DayHarness):
    def test_an_empty_working_day_is_a_broken_crawl_and_says_so(self):
        """Every way discovery can break returns an empty list rather than an error. Left
        unsaid, that is a green run, a complete day, an empty morning, and nothing to tell it
        from a public holiday."""
        self.serve([])
        day, _ = self.run_day("2026-09-02")            # a Wednesday
        self.assertTrue(day["discovery_failed"])
        self.assertFalse(day["complete"])

    def test_an_empty_saturday_is_the_country_resting(self):
        """Saturday carried 5 notices where the Sunday after it carried 72. It is the one day an
        empty answer is not evidence of anything."""
        self.serve([])
        day, _ = self.run_day("2026-09-05")            # a Saturday
        self.assertFalse(day["discovery_failed"])
        self.assertTrue(day["complete"])

    def test_an_empty_sunday_is_not_excused(self):
        """The batch that forwards notices to TED runs on Sundays. A weekday-only expectation
        would call its whole batch a broken crawl, and a weekday-only schedule would lose it."""
        self.serve([])
        day, _ = self.run_day("2026-09-06")            # a Sunday
        self.assertTrue(day["discovery_failed"])


class TheDayIsWritten(DayHarness):
    def test_the_index_is_the_last_file_and_the_day_the_last_of_all(self):
        self.serve([row("EF-1")])
        day, _ = self.run_day()
        folder = os.path.join(self.out, "2026-09-02")
        for name in ("day.json", "changes.json"):
            self.assertTrue(os.path.exists(os.path.join(folder, name)), name)

    def test_the_day_is_named_for_the_end_of_the_window(self):
        """So a reader who knows where yesterday landed knows where a three-day catch-up
        landed too."""
        self.serve([row("EF-1", day="2026-09-04")])
        day, _ = self.run_day("2026-09-02", date_to="2026-09-04")
        self.assertEqual(day["date"], "2026-09-04")
        self.assertTrue(os.path.exists(os.path.join(self.out, "2026-09-04", "day.json")))

    def test_the_day_carries_one_shard_so_every_country_reads_alike(self):
        """Latvia's day is four shards because its register refuses a third of the runner
        addresses it is offered. Hilma refused none of eight, so Finland runs on one -- and a
        reader of `day.json` should not have to know which country it is holding."""
        self.serve([row("EF-1")])
        day, _ = self.run_day()
        self.assertEqual(day["shards"], ["hilma-day-shard-1"])

    def test_the_coverage_block_carries_the_index_s_own_total(self):
        self.serve([row("EF-1"), row("EF-2", main="ContractAwardNotices")])
        day, _ = self.run_day()
        self.assertEqual(day["coverage"]["index_count"], 2)
        self.assertEqual(day["coverage"]["rows_held"], 2)
        self.assertEqual(day["coverage"]["live"], 1)

    def test_a_live_tender_with_no_deadline_is_named(self):
        """Measured: a deadline is present on 50 of 50 contract notices and null on 128 of 130
        awards. A live tender without one is not normal."""
        self.serve([row("EF-1", deadline=None, expirationDate=None)])
        day, _ = self.run_day()
        self.assertEqual(day["tenders_without_deadline"], ["2026-000001"])


class TheGate(DayHarness):
    POLICY = json.dumps({"recall_title_terms": ["rakennusautomaatio"]})

    def test_what_the_gate_cut_is_named_and_not_merely_dropped(self):
        """A tender nobody fetched and nobody mentioned reads exactly like a tender that does
        not exist, and the list of what was cut is the fastest way to see a badly tuned word
        list."""
        self.serve([row("EF-1"), row("EF-2", titleFi="Rakennusautomaation uusiminen")])
        day, changes = self.run_day(policy=self.POLICY)
        self.assertEqual(self.fetched, ["2026-000002"])
        self.assertEqual([g["ref"] for g in changes["gated"]], ["2026-000001"])
        self.assertEqual(day["coverage"]["gated"], 1)

    def test_a_watched_tender_is_fetched_whatever_the_gate_says(self):
        """The gate decides what is worth fetching for the FIRST time. A watched tender was
        already judged worth a card by a person, and dropping it here would silently stop
        answering the question the card is open for."""
        self.serve([row("EF-1")])
        day, _ = self.run_day(policy=self.POLICY, watch=["2026-000001"])
        self.assertEqual(self.fetched, ["2026-000001"])
        self.assertEqual(day["coverage"]["gated"], 0)

    def test_a_watched_tender_inside_the_window_is_counted_once(self):
        self.serve([row("EF-1", titleFi="Rakennusautomaatio")])
        day, changes = self.run_day(policy=self.POLICY, watch=["2026-000001"])
        self.assertEqual(self.fetched, ["2026-000001"])
        self.assertEqual(len(changes["tenders"]), 1)
        self.assertTrue(changes["tenders"][0]["watched"])


class WhoseFailureMakesADayShort(DayHarness):
    def test_a_tender_that_would_not_fetch_makes_the_window_short(self):
        def explode(record, out_root, with_text=True):
            raise RuntimeError("the CDN closed the connection")
        fi_fetch.fetch = explode
        self.serve([row("EF-1")])
        day, _ = self.run_day()
        self.assertFalse(day["complete"])
        self.assertEqual(day["coverage"]["failed"], 1)

    def test_a_watched_notice_the_index_will_not_serve_is_a_hole_not_a_short_day(self):
        """Letting a watch hole mark the night incomplete would mark EVERY night incomplete
        until a person edited the board, which teaches a reader to ignore the flag exactly when
        it starts meaning something."""
        self.serve([row("EF-1")])
        day, _ = self.run_day(watch=["2026-999999"])
        self.assertTrue(day["complete"])
        self.assertEqual(day["coverage"]["watch_holes"], 1)
        self.assertIn("corrigendum", day["lost"][0]["reason"])

    def test_a_link_the_detail_api_disagrees_with_makes_the_day_short(self):
        """Every rendered page on this portal answers 200, so `link_verified is False` means the
        one address that can disagree did. That is a wrong link, not a slow one, and publishing
        it sends somebody to an empty shell."""
        def unverified(record, out_root, with_text=True):
            done = self.fake_fetch(record, out_root, with_text)
            done["link_verified"] = False
            return done
        fi_fetch.fetch = unverified
        self.serve([row("EF-1")])
        day, _ = self.run_day()
        self.assertEqual(day["unverified_links"], ["2026-000001"])
        self.assertFalse(day["complete"])


if __name__ == "__main__":
    unittest.main()
