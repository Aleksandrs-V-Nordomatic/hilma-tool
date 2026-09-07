"""The guards on discovery, which is where this route lies.

The index has one failure mode that looks exactly like success, and every test in the first
class below is about it: asked a second question too soon, it answers with the FIRST question's
rows -- the same records, the same `@odata.count`, HTTP 200, and the filter that was sent nowhere
in the reply. Measured 2026-09-07 from a workstation at two seconds and at fifteen; honest at
twenty. From a GitHub runner, honest at five. It is per address, so a pace can never be the
guard.

Nothing here touches the network. `fi_page.search` is replaced with a scripted liar.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fi_page
import fi_targets


def row(day, identity, **extra):
    out = {"id": identity, "noticeId": int(identity.split("-")[-1]),
           "procedureId": int(identity.split("-")[-1]) + 1000,
           "noticeNumber": "2026-%06d" % int(identity.split("-")[-1]),
           "mainType": "ContractNotices",
           "datePublished": "%sT08:00:00.0000000Z" % day, "titleFi": "t"}
    out.update(extra)
    return out


class Scripted(object):
    """Stands in for the index. Answers whatever the script says, in order."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.asked = []

    def __call__(self, params, timeout=60):
        self.asked.append(params)
        return self.answers.pop(0) if self.answers else self.answers_exhausted()

    def answers_exhausted(self):
        raise AssertionError("the code asked more times than the script answers")


class Patched(unittest.TestCase):
    def use(self, answers):
        self.index = Scripted(answers)
        self._search, self._sleep = fi_page.search, fi_targets.time.sleep
        fi_page.search = self.index
        fi_targets.time.sleep = lambda seconds: None      # the waits are real; the test is not
        self.addCleanup(self.restore)
        return self.index

    def restore(self):
        fi_page.search, fi_targets.time.sleep = self._search, self._sleep


class TheStaleAnswer(Patched):
    def test_an_answer_carrying_another_day_is_thrown_away_and_asked_for_again(self):
        yesterdays = {"@odata.count": 90, "value": [row("2026-09-01", "EF-1")]}
        ours = {"@odata.count": 1, "value": [row("2026-09-02", "EF-2")]}
        index = self.use([yesterdays, ours])

        count, held = fi_targets.rows("2026-09-02")

        self.assertEqual((count, len(held)), (1, 1))
        self.assertEqual(held[0]["id"], "EF-2")
        self.assertEqual(len(index.asked), 2, "the liar's answer was accepted")

    def test_a_run_that_is_never_told_the_truth_stops_rather_than_delivering(self):
        """The alternative is delivering another day's notices under this day's name, with an
        `@odata.count` that agrees with itself. Nothing downstream could ever notice."""
        stale = {"@odata.count": 90, "value": [row("2026-09-01", "EF-1")]}
        self.use([stale] * fi_targets.STALE_TRIES)

        with self.assertRaises(fi_targets.FilterIgnored) as raised:
            fi_targets.rows("2026-09-02")
        self.assertIn("PREVIOUS request", str(raised.exception))
        # The message has to tell a person what to do about it, not just that it happened.
        self.assertIn("HILMA_SEARCH_PACE", str(raised.exception))

    def test_the_count_alone_would_not_have_caught_it(self):
        """A stale answer is internally consistent: its rows and its count agree with each other.
        Only the DATES in the rows give it away, which is why the check is on the rows."""
        stale = {"@odata.count": 1, "value": [row("2026-09-01", "EF-1")]}
        self.use([stale] * fi_targets.STALE_TRIES)
        with self.assertRaises(fi_targets.FilterIgnored):
            fi_targets.rows("2026-09-02")


class TheCoverageAssertion(Patched):
    def test_a_window_short_of_its_own_count_is_not_delivered(self):
        """The rows the index did not serve are named nowhere, so there is nothing honest to
        deliver -- unlike a target that failed, which the day can name in `lost`."""
        self.use([{"@odata.count": 5, "value": [row("2026-09-02", "EF-%d" % n)
                                                for n in range(3)]},
                  {"@odata.count": 5, "value": []}])
        with self.assertRaises(fi_targets.Truncated) as raised:
            fi_targets.rows("2026-09-02")
        self.assertIn("named nowhere", str(raised.exception))

    def test_an_answer_with_no_count_cannot_prove_anything_and_is_refused(self):
        self.use([{"value": [row("2026-09-02", "EF-1")]}])
        with self.assertRaises(fi_targets.FilterIgnored):
            fi_targets.rows("2026-09-02")

    def test_an_index_that_says_zero_and_serves_zero_is_believed(self):
        """A quiet Saturday is a real answer. The day driver decides whether zero is suspicious;
        discovery only has to be sure it asked."""
        self.use([{"@odata.count": 0, "value": []}])
        count, held = fi_targets.rows("2026-09-06")
        self.assertEqual((count, held), (0, []))


