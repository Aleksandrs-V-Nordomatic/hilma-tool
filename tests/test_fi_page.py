"""The reader's judgements, on the shapes this index actually serves.

Every fixture below is a real row, trimmed to the fields under test. The numbers in the
docstrings are measurements from the corpora the route pass left behind -- 376 rows over four
days with all 86 fields, and 5 174 rows over three months.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fi_page


EFORMS = {"id": "EF-56114", "noticeNumber": "2026-056114", "noticeId": 56114,
          "procedureId": 36616, "oldProcurementProjectId": 0, "planId": 0,
          "mainType": "ContractNotices", "titleFi": "Oulaisten seurakuntakeskus"}
LEGACY = {"id": "OLD-160554", "noticeNumber": "2026-160554", "noticeId": 160554,
          "procedureId": 0, "oldProcurementProjectId": 111148, "planId": 0,
          "mainType": "ContractNotices",
          "titleFi": "Sankariniemen kampuksen palovaroitinjarjestelma"}
PLAN = {"id": "PLAN-1272", "noticeNumber": None, "noticeId": 0, "procedureId": 0,
        "oldProcurementProjectId": 0, "planId": 1272, "isPlan": True,
        "mainType": "ProcurementPlan", "titleFi": "Riskienhallintajarjestelman hankinta"}


class TheThreeShapes(unittest.TestCase):
    """One index, three record shapes, and a run that knows only the first loses the others.

    Measured over 376 rows: 367 eForms, 5 legacy, 4 plans. The legacy rows are NOT history --
    all five were published inside the same four-day window as the rest.
    """

    def test_each_row_says_which_shape_it_is(self):
        self.assertEqual(fi_page.shape_of(EFORMS), "eforms")
        self.assertEqual(fi_page.shape_of(LEGACY), "legacy")
        self.assertEqual(fi_page.shape_of(PLAN), "plan")

    def test_no_home_is_ever_a_zero(self):
        """The failure this guards is silent and it destroys data.

        `procedureId` is 0 on every legacy row and every plan -- 2.3% of a live day. Keyed on it
        naively, all of them share `tenders/0/`: each overwrites the last, the day reports every
        one of them delivered, and the folder holds whichever came last.
        """
        homes = [fi_page.home_id(row) for row in (EFORMS, LEGACY, PLAN)]
        self.assertEqual(homes, ["36616", "old-111148", "plan-1272"])
        for home in homes:
            self.assertNotIn(home, ("0", "", None))
        self.assertEqual(len(set(homes)), 3, "two shapes share a home")

    def test_two_legacy_rows_do_not_share_a_home(self):
        other = dict(LEGACY, id="OLD-160561", noticeId=160561, oldProcurementProjectId=111149)
        self.assertNotEqual(fi_page.home_id(LEGACY), fi_page.home_id(other))

    def test_a_link_is_built_from_the_id_its_own_shape_uses(self):
        """A URL built from a zero opens the portal's empty shell under a 200, for ever."""
        self.assertEqual(fi_page.view(EFORMS),
                         "https://www.hankintailmoitukset.fi/fi/public/procedure/36616/"
                         "enotice/56114")
        self.assertEqual(fi_page.view(LEGACY),
                         "https://www.hankintailmoitukset.fi/fi/public/procurement/111148/"
                         "notice/160554/overview")
        self.assertEqual(fi_page.view(PLAN),
                         "https://www.hankintailmoitukset.fi/fi/public/procurementplan/1272")
        for row in (EFORMS, LEGACY, PLAN):
            self.assertNotIn("/0/", fi_page.view(row))

    def test_a_plan_still_gets_a_reference_although_it_has_no_notice_number(self):
        """`noticeNumber` is null on every plan. A record keyed on it alone would key on None."""
        self.assertEqual(fi_page.notice(PLAN)["ref"], "PLAN-1272")


