"""The portal step, without a browser, an account or the network.

What is under test is everything after the door: which tenders are asked for, how an archive's
bytes are told from a page under a 200, how members become records that `changes` can compare
across nights, and that a tender the portal would not give stays `external` with a reason.
"""
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
import urllib.parse
import zipfile
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fi_cloudia
import fi_fetch
import fi_page

URL = "https://tarjouspalvelu.fi/kaupunki?id=123456&tpk=00000000-0000-0000-0000-000000000000"


def archive(members):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in members:
            zf.writestr(name, data)
    return buf.getvalue()


def served(data):
    def get(url, headers=None, timeout=None):
        served.calls.append((url, headers))
        return data, {}
    served.calls = []
    return get


class Reset(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="fi-cloudia-")
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)
        self._session = dict(fi_cloudia._SESSION)
        self._counts = dict(fi_cloudia.COUNTS)
        self._env = {k: os.environ.get(k) for k in ("CLOUDIA_USERNAME", "CLOUDIA_PASSWORD")}
        self._sign_in = fi_cloudia.sign_in
        self.addCleanup(self.restore)
        fi_cloudia._SESSION.update(cookie=None, agent=None, failed=None)
        for key in fi_cloudia.COUNTS:
            fi_cloudia.COUNTS[key] = 0

    def restore(self):
        fi_cloudia._SESSION.update(self._session)
        fi_cloudia.COUNTS.update(self._counts)
        fi_cloudia.sign_in = self._sign_in
        for key, value in self._env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def account(self):
        os.environ["CLOUDIA_USERNAME"] = "buyer-facing@example.invalid"
        os.environ["CLOUDIA_PASSWORD"] = "not-a-password"
        self.sign_ins = 0

        def sign_in(user, password):
            self.sign_ins += 1
            return "ASP.NET_SessionId=s%d" % self.sign_ins, "TestAgent"
        fi_cloudia.sign_in = sign_in


class WhichTenders(unittest.TestCase):
    def test_the_id_comes_from_the_documents_address(self):
        self.assertEqual(fi_cloudia.tp_id(URL), "123456")
        self.assertEqual(fi_cloudia.tp_id("https://www.tarjouspalvelu.fi/x?ID=42"), "42")

    def test_another_host_or_a_missing_id_is_not_asked_for(self):
        self.assertIsNone(fi_cloudia.tp_id("https://tarjouspalvelu.fi.evil.example/x?id=1"))
        self.assertIsNone(fi_cloudia.tp_id("https://tarjouspalvelu.fi/x?tpk=abc"))
        self.assertIsNone(fi_cloudia.tp_id("https://tarjouspalvelu.fi/x?id=12;drop"))
        self.assertIsNone(fi_cloudia.tp_id(None))

    def test_applies_reads_the_host_hilma_already_carries(self):
        row = fi_page.notice({"procurementDocumentsUrl": URL, "noticeNumber": "2026-000001"})
        self.assertEqual(row["documents_available"], "external")
        self.assertTrue(fi_cloudia.applies(row))
        other = fi_page.notice({"procurementDocumentsUrl": "https://www.cloudia.example/x?id=1"})
        self.assertFalse(fi_cloudia.applies(other))


class WhatArrived(unittest.TestCase):
    def test_a_page_under_a_200_is_a_signed_out_session(self):
        data, reason, signed_out = fi_cloudia.download(
            "1", "c", "a", get=served(b"<!DOCTYPE html><html>front page</html>"))
        self.assertIsNone(data)
        self.assertTrue(signed_out)
        self.assertIn("page", reason)

    def test_an_empty_archive_is_not_a_success(self):
        data, reason, _ = fi_cloudia.download("1", "c", "a", get=served(archive([])))
        self.assertIsNone(data)
        self.assertIn("no files", reason)

    def test_the_cookie_rides_on_the_request(self):
        get = served(archive([("a.pdf", b"%PDF-1.7")]))
        data, reason, _ = fi_cloudia.download("77", "k=v", "Agent", get=get)
        self.assertIsNone(reason)
        url, headers = served.calls[0]
        self.assertTrue(url.endswith("/Zip/TarjousPyynnonLiitteet/77"))
        self.assertEqual(headers["Cookie"], "k=v")


