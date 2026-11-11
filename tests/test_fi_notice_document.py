"""The notice as a document: `doc/notice.md`, in every home.

A card's quote is verified against the home's `doc/*.md` and nothing else. Most Finnish tenders
have no document anyone without an account can read, so until the notice itself was a document a
tender judged on its notice alone could never be carded. Under test: what the rendering keeps
(the description, verbatim), what it leaves out (fields with no value), and that every home
`fi_fetch.fetch` writes carries the file, names it, and builds its evidence over it.
"""
import glob
import json
import os
import re
import shutil
import sys
import tempfile
import unicodedata
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fi_fetch
import fi_index
import fi_page
import fi_tp_list
from test_fi_tp_list import PAGE


DESCRIPTION = ("Oulaisten seurakunta pyytää tarjouksia seurakuntakeskuksen ilmanvaihdon\n"
               "saneerauksesta.  Urakkaan sisältyy rakennusautomaation uusiminen.\n\n"
               "* kohta 1: IV-koneet\n"
               "# ei otsikko, vaan ostajan oma rivi\n"
               "„Ilmanvaihto” – huolto ja «säätö» kuuluvat urakkaan.")

FACTS = {"title": "Oulaisten seurakuntakeskuksen ilmanvaihto",
         "ref": "2026-056114",
         "source": "Hilma",
         "buyer": "Oulaisten seurakunta",
         "published": "2026-11-07T16:05:01.157Z",
         "deadline": "2026-12-01T10:00:00Z",
         "kind": "tender",
         "procedure": "open",
         "cpv": ["45331000", "45331210"],
         "value": {"amount": 250000.0, "currency": "EUR"},
         "link": "https://www.hankintailmoitukset.fi/fi/public/procedure/36616/enotice/56114",
         "titles": "Oulaisten seurakuntakeskuksen ilmanvaihto",
         "description": DESCRIPTION}

# Shaped as `fi_ted.collect` returns it, from the real notice 556072-2026.
TED = {"ted_id": "556072-2026",
       "descriptions": [
           "Etela-Karjalan hyvinvointialue pyytaa tarjouksia Imatran paloaseman "
           "automaatiourakasta (sivu-urakka). Rakennuskohteena on palo- ja pelastusasema.",
           "Hankinta toteutetaan avoimella menettelylla. Tarjousten vertailussa hyodynnetaan "
           "hankintalain 44 mukaista sahkoista huutokauppaa."],
       "award_criteria": [{"type": "price", "weight": "100",
                           "text": "Hinta, painoarvo 100 %"}],
       "procedure": "open", "electronic_auction": True}

LABELLED = re.compile(r"^- [^:\n]+: \S")


def labelled(text):
    return [line for line in text.splitlines() if LABELLED.match(line)]


def section(text, heading):
    """The body under a `## ` heading, up to the next `## ` heading."""
    lines = text.splitlines()
    start = lines.index(heading)
    body = []
    for line in lines[start + 1:]:
        if line.startswith("## "):
            break
        body.append(line)
    return "\n".join(body)


def normalise(text):
    """The kit's quote check, as the brief states it."""
    text = unicodedata.normalize("NFC", text)
    for curly in "“”„‟«»‘’‚‛":
        text = text.replace(curly, "'" if curly in "‘’‚‛" else '"')
    text = text.replace("–", "-").replace("—", "-")
    return " ".join(text.split()).lower()


class TheName(unittest.TestCase):
    def test_the_document_is_named_once(self):
        self.assertEqual(fi_page.NOTICE_DOCUMENT, "doc/notice.md")


class TheHeading(unittest.TestCase):
    def test_it_opens_with_the_title(self):
        text = fi_page.notice_markdown(FACTS)
        self.assertIsInstance(text, str)
        self.assertEqual(text.splitlines()[0], "# Oulaisten seurakuntakeskuksen ilmanvaihto")

    def test_a_notice_with_no_title_is_headed_by_its_ref(self):
        text = fi_page.notice_markdown(dict(FACTS, title=None, titles=None))
        self.assertEqual(text.splitlines()[0], "# 2026-056114")


