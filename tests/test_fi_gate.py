"""The recall gate, on the Finnish strings that break it.

Finland's signal is carried by three- and four-letter section abbreviations -- LVIA, RAU, SAH,
KVR, ST -- and as bare substrings they match inside ordinary words. Every trap below is a real
title from the 5 174-row corpus the route pass pulled, and the counts in the docstrings were
measured over it on 2026-09-07.

WHAT THIS PROVES, AND IT IS THE POINT OF THE FILE: no second matcher was needed. `policy.fold`
already turns every run of punctuation into a space and pads the whole text, so a term written
with spaces around it matches only on a token boundary while one written bare stays a substring.
The two behaviours live in one list and the policy author chooses per term.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fi_page
import policy


def notice(**fields):
    row = {"id": "EF-1", "noticeId": 1, "procedureId": 2, "noticeNumber": "2026-000001",
           "mainType": "ContractNotices"}
    row.update(fields)
    return fi_page.notice(row)


def gate(recall, **rest):
    body = {"recall_title_terms": list(recall)}
    body.update(rest)
    import json
    return policy.load_policy(json.dumps(body))


class TheProvenTraps(unittest.TestCase):
    def test_a_spaced_abbreviation_does_not_match_inside_a_word(self):
        """`lvi` matches `pilvisiirtyma` on 18 titles in four days. Bare, the term brings 791
        rows out of 5 174; spaced, 70."""
        rules = gate([" lvi "])
        self.assertTrue(policy.outside_scope(notice(titleFi="Pilvisiirtyman toteutus"), rules))
        self.assertFalse(policy.outside_scope(
            notice(titleFi="Virastotalon LVI-peruskorjaus"), rules))

    def test_a_street_name_is_not_a_ventilation_contract(self):
        """`lvia` matches `Talvialantie`, a road. Bare: 102 rows. Spaced: 55."""
        rules = gate([" lvia "])
        self.assertTrue(policy.outside_scope(notice(titleFi="Talvialantie, perusparannus"),
                                             rules))
        self.assertFalse(policy.outside_scope(notice(titleFi="LVIA-suunnittelu, Oulainen"),
                                              rules))

    def test_a_bare_word_stays_a_substring_because_finnish_inflects(self):
        """`huolto` catches huoltotyot and huoltosopimus, and the price of that is
        `huoltonosturi`, a crane. Writing it spaced would lose the inflections, which is the
        larger loss -- so the policy author writes this one bare and knowingly."""
        rules = gate(["huolto"])
        for title in ("Kiinteiston huolto", "Huoltotyot 2026", "Huoltosopimus 2026",
                      "Huoltonosturin hankinta"):
            with self.subTest(title=title):
                self.assertFalse(policy.outside_scope(notice(titleFi=title), rules))

    def test_consonant_gradation_defeats_a_stem_that_looks_complete(self):
        """THE FINNISH TRAP A LATVIAN OR ESTONIAN POLICY AUTHOR WOULD NOT EXPECT. Finnish does
        not only add endings, it changes the stem: `huolto` becomes `huollon` in the genitive,
        `hankinta` becomes `hankinnan`, `urakka` becomes `urakan`. The `t` and the `k` are not
        there to match on any more.

        So a stem written to the word boundary silently misses the inflection a buyer is most
        likely to use in a title -- `laitteiden huollon hankinta` is ordinary Finnish and the
        term `huolto` does not see it. The stem has to stop before the gradating consonant.
        """
        strict = gate(["huolto"])
        self.assertTrue(policy.outside_scope(
            notice(titleFi="Laitteiden huollon hankinta"), strict))
        graded = gate(["huol"])
        for title in ("Laitteiden huollon hankinta", "Kiinteiston huolto", "Huoltotyot 2026"):
            with self.subTest(title=title):
                self.assertFalse(policy.outside_scope(notice(titleFi=title), graded))

    def test_a_term_at_either_end_of_a_title_still_matches(self):
        """Padding the whole text is what makes this work. Without it a spaced term never
        matches a title that begins or ends with it, and that failure is silent."""
        rules = gate([" lvia "])
        for title in ("LVIA urakka", "Urakka LVIA", "LVIA"):
            with self.subTest(title=title):
                self.assertFalse(policy.outside_scope(notice(titleFi=title), rules))

    def test_punctuation_around_a_term_does_not_hide_it(self):
        rules = gate([" lvia "])
        for title in ("Suunnittelu (LVIA)", "RAK-, GEO-, LVIA-, SAH-suunnittelu",
                      "Urakka: LVIA."):
            with self.subTest(title=title):
                self.assertFalse(policy.outside_scope(notice(titleFi=title), rules))


class TheHaystackIsFourLanguagesAndTheDescription(unittest.TestCase):
    def test_a_notice_with_no_finnish_title_is_still_reachable(self):
        """276 of 5 174 rows carry no `titleFi`, and every one of them carries a title in
        another language. A gate reading only the Finnish title is blind to 5.3% of the day."""
        rules = gate(["ventilation"])
        self.assertFalse(policy.outside_scope(
            notice(titleFi=None, titleSv="Ventilationsentreprenad Vasa"), rules))

    def test_the_buyers_description_is_read_and_it_arrives_free(self):
        """This index carries the description in the SEARCH ROW -- a median of 964 characters --
        which is what lets the whole window be gated before one request is made for a tender."""
        rules = gate(["rakennusautomaatio"])
        self.assertFalse(policy.outside_scope(
            notice(titleFi="Peruskorjaus, vaihe 2",
                   descriptionFi="Urakkaan sisaltyy rakennusautomaatio ja saatolaitteet."),
            rules))

    def test_a_notice_with_no_text_at_all_is_kept(self):
        """Missing signal fails open, in every country tool. Five contract notices in the
        measured window had an empty Finnish description; with a Cloudia-hosted notice and no
        description there is the title and the CPV and nothing else, and that is a legitimate
        unknown rather than a reason to drop it."""
        rules = gate(["rakennusautomaatio"])
        self.assertFalse(policy.outside_scope(notice(titleFi=None, descriptionFi=None), rules))


class TheCodesRecallToo(unittest.TestCase):
    """Unlike Estonia's, the Finnish search row carries the CPV codes -- so a code can bring a
    notice in before a byte moves, not only after a document has been read."""

    def test_a_space_separated_code_string_is_read_as_codes(self):
        built = notice(cpvCodes="45331000 71321000")
        self.assertEqual(built["cpv"], ["45331000", "71321000"])
        self.assertEqual(built["cpv_main"], "45331000")

    def test_an_exact_code_rescues_a_vague_title(self):
        rules = gate(["rakennusautomaatio"], recall_cpv_prefixes=["45331"])
        self.assertFalse(policy.outside_scope(
            notice(titleFi="Hankinta 12/2026", cpvCodes="45331000"), rules))

    def test_an_excluded_code_set_still_drops_a_notice_whose_text_says_nothing(self):
        rules = gate(["rakennusautomaatio"], hard_exclude_prefixes=["15"])
        self.assertTrue(policy.outside_scope(
            notice(titleFi="Elintarvikkeet", cpvCodes="15100000 15800000"), rules))

    def test_an_excluded_code_set_wins_even_over_a_matching_text(self):
        """RECORDED BECAUSE THE FILE ITSELF READS BOTH WAYS. `policy.py`'s header says in one
        bullet that a classification code never vetoes a matching title, and two lines later
        that exclusions win over recall. The code does the second: the exclusion test runs
        before the recall test and returns.

        That behaviour is shared with Latvia, Lithuania and Estonia, and it is not Finland's to
        change on its own -- so it is asserted here as it is, and the contradiction belongs in
        front of whoever owns the engine. What it means in practice: a `hard_exclude_prefixes`
        entry is absolute for a notice carrying no other code, so it is written narrowly, and an
        `override_prefixes` entry is how a genuine exception gets back in.
        """
        rules = gate(["rakennusautomaatio"], hard_exclude_prefixes=["15"])
        self.assertTrue(policy.outside_scope(
            notice(titleFi="Rakennusautomaatio, keittio", cpvCodes="15100000"), rules))

    def test_an_override_is_how_such_a_notice_gets_back_in(self):
        rules = gate(["rakennusautomaatio"], hard_exclude_prefixes=["15"],
                     override_prefixes=["15100"])
        self.assertFalse(policy.outside_scope(
            notice(titleFi="Rakennusautomaatio, keittio", cpvCodes="15100000"), rules))

    def test_one_code_outside_the_excluded_divisions_is_enough_to_keep_it(self):
        """`all`, not `any`: a notice carrying an excluded code beside a relevant one is kept."""
        rules = gate(["rakennusautomaatio"], hard_exclude_prefixes=["15"])
        self.assertFalse(policy.outside_scope(
            notice(titleFi="Rakennusautomaatio, keittio", cpvCodes="15100000 45331000"), rules))


class TheGateFailsOpen(unittest.TestCase):
    def test_an_unreadable_policy_fetches_everything(self):
        """Fetching too much costs time. Dropping silently costs a tender."""
        self.assertIsNone(policy.load_policy("{not json"))
        self.assertIsNone(policy.load_policy('{"recall_title_terms": []}'))

    def test_a_sibling_countrys_policy_name_stops_the_run(self):
        """A deployment is set up by copying the last one that worked. An environment carrying
        only a sibling's name would load no policy here, and no policy means fetch the whole day
        ungated -- reported as success."""
        original = dict(os.environ)
        try:
            for name in ("FI_POLICY",):
                os.environ.pop(name, None)
            os.environ["EE_POLICY"] = '{"recall_title_terms": ["x"]}'
            with self.assertRaises(EnvironmentError) as raised:
                policy.load_policy()
            self.assertIn("FI_POLICY", str(raised.exception))
        finally:
            os.environ.clear()
            os.environ.update(original)

    def test_the_policy_this_repository_ships_is_the_finnish_one(self):
        self.assertEqual(policy.POLICY_ENV, "FI_POLICY")
        self.assertIn("EE_POLICY", policy.FOREIGN_POLICY_ENVS)

    def test_the_committed_example_demonstrates_both_kinds_of_term(self):
        """The example is what the next person copies. If it showed only bare substrings, the
        boundary rule would be discovered the way it was discovered here -- from a board full of
        cloud-migration tenders."""
        import json
        with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                               "fi_policy.example.json"), encoding="utf-8") as fh:
            example = json.load(fh)
        terms = example["recall_title_terms"]
        self.assertTrue(any(t.startswith(" ") and t.endswith(" ") for t in terms),
                        "no spaced term: the boundary rule is not demonstrated")
        self.assertTrue(any(not t.startswith(" ") for t in terms),
                        "no bare term: the substring rule is not demonstrated")
        self.assertIsNotNone(policy.load_policy(json.dumps(example)))


if __name__ == "__main__":
    unittest.main()