class Members(Reset):
    def test_each_member_is_a_record_keyed_by_its_path(self):
        data = archive([("Liitteet/Tarjouspyyntö.pdf", b"%PDF-1"),
                        ("Piirustukset/Tarjouspyyntö.pdf", b"%PDF-2"),
                        ("../../escape.txt", b"x")])
        records, total, reason = fi_cloudia.unpack(self.home, data, "src")
        self.assertIsNone(reason)
        self.assertEqual(total, 13)
        ids = [r["id"] for r in records]
        self.assertIn("zip:Liitteet/Tarjouspyyntö.pdf", ids)
        self.assertIn("zip:Piirustukset/Tarjouspyyntö.pdf", ids)
        names = sorted(os.listdir(os.path.join(self.home, "originals")))
        # Flattened, never climbing out of the home, and the same name twice kept twice.
        self.assertEqual(names, ["Tarjouspyyntö (2).pdf", "Tarjouspyyntö.pdf", "escape.txt"])
        for r in records:
            self.assertTrue(os.path.exists(os.path.join(self.home, r["files"][0]["path"])))

    def test_a_rebuilt_archive_with_the_same_files_is_not_a_change(self):
        import changes
        first = archive([("a.pdf", b"%PDF-1"), ("b.docx", b"PK-doc")])
        records_1, _, _ = fi_cloudia.unpack(self.home, first, "src")
        buf = io.BytesIO()                     # same members, other order and timestamps
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr(zipfile.ZipInfo("b.docx", (2030, 1, 1, 0, 0, 0)), b"PK-doc")
            zf.writestr(zipfile.ZipInfo("a.pdf", (2031, 1, 1, 0, 0, 0)), b"%PDF-1")
        records_2, _, _ = fi_cloudia.unpack(self.home, buf.getvalue(), "src")
        self.assertNotEqual(first, buf.getvalue())
        self.assertEqual(changes._records({"documents": records_1}),
                         changes._records({"documents": records_2}))

    def test_an_archive_claiming_too_much_is_refused(self):
        limit, fi_cloudia.MAX_UNPACKED = fi_cloudia.MAX_UNPACKED, 4
        self.addCleanup(setattr, fi_cloudia, "MAX_UNPACKED", limit)
        records, _, reason = fi_cloudia.unpack(self.home, archive([("a", b"12345")]), "src")
        self.assertEqual(records, [])
        self.assertIn("unpacked", reason)


class Outcome(Reset):
    row = {"documents_host": "tarjouspalvelu.fi", "documents_url": URL,
           "documents_available": "external"}

    def test_no_account_is_skipped_and_stays_external(self):
        os.environ.pop("CLOUDIA_USERNAME", None)
        out = fi_cloudia.fetch_into(self.home, self.row, get=served(b"never"))
        self.assertEqual(out["status"], "skipped")
        self.assertEqual(served.calls, [])

    def test_one_sign_in_serves_the_run_and_an_ended_session_gets_one_more(self):
        self.account()
        good = archive([("a.pdf", b"%PDF-1")])
        answers = [good, b"<html>signed out</html>", good]

        def get(url, headers=None, timeout=None):
            return answers.pop(0), {}
        self.assertEqual(fi_cloudia.fetch_into(self.home, self.row, get=get)["status"], "read")
        self.assertEqual(fi_cloudia.fetch_into(self.home, self.row, get=get)["status"], "read")
        self.assertEqual(self.sign_ins, 2)
        self.assertEqual(fi_cloudia.COUNTS["read"], 2)

    def test_a_refused_account_is_tried_once_per_run(self):
        os.environ["CLOUDIA_USERNAME"] = "u@example.invalid"
        os.environ["CLOUDIA_PASSWORD"] = "p"
        tries = []

        def refuse(user, password):
            tries.append(1)
            raise fi_cloudia.SignInFailed("the portal refused the account: loginMethod=ERROR")
        fi_cloudia.sign_in = refuse
        for _ in range(3):
            out = fi_cloudia.fetch_into(self.home, self.row, get=served(b""))
            self.assertEqual(out["status"], "failed")
            self.assertIn("sign-in failed", out["reason"])
        self.assertEqual(len(tries), 1)

    def test_the_summary_carries_counts_and_no_account(self):
        self.account()
        fi_cloudia.fetch_into(self.home, self.row, get=served(archive([("a.pdf", b"%PDF")])))
        line = fi_cloudia.summary()
        self.assertIn("1 read", line)
        self.assertNotIn("example.invalid", line)
        self.assertNotIn("123456", line)


