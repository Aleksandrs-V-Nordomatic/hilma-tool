#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A tender deep enough to break Windows, and the two rules that make it readable.

FOUND ON A REAL WINDOW, NOT IMAGINED. A four-day trial against the live register lost 15 of
79 procurements to `WinError 206` — the path was too long. Not one of them failed to download
and not one was unreadable: the extractor could not create the file it was about to write, and
each tender was reported lost. A building project arrives as an archive of archives and the
extractor mirrors that nesting, so a runtime root plus a tender id plus a section plus two
levels of folder names spends 260 characters without trying.

The production runner is Linux, where the limit is 4096 and none of this fires — which is
precisely why it is worth holding here. The tool promises that a VPS, a laptop and a runner
execute the identical thing, and a laptop that silently drops a fifth of a window does not.

The path arithmetic below is asserted on every platform, because getting it wrong on Windows
is a failure nobody would see on the machine they wrote it on.
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
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import normalize
from test_archives import docx_bytes


class TheTwoRulesThePrefixComesWith(unittest.TestCase):
    def test_the_path_is_made_absolute(self):
        self.assertTrue(os.path.isabs(normalize.plainpath(normalize.fspath("a/b"))))

    def test_only_the_platforms_own_separator_survives(self):
        """The rule that is easy to satisfy by accident and easy to break by accident.

        A prefixed path containing a forward slash is not the file anybody meant; it is a
        file that does not exist. Both places where a logical address — joined with forward
        slashes on purpose, because it travels into the manifest — becomes a real path have
        to go through here.

        Asserted as "no FOREIGN separator" rather than as "no forward slash", because the
        second sentence is only true on Windows and this test has to mean something on the
        machine the tool actually runs on. `os.altsep` is `/` on Windows and None elsewhere,
        which is exactly the distinction being made.
        """
        real = normalize.plainpath(normalize.fspath("a/b/c/d"))
        self.assertIn(os.sep, real)
        if os.altsep:
            self.assertNotIn(os.altsep, real)

    def test_applying_it_twice_changes_nothing(self):
        once = normalize.fspath("a/b")
        self.assertEqual(normalize.fspath(once), once)

    def test_the_prefix_is_removable_for_a_program_that_may_not_know_it(self):
        plain = normalize.plainpath(normalize.fspath("a/b"))
        self.assertFalse(plain.startswith(normalize._LONG))

    @unittest.skipUnless(os.name == "nt", "the prefix only exists on Windows")
    def test_it_is_applied_where_it_is_needed(self):
        self.assertTrue(normalize.fspath("a/b").startswith(normalize._LONG))

    @unittest.skipIf(os.name == "nt", "elsewhere there is nothing to prefix")
    def test_elsewhere_it_is_an_ordinary_absolute_path(self):
        self.assertFalse(normalize.fspath("a/b").startswith("\\"))


class ADeeplyNestedTenderIsStillRead(unittest.TestCase):
    """The end-to-end shape of the failure, at a depth a real building project reaches.

    Every level here is a folder name of the length Estonian authorities actually use, and
    the manifest addresses the top of it with forward slashes exactly as the fetch writes it.
    """

    LEVEL = "Vaikehanke alusdokumendid ja lisad 31082026"

    @classmethod
    def setUpClass(cls):
        cls.root = tempfile.mkdtemp(prefix="ee_deep_")
        os.makedirs(os.path.join(cls.root, "originals"))

        deep = io.BytesIO()
        with zipfile.ZipFile(deep, "w") as z:
            path = "/".join([cls.LEVEL] * 3) + "/Tehniline kirjeldus.docx"
            z.writestr(path, docx_bytes("VENTILATSIOON ja automaatika"))
        outer = os.path.join(cls.root, "originals", "%s.zip" % cls.LEVEL)
        with zipfile.ZipFile(outer, "w") as z:
            z.writestr("%s.zip" % cls.LEVEL, deep.getvalue())

        with open(os.path.join(cls.root, "manifest.json"), "w", encoding="utf-8") as fh:
            json.dump({"schema": 2, "procurement_id": "10758124", "documents": [
                {"id": 1, "title": "Alusdokumendid", "section": "current",
                 # Forward slashes: this is the manifest's logical address, and the fetch
                 # writes it this way on every platform.
                 "files": [{"filename": "%s.zip" % cls.LEVEL,
                            "path": "originals/%s.zip" % cls.LEVEL,
                            "size": os.path.getsize(outer), "sha256": "z" * 64}]}]}, fh)

        out = os.path.join(cls.root, "normalized")
        normalize.main(["--in", cls.root, "--out", out])
        with open(os.path.join(out, "manifest_normalized.json"), encoding="utf-8") as fh:
            cls.manifest = json.load(fh)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root, ignore_errors=True)

    def test_the_document_at_the_bottom_was_read(self):
        self.assertEqual(self.manifest["markdown"], 1, self.manifest)

    def test_its_text_survived_the_journey(self):
        found = [e for e in self.manifest["documents"] if e.get("markdown_path")]
        self.assertEqual(len(found), 1)
        self.assertTrue(found[0]["markdown_chars"] > 0)

    def test_nothing_was_reported_unreadable(self):
        self.assertEqual(self.manifest.get("unreadable_files") or [], [])

    def test_the_manifest_still_addresses_it_with_forward_slashes(self):
        """The prefix is for the filesystem and must not leak into what a reader is handed.

        A consumer joins `markdown_path` onto a URL and onto a drive path, and neither
        accepts a Windows device prefix.
        """
        found = [e for e in self.manifest["documents"] if e.get("markdown_path")][0]
        self.assertNotIn("\\", found["markdown_path"])
        self.assertFalse(found["markdown_path"].startswith(normalize._LONG))


if __name__ == "__main__":
    unittest.main()
