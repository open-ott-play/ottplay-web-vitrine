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
        source = Path(__file__).parents[1]
        for name in ("prepare-dist.py", "beta_release.py", "demo_media.py", "msx.py", "swop.py"):
            shutil.copyfile(source / "scripts" / name, self.root / "scripts" / name)
        shutil.copytree(source / "static", self.root / "static")
        shutil.copyfile(source / "demo-media.json", self.root / "demo-media.json")
        self.sha = "a" * 40
        self.tag = "v1.2.3"
        self.prerelease = False
        self.conclusion = "success"
        self.gate = "success"
        self.archive = self.tar()
        self.checksum = None
        self.attempt = 1
        self.run_attempt = None
        self.recheck_attempt = None
        self.manifest_sha256 = None
        self.api_calls = []
        self.downloaded = []
        self.run_reads = 0

    def manifest(self):
        return json.dumps({"repository": "open-ott-play/ottplay-foss", "version": "1.2.3", "channel": "rc",
                           "source_sha": self.sha, "run_id": 42, "run_attempt": self.attempt, "assets": [
                               {"name": "ottplay-foss-dist.tar.gz", "size": len(self.archive),
                                "sha256": self.checksum or hashlib.sha256(self.archive).hexdigest()}]}).encode()

    def tar(self, name="index.html", symlink=False, demo=False,
            runtime=b'window.__OTTPLAY_HOSTED_PROTOCOL__="hosted-profile-v1"; var protocol="ottplay.swop.v2"; window.__OTT_CONTROL_DISCOVERY_VERSION__=1; window.__OTT_HOSTED_EPG_SERVER_VERSION__=1;'):
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode="w:gz") as archive:
            entry = tarfile.TarInfo(name)
            if symlink:
                entry.type = tarfile.SYMTYPE
                entry.linkname = "/etc/passwd"
                archive.addfile(entry)
            else:
                data = b'<!doctype html><html><head><title>verified</title></head><body></body></html>'
                entry.size = len(data)
                archive.addfile(entry, io.BytesIO(data))
            if runtime is not None:
                entry = tarfile.TarInfo("dist/player.js")
                entry.size = len(runtime)
                archive.addfile(entry, io.BytesIO(runtime))
                for name in ("hosted/epg-worker.js", "hosted/epg-server.js", "hosted/epg-diagnostics.js", "hosted/pako-inflate.js", "hosted/sax.js",
                             "js/runtime-polyfills.js", "js/ottplay-core.js",
                             "swop-input/index.html", "swop-input/app.js"):
                    entry = tarfile.TarInfo(name)
                    data = (b'importScripts("../js/runtime-polyfills.js", "../js/ottplay-core.js", "pako-inflate.js", "sax.js");\nself.onmessage = function () {};\n'
                            if name == "hosted/epg-worker.js" else b"/* runtime-fixture */\n")
                    entry.size = len(data)
                    archive.addfile(entry, io.BytesIO(data))
            if demo:
                entry = tarfile.TarInfo("demo/pattern.mp4")
                archive.addfile(entry, io.BytesIO())
        return stream.getvalue()

    def api(self, command):
        path = command[-1]
        self.api_calls.append(path)
        if "/releases/tags/" in path:
            return json.dumps({"tag_name": self.tag, "draft": False, "prerelease": self.prerelease}).encode()
        if "/git/ref/tags/" in path:
            return json.dumps({"object": {"type": "commit", "sha": self.sha}}).encode()
        if "/jobs?" in path:
            return json.dumps([{"jobs": [{"name": "Release gate", "status": "completed", "conclusion": self.gate, "head_sha": self.sha}]}]).encode()
        self.assertTrue(path.endswith("/actions/runs/42"), "Unexpected API request: " + path)
        self.run_reads += 1
        attempt = self.attempt if self.run_attempt is None else self.run_attempt
        if self.run_reads > 1 and self.recheck_attempt is not None:
            attempt = self.recheck_attempt
        return json.dumps({"id": 42, "status": "completed", "conclusion": self.conclusion, "head_sha": self.sha,
                           "run_attempt": attempt, "path": ".github/workflows/release-pipeline.yml",
                           "repository": {"full_name": "open-ott-play/ottplay-foss"}}).encode()

    def download(self, command, **kwargs):
        self.assertEqual(command[:3], ["gh", "release", "download"])
        directory = Path(command[command.index("--dir") + 1])
        name = command[command.index("--pattern") + 1]
        self.downloaded.append(name)
        if name == "ottplay-foss-dist.tar.gz":
            (directory / name).write_bytes(self.archive)
        else:
            (directory / name).write_bytes(self.manifest())
        return subprocess.CompletedProcess(command, 0)

    def execute(self):
        digest = self.manifest_sha256 if self.manifest_sha256 is not None else hashlib.sha256(self.manifest()).hexdigest()
        with patch.object(sys, "argv", [str(self.script), self.tag, digest]), \
             patch.object(sys, "path", [str(self.script.parent), *sys.path]), \
             patch("subprocess.check_output", side_effect=self.api), \
             patch("subprocess.run", side_effect=self.download):
            runpy.run_path(str(self.script), run_name="__main__")

    def test_verified_stable_bytes_are_prepared_without_publishing(self):
        self.execute()
        self.assertRegex((self.root / "dist/index.html").read_text(), r'<head>\n        <script src="/local/hosted\.js\?v=[0-9a-f]{64}"></script>')
        self.assertIn(b"hosted-profile-v1", (self.root / "dist/dist/player.js").read_bytes())
        self.assertIn(b"window.__OTT_CONTROL_DISCOVERY_VERSION__=1;", (self.root / "dist/dist/player.js").read_bytes())
        self.assertFalse((self.root / "dist/player.js").exists())
        source = self.root / "static/demo"
        staged = self.root / "dist/demo"
        self.assertEqual({path.name for path in staged.iterdir()}, {path.name for path in source.iterdir()})
        for path in source.iterdir():
            self.assertEqual((staged / path.name).read_bytes(), path.read_bytes())
        start = json.loads((self.root / "dist/msx/start.json").read_text())
        parameter = start["parameter"].replace("{PREFIX}", "https://").replace("{SERVER}", "player.ottplay.here.now")
        self.assertEqual(parameter, "content:https://player.ottplay.here.now/msx/content.json")
        content = json.loads((self.root / "dist/msx/content.json").read_text())
        self.assertEqual(content["action"], "link:https://player.ottplay.here.now/")
        self.assertEqual(content["pages"][0]["items"][0]["action"], content["action"])
        swop = json.loads((self.root / "dist/local/swop.json").read_text())
        self.assertEqual(swop, {})
        self.assertEqual((self.root / "dist/.herenow/proxy.json").read_bytes(),
                         (self.root / "static/.herenow/proxy.json").read_bytes())
        self.assertEqual((self.root / "dist/.herenow/data.json").read_bytes(),
                         (self.root / "static/.herenow/data.json").read_bytes())
        self.assertTrue((self.root / "dist/local/hosted.js").read_text().endswith(
            'window.__OTT_CONTROL_DISCOVERY_URL__ = "https://www.2560801.xyz/ott-control/api/discovery";\n'))

    def test_retried_rc_uses_accepted_manifest_and_current_attempt(self):
        self.attempt = 2
        self.execute()
        self.assertTrue((self.root / "dist/index.html").is_file())
        self.assertTrue(any("/attempts/2/jobs?" in path for path in self.api_calls))
        self.assertEqual(self.run_reads, 2)
        self.assertFalse(any("/artifacts" in path for path in self.api_calls))

    def test_replaced_manifest_and_archive_cannot_replace_accepted_bytes(self):
        self.manifest_sha256 = hashlib.sha256(self.manifest()).hexdigest()
        self.archive = self.tar(runtime=b'"hosted-profile-v1"; "ottplay.swop.v2"; /* replaced package */')
        with self.assertRaisesRegex(SystemExit, "differs from the independently verified accepted RC manifest"):
            self.execute()
        self.assertEqual(self.downloaded, ["release-manifest.json"])
        self.assertFalse((self.root / "dist").exists())

    def test_digest_is_required_and_checked_before_network_access(self):
        for value in ("", "0" * 63, "A" * 64, "0" * 65, "0" * 64 + ";id"):
            self.manifest_sha256 = value
            with self.subTest(value=value), self.assertRaisesRegex(SystemExit, "manifest SHA-256 is required"):
                self.execute()
            self.assertEqual(self.api_calls, [])
            self.assertEqual(self.downloaded, [])
        with patch.object(sys, "argv", [str(self.script), self.tag]), \
             patch.object(sys, "path", [str(self.script.parent), *sys.path]), \
             patch("subprocess.check_output") as api, patch("subprocess.run") as download, \
             self.assertRaises(SystemExit):
            runpy.run_path(str(self.script), run_name="__main__")
        api.assert_not_called()
        download.assert_not_called()

    def test_stale_or_newly_retried_run_does_not_stage(self):
        for first_attempt, recheck in ((2, None), (1, 2)):
            self.run_attempt, self.recheck_attempt = first_attempt, recheck
            self.run_reads = 0
            with self.subTest(first=first_attempt, recheck=recheck), self.assertRaisesRegex(SystemExit, "RC validation run is not successful/current"):
                self.execute()
            self.assertFalse((self.root / "dist").exists())

    def test_publish_wrapper_rejects_a_shared_swop_device_identity(self):
        self.execute()
        config = self.root / "dist/local/swop.json"
        config.write_text(json.dumps({"swopBaseUrl": "/swop", "clientId": "dev_shared_identity"}))
        wrapper = Path(__file__).parents[1] / "scripts/publish-herenow.sh"
        result = subprocess.run(["bash", str(wrapper), str(self.root / "dist")], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("SWOP public configuration", result.stderr)

    def test_old_or_missing_player_protocol_is_rejected_before_staging(self):
        for runtime in (None, b'function oldPoll(){return "/val?c=123456";}',
                        b'var sessionToken=""; alert("Allowlist this Device ID");'):
            self.archive = self.tar(runtime=runtime)
            with self.subTest(runtime=runtime), self.assertRaisesRegex(SystemExit, "SWOP relay requires"):
                self.execute()
            self.assertFalse((self.root / "dist").exists())

    def test_missing_or_newer_discovery_capability_is_rejected_before_staging(self):
        existing = b'"hosted-profile-v1"; "ottplay.swop.v2";'
        for marker in (b"", b"window.__OTT_CONTROL_DISCOVERY_VERSION__=2;"):
            self.archive = self.tar(runtime=existing + marker)
            with self.subTest(marker=marker), self.assertRaisesRegex(SystemExit, "Hosted control discovery requires"):
                self.execute()
            self.assertFalse((self.root / "dist").exists())

    def test_publish_wrapper_rejects_old_player_runtime_before_network_access(self):
        self.execute()
        (self.root / "dist/dist/player.js").write_text('alert("Allowlist this Device ID");')
        wrapper = Path(__file__).parents[1] / "scripts/publish-herenow.sh"
        result = subprocess.run(["bash", str(wrapper), str(self.root / "dist")], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("SWOP relay requires", result.stderr)

    def test_publish_wrapper_rejects_tampered_runtime_graph_before_network_access(self):
        self.execute()
        wrapper = Path(__file__).parents[1] / "scripts/publish-herenow.sh"
        graph = next((self.root / "dist/hosted-runtime").iterdir())
        (graph / "js/ottplay-core.js").write_text("changed dependency")
        result = subprocess.run(["bash", str(wrapper), str(self.root / "dist")], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Hosted runtime graph", result.stderr)

    def test_publish_wrapper_refuses_missing_or_modified_swop_proxy_before_network_access(self):
        self.execute()
        wrapper = Path(__file__).parents[1] / "scripts/publish-herenow.sh"
        manifest = self.root / "dist/.herenow/proxy.json"
        for replacement in (None, b'{"proxies":{"/swop/*":{"upstream":"https://wrong.example/"}}}'):
            if replacement is None:
                manifest.unlink()
            else:
                manifest.write_bytes(replacement)
            result = subprocess.run(["bash", str(wrapper), str(self.root / "dist")], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("SWOP proxy manifest", result.stderr)

    def test_upstream_swop_configuration_or_publication_controls_are_rejected(self):
        original = self.archive
        for name in ("local/swop.json", "local/hosted.js", ".herenow/proxy.json", ".herenow/data.json", "hosted-runtime/unexpected.js"):
            result = io.BytesIO()
            with tarfile.open(fileobj=io.BytesIO(original), mode="r:gz") as source, \
                 tarfile.open(fileobj=result, mode="w:gz") as target:
                for member in source:
                    target.addfile(member, source.extractfile(member))
                target.addfile(tarfile.TarInfo(name), io.BytesIO())
            self.archive = result.getvalue()
            with self.subTest(name=name), self.assertRaisesRegex(SystemExit, "SWOP configuration"):
                self.execute()
            self.assertFalse((self.root / "dist").exists())

    def test_publish_wrapper_refuses_missing_or_modified_msx_before_network_access(self):
        self.execute()
        wrapper = Path(__file__).parents[1] / "scripts/publish-herenow.sh"
        content = self.root / "dist/msx/content.json"
        for replacement in (None, b'{"action":"link:https://wrong.example/"}'):
            if replacement is None:
                content.unlink()
            else:
                content.write_bytes(replacement)
            result = subprocess.run(["bash", str(wrapper), str(self.root / "dist")], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("MSX bootstrap", result.stderr)

    def test_upstream_msx_collision_does_not_silently_replace_verified_bytes(self):
        stream = io.BytesIO(self.archive)
        result = io.BytesIO()
        with tarfile.open(fileobj=stream, mode="r:gz") as source, tarfile.open(fileobj=result, mode="w:gz") as target:
            for member in source:
                target.addfile(member, source.extractfile(member))
            target.addfile(tarfile.TarInfo("msx/start.json"), io.BytesIO())
        self.archive = result.getvalue()
        with self.assertRaisesRegex(SystemExit, "MSX directory"):
            self.execute()
        self.assertFalse((self.root / "dist").exists())

    def test_missing_demo_segment_does_not_stage_a_publishable_distribution(self):
        (self.root / "static/demo/pattern0.ts").unlink()
        with self.assertRaisesRegex(SystemExit, "Demo file inventory"):
            self.execute()
        self.assertFalse((self.root / "dist").exists())

    def test_upstream_demo_collision_does_not_silently_replace_verified_bytes(self):
        self.archive = self.tar(demo=True)
        with self.assertRaisesRegex(SystemExit, "conflicts"):
            self.execute()
        self.assertFalse((self.root / "dist").exists())

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
        for name, symlink in (("../escape", False), ("/absolute", False), ("index.html", True)):
            self.archive = self.tar(name, symlink)
            with self.subTest(name=name), self.assertRaises(SystemExit):
                self.execute()
            self.assertFalse((self.root / "dist").exists())


if __name__ == "__main__":
    unittest.main()