class IntoAHome(Reset):
    """`fi_fetch.fetch` end to end, with Hilma's half stubbed and the portal served a fixture."""

    def setUp(self):
        super().setUp()
        self._collect = fi_page.collect
        self._download = fi_cloudia.download
        self.addCleanup(setattr, fi_page, "collect", self._collect)
        self.addCleanup(setattr, fi_cloudia, "download", self._download)
        fi_page.collect = lambda record, kind=None: {"link_verified": True, "documents": []}

    def row(self):
        return {"pid": "990001", "ref": "2026-990001", "kind": "ContractNotice",
                "documents_host": "tarjouspalvelu.fi", "documents_url": URL,
                "documents_available": "external", "record": {}}

    def test_a_read_archive_makes_the_tender_portal(self):
        self.account()
        data = archive([("Tarjouspyyntö.txt", b"Rakennusautomaatio")])
        fi_cloudia.download = lambda tp, c, a, get=None: (data, None, False)
        done = fi_fetch.fetch(self.row(), self.home, with_text=False)
        self.assertEqual(done["documents_available"], "portal")
        self.assertEqual(done["documents"], 1)
        self.assertEqual(done["catalogued"], 1)
        with open(os.path.join(self.home, "tenders", "990001", "index.json"), encoding="utf-8") as fh:
            index = json.load(fh)
        self.assertEqual(index["documents_available"], "portal")
        self.assertEqual(index["portal"]["status"], "read")
        self.assertEqual(index["documents"][0]["original"], "originals/Tarjouspyyntö.txt")

    def test_a_failed_portal_keeps_external_with_the_reason(self):
        self.account()
        fi_cloudia.download = lambda tp, c, a, get=None: (None, "the portal served an empty body",
                                                          False)
        done = fi_fetch.fetch(self.row(), self.home, with_text=False)
        self.assertEqual(done["documents_available"], "external")
        self.assertEqual(done["index"]["portal"]["reason"], "the portal served an empty body")


USER = "buyer-facing@example.invalid"
PASSWORD = "not-a-password"
SECOND = " (on the second attempt)"


class TheFailure(unittest.TestCase):
    """`SignInFailed` carries its message and whether one more try could help."""

    def test_an_answer_about_the_account_is_not_transient_by_default(self):
        exc = fi_cloudia.SignInFailed("the portal refused the account")
        self.assertIsInstance(exc, Exception)
        self.assertFalse(exc.transient)
        self.assertEqual(str(exc), "the portal refused the account")

    def test_a_broken_browser_is_transient(self):
        exc = fi_cloudia.SignInFailed("the form: timed out", transient=True)
        self.assertTrue(exc.transient)
        self.assertEqual(str(exc), "the form: timed out")