class TheFacts(unittest.TestCase):
    def test_the_stated_facts_are_labelled_lines(self):
        lines = "\n".join(labelled(fi_page.notice_markdown(FACTS)))
        for value in ("Oulaisten seurakunta", FACTS["link"], "45331000", "45331210"):
            with self.subTest(value=value):
                self.assertIn(value, lines)

    def test_a_field_with_no_value_is_left_out(self):
        """An empty label reads as the register saying something it did not."""
        whole = len(labelled(fi_page.notice_markdown(FACTS)))
        for field in ("buyer", "published", "deadline", "kind", "procedure", "cpv", "value",
                      "link"):
            with self.subTest(field=field):
                text = fi_page.notice_markdown(dict(FACTS, **{field: None}))
                self.assertEqual(len(labelled(text)), whole - 1)
                self.assertNotIn("None", text)

    def test_a_missing_key_is_the_same_as_no_value(self):
        facts = {k: v for k, v in FACTS.items() if k != "buyer"}
        self.assertEqual(fi_page.notice_markdown(facts),
                         fi_page.notice_markdown(dict(FACTS, buyer=None)))


class TheTitles(unittest.TestCase):
    def test_no_titles_section_when_they_say_the_same(self):
        self.assertNotIn("## Titles", fi_page.notice_markdown(FACTS).splitlines())

    def test_every_language_is_kept_when_they_differ(self):
        """A bilingual buyer writes different words for two audiences, and a quote may be
        taken from either."""
        both = "Oulaisten seurakuntakeskuksen ilmanvaihto Ventilation av församlingscentret"
        text = fi_page.notice_markdown(dict(FACTS, titles=both))
        self.assertIn("## Titles", text.splitlines())
        self.assertIn(both, section(text, "## Titles"))


class TheDescription(unittest.TestCase):
    def test_the_description_is_verbatim(self):
        """A quote is found byte for byte or not at all: the renderer may not tidy spacing,
        straighten quotes or escape the buyer's own asterisks."""
        text = fi_page.notice_markdown(FACTS)
        self.assertIn("## Description", text.splitlines())
        self.assertIn(DESCRIPTION, text)
        self.assertEqual(section(text, "## Description").strip(), DESCRIPTION)

    def test_only_the_surrounding_whitespace_is_stripped(self):
        text = fi_page.notice_markdown(dict(FACTS, description="\n   " + DESCRIPTION + "  \n\n"))
        self.assertEqual(section(text, "## Description").strip(), DESCRIPTION)
        self.assertIn(DESCRIPTION, text)

    def test_a_quote_from_index_json_is_found_by_the_kits_check(self):
        text = normalise(fi_page.notice_markdown(FACTS))
        for quote in ("saneerauksesta.  Urakkaan sisältyy",
                      "„Ilmanvaihto” – huolto ja «säätö»"):
            with self.subTest(quote=quote):
                self.assertIn(normalise(quote), text)

    def test_no_description_still_has_its_section_and_says_so(self):
        for empty in (None, "", "   \n"):
            with self.subTest(description=empty):
                text = fi_page.notice_markdown(dict(FACTS, description=empty))
                self.assertIn("## Description", text.splitlines())
                self.assertTrue(section(text, "## Description").strip(),
                                "the placeholder sentence is missing")
                self.assertNotIn("None", text)

    def test_the_description_comes_after_the_facts(self):
        text = fi_page.notice_markdown(FACTS)
        last_fact = max(text.index(line) for line in labelled(text))
        self.assertLess(last_fact, text.index("## Description"))


class TheEuTier(unittest.TestCase):
    def test_teds_text_follows_the_description(self):
        text = fi_page.notice_markdown(FACTS, TED)
        self.assertIn("## TED 556072-2026", text.splitlines())
        self.assertLess(text.index("## Description"), text.index("## TED 556072-2026"))
        body = text[text.index("## TED 556072-2026"):]
        for description in TED["descriptions"]:
            with self.subTest(description=description[:30]):
                self.assertIn(description, body)

    def test_the_award_criteria_are_a_list(self):
        text = fi_page.notice_markdown(FACTS, TED)
        body = text[text.index("## TED 556072-2026"):]
        self.assertIn("### Award criteria", body.splitlines())
        criteria = body[body.index("### Award criteria"):]
        items = [line for line in criteria.splitlines() if line.startswith("- ")]
        self.assertTrue(any("Hinta, painoarvo 100 %" in line for line in items))

    def test_the_description_is_unchanged_by_ted(self):
        text = fi_page.notice_markdown(FACTS, TED)
        self.assertEqual(section(text, "## Description").strip(), DESCRIPTION)

    def test_no_ted_and_a_failed_ted_add_nothing(self):
        plain = fi_page.notice_markdown(FACTS)
        for ted in (None, {"error": "TED timed out", "ted_id": "556072-2026"}):
            with self.subTest(ted=ted):
                text = fi_page.notice_markdown(FACTS, ted)
                self.assertNotIn("## TED", text)
                self.assertNotIn("### Award criteria", text)
                self.assertEqual(text, plain)


