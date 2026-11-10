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
import zipfile

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


if __name__ == "__main__":
    unittest.main()