class WhatTheBrowserSaid(unittest.TestCase):
    """`_said`: the first line of what broke, with the account taken out of it."""

    def test_the_first_line_only(self):
        exc = RuntimeError("Timeout 30000ms exceeded.\n=== logs ===\nwaiting for selector")
        self.assertEqual(fi_cloudia._said(exc, USER, PASSWORD), "Timeout 30000ms exceeded.")

    def test_an_empty_message_gives_the_type_name(self):
        self.assertEqual(fi_cloudia._said(RuntimeError(), USER, PASSWORD), "RuntimeError")
        self.assertEqual(fi_cloudia._said(TimeoutError(""), USER, PASSWORD), "TimeoutError")

    def test_the_password_and_the_user_name_become_account(self):
        exc = RuntimeError("fill(%s) then fill(%s) failed" % (USER, PASSWORD))
        said = fi_cloudia._said(exc, USER, PASSWORD)
        self.assertEqual(said, "fill(<account>) then fill(<account>) failed")

    def test_the_user_name_is_taken_out_in_its_encoded_forms_too(self):
        user = "matti meikäläinen@example.invalid"
        quoted = urllib.parse.quote(user)
        plussed = urllib.parse.quote_plus(user)
        self.assertNotEqual(quoted, plussed)
        exc = RuntimeError("goto https://x.example/?a=%s&b=%s failed" % (quoted, plussed))
        said = fi_cloudia._said(exc, user, "pw-secret")
        self.assertEqual(said, "goto https://x.example/?a=<account>&b=<account> failed")

    def test_cut_to_two_hundred_characters(self):
        self.assertEqual(fi_cloudia._said(RuntimeError("x" * 500), USER, PASSWORD), "x" * 200)
        short = fi_cloudia._said(RuntimeError("x" * 200), USER, PASSWORD)
        self.assertEqual(short, "x" * 200)

    def test_nothing_of_the_account_survives_a_long_line(self):
        exc = RuntimeError(("step " * 30) + USER + " " + PASSWORD + (" tail" * 60) + "\nmore")
        said = fi_cloudia._said(exc, USER, PASSWORD)
        self.assertLessEqual(len(said), 200)
        self.assertNotIn(USER, said)
        self.assertNotIn(PASSWORD, said)
        self.assertNotIn("more", said)
        self.assertIn("<account>", said)


