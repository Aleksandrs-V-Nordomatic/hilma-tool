"""The document index: deterministic, located, and checkable.

A Finnish tender pack is hundreds of thousands of characters and the sentence that decides the
answer is one of them. This file is about finding it without a model, and about the two ways the
finding can be worthless: quoting a table of contents, and quoting something that has moved.
"""
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fi_index

BODY = """URAKKAOHJELMA
Sisallysluettelo
2 URAKKAMUOTO ........................................................................ 4
2.1 Kokonaisurakka ................................................................... 4
7.3 Asiakirjojen patevyysjarjestys ................................................. 8

2 URAKKAMUOTO
2.1 Kokonaisurakka
Rakennusautomaatiourakoitsija toimii paaurakoitsijana ja lainsaadannon tarkoittamana
paatoteuttajana. Urakkaan sisaltyy rakennusautomaation saatokaaviot ja alakeskukset.

7.3 Asiakirjojen patevyysjarjestys
Urakoitsijalla tulee olla RALA-patevyys tai vastaavat naytot. Urakoitsijan on kuuluttava
tilaajavastuu.fi-jarjestelman piiriin.
"""


class Harness(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="fi-index-")
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)
        os.makedirs(os.path.join(self.home, "doc"))

    def write(self, digest, text):
        with io.open(os.path.join(self.home, "doc", "%s.md" % digest), "w",
                     encoding="utf-8", newline="\n") as fh:
            fh.write(text)


class TheTableOfContentsIsNotAnAnswer(Harness):
    def test_a_contents_line_is_never_quoted(self):
        """Every Finnish contract programme opens with one, so the earliest hits for the
        questions that decide most were landing on `2 URAKKAMUOTO .........4` -- the right word
        in the wrong place, spending the document's whole quota before the section was reached.
        """
        self.write("a1", BODY)
        evidence = fi_index.build(self.home)
        for hit in evidence["questions"]["boundary"]:
            if hit.get("quote"):
                self.assertNotIn("....", hit["quote"])
        self.assertIn("toimii paaurakoitsijana", evidence["questions"]["boundary"][0]["quote"])

    def test_a_document_that_only_lists_our_subject_says_so(self):
        """A heading with no section under it is a fact about the tender, not an absence."""
        self.write("a1", "Sisallysluettelo\n2 URAKKAMUOTO ....................... 4\n")
        evidence = fi_index.build(self.home)
        hits = evidence["questions"]["boundary"]
        self.assertIsNone(hits[0]["offset"])
        self.assertIn("only in the table of contents", hits[0]["note"])

    def test_ordinary_prose_with_a_full_stop_is_not_mistaken_for_contents(self):
        self.write("a1", "Urakkamuoto on kokonaisurakka. Tama on tavallinen lause. Ei pisteita.")
        evidence = fi_index.build(self.home)
        self.assertIsNotNone(evidence["questions"]["boundary"][0]["offset"])


class EveryQuoteIsLocated(Harness):
    def test_a_quote_slices_back_out_of_the_file_it_names(self):
        """`No located quote, no card` becomes a check rather than a promise."""
        self.write("a1", BODY)
        evidence = fi_index.build(self.home)
        result = fi_index.verify(self.home, evidence)
        self.assertTrue(result["ok"])
        self.assertGreaterEqual(result["checked"], 5)
        self.assertEqual(result["drifted"], 0)

    def test_a_document_that_changed_under_the_index_is_caught(self):
        """The failure this guards is the extractor moving while the evidence stands still."""
        self.write("a1", BODY)
        evidence = fi_index.build(self.home)
        self.write("a1", "completely different text now")
        self.assertFalse(fi_index.verify(self.home, evidence)["ok"])

    def test_the_offset_is_into_the_original_not_the_folded_text(self):
        self.write("a1", BODY)
        evidence = fi_index.build(self.home)
        text = io.open(os.path.join(self.home, "doc", "a1.md"), encoding="utf-8").read()
        for hit in evidence["questions"]["control_system"]:
            if hit.get("offset") is not None:
                self.assertEqual(text[hit["offset"]:hit["end"]], hit["term"])


class ItIsDeterministic(Harness):
    def test_two_runs_over_one_pack_agree_exactly(self):
        """Same bytes in, same index out -- which is what lets `changes` tell a buyer's
        amendment from our own tooling moving."""
        self.write("a1", BODY)
        first = fi_index.build(self.home)
        second = fi_index.build(self.home)
        first.pop("built_at"); second.pop("built_at")
        self.assertEqual(json.dumps(first, sort_keys=True), json.dumps(second, sort_keys=True))


class WhichDocumentToOpenFirst(Harness):
    def test_the_kind_comes_from_the_heading_when_the_name_is_a_code(self):
        """Finnish design packs are numbered, not named: the Lapua retrofit arrives as J6000.pdf
        through J6998.pdf and the file name carries nothing at all."""
        self.assertEqual(fi_index.kind_of("J6998.pdf", BODY), "urakkaohjelma")
        self.assertEqual(fi_index.kind_of("J6998.pdf", None), "muu")

    def test_the_name_wins_when_it_says_something(self):
        self.assertEqual(fi_index.kind_of("Urakkarajaliite.pdf"), "urakkarajaliite")
        self.assertEqual(fi_index.kind_of("Tarjouspyynto_LVIA.pdf"), "tarjouspyynto")

    def test_the_boundary_annex_is_read_before_anything_else(self):
        """It answers the question that refuses most Finnish tenders, in the buyer's writing."""
        self.write("a1", "Piirustus 1")
        self.write("b2", "URAKKARAJALIITE\nUrakkarajat on esitetty taulukossa.")
        evidence = fi_index.build(self.home)
        self.assertEqual(evidence["reading_order"][0]["kind"], "urakkarajaliite")

    def test_an_unanswered_question_is_named(self):
        """An absence is a finding: a pack silent on the boundary is a tender whose contract
        shape nobody wrote down."""
        self.write("a1", "Tama asiakirja ei kerro mitaan.")
        evidence = fi_index.build(self.home)
        self.assertIn("boundary", evidence["coverage"]["questions_silent"])


if __name__ == "__main__":
    unittest.main()
