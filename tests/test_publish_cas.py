"""Exercise the unmodified pinned publisher offline, including its actual PUT body."""
import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PIN = "8cf033ed53b82c0c67b16359c8c431f99e111d04"
SLUG = "liminal-sketch-vv8r"
VERSION = "01M3QSXKPXGQRF0875XAQQTNSC"
SWOP = runpy.run_path(str(ROOT / "scripts/swop.py"))

# No request can reach a socket. The fixture records bodies, never auth headers,
# and binds the pending version to its original base before simulating finalize.
FAKE_CURL = r'''#!/usr/bin/env python3
import json
import os
from pathlib import Path
import stat
import sys

args = sys.argv[1:]
method = args[args.index("-X") + 1]
url = next(arg for arg in args if arg.startswith("https://"))
root = Path(os.environ["CAS_TEST_ROOT"])
scenario = os.environ["CAS_TEST_SCENARIO"]
expected = os.environ["HERENOW_EXPECTED_VERSION"]
owner = "https://here.now/api/v1/publish/liminal-sketch-vv8r"
state = Path.cwd() / ".herenow/state.json"
record = {"method": method, "url": url, "cwd": str(Path.cwd()),
          "stateMode": stat.S_IMODE(state.stat().st_mode),
          "directoryMode": stat.S_IMODE(Path.cwd().stat().st_mode)}
body = json.loads(args[args.index("-d") + 1]) if "-d" in args else None
record["body"] = body
with (root / "requests.jsonl").open("a") as out:
    out.write(json.dumps(record) + "\n")

def conflict():
    print(json.dumps({"error": "live version changed", "code": "version_conflict",
                      "details": {"currentVersionId": "changed-version"}}))

if method == "PUT" and url == owner:
    assert body["baseVersionId"] == expected
    assert body["spaMode"] is True
    assert not body.get("claimToken")
    assert any(arg == "x-herenow-account: ottplay" for arg in args)
    assert any(arg == "authorization: Bearer offline-test-key" for arg in args)
    if scenario == "update-conflict":
        conflict()
    else:
        (root / "pending.json").write_text(json.dumps({"baseVersionId": body["baseVersionId"]}))
        print(json.dumps({"slug": "liminal-sketch-vv8r", "siteUrl": "https://player.ottplay.here.now/",
                          "upload": {"versionId": "pending-test-version", "finalizeUrl": owner + "/finalize",
                                     "uploads": [{"path": "demo/pattern0.ts",
                                                  "url": "https://offline.r2.cloudflarestorage.com/object",
                                                  "headers": {"Content-Type": "video/mp2t"}}]}}))
elif method == "PUT" and url == "https://offline.r2.cloudflarestorage.com/object":
    assert "--data-binary" in args
    assert not any("Bearer" in arg for arg in args)
    print("200", end="")
elif method == "POST" and url == owner + "/finalize":
    assert body == {"versionId": "pending-test-version"}
    assert json.loads((root / "pending.json").read_text())["baseVersionId"] == expected
    if scenario == "finalize-conflict":
        conflict()
    else:
        print(json.dumps({"currentVersionId": "accepted-test-version",
                          "accountUrl": "https://player.ottplay.here.now/"}))
else:
    raise SystemExit("Unexpected offline request")
'''


class PublishCasTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.publisher = Path(os.environ.get("HERENOW_PUBLISH_SCRIPT", str(
            ROOT / ".ci-tools/herenow/here-now/scripts/publish.sh"))).resolve()
        if not cls.publisher.is_file():
            raise AssertionError("Check out heredotnow/skill at " + PIN + " into .ci-tools/herenow")
        revision = subprocess.check_output(["git", "-C", str(cls.publisher.parents[2]),
                                            "rev-parse", "HEAD"], text=True).strip()
        if revision != PIN:
            raise AssertionError("Request contract tests require the real pinned publisher")

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.site = self.root / "site with spaces"
        shutil.copytree(ROOT / "static", self.site)
        for name in ("local", ".herenow"):
            shutil.rmtree(self.site / name)
        (self.site / "index.html").write_text('<!doctype html><html><head></head></html>')
        (self.site / "dist").mkdir()
        (self.site / "dist/player.js").write_text(
            'window.__OTTPLAY_HOSTED_PROTOCOL__="hosted-profile-v1"; var protocol="ottplay.swop.v2"; '
            'window.__OTT_CONTROL_DISCOVERY_VERSION__=1; window.__OTT_HOSTED_EPG_SERVER_VERSION__=1;')
        for relative in SWOP["REQUIRED_RUNTIME_ASSETS"]:
            asset = self.site / relative
            asset.parent.mkdir(parents=True, exist_ok=True)
            asset.write_bytes(SWOP["WORKER_HEADER"] + b"self.onmessage = function () {};\n"
                              if relative == Path("hosted/epg-worker.js") else b"/* fixture */\n")
        SWOP["stage_swop"](self.site, ROOT / "static")
        self.binary = self.root / "bin"
        self.binary.mkdir()
        curl = self.binary / "curl"
        curl.write_text(FAKE_CURL)
        curl.chmod(0o755)
        self.tmp = self.root / "temporary state"
        self.tmp.mkdir()
        # The caller's stale state must neither supply a base/claim token nor
        # get replaced by the temporary publisher's success/failure state.
        self.prior_state = self.root / ".herenow/state.json"
        self.prior_state.parent.mkdir()
        self.prior_state.write_text(json.dumps({"publishes": {SLUG: {
            "versionId": "unreviewed-newer-version", "path": str(self.site), "claimToken": "must-not-adopt"}}}))
        self.prior_bytes = self.prior_state.read_bytes()
        self.env = {**os.environ, "HOME": str(self.root / "home"), "TMPDIR": str(self.tmp),
                    "PATH": str(self.binary) + os.pathsep + os.environ["PATH"],
                    "HERENOW_API_KEY": "offline-test-key", "HERENOW_PUBLISH_SCRIPT": str(self.publisher),
                    "HERENOW_SITE_SLUG": SLUG, "HERENOW_WORKSPACE": "ottplay",
                    "HERENOW_EXPECTED_VERSION": VERSION, "OVERWRITE": "0", "SPA": "1",
                    "CAS_TEST_ROOT": str(self.root), "CAS_TEST_SCENARIO": "success"}

    def publish(self, **env):
        result = subprocess.run(["bash", str(ROOT / "scripts/publish-herenow.sh"), self.site.name],
                                cwd=self.root, env={**self.env, **env}, capture_output=True, text=True,
                                timeout=40, check=False)
        self.assertEqual(self.prior_state.read_bytes(), self.prior_bytes)
        self.assertEqual(list(self.tmp.iterdir()), [], "private publisher state must be removed")
        self.assertFalse((self.root / "home/.herenow/credentials").exists())
        self.assertNotIn("offline-test-key", result.stdout + result.stderr)
        log = self.root / "requests.jsonl"
        records = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
        for record in records:
            self.assertEqual(record["stateMode"], 0o600)
            self.assertEqual(record["directoryMode"], 0o700)
            self.assertNotEqual(record["cwd"], str(self.root))
        return result, records

    def test_real_update_body_carries_reviewed_base_and_preserves_full_manifest(self):
        result, requests = self.publish()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([r["method"] for r in requests], ["PUT", "PUT", "POST"])
        body = requests[0]["body"]
        self.assertEqual(body["baseVersionId"], VERSION)
        files = {row["path"]: row for row in body["files"]}
        self.assertEqual(set(files), {p.relative_to(self.site).as_posix() for p in self.site.rglob("*") if p.is_file()})
        self.assertNotIn(".herenow/state.json", files)
        self.assertEqual(files["demo/pattern.m3u8"]["contentType"], "application/vnd.apple.mpegurl")
        self.assertEqual(files["demo/pattern0.ts"]["contentType"], "video/mp2t")
        self.assertIn("publish_result.live_version_id=accepted-test-version", result.stderr)

    def test_update_conflict_stops_before_upload_or_finalize_without_retry(self):
        result, requests = self.publish(CAS_TEST_SCENARIO="update-conflict")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0]["body"]["baseVersionId"], VERSION)
        self.assertIn("publish_result.conflict=version_conflict", result.stderr)
        self.assertNotIn("publish_result.live_version_id=", result.stderr)

    def test_finalize_conflict_fails_without_retry_or_adopting_new_live_version(self):
        result, requests = self.publish(CAS_TEST_SCENARIO="finalize-conflict")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual([r["method"] for r in requests], ["PUT", "PUT", "POST"])
        self.assertIn("publish_result.conflict=version_conflict", result.stderr)
        self.assertNotIn("publish_result.live_version_id=", result.stderr)

    def test_missing_or_invalid_base_and_overwrite_fail_before_any_api_request(self):
        for env in ({"HERENOW_EXPECTED_VERSION": ""}, {"HERENOW_EXPECTED_VERSION": "bad value"},
                    {"OVERWRITE": "1"}, {"OVERWRITE": "true"}, {"HERENOW_SITE_SLUG": ""}):
            with self.subTest(env=env):
                result, requests = self.publish(**env)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(requests, [])


if __name__ == "__main__":
    unittest.main()
