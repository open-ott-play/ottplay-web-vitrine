"""Regression checks for here.now profile staging and backend-free routes."""
import copy
import json
from pathlib import Path
import runpy
import shutil
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SWOP = runpy.run_path(str(ROOT / "scripts/swop.py"))
verify_swop = SWOP["verify_swop"]
stage_swop = SWOP["stage_swop"]
verify_swop_runtime = SWOP["verify_swop_runtime"]


class SwopPublicationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / "static"
        for relative in ("local/swop.json", "local/hosted.js", ".herenow/proxy.json", ".herenow/data.json"):
            destination = self.source / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / "static" / relative, destination)
        self.target = self.root / "dist"
        self.target.mkdir()
        (self.target / "index.html").write_text('<!doctype html><html><head><script src="/player-loader.js"></script></head></html>')

    def runtime(self, script='window.__OTTPLAY_HOSTED_PROTOCOL__="hosted-profile-v1"; var protocol="ottplay.swop.v2";'):
        runtime = self.target / "dist/player.js"
        runtime.parent.mkdir(exist_ok=True)
        runtime.write_text(script)
        for relative in SWOP["REQUIRED_RUNTIME_ASSETS"]:
            asset = self.target / relative
            asset.parent.mkdir(parents=True, exist_ok=True)
            asset.write_text("runtime-fixture")
        return runtime

    def test_stages_site_data_and_fixed_proxy_with_profile_before_player(self):
        stage_swop(self.target, self.source)
        verify_swop(self.target)
        document = (self.target / "index.html").read_text()
        self.assertLess(document.index(SWOP["BOOTSTRAP_TAG"]), document.index('/player-loader.js'))
        self.assertEqual({p.relative_to(self.target).as_posix() for p in self.target.rglob("*") if p.is_file()},
                         {"index.html", "local/swop.json", "local/hosted.js", ".herenow/proxy.json", ".herenow/data.json"})
        self.assertNotIn("2560801.xyz", (self.target / ".herenow/proxy.json").read_text())

    def test_rejects_leaked_credentials_and_changed_upstreams(self):
        path = self.source / ".herenow/proxy.json"
        original = json.loads(path.read_text())
        invalid = []
        for field, value in (("headers", {"Authorization": "Bearer accidentally-pasted-secret"}),
                             ("upstream", "https://attacker.example/session"),
                             ("method", "GET"), ("rateLimit", "999999/hour/ip")):
            mutated = copy.deepcopy(original)
            mutated["proxies"]["/vportal/provider-1"][field] = value
            invalid.append(mutated)
        invalid.append(original["proxies"])
        for value in invalid:
            path.write_text(json.dumps(value))
            with self.assertRaisesRegex(SystemExit, "SWOP proxy manifest"):
                verify_swop(self.source)

    def test_removed_infrastructure_routes_cannot_reappear(self):
        path = self.source / ".herenow/proxy.json"
        original = json.loads(path.read_text())
        for route in ("/m3u/match-channels", "/m3u/match-logos", "/epg/*", "/logo/*",
                      "/swop/session", "/swop/val", "/vportal/api", "/m3u/cp.php", "/*"):
            changed = copy.deepcopy(original)
            changed["proxies"][route] = {"upstream": "https://epg.2560801.xyz/", "method": "POST"}
            path.write_text(json.dumps(changed))
            with self.subTest(route=route), self.assertRaisesRegex(SystemExit, "SWOP proxy manifest"):
                verify_swop(self.source)

    def test_vportal_exact_route_cannot_become_open_proxy(self):
        path = self.source / ".herenow/proxy.json"
        original = json.loads(path.read_text())
        for routes in ({}, {"/vportal/*": original["proxies"]["/vportal/provider-1"]}):
            path.write_text(json.dumps({"proxies": routes}))
            with self.assertRaisesRegex(SystemExit, "SWOP proxy manifest"):
                verify_swop(self.source)

    def test_legacy_swop_settings_cannot_reactivate_retired_worker(self):
        path = self.source / "local/swop.json"
        for value in ({"swopBaseUrl": "/swop"}, {"clientId": "shared-device"},
                      {"token": "accidentally-pasted-secret"}):
            path.write_text(json.dumps(value))
            with self.assertRaisesRegex(SystemExit, "SWOP public configuration"):
                verify_swop(self.source)

    def test_data_schema_requires_reviewed_bounds_and_allows_pairing(self):
        path = self.source / ".herenow/data.json"
        original = json.loads(path.read_text())
        changed = copy.deepcopy(original)
        changed["collections"]["swop_pairs"]["fields"]["reply"]["maxLength"] = 999999
        missing_mutation = copy.deepcopy(original)
        del missing_mutation["collections"]["swop_pairs"]["publicMutation"]
        for value in ({"collections": {}}, changed, missing_mutation):
            path.write_text(json.dumps(value))
            with self.assertRaisesRegex(SystemExit, "SWOP Site Data manifest"):
                verify_swop(self.source)

    def test_profile_changes_require_review(self):
        path = self.source / "local/hosted.js"
        path.write_text(path.read_text().replace('"version": 1', '"version": 2'))
        with self.assertRaisesRegex(SystemExit, "Hosted public configuration"):
            verify_swop(self.source)

    def test_rejects_duplicate_json_keys(self):
        (self.source / "local/swop.json").write_text('{"token":"x","token":"y"}')
        with self.assertRaisesRegex(SystemExit, "duplicate keys"):
            verify_swop(self.source)

    def test_rejects_additional_publication_control_files(self):
        (self.source / ".herenow/credentials").write_text("must-not-publish")
        with self.assertRaisesRegex(SystemExit, "unexpected publication controls"):
            verify_swop(self.source)

    def test_rejects_file_and_parent_symlinks(self):
        for relative in ("local/swop.json", "local/hosted.js", ".herenow/proxy.json", ".herenow/data.json", "local", ".herenow"):
            path = self.source / relative
            original = path.with_name(path.name + ".original")
            path.rename(original)
            path.symlink_to(original)
            with self.subTest(path=relative), self.assertRaises(SystemExit):
                verify_swop(self.source)
            path.unlink()
            original.rename(path)

    def test_staging_rejects_hidden_controls_and_dangling_symlinks(self):
        path = self.target / ".herenow"
        path.mkdir()
        with self.assertRaisesRegex(SystemExit, "conflicts"):
            stage_swop(self.target, self.source)
        path.rmdir()
        path.symlink_to(self.root / "missing")
        with self.assertRaisesRegex(SystemExit, "conflicts"):
            stage_swop(self.target, self.source)
        self.assertFalse((self.target / "local").exists())

    def test_invalid_source_does_not_stage_files(self):
        (self.source / ".herenow/proxy.json").write_text("{}")
        with self.assertRaisesRegex(SystemExit, "SWOP proxy manifest"):
            stage_swop(self.target, self.source)
        self.assertEqual([p.name for p in self.target.iterdir()], ["index.html"])

    def test_old_runtime_or_root_decoy_cannot_pass_guard(self):
        (self.target / "player.js").write_text('"hosted-profile-v1"; "ottplay.swop.v2";')
        with self.assertRaisesRegex(SystemExit, "compatible dist/player.js"):
            verify_swop_runtime(self.target)
        for script in ('var sessionToken="";', '"hosted-profile-v1";', '"ottplay.swop.v2";'):
            self.runtime(script)
            with self.assertRaisesRegex(SystemExit, "SWOP relay requires"):
                verify_swop_runtime(self.target)
        self.runtime()
        verify_swop_runtime(self.target)

    def test_missing_worker_or_companion_stops_publication(self):
        self.runtime()
        for relative in SWOP["REQUIRED_RUNTIME_ASSETS"]:
            path = self.target / relative
            path.unlink()
            with self.subTest(path=relative), self.assertRaisesRegex(SystemExit, "Hosted runtime asset"):
                verify_swop_runtime(self.target)
            path.write_text("runtime-fixture")

    def test_delayed_duplicate_or_async_bootstrap_is_rejected(self):
        stage_swop(self.target, self.source)
        path = self.target / "index.html"
        original = path.read_text()
        for changed in (original.replace(SWOP["BOOTSTRAP_TAG"], ''),
                        original.replace(SWOP["BOOTSTRAP_TAG"], SWOP["BOOTSTRAP_TAG"] * 2),
                        original.replace('<script src="/local/hosted.js">', '<script async src="/local/hosted.js">'),
                        original.replace(SWOP["BOOTSTRAP_TAG"], '<script src="/first.js"></script>' + SWOP["BOOTSTRAP_TAG"])):
            path.write_text(changed)
            with self.assertRaisesRegex(SystemExit, "Hosted bootstrap"):
                verify_swop(self.target)

    def test_runtime_assets_reject_symlinks(self):
        self.runtime()
        for relative in ("dist/player.js", "dist", "hosted/epg-worker.js", "swop-input"):
            path = self.target / relative
            original = path.with_name(path.name + ".original")
            path.rename(original)
            path.symlink_to(original)
            with self.subTest(path=relative), self.assertRaises(SystemExit):
                verify_swop_runtime(self.target)
            path.unlink()
            original.rename(path)


if __name__ == "__main__":
    unittest.main()
