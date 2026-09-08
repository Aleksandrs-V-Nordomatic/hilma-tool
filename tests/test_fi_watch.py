"""The registration queue, and the one failure it exists to make impossible.

Finland is the only country in this family where a person stands between the run deciding a
tender is interesting and the run being able to read it. Everything else here can fail loudly;
this can fail by everything working and nobody bidding.
"""
import datetime
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fi_watch


def record(pid="36616", ref="2026-056114", deadline=None, host="tarjouspalvelu.fi",
           available="external", **extra):
    out = {"pid": pid, "ref": ref, "title": "Rakennusautomaatiourakka", "buyer": "Kaupunki",
           "deadline": deadline, "link": "https://example.invalid/%s" % pid,
           "documents_host": host, "documents_url": "https://%s/x" % host,
           "documents_available": available}
    out.update(extra)
    return out


def days(n):
    return (datetime.datetime.now(datetime.timezone.utc)
            + datetime.timedelta(days=n)).strftime("%Y-%m-%dT%H:%M:%SZ")


class Harness(unittest.TestCase):
    def setUp(self):
        self.out = tempfile.mkdtemp(prefix="fi-watch-")
        self.addCleanup(shutil.rmtree, self.out, ignore_errors=True)
        self.queue = fi_watch.load(self.out)


class WhatTheQueueHolds(Harness):
    def test_everything_the_day_saw_is_recorded_including_what_the_gate_dropped(self):
        """The queue is the picture. The gate governs downloads, never knowledge."""
        fi_watch.observe(self.queue, [record("1", "2026-000001"), record("2", "2026-000002")])
        self.assertEqual(len(self.queue["tenders"]), 2)
        self.assertEqual({e["state"] for e in self.queue["tenders"].values()}, {"seen"})

    def test_seeing_a_tender_again_keeps_its_state_and_refreshes_its_facts(self):
        fi_watch.observe(self.queue, [record(deadline=days(20))])
        fi_watch.note(self.queue["tenders"]["36616"], fi_watch.REGISTERED)
        fi_watch.observe(self.queue, [record(deadline=days(20), title="renamed")])
        entry = self.queue["tenders"]["36616"]
        self.assertEqual(entry["state"], "registered", "a second sighting reset the state")
        self.assertEqual(entry["title"], "renamed")

    def test_a_moved_deadline_is_news_and_is_kept_beside_the_old_one(self):
        """A corrigendum can take a tender from comfortable to overdue between two nights, and
        the person waiting to register would otherwise never be told."""
        fi_watch.observe(self.queue, [record(deadline=days(30))])
        fi_watch.note(self.queue["tenders"]["36616"], fi_watch.CANDIDATE)
        fi_watch.observe(self.queue, [record(deadline=days(2))])
        entry = self.queue["tenders"]["36616"]
        self.assertTrue(entry["deadline_was"])
        self.assertIn("moved the deadline", entry["history"][-1]["why"])

    def test_the_portal_a_person_must_go_to_is_carried(self):
        fi_watch.observe(self.queue, [record(host="tarjouspalvelu.fi")])
        self.assertEqual(self.queue["tenders"]["36616"]["portal"], "tarjouspalvelu.fi")


class TheDeadlineIsTheFailure(Harness):
    def test_a_deadline_that_passes_while_waiting_to_be_registered_is_its_own_state(self):
        """Everything worked and nobody bid. That is not housekeeping and it is not a drop."""
        fi_watch.observe(self.queue, [record(deadline=days(-1))])
        fi_watch.note(self.queue["tenders"]["36616"], fi_watch.CANDIDATE)
        ready, expired = fi_watch.refresh(self.queue, available=lambda e: False)
        self.assertEqual(expired, ["36616"])
        self.assertEqual(self.queue["tenders"]["36616"]["state"], "expired")
        self.assertIn("while this was waiting",
                      self.queue["tenders"]["36616"]["history"][-1]["why"])

    def test_such_a_loss_is_reported_and_not_merely_stored(self):
        fi_watch.observe(self.queue, [record(deadline=days(-1))])
        fi_watch.note(self.queue["tenders"]["36616"], fi_watch.REGISTERED)
        fi_watch.refresh(self.queue, available=lambda e: False)
        work = fi_watch.due(self.queue)
        self.assertEqual([e["ref"] for e in work["overdue"]], ["2026-056114"])
        self.assertTrue(any("LOST" in line for line in fi_watch.report(self.queue)))

    def test_a_tender_nobody_wanted_expiring_is_not_reported_as_a_loss(self):
        """It reached `seen` and no further, so nothing was lost -- and a report that cried
        about every expiring notice in the country would be read by nobody."""
        fi_watch.observe(self.queue, [record(deadline=days(-1))])
        fi_watch.refresh(self.queue, available=lambda e: False)
        self.assertEqual(fi_watch.due(self.queue)["overdue"], [])

    def test_a_deadline_inside_the_week_is_shouted_about_rather_than_listed(self):
        fi_watch.observe(self.queue, [record("1", "2026-000001", deadline=days(3)),
                                      record("2", "2026-000002", deadline=days(30))])
        for pid in ("1", "2"):
            fi_watch.note(self.queue["tenders"][pid], fi_watch.CANDIDATE)
        work = fi_watch.due(self.queue)
        self.assertEqual([e["ref"] for e in work["urgent"]], ["2026-000001"])
        self.assertEqual([e["ref"] for e in work["waiting"]], ["2026-000002"])
        self.assertTrue(any("REGISTER NOW" in l for l in fi_watch.report(self.queue)))

    def test_the_urgent_list_is_soonest_first(self):
        for pid, n in (("1", 6), ("2", 2), ("3", 4)):
            fi_watch.observe(self.queue, [record(pid, "2026-00000%s" % pid, deadline=days(n))])
            fi_watch.note(self.queue["tenders"][pid], fi_watch.CANDIDATE)
        self.assertEqual([e["pid"] for e in fi_watch.due(self.queue)["urgent"]],
                         ["2", "3", "1"])

    def test_a_tender_with_no_stated_deadline_waits_rather_than_expiring(self):
        fi_watch.observe(self.queue, [record(deadline=None)])
        fi_watch.note(self.queue["tenders"]["36616"], fi_watch.CANDIDATE)
        _, expired = fi_watch.refresh(self.queue, available=lambda e: False)
        self.assertEqual(expired, [])
        self.assertEqual([e["pid"] for e in fi_watch.due(self.queue)["waiting"]], ["36616"])