class APortalRow(unittest.TestCase):
    def test_a_tarjouspalvelu_list_row_renders(self):
        _, rows = fi_tp_list.window("2026-11-10", "2026-11-10", fetch=lambda n: PAGE)
        text = fi_page.notice_markdown(rows[0])
        self.assertEqual(text.splitlines()[0], "# Ilmanvaihdon huolto")
        self.assertIn("Kunta pyytää tarjouksia.", section(text, "## Description"))
        self.assertIn("Esimerkin kunta", "\n".join(labelled(text)))


class InEveryHome(unittest.TestCase):
    """`fi_fetch.fetch` offline on a notice-only Tarjouspalvelu tender, as in
    `test_fi_tp_list.TheFetch`."""

    def setUp(self):
        self.out = tempfile.mkdtemp(prefix="fi-notice-")
        self.addCleanup(shutil.rmtree, self.out, ignore_errors=True)
        collect = fi_page.collect
        self.addCleanup(setattr, fi_page, "collect", collect)
        fi_page.collect = lambda *a, **k: self.fail("Hilma was asked about a portal tender")
        env = os.environ.pop("CLOUDIA_USERNAME", None)
        self.addCleanup(lambda: env and os.environ.__setitem__("CLOUDIA_USERNAME", env))
        _, self.rows = fi_tp_list.window("2026-11-10", "2026-11-10", fetch=lambda n: PAGE)
        fi_fetch.fetch(self.rows[0], self.out, with_text=False)
        self.home = os.path.join(self.out, "tenders", "tp-700002")
        with open(os.path.join(self.home, "index.json"), encoding="utf-8") as fh:
            self.index = json.load(fh)

    def notice(self, home=None):
        with open(os.path.join(home or self.home, "doc", "notice.md"), encoding="utf-8") as fh:
            return fh.read()

    def test_a_home_with_no_documents_still_has_the_notice(self):
        self.assertEqual(self.index["notice_document"], "doc/notice.md")
        text = self.notice()
        self.assertEqual(text.splitlines()[0], "# Ilmanvaihdon huolto")
        self.assertIn("Tarjouspalvelu", text, "the source travels with the notice")

    def test_the_description_is_exactly_as_index_json_holds_it(self):
        """So a quote taken from either is found in it."""
        description = self.index["description"]
        self.assertTrue(description)
        self.assertIn(description.strip(), self.notice())

    def test_the_kits_check_finds_the_description_among_the_documents(self):
        quote = normalise(self.index["description"])
        found = [path for path in glob.glob(os.path.join(self.home, "doc", "*.md"))
                 if quote in normalise(open(path, encoding="utf-8").read())]
        self.assertEqual([os.path.basename(p) for p in found], ["notice.md"])

    def test_the_file_is_what_notice_markdown_renders(self):
        self.assertEqual(self.notice(),
                         fi_page.notice_markdown(dict(self.rows[0], source="Tarjouspalvelu")))

    def test_evidence_is_built_without_documents(self):
        self.assertIn("evidence", self.index)
        self.assertIn("evidence_coverage", self.index)
        path = os.path.join(self.home, "evidence.json")
        self.assertTrue(os.path.exists(path))
        with open(path, encoding="utf-8") as fh:
            evidence = json.load(fh)
        self.assertTrue(fi_index.verify(self.home, evidence)["ok"])

    def test_every_home_gets_one(self):
        fi_fetch.fetch(self.rows[1], self.out, with_text=False)
        other = os.path.join(self.out, "tenders", "tp-700004")
        self.assertEqual(self.notice(other).splitlines()[0], "# Rakennusautomaatio")


if __name__ == "__main__":
    unittest.main()
