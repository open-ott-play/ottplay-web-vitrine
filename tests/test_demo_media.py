"""Regression checks for demo completeness at the publication boundary."""
import hashlib
import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
verify_demo = runpy.run_path(str(ROOT / "scripts/demo_media.py"))["verify_demo"]


class DemoMediaTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.site = self.root / "site"
        shutil.copytree(ROOT / "static", self.site)
        (self.site / "index.html").write_text("<!doctype html><title>player</title>")
        self.demo = self.site / "demo"
        self.manifest = self.root / "demo-media.json"
        shutil.copyfile(ROOT / "demo-media.json", self.manifest)

    def verify(self):
        verify_demo(self.demo, self.manifest)

    def test_checked_in_media_is_complete(self):
        self.verify()

    def test_each_pinned_file_is_required(self):
        for path in sorted(self.demo.iterdir()):
            with self.subTest(file=path.name):
                data = path.read_bytes()
                path.unlink()
                with self.assertRaisesRegex(SystemExit, "inventory"):
                    self.verify()
                path.write_bytes(data)

    def test_changed_bytes_with_the_same_size_are_rejected(self):
        path = self.demo / "pattern0.ts"
        data = path.read_bytes()
        path.write_bytes(bytes([data[0] ^ 1]) + data[1:])
        with self.assertRaisesRegex(SystemExit, "checksum"):
            self.verify()

    def test_extra_unreviewed_media_is_rejected(self):
        (self.demo / "extra.ts").write_bytes(b"unreviewed")
        with self.assertRaisesRegex(SystemExit, "inventory"):
            self.verify()

    def test_symlink_to_matching_media_is_rejected(self):
        path = self.demo / "pattern.mp4"
        target = self.root / "outside.mp4"
        path.rename(target)
        path.symlink_to(target)
        with self.assertRaisesRegex(SystemExit, "unsafe"):
            self.verify()

    def test_even_checksum_pinned_playlist_cannot_reference_missing_or_remote_segments(self):
        path = self.demo / "pattern.m3u8"
        original = path.read_text()
        for replacement in ("missing.ts", "https://example.com/pattern0.ts", "../pattern0.ts"):
            with self.subTest(reference=replacement):
                path.write_text(original.replace("\npattern0.ts\n", "\n" + replacement + "\n"))
                manifest = json.loads(self.manifest.read_text())
                manifest["files"][path.name] = {
                    "size": path.stat().st_size,
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                }
                self.manifest.write_text(json.dumps(manifest))
                with self.assertRaisesRegex(SystemExit, "segment references"):
                    self.verify()

    def test_direct_publish_rejects_missing_or_corrupt_demo_before_using_credentials(self):
        home = self.root / "home"
        home.mkdir()
        env = {**os.environ, "HOME": str(home), "HERENOW_API_KEY": "unused-test-key"}
        path = self.demo / "pattern0.ts"
        for damage, message in (("missing", "inventory"), ("corrupt", "checksum")):
            with self.subTest(damage=damage):
                if damage == "missing":
                    path.unlink()
                else:
                    path.write_bytes(b"corrupt")
                result = subprocess.run(
                    ["bash", str(ROOT / "scripts/publish-herenow.sh"), str(self.site)],
                    cwd=self.root, env=env, capture_output=True, text=True, check=False,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stderr)
                self.assertFalse((home / ".herenow/credentials").exists())

    def test_complete_demo_reaches_publisher_with_hls_mime_types(self):
        # Stub only the external publisher and its git revision check. Exercise
        # the real wrapper, media validation and Bash subprocess inheritance.
        binary = self.root / "bin"
        binary.mkdir()
        git = binary / "git"
        git.write_text("#!/usr/bin/env bash\n"
                       "if [[ \"$3\" == rev-parse && \"$4\" == HEAD ]]; then\n"
                       "  echo 8cf033ed53b82c0c67b16359c8c431f99e111d04\n"
                       "elif [[ \"$3\" != status ]]; then exit 1; fi\n")
        git.chmod(0o755)
        publisher = self.root / "publisher/here-now/scripts/publish.sh"
        publisher.parent.mkdir(parents=True)
        publisher.write_text("#!/usr/bin/env bash\nset -euo pipefail\n"
                             "file --brief --mime-type \"$1/demo/pattern.m3u8\"\n"
                             "file --brief --mime-type \"$1/demo/pattern0.ts\"\n"
                             "file --brief --mime-type \"$1/index.html\"\n")
        publisher.chmod(0o755)
        env = {**os.environ, "HOME": str(self.root / "home"),
               "PATH": str(binary) + os.pathsep + os.environ["PATH"],
               "HERENOW_API_KEY": "unused-test-key", "HERENOW_PUBLISH_SCRIPT": str(publisher)}
        result = subprocess.run(
            ["bash", str(ROOT / "scripts/publish-herenow.sh"), str(self.site)],
            cwd=self.root, env=env, capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), [
            "Verified complete MP4/HLS demo media.",
            "application/vnd.apple.mpegurl", "video/mp2t", "text/html",
        ])


if __name__ == "__main__":
    unittest.main()