class WhichPopulation(unittest.TestCase):
    """Awards are 35% of the volume and look exactly like tenders."""

    def test_an_award_is_never_a_tender(self):
        self.assertEqual(fi_page.kind_of(dict(EFORMS, mainType="ContractAwardNotices")), "award")

    def test_a_corrigendum_to_an_award_is_still_an_award(self):
        """Order matters. Asked about the corrigendum flag first, this row becomes an
        `amendment` -- and a decided procurement is then put in front of somebody as a change
        worth reading."""
        row = dict(EFORMS, mainType="ContractAwardNotices", isCorrigendum=True)
        self.assertEqual(fi_page.kind_of(row), "award")

    def test_a_contract_modification_is_not_an_opportunity(self):
        """`Sopimusmuutos`: a change to a contract somebody else already won. One row on the
        measured day, and it is the difference between 68 live notices and the 67 there are."""
        self.assertEqual(fi_page.kind_of(dict(EFORMS, mainType="Modification")), "modification")
        self.assertFalse(fi_page.is_live(dict(EFORMS, mainType="Modification")))

    def test_the_live_populations_are_the_ones_a_card_can_come_from(self):
        for main, kind, live in (("ContractNotices", "tender", True),
                                 ("PriorInformationNotices", "consultation", True),
                                 ("ProcurementPlan", "plan", True),
                                 ("ContractAwardNotices", "award", False),
                                 ("Modification", "modification", False)):
            row = dict(EFORMS, mainType=main)
            with self.subTest(main=main):
                self.assertEqual(fi_page.kind_of(row), kind)
                self.assertEqual(fi_page.is_live(row), live)

    def test_is_live_reads_a_built_record_as_readily_as_a_row(self):
        self.assertTrue(fi_page.is_live(fi_page.notice(EFORMS)))
        self.assertFalse(fi_page.is_live(
            fi_page.notice(dict(EFORMS, mainType="ContractAwardNotices"))))


class FourLanguages(unittest.TestCase):
    """276 of 5 174 rows carry no Finnish title, and every one of them has another."""

    def test_the_languages_are_added_together_and_not_chosen_between(self):
        row = {"titleFi": "Rakennusautomaatio", "titleSv": "Fastighetsautomation",
               "titleEn": None, "titleOther": None}
        self.assertEqual(fi_page.texts(row, "title"),
                         "Rakennusautomaatio Fastighetsautomation")

    def test_a_row_with_no_finnish_title_is_not_blank(self):
        """Taking the first non-empty title finds the same text most of the time and goes blind
        exactly where the languages disagree -- which is where a bilingual municipality wrote
        different words for two audiences."""
        row = {"titleFi": None, "titleSv": "Ventilationsentreprenad", "titleEn": None}
        self.assertEqual(fi_page.texts(row, "title"), "Ventilationsentreprenad")
        self.assertTrue(fi_page.notice(row)["titles"])

    def test_the_display_title_falls_back_but_the_haystack_does_not(self):
        row = {"titleFi": None, "titleSv": "Svenska", "titleEn": "English"}
        built = fi_page.notice(row)
        self.assertEqual(built["title"], "Svenska")
        self.assertEqual(built["titles"], "Svenska English")


class TheValue(unittest.TestCase):
    """Five rows in 376 carry `0.0` with a null currency and 258 carry a plain null."""

    def test_zero_with_no_currency_is_the_index_saying_it_does_not_know(self):
        self.assertIsNone(fi_page.value_of({"estimatedValue": 0.0, "currency": None}))

    def test_a_stated_zero_is_left_alone(self):
        """A buyer who wrote a currency beside a zero said something, and it is not ours to
        overwrite."""
        self.assertEqual(fi_page.value_of({"estimatedValue": 0.0, "currency": "EUR"}),
                         {"amount": 0.0, "currency": "EUR"})

    def test_a_real_value_survives(self):
        self.assertEqual(fi_page.value_of({"estimatedValue": 250000, "currency": "EUR"}),
                         {"amount": 250000.0, "currency": "EUR"})

    def test_a_missing_value_is_missing(self):
        self.assertIsNone(fi_page.value_of({"estimatedValue": None, "currency": "EUR"}))


