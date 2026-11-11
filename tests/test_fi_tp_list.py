"""Tarjouspalvelu's own list, from a page built to the portal's markup, without the network.

Under test: which rows are left to Hilma, which are competitions, how a portal time becomes UTC,
when a catch-up stops walking back, and that the day adds these tenders without letting them
touch the proof it makes against Hilma.
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fi_cloudia
import fi_day
import fi_fetch
import fi_page
import fi_tp_list
from test_fi_day import DayHarness, row


def card(tp, label, published, deadline="24.11.2026", title="Ilmanvaihdon huolto",
         buyer="Esimerkin kunta", description="Kunta pyytää tarjouksia."):
    stamp = lambda day, time: ('<span>%s </span>\n<span>%s </span>\n'
                               '<span class="">(UTC+02:00)</span>' % (day, time))
    name = ('<div class="font-075-rem">\n %s\n</div>' % label) if label is not None else ""
    return (
        '<a class="tp-list__row" href="/UX/TP/SiirryTarjouspyyntoon/?tpId=%s" onclick="x">'
        '<div class="tp-list__column tp-list__column--hy"><span class="sr-only">Avaa</span>'
        '<div class="label-xl-aria">Organisaatio</div>\n %s\n</div>'
        '<div class="tp-list__column tp-list__column--name"><span class="sr-only">Nimi</span>'
        '<span class="text-bold">%s</span>%s</div>'
        '<div class="tp-list__column xl-order-3"><div class="label-xl-aria">Kuvaus</div>'
        '<span title="%s">%s...</span></div>'
        '<div class="tp-list__column xl-order-3"><div class="label-xl-aria">Julkaisuaika</div>'
        '%s</div>'
        '<div class="tp-list__column xl-order-3"><div class="label-xl-aria">'
        'M&#228;&#228;r&#228;aika</div>%s</div></a>'
        % (tp, buyer, title, name, description, description[:10], stamp(published, "16.17"),
           stamp(deadline, "12.00") if deadline else ""))


PAGE = "<html>" + "".join([
    card("700001", "Kilpailu [E3]", "10.11.2026"),
    card("700002", "Pienhankinta", "10.11.2026"),
    card("700003", "Kansallisten hankintojen suorahankintailmoitus", "10.11.2026"),
    card("700004", "Kevennetty kilpailutus (pisteytys)", "10.11.2026", title="Rakennusautomaatio"),
    card("700005", "Pienhankinta", "9.11.2026"),
]) + "</html>"


class ThePage(unittest.TestCase):
    def test_a_row_is_read_whole(self):
        items = fi_tp_list.parse(PAGE)
        self.assertEqual(len(items), 5)
        first = items[1]
        self.assertEqual(first["tp_id"], "700002")
        self.assertEqual(first["buyer"], "Esimerkin kunta")
        self.assertEqual(first["label"], "Pienhankinta")
        self.assertEqual(first["description"], "Kunta pyytää tarjouksia.")
        # 16.17 at UTC+02:00 is 14.17 UTC; the window is in UTC dates.
        self.assertEqual(first["published"], "2026-11-10T14:17:00Z")
        self.assertEqual(first["deadline"], "2026-11-24T10:00:00Z")

    def test_hilma_keeps_its_own_and_only_competitions_are_kept(self):
        report, rows = fi_tp_list.window("2026-11-10", "2026-11-10", fetch=lambda n: PAGE)
        self.assertEqual([r["ref"] for r in rows], ["TP-700002", "TP-700004"])
        self.assertEqual((report["in_hilma"], report["not_competition"]), (1, 1))
        self.assertTrue(report["reached_start"])

    def test_a_catch_up_walks_back_and_stops_at_the_window_start(self):
        older = "<html>" + card("700009", "Pienhankinta", "1.11.2026") + "</html>"
        pages = {1: PAGE, 2: older, 3: PAGE}
        asked = []

        def fetch(n):
            asked.append(n)
            return pages[n]
        _, rows = fi_tp_list.window("2026-11-05", "2026-11-10", fetch=fetch)
        self.assertEqual(asked, [1, 2])
        self.assertEqual(sorted(r["ref"] for r in rows), ["TP-700002", "TP-700004", "TP-700005"])

    def test_the_row_is_a_tender_cloudia_can_read(self):
        _, rows = fi_tp_list.window("2026-11-10", "2026-11-10", fetch=lambda n: PAGE)
        tender = rows[0]
        self.assertEqual(tender["pid"], "tp-700002")
        self.assertEqual(tender["kind"], "tender")
        self.assertTrue(fi_page.is_live(tender))
        self.assertTrue(fi_cloudia.applies(tender))
        self.assertEqual(fi_cloudia._tender(tender), "700002")


class TheDay(DayHarness):
    def setUp(self):
        super().setUp()
        self._window = fi_tp_list.window
        self.addCleanup(setattr, fi_tp_list, "window", self._window)

    def test_portal_tenders_ride_after_the_window_and_leave_its_proof_alone(self):
        self.serve([row("EF-1")])
        fi_tp_list.window = lambda a, b: self._window(a, b, fetch=lambda n: PAGE)
        day, changes = self.run_day("2026-11-10", tarjouspalvelu=True)
        self.assertEqual(self.fetched, ["2026-000001", "TP-700002", "TP-700004"])
        self.assertEqual(day["coverage"]["rows_held"], 1, "Hilma's count is Hilma's alone")
        self.assertEqual(day["tarjouspalvelu_list"]["kept"], 2)

    def test_a_list_that_does_not_answer_costs_only_its_own_line(self):
        self.serve([row("EF-1")])

        def broken(a, b):
            raise OSError("connection reset")
        fi_tp_list.window = broken
        day, _ = self.run_day("2026-11-10", tarjouspalvelu=True)
        self.assertEqual(self.fetched, ["2026-000001"])
        self.assertIn("connection reset", day["tarjouspalvelu_list"]["error"])
        self.assertTrue(day["complete"])

    def test_a_watched_portal_tender_is_read_again_from_the_list(self):
        self.serve([row("EF-1")])
        find = fi_tp_list.find
        self.addCleanup(setattr, fi_tp_list, "find", find)
        fi_tp_list.find = lambda ids: find(ids, fetch=lambda n: PAGE if n == 1 else "")
        day, changes = self.run_day("2026-11-10", watch=["TP-700005", "TP-799999"])
        self.assertIn("TP-700005", self.fetched)
        holes = [f["ref"] for f in day["lost"]]
        self.assertEqual(holes, ["TP-799999"])
        self.assertTrue(day["complete"], "a hole in the watch is not a short window")

    def test_without_the_flag_the_list_is_never_asked(self):
        self.serve([row("EF-1")])
        fi_tp_list.window = lambda a, b: self.fail("the list was asked")
        day, _ = self.run_day("2026-11-10")
        self.assertIsNone(day["tarjouspalvelu_list"])


class TheFetch(unittest.TestCase):
    def test_a_portal_tender_never_asks_hilma(self):
        import shutil
        import tempfile
        out = tempfile.mkdtemp(prefix="fi-tp-")
        self.addCleanup(shutil.rmtree, out, ignore_errors=True)
        collect = fi_page.collect
        self.addCleanup(setattr, fi_page, "collect", collect)
        fi_page.collect = lambda *a, **k: self.fail("Hilma was asked about a portal tender")
        env = os.environ.pop("CLOUDIA_USERNAME", None)
        self.addCleanup(lambda: env and os.environ.__setitem__("CLOUDIA_USERNAME", env))
        _, rows = fi_tp_list.window("2026-11-10", "2026-11-10", fetch=lambda n: PAGE)
        done = fi_fetch.fetch(rows[0], out, with_text=False)
        with open(os.path.join(out, "tenders", "tp-700002", "index.json"), encoding="utf-8") as fh:
            index = json.load(fh)
        self.assertEqual(index["source"], "Tarjouspalvelu")
        self.assertEqual(index["ref"], "TP-700002")
        self.assertEqual(index["notice_type"], "Pienhankinta")
        self.assertEqual(done["documents_available"], "external")
        self.assertEqual(index["portal"]["status"], "skipped")


if __name__ == "__main__":
    unittest.main()
