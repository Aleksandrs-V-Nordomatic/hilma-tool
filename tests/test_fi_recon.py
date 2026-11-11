"""The TED control, with TED and Hilma both served from memory.

Two ways it reported a whole day as short on 2026-11-09, and both are under test: a joint notice
of the EU institutions that names a Finnish agency among forty-eight buyers, and a notice Hilma
holds only as the predecessor a corrigendum replaced.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fi_recon
import fi_targets
import net


class TedSide(unittest.TestCase):
    def setUp(self):
        self._open = net.open_url
        self.addCleanup(setattr, net, "open_url", self._open)

    def test_a_notice_led_from_abroad_is_returned_apart(self):
        body = {"totalNoticeCount": 2, "notices": [
            {"publication-number": "778153-2026", "buyer-country": ["FIN", "FIN"]},
            {"publication-number": "778712-2026", "buyer-country": ["BEL", "FIN", "DEU"]}]}
        net.open_url = lambda *a, **k: (body, {})
        total, numbers, abroad = fi_recon.ted_notices("2026-11-09")
        self.assertEqual((total, numbers), (2, ["778153-2026", "778712-2026"]))
        self.assertEqual(abroad, ["778712-2026"])


class HilmaSide(unittest.TestCase):
    def setUp(self):
        self._ask = fi_targets._ask
        self.addCleanup(setattr, fi_targets, "_ask", self._ask)
        self.asked = []

    def test_a_superseded_notice_is_found_through_its_successor(self):
        def ask(criteria, **kw):
            self.asked.append(criteria)
            return 2, [{"noticeNumber": "2026-061483", "tedPublicationId": "781017-2026",
                        "previousTedPublicationId": ["778153-2026", "746079-2026"]},
                       {"noticeNumber": "2026-061000", "tedPublicationId": "777000-2026"}]
        fi_targets._ask = ask
        found = fi_recon.in_hilma(["778153-2026", "777000-2026", "779999-2026"], "2026-11-09")
        self.assertEqual(found, {"778153-2026", "777000-2026"},
                         "only the numbers asked for count, whichever field carried them")
        self.assertIn("previousTedPublicationId/any(p: p eq '778153-2026')", self.asked[0])


class TheControl(unittest.TestCase):
    def setUp(self):
        self.saved = (fi_recon.ted_notices, fi_recon.in_hilma, fi_recon.statistics)
        self.addCleanup(self.restore)
        fi_recon.statistics = lambda: {}

    def restore(self):
        fi_recon.ted_notices, fi_recon.in_hilma, fi_recon.statistics = self.saved

    def test_a_notice_led_from_abroad_never_makes_the_day_short(self):
        fi_recon.ted_notices = lambda d: (2, ["778153-2026", "778712-2026"], ["778712-2026"])
        fi_recon.in_hilma = lambda numbers, d: set(numbers) & {"778153-2026"}
        report = fi_recon.run("2026-11-09", records=[], count=0)
        ted = [c for c in report["controls"] if c["control"] == "TED"][0]
        self.assertTrue(ted["ok"])
        self.assertEqual((ted["asked"], ted["led_abroad"]), (1, ["778712-2026"]))
        self.assertIn("+1 led abroad", fi_recon.line(report))


if __name__ == "__main__":
    unittest.main()
