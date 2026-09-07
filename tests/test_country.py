#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""One run is one country, and the failure these tests exist to prevent succeeds quietly.

A run that reads one register and writes under another country's folder uploads cleanly,
produces a valid index, and hands that country's reader the wrong tenders with nothing
anywhere saying so. There
is no error to notice. So the rule is structural rather than checked after the fact: the
source and the destination are both derived from one resolved code, and every way of
expressing a mismatch is refused at the point it is expressed.

THIS REPOSITORY FETCHES ONE COUNTRY, AND THAT MAKES THE CHECK MORE IMPORTANT, NOT LESS.
Another country's code is one this tool has no source for, so it is refused here by the
same line that refuses `EE` — which is exactly the guarantee a country-per-repository split
has to provide. This tool cannot be pointed at another country's register, and it cannot be
pointed at another country's folder.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import country

# A code this repository deliberately has no source for. Any sibling's code will do: what
# matters is that a stale command line or a secret copied from another deployment names one,
# and that the answer is a refusal rather than a folder the tool guessed.
ELSEWHERE = "LV"


class Resolve(unittest.TestCase):
    def test_the_flag_wins_and_is_normalised(self):
        self.assertEqual(country.resolve("fi"), "FI")
        self.assertEqual(country.resolve(" FI "), "FI")

    def test_the_environment_answers_when_the_flag_does_not(self):
        self.assertEqual(country.resolve(None, {"EIS_COUNTRY": "FI"}), "FI")

    def test_there_is_no_default_even_with_one_country(self):
        """A tool that assumes its own country cannot say when it was pointed elsewhere."""
        with self.assertRaises(country.Mismatch) as raised:
            country.resolve(None, {})
        self.assertIn("no country", str(raised.exception))

    def test_a_code_that_is_not_two_letters_is_refused(self):
        for bad in ("E", "EST", "e_e", "../LV", "1E"):
            with self.subTest(bad=bad), self.assertRaises(country.Mismatch):
                country.resolve(bad)

    def test_a_country_with_no_source_is_refused_not_guessed(self):
        for code in (ELSEWHERE, "LT"):
            with self.subTest(code=code), self.assertRaises(country.Mismatch) as raised:
                country.resolve(code)
            self.assertIn("no source", str(raised.exception))

    def test_the_refusal_names_what_this_repository_does_have(self):
        # So a reader who ran the wrong tool learns which one they wanted, in one line.
        with self.assertRaises(country.Mismatch) as raised:
            country.resolve(ELSEWHERE)
        self.assertIn("FI", str(raised.exception))


class Destination(unittest.TestCase):
    def test_the_country_folder_is_appended_to_the_runtime_root(self):
        self.assertEqual(country.destination("Shared/project/work", "FI"),
                         "Shared/project/work/FI")

    def test_stray_slashes_do_not_double(self):
        self.assertEqual(country.destination("/base/work/", "FI"), "base/work/FI")

    def test_a_root_that_already_names_a_country_is_refused(self):
        """This is how `work/EE/EE` gets created, and then quietly filled."""
        with self.assertRaises(country.Mismatch) as raised:
            country.destination("project/work/FI", "FI")
        self.assertIn("already ends in a country code", str(raised.exception))

    def test_a_root_naming_the_country_this_tool_is_not_is_refused_too(self):
        # The likeliest misconfiguration of all: GRAPH_DEST_ROOT copied across from the
        # another country's deployment, still ending in its own country folder.
        with self.assertRaises(country.Mismatch):
            country.destination("project/work/%s" % ELSEWHERE, "FI")

    def test_an_empty_root_is_refused(self):
        with self.assertRaises(country.Mismatch):
            country.destination("", "FI")

    def test_the_folder_is_this_countrys_and_no_other(self):
        self.assertTrue(country.destination("project/work", "FI").endswith("/FI"))


class Source(unittest.TestCase):
    def test_the_reader_answers_the_questions_downstream_asks(self):
        """The whole point of the seam: downstream never learns which country it has.

        THREE QUESTIONS, NOT THREE FUNCTION NAMES. A sibling tool spells these
        `parse_notice`, `parse_documents` and `is_published`, because it reads a portal that
        renders HTML and answers 200 with a login form for a resource it will not serve. Two
        of those names would be false here — nothing is parsed, the register answers in
        JSON — and the third would be a check that cannot fail, which is worse than no check:
        it teaches a reader that the failure it names happens here, and it does not. This
        register says no by refusing.

        So the seam is asserted as the three questions any country tool must answer, under
        the names that are true of this one.
        """
        for code in sorted(country.SOURCES):
            page, fetch = country.source(code)
            with self.subTest(code=code):
                for name in ("notice", "catalogue", "collect"):
                    self.assertTrue(callable(getattr(page, name, None)),
                                    "%s.%s is missing" % (code, name))
                # How the reader says a procurement is not there, as a type a caller can
                # catch rather than a string it has to match.
                self.assertTrue(issubclass(getattr(page, "Refused", type(None)), Exception),
                                "%s has no way to say the register refused" % code)
                self.assertIsNotNone(fetch)

    def test_a_country_this_repository_does_not_fetch_has_no_source(self):
        for code in (ELSEWHERE, "LT"):
            with self.subTest(code=code), self.assertRaises(country.Mismatch):
                country.source(code)


class Describe(unittest.TestCase):
    def test_a_report_can_say_which_portal_it_read(self):
        self.assertEqual(country.describe("FI")["portal"], "Hilma")

    def test_every_country_names_a_timezone(self):
        for code in sorted(country.SOURCES):
            self.assertTrue(country.describe(code)["timezone"])

    def test_the_parser_stamped_beside_a_fingerprint_is_this_countrys(self):
        self.assertEqual(country.parser_files("FI"), ("fi_page.py",))


if __name__ == "__main__":
    unittest.main()
