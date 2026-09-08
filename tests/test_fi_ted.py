"""The EU's copy of a Finnish tender, and what it is worth.

Ninety per cent of Finnish tenders keep their documents behind an account, so the notice is
usually the whole of the evidence. TED holds more of it than Hilma does for the EU tier, and this
file is about extracting that without inventing any of it.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fi_ted

# Trimmed from the real notice 556072-2026, the Imatra fire station automation side-contract.
XML = """<?xml version="1.0" encoding="UTF-8"?>
<ContractNotice xmlns:cbc="urn:cbc" xmlns:cac="urn:cac">
  <cbc:Description>Etela-Karjalan hyvinvointialue pyytaa tarjouksia Imatran paloaseman
    automaatiourakasta (sivu-urakka). Rakennuskohteena on palo- ja pelastusasema.</cbc:Description>
  <cbc:Description>Hankinta toteutetaan avoimella menettelylla. Tarjousten vertailussa
    hyodynnetaan hankintalain 44 mukaista sahkoista huutokauppaa.</cbc:Description>
  <cbc:Description>ei</cbc:Description>
  <cac:ProcurementProjectLot>
    <cbc:ID>LOT-0000</cbc:ID>
    <cbc:DocumentStatusCode>restricted-document</cbc:DocumentStatusCode>
  </cac:ProcurementProjectLot>
  <cac:AwardingCriterion>
    <cbc:AwardingCriterionTypeCode>price</cbc:AwardingCriterionTypeCode>
    <cbc:ParameterNumeric>100</cbc:ParameterNumeric>
    <cbc:Description>Hinta, painoarvo 100 %</cbc:Description>
  </cac:AwardingCriterion>
  <cac:TenderingProcess><cbc:ProcedureCode>open</cbc:ProcedureCode></cac:TenderingProcess>
  <cac:TendererQualificationRequest>
    <cbc:CompanyLegalFormCode>epo-sub-espd</cbc:CompanyLegalFormCode>
  </cac:TendererQualificationRequest>
</ContractNotice>"""


class WhatItExtracts(unittest.TestCase):
    def setUp(self):
        self.found = fi_ted.evidence(XML)

    def test_the_two_facts_hilma_never_carries(self):
        """A card without them sends somebody into a price-only reverse auction believing it
        was a quality competition."""
        self.assertEqual(self.found["procedure"], "open")
        self.assertTrue(self.found["electronic_auction"])
        self.assertEqual(self.found["award_criteria"][0]["type"], "price")
        self.assertEqual(self.found["award_criteria"][0]["weight"], "100")

    def test_it_says_whether_the_documents_can_be_had(self):
        """TED states this in as many words, and it is the same fact `documents_available`
        records from the Hilma side -- two independent sources agreeing is worth having."""
        self.assertEqual(self.found["document_access"], "restricted-document")

    def test_short_and_repeated_descriptions_are_dropped(self):
        """TED repeats: a criterion appears alone and again inside its lot. A card showing one
        paragraph three times reads as carelessness."""
        self.assertEqual(len(self.found["descriptions"]), 2)
        self.assertTrue(all(len(d) > 40 for d in self.found["descriptions"]))

    def test_the_lots_are_counted_because_the_component_test_asks(self):
        self.assertEqual(self.found["lots"], 1)

    def test_nothing_is_summarised(self):
        """`no located quote, no card` -- a paraphrase cannot be found in its source."""
        for description in self.found["descriptions"]:
            self.assertIn(description.split(".")[0][:40].strip(),
                          " ".join(XML.split()))


class WhenTedDoesNotHaveIt(unittest.TestCase):
    def test_a_notice_that_is_not_published_is_a_fact_not_a_failure(self):
        """The national tier never reaches TED at all, and that is 48% of a Finnish day."""
        original = fi_ted.fetch
        fi_ted.fetch = lambda ted_id, timeout=60: (_ for _ in ()).throw(
            fi_ted.NotPublished("nope"))
        try:
            self.assertIsNone(fi_ted.collect("1-2026"))
        finally:
            fi_ted.fetch = original

    def test_the_summary_says_so_rather_than_printing_nothing(self):
        self.assertEqual(fi_ted.summary(None), "TED: not published")

    def test_an_empty_notice_does_not_invent_fields(self):
        found = fi_ted.evidence("<ContractNotice/>")
        self.assertEqual(found["descriptions"], [])
        self.assertIsNone(found["procedure"])
        self.assertFalse(found["electronic_auction"])


class TheSummaryLine(unittest.TestCase):
    def test_it_names_the_auction_loudly(self):
        found = dict(fi_ted.evidence(XML), ted_id="556072-2026")
        line = fi_ted.summary(found)
        self.assertIn("ELECTRONIC AUCTION", line)
        self.assertIn("price at 100", line)
        self.assertIn("restricted-document", line)


if __name__ == "__main__":
    unittest.main()