class WhereTheDocumentsAre(unittest.TestCase):
    """Eleven per cent readable, eighty-nine behind a supplier account, some none at all."""

    def test_the_three_states(self):
        for url, expected in (
                ("https://www.hankintailmoitukset.fi/fi/public/procedure/1/enotice/2", "hilma"),
                ("https://tarjouspalvelu.fi/oulu?id=123", "external"),
                ("https://bem.buildercom.net/x", "external"),
                ("", "none"),
                (None, "none")):
            with self.subTest(url=url):
                self.assertEqual(fi_page.documents_available({"procurementDocumentsUrl": url}),
                                 expected)

    def test_a_lookalike_host_is_not_hilma(self):
        """`endswith` on the host, never `in` on the URL: a buyer platform whose path happens to
        mention the portal must not be read as the portal."""
        self.assertEqual(
            fi_page.documents_available(
                {"procurementDocumentsUrl": "https://evil.example/hankintailmoitukset.fi/x"}),
            "external")

    def test_the_flag_reaches_the_record(self):
        built = fi_page.notice(dict(EFORMS,
                                    procurementDocumentsUrl="https://tarjouspalvelu.fi/x"))
        self.assertEqual(built["documents_available"], "external")


class TheApisOwnSpelling(unittest.TestCase):
    def test_the_misspelt_field_is_read_as_the_api_spells_it(self):
        """`includesDynamicPurcharingSystem`. Correcting it here reads a field that does not
        exist, and every dynamic purchasing system then reports as not being one -- which
        matters more in Finland than anywhere else, because a DPS is the one vehicle whose
        call-offs stay visible when a framework's do not."""
        self.assertTrue(fi_page.notice(dict(EFORMS,
                                            includesDynamicPurcharingSystem=True))["dps"])
        self.assertFalse(fi_page.notice(
            dict(EFORMS, includesDynamicPurchasingSystem=True))["dps"])


class TheCatalogue(unittest.TestCase):
    def test_an_attachment_the_portal_is_not_serving_is_kept_apart(self):
        """Downloading a status the portal has never served fetches an error page under a 200,
        and the manifest then carries it as a document."""
        public, withheld = fi_page.catalogue({"attachments": [
            {"id": "a", "name": "spec.zip", "status": 10, "attachmentEntityType": 1},
            {"id": "b", "name": "later.zip", "status": 20, "attachmentEntityType": 1}]})
        self.assertEqual([r["id"] for r in public], ["a"])
        self.assertEqual([r["id"] for r in withheld], ["b"])
        self.assertEqual(public[0]["download"],
                         "https://cdn.hankintailmoitukset.fi/public-attachments/a")

    def test_a_notice_with_no_attachments_is_two_empty_lists_and_not_an_error(self):
        self.assertEqual(fi_page.catalogue(None), ([], []))
        self.assertEqual(fi_page.catalogue({}), ([], []))


class TheQuery(unittest.TestCase):
    def test_every_odata_parameter_is_sent_with_its_dollar(self):
        """The single most dangerous failure on this route: a `$filter` whose dollar is lost is
        IGNORED rather than refused, and the endpoint answers with the whole index under a 200.
        """
        sent = {}

        def fake(url, headers=None, timeout=None, log=None):
            sent["url"] = url
            return {"value": [], "@odata.count": 0}, {}

        original_get, original_pace = fi_page.net.get_json, fi_page._pace
        fi_page.net.get_json = fake
        fi_page._pace = lambda *a, **k: None
        try:
            fi_page.search({"search": "*", "count": "true", "top": 5, "filter": "a eq 1"})
        finally:
            fi_page.net.get_json, fi_page._pace = original_get, original_pace

        self.assertIn("%24filter=", sent["url"])
        self.assertIn("%24count=", sent["url"])
        self.assertIn("%24top=", sent["url"])
        # `search` is the one parameter that carries no dollar, and adding one would make it a
        # parameter the index does not know -- which it ignores rather than refuses.
        self.assertIn("search=%2A", sent["url"])
        self.assertNotIn("%24search", sent["url"])

    def test_a_parameter_with_no_value_is_not_sent_at_all(self):
        sent = {}

        def fake(url, headers=None, timeout=None, log=None):
            sent["url"] = url
            return {"value": [], "@odata.count": 0}, {}

        original_get, original_pace = fi_page.net.get_json, fi_page._pace
        fi_page.net.get_json = fake
        fi_page._pace = lambda *a, **k: None
        try:
            fi_page.search({"search": "*", "skip": None, "orderby": None})
        finally:
            fi_page.net.get_json, fi_page._pace = original_get, original_pace
        self.assertNotIn("skip", sent["url"])
        self.assertNotIn("orderby", sent["url"])


if __name__ == "__main__":
    unittest.main()