class OneMoreTry(Reset):
    """`session`: a broken browser or a slow page is tried twice, the account itself once."""

    row = {"documents_host": "tarjouspalvelu.fi", "documents_url": URL,
           "documents_available": "external"}

    def setUp(self):
        super().setUp()
        self._pause = fi_cloudia.RETRY_PAUSE
        self.addCleanup(setattr, fi_cloudia, "RETRY_PAUSE", self._pause)
        fi_cloudia.RETRY_PAUSE = 0
        os.environ["CLOUDIA_USERNAME"] = USER
        os.environ["CLOUDIA_PASSWORD"] = PASSWORD
        self.calls = []
        self.slept = []
        patcher = mock.patch("time.sleep", side_effect=lambda s: self.slept.append(s))
        patcher.start()
        self.addCleanup(patcher.stop)

    def answers(self, *outcomes):
        """Each call to the stub takes the next outcome: an exception is raised, else returned."""
        queue = list(outcomes)

        def sign_in(user, password):
            self.calls.append((user, password))
            outcome = queue.pop(0)
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome
        fi_cloudia.sign_in = sign_in

    def gives_up(self):
        with self.assertRaises(fi_cloudia.SignInFailed) as caught:
            fi_cloudia.session()
        return str(caught.exception)

    def test_the_pause_is_thirty_seconds(self):
        self.assertEqual(self._pause, 30)

    def test_a_broken_browser_then_a_good_one_signs_in(self):
        self.answers(fi_cloudia.SignInFailed("the front page: Timeout", transient=True),
                     ("ASP.NET_SessionId=s1", "TestAgent"))
        fi_cloudia.session()
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.calls[0], (USER, PASSWORD))
        self.assertEqual(self.calls[1], (USER, PASSWORD))
        self.assertEqual(self.slept, [0])
        self.assertFalse(fi_cloudia._SESSION.get("failed"))

    def test_the_pause_comes_from_retry_pause(self):
        fi_cloudia.RETRY_PAUSE = 7
        self.answers(fi_cloudia.SignInFailed("the form: Timeout", transient=True),
                     ("c=1", "TestAgent"))
        fi_cloudia.session()
        self.assertEqual(self.slept, [7])

    def test_a_missing_browser_is_also_tried_twice(self):
        self.answers(RuntimeError("Error"), ("c=1", "TestAgent"))
        fi_cloudia.session()
        self.assertEqual(len(self.calls), 2)

    def test_two_broken_browsers_say_the_last_and_that_it_was_the_second(self):
        self.answers(fi_cloudia.SignInFailed("the front page: Timeout", transient=True),
                     fi_cloudia.SignInFailed("the cookie banner: Target closed", transient=True))
        self.assertEqual(self.gives_up(), "the cookie banner: Target closed" + SECOND)
        self.assertEqual(len(self.calls), 2)

    def test_an_exception_that_is_not_a_sign_in_failure_names_its_type(self):
        self.answers(RuntimeError("Error"), RuntimeError("Error"))
        self.assertEqual(self.gives_up(), "the browser could not sign in (RuntimeError)" + SECOND)

    def test_a_refused_account_is_tried_once_without_a_pause(self):
        self.answers(fi_cloudia.SignInFailed("the portal refused the account"),
                     ("never", "used"))
        self.assertEqual(self.gives_up(), "the portal refused the account")
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.slept, [])

    def test_a_refusal_on_the_second_attempt_says_so(self):
        self.answers(fi_cloudia.SignInFailed("single sign-on: Timeout", transient=True),
                     fi_cloudia.SignInFailed("the portal asked for a second factor"))
        self.assertEqual(self.gives_up(), "the portal asked for a second factor" + SECOND)
        self.assertEqual(len(self.calls), 2)

    def test_never_a_third_attempt(self):
        self.answers(fi_cloudia.SignInFailed("a", transient=True),
                     fi_cloudia.SignInFailed("b", transient=True),
                     ("c=1", "TestAgent"))
        self.gives_up()
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(len(self.slept), 1)

    def test_a_failure_is_remembered_for_the_run(self):
        self.answers(fi_cloudia.SignInFailed("the way back: Timeout", transient=True),
                     fi_cloudia.SignInFailed("the way back: Timeout", transient=True))
        first = self.gives_up()
        self.assertTrue(fi_cloudia._SESSION["failed"])
        for _ in range(2):
            self.assertEqual(self.gives_up(), first)
        self.assertEqual(len(self.calls), 2)

    def test_a_remembered_refusal_does_not_call_the_browser_again(self):
        self.answers(fi_cloudia.SignInFailed("the portal refused the account"))
        first = self.gives_up()
        self.assertEqual(self.gives_up(), first)
        self.assertEqual(len(self.calls), 1)

    def test_one_broken_browser_no_longer_loses_the_night(self):
        self.answers(RuntimeError("Error"), ("ASP.NET_SessionId=s1", "TestAgent"))
        out = fi_cloudia.fetch_into(self.home, self.row,
                                    get=served(archive([("a.pdf", b"%PDF-1")])))
        self.assertEqual(out["status"], "read")
        self.assertEqual(len(self.calls), 2)

    def test_the_reason_in_the_index_carries_the_message(self):
        self.answers(fi_cloudia.SignInFailed("the cookies: none were set", transient=True),
                     fi_cloudia.SignInFailed("the cookies: none were set", transient=True))
        for _ in range(2):
            out = fi_cloudia.fetch_into(self.home, self.row, get=served(b""))
            self.assertEqual(out["status"], "failed")
            self.assertEqual(out["reason"],
                             "sign-in failed: the cookies: none were set" + SECOND)
        self.assertEqual(len(self.calls), 2)

    def test_the_reason_after_two_missing_browsers(self):
        self.answers(RuntimeError("Error"), RuntimeError("Error"))
        out = fi_cloudia.fetch_into(self.home, self.row, get=served(b""))
        self.assertEqual(out["reason"],
                         "sign-in failed: the browser could not sign in (RuntimeError)" + SECOND)


if __name__ == "__main__":
    unittest.main()