class WaitingForTheDocuments(Harness):
    def test_documents_appearing_moves_a_tender_to_ready(self):
        fi_watch.observe(self.queue, [record(deadline=days(20))])
        fi_watch.note(self.queue["tenders"]["36616"], fi_watch.REGISTERED)
        ready, _ = fi_watch.refresh(self.queue, available=lambda e: True)
        self.assertEqual(ready, ["36616"])
        self.assertEqual(self.queue["tenders"]["36616"]["state"], "ready")

    def test_a_tender_nobody_registered_on_is_still_asked_about(self):
        """A Hilma-hosted tender needs no registration at all, so a candidate can become ready
        on its own -- which is the 10% of the country that costs nobody an act."""
        fi_watch.observe(self.queue, [record(deadline=days(20), available="hilma")])
        fi_watch.note(self.queue["tenders"]["36616"], fi_watch.CANDIDATE)
        ready, _ = fi_watch.refresh(self.queue, available=lambda e: True)
        self.assertEqual(ready, ["36616"])

    def test_a_judged_tender_is_not_asked_about_again(self):
        fi_watch.observe(self.queue, [record(deadline=days(20))])
        fi_watch.note(self.queue["tenders"]["36616"], fi_watch.JUDGED)
        asked = []
        fi_watch.refresh(self.queue, available=lambda e: asked.append(e) or True)
        self.assertEqual(asked, [], "a settled tender was re-checked against the portal")

    def test_the_default_check_never_asks_a_portal_that_needs_an_account(self):
        """Asking one would be answered with a login page under a 200, and the queue would
        record a tender as ready whose documents nobody can read."""
        self.assertFalse(fi_watch._hilma_has_documents(
            {"documents_available": "external", "pid": "36616", "ref": "2026-056114"}))
        self.assertFalse(fi_watch._hilma_has_documents(
            {"documents_available": "none", "pid": "36616", "ref": "2026-056114"}))


class ThePersistedFile(Harness):
    def test_the_queue_survives_a_run_and_keeps_what_a_person_did(self):
        fi_watch.observe(self.queue, [record()])
        fi_watch.note(self.queue["tenders"]["36616"], fi_watch.REGISTERED, why="registered by AV")
        fi_watch.save(self.out, self.queue)
        again = fi_watch.load(self.out)
        self.assertEqual(again["tenders"]["36616"]["state"], "registered")
        self.assertEqual(again["tenders"]["36616"]["history"][-1]["why"], "registered by AV")

    def test_a_queue_that_will_not_parse_is_never_silently_replaced(self):
        """It is the only record of which tenders somebody has already registered on, and no
        portal will tell us that again."""
        with open(fi_watch.path(self.out), "w", encoding="utf-8") as fh:
            fh.write("{ this is not json")
        with self.assertRaises(ValueError):
            fi_watch.load(self.out)

    def test_it_is_written_whole_or_not_at_all(self):
        fi_watch.observe(self.queue, [record()])
        where = fi_watch.save(self.out, self.queue)
        self.assertTrue(os.path.exists(where))
        self.assertFalse(os.path.exists(where + ".writing"))
        with open(where, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["schema"], fi_watch.SCHEMA)

    def test_the_history_keeps_how_long_a_tender_sat_where(self):
        """The interesting question about a lost tender is never the state it ended in."""
        fi_watch.observe(self.queue, [record()])
        entry = self.queue["tenders"]["36616"]
        for state in (fi_watch.CANDIDATE, fi_watch.REGISTERED, fi_watch.READY):
            fi_watch.note(entry, state)
        self.assertEqual([h["state"] for h in entry["history"]],
                         ["seen", "candidate", "registered", "ready"])


if __name__ == "__main__":
    unittest.main()
