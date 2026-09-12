"""Offline checks for the stable-release deployment boundary."""
import hashlib
import io
import json
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch


class PrepareDistributionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "scripts").mkdir()
        self.script = self.root / "scripts/prepare-dist.py"
        shutil.copyfile(Path(__file__).parents[1] / "scripts/prepare-dist.py", self.script)
        self.sha = "a" * 40
        self.tag = "v1.2.3"
        self.prerelease = False
        self.conclusion = "success"
        self.gate = "success"
        self.archive = self.tar()
        self.checksum = None

    def tar(self, name="dist/index.html", symlink=False):
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode="w:gz") as archive:
            entry = tarfile.TarInfo(name)
            if symlink:
                entry.type = tarfile.SYMTYPE
                entry.linkname = "/etc/passwd"
                archive.addfile(entry)
            else:
                data = b"<!doctype html><title>verified</title>"
                entry.size = len(data)
                archive.addfile(entry, io.BytesIO(data))
        return stream.getvalue()

    def api(self, command):
        path = command[-1]
        if "/releases/tags/" in path:
            return json.dumps({"tag_name": self.tag, "draft": False, "prerelease": self.prerelease}).encode()
        if "/git/ref/tags/" in path:
            return json.dumps({"object": {"type": "commit", "sha": self.sha}}).encode()
        if "/jobs?" in path:
            return json.dumps([{"jobs": [{"name": "Release gate", "status": "completed", "conclusion": self.gate, "head_sha": self.sha}]}]).encode()
        return json.dumps({"status": "completed", "conclusion": self.conclusion, "head_sha": self.sha,
                           "run_attempt": 1, "path": ".github/workflows/release-pipeline.yml",
                           "repository": {"full_name": "open-ott-play/ottplay-foss"}}).encode()

    def download(self, command, **kwargs):
        self.assertEqual(command[:3], ["gh", "release", "download"])
        directory = Path(command[command.index("--dir") + 1])
        name = command[command.index("--pattern") + 1]
        if name == "ottplay-foss-dist.tar.gz":
            (directory / name).write_bytes(self.archive)
        else:
            manifest = {"repository": "open-ott-play/ottplay-foss", "version": "1.2.3", "channel": "rc",
                        "source_sha": self.sha, "run_id": 42, "run_attempt": 1, "assets": [
                            {"name": "ottplay-foss-dist.tar.gz", "size": len(self.archive),
                             "sha256": self.checksum or hashlib.sha256(self.archive).hexdigest()}]}
            (directory / name).write_text(json.dumps(manifest))
        return subprocess.CompletedProcess(command, 0)

    def execute(self):
        with patch.object(sys, "argv", [str(self.script), self.tag]), \
             patch("subprocess.check_output", side_effect=self.api), \
             patch("subprocess.run", side_effect=self.download):
            runpy.run_path(str(self.script), run_name="__main__")

    def test_verified_stable_bytes_are_prepared_without_publishing(self):
        self.execute()
        self.assertTrue((self.root / "dist/index.html").is_file())

    def test_nonstable_or_injected_tag_fails_before_download(self):
        for tag in ("v1.2.3-rc.1", "latest", "v1.2.3;id", "v01.2.3"):
            self.tag = tag
            with self.subTest(tag=tag), self.assertRaises(SystemExit):
                self.execute()

    def test_prerelease_does_not_deploy(self):
        self.prerelease = True
        with self.assertRaises(SystemExit):
            self.execute()

    def test_skipped_gate_does_not_deploy(self):
        self.gate = "skipped"
        with self.assertRaises(SystemExit):
            self.execute()

    def test_failed_run_does_not_deploy(self):
        self.conclusion = "failure"
        with self.assertRaises(SystemExit):
            self.execute()

    def test_changed_asset_does_not_deploy(self):
        self.checksum = "0" * 64
        with self.assertRaises(SystemExit):
            self.execute()

    def test_verified_but_unsafe_archive_does_not_extract(self):
        for name, symlink in (("../escape", False), ("/absolute", False), ("dist/index.html", True)):
            self.archive = self.tar(name, symlink)
            with self.subTest(name=name), self.assertRaises(SystemExit):
                self.execute()
            self.assertFalse((self.root / "dist").exists())


if __name__ == "__main__":
    unittest.main()
