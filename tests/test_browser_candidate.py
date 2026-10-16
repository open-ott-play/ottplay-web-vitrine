"""The site accepted by browsers must be the site sent to the publisher."""
import importlib.util
import io
import json
import os
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("browser_candidate", Path(__file__).parents[1] / "scripts/browser_candidate.py")
CANDIDATE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CANDIDATE)


class CandidateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "site"
        self.source.mkdir()
        (self.source / "index.html").write_text("<h1>Exact staged site</h1>")
        (self.source / ".well-known").mkdir()
        (self.source / ".well-known/receipt.json").write_text('{"ok":true}')
        self.bundle = self.root / "candidate"
        self.env = patch.dict(os.environ, {"GITHUB_REPOSITORY":"example/site", "GITHUB_SHA":"a"*40,
                                         "GITHUB_RUN_ID":"123", "GITHUB_RUN_ATTEMPT":"1"})
        self.env.start()
        self.addCleanup(self.env.stop)

    def pack(self):
        return CANDIDATE.pack(self.source, self.bundle, "v1.2.3", "b"*64, "stable")

    def unpack(self, digest):
        return CANDIDATE.unpack(self.bundle, self.root / "restored", digest)

    def test_round_trip_preserves_every_byte_including_hidden_paths(self):
        digest = self.pack()
        receipt = self.unpack(digest)
        self.assertEqual(receipt["acceptedManifestSha256"], "b"*64)
        self.assertEqual(CANDIDATE.inventory(self.source), CANDIDATE.inventory(self.root / "restored"))

    def test_mutated_manifest_is_rejected(self):
        digest = self.pack()
        (self.bundle / "site-manifest.json").write_text("{}")
        with self.assertRaisesRegex(ValueError, "manifest differs"):
            self.unpack(digest)

    def test_mutated_archive_is_rejected(self):
        digest = self.pack()
        with (self.bundle / "site.tar.gz").open("ab") as stream:
            stream.write(b"unaccepted")
        with self.assertRaisesRegex(ValueError, "archive differs"):
            self.unpack(digest)

    def test_other_source_run_or_attempt_is_rejected(self):
        digest = self.pack()
        for key in ("GITHUB_REPOSITORY", "GITHUB_SHA", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT"):
            with self.subTest(key=key), patch.dict(os.environ, {key:"different"}), self.assertRaisesRegex(ValueError, "another source, run or attempt"):
                self.unpack(digest)

    def test_unexpected_file_is_rejected(self):
        digest = self.pack()
        (self.bundle / "extra").touch()
        with self.assertRaisesRegex(ValueError, "Unexpected candidate files"):
            self.unpack(digest)

    def test_symlink_is_rejected_at_pack(self):
        (self.source / "linked").symlink_to(self.source / "index.html")
        with self.assertRaisesRegex(ValueError, "symbolic links"):
            self.pack()

    def test_case_colliding_paths_are_rejected_at_pack(self):
        # Validate on case-sensitive runners; macOS commonly aliases these names.
        (self.source / "INDEX.html").write_text("other")
        if len(list(self.source.glob("*.html"))) < 2:
            self.skipTest("case-insensitive filesystem")
        with self.assertRaisesRegex(ValueError, "case-colliding"):
            self.pack()

    def test_portable_archive_names(self):
        for name in ("../escape", "/root", "C:/escape", "a\\b", "CON", "aux.txt", "a.", "a ", "a//b", "./a"):
            with self.subTest(name=name):
                self.assertFalse(CANDIDATE.safe_name(name))
        self.assertTrue(CANDIDATE.safe_name(".well-known/a.json"))

    def test_unsafe_member_is_rejected_even_with_a_matching_archive_digest(self):
        self.pack()
        with tarfile.open(self.bundle / "site.tar.gz", "w:gz") as tar:
            member = tarfile.TarInfo("../escape")
            member.size = 4
            tar.addfile(member, io.BytesIO(b"nope"))
        manifest = self.bundle / "site-manifest.json"
        data = json.loads(manifest.read_text())
        data["archiveSha256"] = CANDIDATE.digest(self.bundle / "site.tar.gz")
        manifest.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, "Unsafe"):
            self.unpack(CANDIDATE.digest(manifest))
        self.assertFalse((self.root / "escape").exists())
        self.assertFalse((self.root / "restored").exists())

    def test_inventory_mismatch_does_not_leave_a_publishable_directory(self):
        self.pack()
        manifest = self.bundle / "site-manifest.json"
        data = json.loads(manifest.read_text())
        data["files"][0]["sha256"] = "0"*64
        manifest.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, "inventory differs"):
            self.unpack(CANDIDATE.digest(manifest))
        self.assertFalse((self.root / "restored").exists())


if __name__ == "__main__":
    unittest.main()