class Paging(Patched):
    def test_a_window_wider_than_a_page_is_walked_to_its_end(self):
        first = {"@odata.count": 3, "value": [row("2026-09-02", "EF-1"),
                                              row("2026-09-02", "EF-2")]}
        second = {"@odata.count": 3, "value": [row("2026-09-02", "EF-3")]}
        index = self.use([first, second])
        count, held = fi_targets.rows("2026-09-02")
        self.assertEqual([r["id"] for r in held], ["EF-1", "EF-2", "EF-3"])
        self.assertEqual(index.asked[1]["skip"], 2, "the second page did not skip the first")
        self.assertEqual(count, 3)

    def test_a_page_that_repeats_the_previous_one_stops_the_run(self):
        """The stale answer arriving as a repeat rather than as a foreign date -- the one shape
        the row check cannot see, because every row is from the right day."""
        page = {"@odata.count": 4, "value": [row("2026-09-02", "EF-1"),
                                             row("2026-09-02", "EF-2")]}
        self.use([page, dict(page)])
        with self.assertRaises(fi_targets.FilterIgnored) as raised:
            fi_targets.rows("2026-09-02")
        self.assertIn("not paging", str(raised.exception))

    def test_a_row_the_index_serves_twice_is_held_once(self):
        first = {"@odata.count": 3, "value": [row("2026-09-02", "EF-1"),
                                              row("2026-09-02", "EF-2")]}
        second = {"@odata.count": 3, "value": [row("2026-09-02", "EF-2"),
                                               row("2026-09-02", "EF-3")]}
        self.use([first, second])
        _, held = fi_targets.rows("2026-09-02")
        self.assertEqual([r["id"] for r in held], ["EF-1", "EF-2", "EF-3"])


class TheWindowIsUtc(Patched):
    def test_the_filter_is_half_open_and_spelled_the_way_the_index_spells_it(self):
        index = self.use([{"@odata.count": 0, "value": []}])
        fi_targets.rows("2026-09-02", "2026-09-04")
        sent = index.asked[0]["filter"]
        self.assertIn("datePublished ge 2026-09-02T00:00:00Z", sent)
        # Exclusive at the top and one day beyond the last: an inclusive `le` on midnight would
        # take the last day's first second and drop the other 86 399.
        self.assertIn("datePublished lt 2026-09-05T00:00:00Z", sent)

    def test_both_ends_of_a_range_are_inside_it(self):
        self.assertEqual(fi_targets.days("2026-09-02", "2026-09-04"),
                         ["2026-09-02", "2026-09-03", "2026-09-04"])

    def test_a_window_that_ends_before_it_begins_is_refused(self):
        with self.assertRaises(ValueError):
            fi_targets.days("2026-09-04", "2026-09-02")

    def test_saturday_is_the_only_day_an_empty_answer_proves_nothing(self):
        """The Sunday batch that forwards to TED carried 72 notices on 2026-08-30, 65 of them EU;
        the Saturday before carried 5. A weekday-only expectation would call that Sunday's whole
        batch a broken crawl."""
        # 2026-09-05 Sat, 09-06 Sun.
        self.assertEqual(fi_targets.publishing_days("2026-09-05", "2026-09-05"), [])
        self.assertEqual(fi_targets.publishing_days("2026-09-06", "2026-09-06"), ["2026-09-06"])


class OneNotice(Patched):
    def test_a_notice_is_looked_up_by_its_number_and_not_by_its_id(self):
        """`noticeId 55293` resolves to a legacy notice from 2020 while `2026-055293` was a
        separate eForms notice. Keyed on the wrong one, a watched card reports somebody else's
        tender."""
        index = self.use([{"@odata.count": 1, "value": [row("2026-09-02", "EF-55293")]}])
        found = fi_targets.one("2026-055293")
        self.assertIn("noticeNumber eq '2026-055293'", index.asked[0]["filter"])
        self.assertEqual(found["ref"], "2026-055293")

    def test_a_card_prefix_is_stripped_the_way_a_person_writes_it(self):
        index = self.use([{"@odata.count": 0, "value": []}])
        fi_targets.one("FI:2026-055293")
        self.assertIn("'2026-055293'", index.asked[0]["filter"])

    def test_a_row_that_came_back_but_is_not_the_one_asked_for_is_a_miss(self):
        self.use([{"@odata.count": 1, "value": [row("2026-09-02", "EF-55294")]}])
        self.assertIsNone(fi_targets.one("2026-055293"))

    def test_a_quote_in_a_reference_cannot_break_out_of_the_filter(self):
        index = self.use([{"@odata.count": 0, "value": []}])
        fi_targets.one("2026-05'5293")
        self.assertIn("'2026-05''5293'", index.asked[0]["filter"])


if __name__ == "__main__":
    unittest.main()
