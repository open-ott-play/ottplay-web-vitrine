"""Qualified beta deployment failures, with mocked public GitHub responses only."""
import base64
import copy
import hashlib
import io
import json
from pathlib import Path
import runpy
import sys
import tarfile
import unittest
from unittest.mock import patch

import test_prepare_dist as stable


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()


def sha(data):
    return hashlib.sha256(data).hexdigest()


class BetaReleaseTests(unittest.TestCase):
    tar = stable.PrepareDistributionTests.tar
    download = stable.PrepareDistributionTests.download

    def setUp(self):
        stable.PrepareDistributionTests.setUp(self)
        self.tag = "v1.2.3-beta.1"
        self.prerelease = True
        self.channel = "beta"
        self.release_changes = {}
        self.run_changes = {"head_branch": "main", "event": "push",
                            "head_repository": {"full_name": "open-ott-play/ottplay-foss"}}
        self.policy = {"repository": "open-ott-play/ottplay-foss", "mode": "release", "default_branch": "main",
                       "versioning": {"schema": 1, "promotion": "promote-bytes", "build_number_floor": 100}}
        self.plan = {"schema_version": 1, "base_version": "1.2.3", "version": "1.2.3-beta.1", "channel": "beta",
                     "sequence": 1, "tag": self.tag, "source_sha": self.sha, "build_number": 101,
                     "policy_sha256": sha(canonical(self.policy)), "promotion": "promote-bytes"}
        self.manifest_changes = {}
        self.asset_change = None
        self.inventory_reads = 0
        self.policy_change = None
        self.build = {"version": self.tag[1:], "revision": self.sha}
        self.make_archive()

    def make_archive(self):
        stream = io.BytesIO()
        with tarfile.open(fileobj=io.BytesIO(self.tar()), mode="r:gz") as source, \
             tarfile.open(fileobj=stream, mode="w:gz") as target:
            for member in source:
                raw = source.extractfile(member).read()
                if member.name == "dist/player.js":
                    self.build["bundleSha256"] = sha(raw)
                target.addfile(member, io.BytesIO(raw))
            raw = json.dumps(self.build).encode()
            member = tarfile.TarInfo("build-info.json")
            member.size = len(raw)
            target.addfile(member, io.BytesIO(raw))
        self.archive = stream.getvalue()

    def policy_raw(self):
        return json.dumps(self.policy, indent=2).encode() + b"\n"

    def policy_blob(self):
        raw = self.policy_raw()
        return hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()

    def manifest(self):
        value = {"schema": 1, "repository": "open-ott-play/ottplay-foss", "version": "1.2.3", "channel": "beta",
                 "tag": self.tag, "source_sha": self.sha, "run_id": 42, "run_attempt": self.attempt,
                 "workflow_path": ".github/workflows/release-pipeline.yml", "version_plan": self.plan,
                 "plan_sha256": sha(canonical(self.plan)),
                 "source_policy": {"schema": 1, "path": ".release-policy.json", "data": self.policy,
                                   "sha256": sha(self.policy_raw()), "git_blob_sha": self.policy_blob()},
                 "assets": [{"name": "ottplay-foss-dist.tar.gz", "size": len(self.archive),
                             "sha256": self.checksum or sha(self.archive)}]}
        value.update(self.manifest_changes)
        return json.dumps(value).encode()

    def api(self, command):
        path = command[-1]
        if "/contents/.release-policy.json?ref=" in path:
            self.api_calls.append(path)
            value = {"type": "file", "encoding": "base64", "size": len(self.policy_raw()),
                     "content": base64.b64encode(self.policy_raw()).decode(), "sha": self.policy_blob()}
            if self.policy_change:
                self.policy_change(value)
            return json.dumps(value).encode()
        if "/releases/7/assets?" in path:
            self.api_calls.append(path)
            self.inventory_reads += 1
            value = [{"id": 100 + i, "name": name, "state": "uploaded", "size": len(raw), "digest": "sha256:" + sha(raw)}
                     for i, (name, raw) in enumerate([("release-manifest.json", self.manifest()),
                                                    ("ottplay-foss-dist.tar.gz", self.archive)])]
            if self.asset_change:
                self.asset_change(value)
            return json.dumps(value).encode()
        value = json.loads(stable.PrepareDistributionTests.api(self, command))
        if "/releases/tags/" in path:
            value.update({"id": 7, **self.release_changes})
        elif path.endswith("/actions/runs/42"):
            value.update(self.run_changes)
        return json.dumps(value).encode()

    def execute(self):
        digest = self.manifest_sha256 if self.manifest_sha256 is not None else sha(self.manifest())
        with patch.object(sys, "argv", [str(self.script), self.tag, digest, self.channel]), \
             patch.object(sys, "path", [str(self.script.parent), *sys.path]), \
             patch("subprocess.check_output", side_effect=self.api), \
             patch("subprocess.run", side_effect=self.download):
            runpy.run_path(str(self.script), run_name="__main__")

    def refused(self):
        with self.assertRaises(SystemExit):
            self.execute()
        self.assertFalse((self.root / "dist").exists())

    def test_exact_beta_bytes_staged_without_rebuild_or_actions_artifact_access(self):
        self.execute()
        self.assertEqual(json.loads((self.root / "dist/build-info.json").read_text()), self.build)
        with tarfile.open(fileobj=io.BytesIO(self.archive), mode="r:gz") as archive:
            self.assertEqual((self.root / "dist/dist/player.js").read_bytes(), archive.extractfile("dist/player.js").read())
        self.assertTrue((self.root / "dist/demo/pattern.mp4").is_file())
        self.assertTrue((self.root / "dist/msx/start.json").is_file())
        self.assertEqual(self.downloaded, ["release-manifest.json", "ottplay-foss-dist.tar.gz"])
        self.assertEqual(self.inventory_reads, 2)
        self.assertFalse(any("/artifacts" in path for path in self.api_calls))

    def test_manual_beta_current_retry_is_allowed(self):
        self.attempt = 2
        self.run_changes["event"] = "workflow_dispatch"
        self.execute()
        self.assertTrue(any("/attempts/2/jobs?" in path for path in self.api_calls))

    def test_channel_and_tag_cannot_masquerade(self):
        for channel, tag in [("stable", "v1.2.3-beta.1"), ("beta", "v1.2.3"), ("rc", "v1.2.3-rc.1"),
                             ("beta", "v1.2.3-rc.1"), ("beta", "v1.2.3-beta.0"), ("beta", "v1.2.3-beta.01"),
                             ("beta", "v01.2.3-beta.1"), ("beta", "v1.2.3-beta.1;id")]:
            with self.subTest(channel=channel, tag=tag):
                self.channel, self.tag = channel, tag
                self.refused()
                self.assertEqual(self.api_calls, [])

    def test_digest_missing_or_substituted_fails_before_archive_download(self):
        for digest in ("", "0" * 64, "A" * 64):
            self.manifest_sha256 = digest
            self.refused()
        self.assertNotIn("ottplay-foss-dist.tar.gz", self.downloaded)

    def test_wrong_manifest_or_plan_identity_is_rejected(self):
        for key, value in [("schema", True), ("channel", "rc"), ("tag", "v1.2.3-beta.2"), ("version", "1.2.3-beta.1"),
                           ("source_sha", "b" * 40), ("workflow_path", "other.yml"), ("run_id", True),
                           ("run_attempt", 0), ("plan_sha256", "0" * 64)]:
            with self.subTest(manifest=key):
                self.manifest_changes = {key: value}
                self.refused()
        self.manifest_changes = {}
        original = copy.deepcopy(self.plan)
        for key, value in [("sequence", True), ("sequence", 2), ("tag", "v1.2.3-rc.1"), ("channel", "stable"),
                           ("source_sha", "b" * 40), ("version", "1.2.3"), ("build_number", True),
                           ("build_number", 100), ("policy_sha256", "0" * 64), ("promotion", "final-build")]:
            with self.subTest(plan=key, value=value):
                self.plan = {**original, key: value}
                self.refused()

    def test_wrong_publication_status_fails(self):
        for change in ({"draft": True}, {"prerelease": False}, {"id": False}):
            self.release_changes = change
            self.refused()

    def test_wrong_run_main_event_source_and_current_attempt_fail(self):
        original = self.run_changes.copy()
        for change in ({"head_branch": "feature"}, {"event": "schedule"}, {"event": "pull_request"},
                       {"head_repository": {"full_name": "attacker/fork"}}, {"head_sha": "b" * 40},
                       {"path": "other.yml"}, {"conclusion": "failure"}, {"status": "in_progress"},
                       {"run_attempt": 2}, {"run_attempt": True}):
            self.run_changes = {**original, **change}
            self.refused()
        self.run_changes = original
        self.recheck_attempt = 2
        self.run_reads = 0
        self.refused()

    def test_skipped_release_gate_fails(self):
        self.gate = "skipped"
        self.refused()

    def test_wrong_source_policy_or_qualification_fails(self):
        for change in (lambda v: v.update(sha="0" * 40), lambda v: v.update(size=1),
                       lambda v: v.update(content=base64.b64encode(b"{}").decode())):
            self.policy_change = change
            self.refused()
        self.policy_change = None
        self.policy["stable_blockers"] = ["not qualified"]
        self.plan["policy_sha256"] = sha(canonical(self.policy))
        self.refused()

    def test_asset_inventory_and_digest_mutations_fail(self):
        for change in (lambda v: v.pop(), lambda v: v.append(v[0]),
                       lambda v: v.append({**v[0], "name": "unexpected"}),
                       lambda v: v[0].update(digest="sha256:" + "0" * 64),
                       lambda v: v[1].update(size=True), lambda v: v[1].update(state="new"),
                       lambda v: v[1].update(digest=None)):
            self.asset_change = change
            self.refused()

    def test_release_asset_replaced_after_download_fails(self):
        def changed_after_download(assets):
            if self.inventory_reads > 1:
                assets[1]["id"] += 1
        self.asset_change = changed_after_download
        self.refused()

    def test_web_numeric_version_or_wrong_source_fails(self):
        for key, value in (("version", "1.2.3"), ("revision", "b" * 40)):
            self.build = {"version": "1.2.3-beta.1", "revision": self.sha, key: value}
            self.make_archive()
            self.refused()


if __name__ == "__main__":
    unittest.main()
