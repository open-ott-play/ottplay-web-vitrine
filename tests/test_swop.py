"""Regression checks for here.now profile staging and backend-free routes."""
import copy
import hashlib
import json
import posixpath
import re
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
            asset.write_bytes(SWOP["WORKER_HEADER"] + b"self.onmessage = function () {};\n" if relative == Path("hosted/epg-worker.js") else b"/* runtime-fixture */\n")
        return runtime

    def profile(self, target=None):
        script = ((target or self.target) / "local/hosted.js").read_text()
        return json.loads(script.removeprefix("window.__OTTPLAY_HOSTED__ = ").removesuffix(";\n"))

    def graph(self, target=None):
        target = target or self.target
        worker = target / self.profile(target)["epg"]["workerUrl"].lstrip("/")
        return worker.parent.parent

    def test_stages_site_data_and_fixed_proxy_with_profile_before_player(self):
        self.runtime()
        original = {p.relative_to(self.target): p.read_bytes() for p in self.target.rglob("*") if p.is_file()}
        stage_swop(self.target, self.source)
        verify_swop(self.target)
        document = (self.target / "index.html").read_text()
        tag = SWOP["bootstrap_tag"]((self.target / "local/hosted.js").read_text())
        self.assertLess(document.index(tag), document.index('/player-loader.js'))
        graph = self.profile()["epg"]["workerUrl"].split("/")[2]
        self.assertRegex(graph, r"^[0-9a-f]{64}$")
        for relative, data in original.items():
            if relative != Path("index.html"):
                self.assertEqual((self.target / relative).read_bytes(), data)
        graph_files = {p.relative_to(self.target / "hosted-runtime" / graph).as_posix()
                       for p in (self.target / "hosted-runtime").rglob("*") if p.is_file()}
        self.assertEqual(graph_files, {p.as_posix() for p in SWOP["GRAPH_ASSETS"]})
        for relative in SWOP["GRAPH_ASSETS"]:
            self.assertEqual((self.target / "hosted-runtime" / graph / relative).read_bytes(), original[relative])
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
                verify_swop(self.source, template=True)

    def test_removed_infrastructure_routes_cannot_reappear(self):
        path = self.source / ".herenow/proxy.json"
        original = json.loads(path.read_text())
        for route in ("/m3u/match-channels", "/m3u/match-logos", "/epg/*", "/logo/*",
                      "/swop/session", "/swop/val", "/vportal/api", "/m3u/cp.php", "/*"):
            changed = copy.deepcopy(original)
            changed["proxies"][route] = {"upstream": "https://epg.2560801.xyz/", "method": "POST"}
            path.write_text(json.dumps(changed))
            with self.subTest(route=route), self.assertRaisesRegex(SystemExit, "SWOP proxy manifest"):
                verify_swop(self.source, template=True)

    def test_vportal_exact_route_cannot_become_open_proxy(self):
        path = self.source / ".herenow/proxy.json"
        original = json.loads(path.read_text())
        for routes in ({}, {"/vportal/*": original["proxies"]["/vportal/provider-1"]}):
            path.write_text(json.dumps({"proxies": routes}))
            with self.assertRaisesRegex(SystemExit, "SWOP proxy manifest"):
                verify_swop(self.source, template=True)

    def test_legacy_swop_settings_cannot_reactivate_retired_worker(self):
        path = self.source / "local/swop.json"
        for value in ({"swopBaseUrl": "/swop"}, {"clientId": "shared-device"},
                      {"token": "accidentally-pasted-secret"}):
            path.write_text(json.dumps(value))
            with self.assertRaisesRegex(SystemExit, "SWOP public configuration"):
                verify_swop(self.source, template=True)

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
                verify_swop(self.source, template=True)

    def test_profile_changes_require_review(self):
        path = self.source / "local/hosted.js"
        path.write_text(path.read_text().replace('"version": 1', '"version": 2'))
        with self.assertRaisesRegex(SystemExit, "Hosted public configuration"):
            verify_swop(self.source, template=True)

    def test_rejects_duplicate_json_keys(self):
        (self.source / "local/swop.json").write_text('{"token":"x","token":"y"}')
        with self.assertRaisesRegex(SystemExit, "duplicate keys"):
            verify_swop(self.source, template=True)

    def test_rejects_additional_publication_control_files(self):
        (self.source / ".herenow/credentials").write_text("must-not-publish")
        with self.assertRaisesRegex(SystemExit, "unexpected publication controls"):
            verify_swop(self.source, template=True)

    def test_rejects_file_and_parent_symlinks(self):
        for relative in ("local/swop.json", "local/hosted.js", ".herenow/proxy.json", ".herenow/data.json", "local", ".herenow"):
            path = self.source / relative
            original = path.with_name(path.name + ".original")
            path.rename(original)
            path.symlink_to(original)
            with self.subTest(path=relative), self.assertRaises(SystemExit):
                verify_swop(self.source, template=True)
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

    def test_missing_worker_dependency_or_companion_stops_publication(self):
        self.runtime()
        for relative in SWOP["REQUIRED_RUNTIME_ASSETS"]:
            path = self.target / relative
            path.unlink()
            with self.subTest(path=relative), self.assertRaisesRegex(SystemExit, "Hosted runtime asset"):
                verify_swop_runtime(self.target)
            path.write_bytes(SWOP["WORKER_HEADER"] + b"self.onmessage = function () {};\n" if relative == Path("hosted/epg-worker.js") else b"/* runtime-fixture */\n")

    def test_delayed_duplicate_or_async_bootstrap_is_rejected(self):
        self.runtime()
        stage_swop(self.target, self.source)
        path = self.target / "index.html"
        original = path.read_text()
        tag = SWOP["bootstrap_tag"]((self.target / "local/hosted.js").read_text())
        for changed in (original.replace(tag, ''),
                        original.replace(tag, tag * 2),
                        original.replace(tag, tag.replace('<script ', '<script async ')),
                        original.replace(tag, '<script src="/first.js"></script>' + tag),
                        original.replace(tag, '<!-- ' + tag + ' -->'),
                        original.replace(tag, '<template>' + tag + '</template>'),
                        original.replace(tag, '<noscript>' + tag + '</noscript>'),
                        original.replace(tag, SWOP["BOOTSTRAP_TAG"]),
                        original.replace(tag, tag.replace('?v=', '?old='))):
            path.write_text(changed)
            with self.assertRaisesRegex(SystemExit, "Hosted bootstrap"):
                verify_swop(self.target)

    def test_runtime_assets_reject_symlinks(self):
        self.runtime()
        for relative in ("dist/player.js", "dist", "hosted/epg-worker.js", "hosted/pako-inflate.js",
                         "hosted/sax.js", "js/runtime-polyfills.js", "js/ottplay-core.js", "js", "swop-input"):
            path = self.target / relative
            original = path.with_name(path.name + ".original")
            path.rename(original)
            path.symlink_to(original)
            with self.subTest(path=relative), self.assertRaises(SystemExit):
                verify_swop_runtime(self.target)
            path.unlink()
            original.rename(path)

    def test_dependency_only_change_invalidates_graph_profile_and_bootstrap(self):
        self.runtime()
        release = self.root / "release"
        shutil.copytree(self.target, release)
        stage_swop(self.target, self.source)
        old_profile = (self.target / "local/hosted.js").read_bytes()
        old_worker = (self.target / "hosted/epg-worker.js").read_bytes()
        same = self.root / "identical"
        shutil.copytree(release, same)
        stage_swop(same, self.source)
        self.assertEqual((same / "local/hosted.js").read_bytes(), old_profile)
        for index, dependency in enumerate(SWOP["GRAPH_ASSETS"][1:]):
            with self.subTest(dependency=dependency):
                target = self.root / ("changed-" + str(index))
                shutil.copytree(release, target)
                asset = target / dependency
                asset.write_bytes(asset.read_bytes() + b"/* new release */\n")
                stage_swop(target, self.source)
                verify_swop(target)
                new_profile = (target / "local/hosted.js").read_bytes()
                self.assertNotEqual(new_profile, old_profile)
                self.assertNotEqual(self.profile(target)["epg"]["workerUrl"], self.profile()["epg"]["workerUrl"])
                self.assertEqual((target / "hosted/epg-worker.js").read_bytes(), old_worker)
                self.assertIn("?v=" + hashlib.sha256(new_profile).hexdigest(), (target / "index.html").read_text())
                self.assertNotIn(hashlib.sha256(old_profile).hexdigest(), (target / "index.html").read_text())
                index = target / "index.html"
                index.write_text(index.read_text().replace(hashlib.sha256(new_profile).hexdigest(), hashlib.sha256(old_profile).hexdigest()))
                with self.assertRaisesRegex(SystemExit, "Hosted bootstrap"):
                    verify_swop(target)

    def test_worker_relative_imports_resolve_within_the_five_file_graph(self):
        self.runtime()
        stage_swop(self.target, self.source)
        worker_url = self.profile()["epg"]["workerUrl"]
        worker = (self.target / worker_url.lstrip("/")).read_bytes()
        imports = re.findall(rb'"([^"]+)"', worker.splitlines()[0])
        self.assertEqual(len(imports), 4)
        prefix = "/" + self.graph().relative_to(self.target).as_posix() + "/"
        for name in imports:
            resolved = posixpath.normpath(posixpath.join(posixpath.dirname(worker_url), name.decode()))
            self.assertTrue(resolved.startswith(prefix))
            relative = Path(resolved.removeprefix(prefix))
            self.assertIn(relative, SWOP["GRAPH_ASSETS"])
            self.assertEqual((self.target / resolved.lstrip("/")).read_bytes(), (self.target / relative).read_bytes())

    def test_unreviewed_worker_import_graph_is_rejected_before_staging(self):
        self.runtime()
        worker = self.target / "hosted/epg-worker.js"
        original = worker.read_bytes()
        for value in (original.replace(b'"sax.js"', b'"sax.js?v=old"'),
                      original.replace(b'"pako-inflate.js", "sax.js"', b'"sax.js", "pako-inflate.js"'),
                      original.replace(b'"sax.js"', b'"sax" + ".js"'),
                      b"/* changed loader */\n" + original,
                      original + b'importScripts("other.js");\n',
                      original + b'self["importScripts"]("other.js");\n'):
            worker.write_bytes(value)
            with self.subTest(value=value), self.assertRaisesRegex(SystemExit, "import graph"):
                stage_swop(self.target, self.source)
            self.assertFalse((self.target / "hosted-runtime").exists())
            self.assertFalse((self.target / "local").exists())

    def test_staged_graph_missing_changed_extra_and_conflicting_paths_are_rejected(self):
        self.runtime()
        stage_swop(self.target, self.source)
        for case in ("missing", "changed", "source-changed", "extra-file", "extra-directory", "other-generation"):
            target = self.root / case
            shutil.copytree(self.target, target)
            graph = self.graph(target)
            if case == "missing":
                (graph / "hosted/sax.js").unlink()
            elif case == "changed":
                (graph / "hosted/sax.js").write_text("tampered")
            elif case == "source-changed":
                (target / "js/ottplay-core.js").write_text("different release")
            elif case == "extra-file":
                (graph / "hosted/extra.js").write_text("extra")
            elif case == "extra-directory":
                (graph / "empty").mkdir()
            else:
                (target / "hosted-runtime" / ("0" * 64)).mkdir()
            with self.subTest(case=case), self.assertRaisesRegex(SystemExit, "Hosted runtime graph"):
                verify_swop(target)

    def test_staged_graph_rejects_symlinks_at_every_level(self):
        self.runtime()
        stage_swop(self.target, self.source)
        graph = self.graph().relative_to(self.target)
        for index, relative in enumerate((Path("hosted-runtime"), graph, graph / "hosted", graph / "js",
                                          *(graph / file for file in SWOP["GRAPH_ASSETS"]))):
            target = self.root / ("symlink-" + str(index))
            shutil.copytree(self.target, target)
            path = target / relative
            outside = self.root / ("outside-" + str(index))
            path.rename(outside)
            path.symlink_to(outside)
            with self.subTest(path=relative), self.assertRaises(SystemExit):
                verify_swop(target)

    def test_generated_profile_only_changes_reviewed_worker_location(self):
        self.runtime()
        stage_swop(self.target, self.source)
        path = self.target / "local/hosted.js"
        original = path.read_text()
        for value in (SWOP["HOSTED_SCRIPT"],
                      original.replace(self.profile()["epg"]["workerUrl"], "/hosted-runtime/" + "0" * 64 + "/hosted/epg-worker.js"),
                      original.replace('"version": 1', '"version": 2'),
                      original.replace('"collection": "swop_pairs"', '"collection": "other"'),
                      original.replace(SWOP["PROVIDER_URL"], "https://attacker.example/")):
            path.write_text(value)
            with self.subTest(value=value), self.assertRaisesRegex(SystemExit, "Hosted public configuration"):
                verify_swop(self.target)

    def test_stage_rejects_any_existing_runtime_namespace(self):
        self.runtime()
        path = self.target / "hosted-runtime"
        for kind in ("directory", "file", "dangling-symlink"):
            if kind == "directory":
                path.mkdir()
            elif kind == "file":
                path.write_text("archive conflict")
            else:
                path.symlink_to(self.root / "does-not-exist")
            with self.subTest(kind=kind), self.assertRaisesRegex(SystemExit, "conflicts"):
                stage_swop(self.target, self.source)
            self.assertFalse((self.target / "local").exists())
            if kind == "directory":
                path.rmdir()
            else:
                path.unlink()

    def test_staged_verification_cannot_fall_back_to_template(self):
        self.runtime()
        stage_swop(self.target, self.source)
        with self.assertRaisesRegex(SystemExit, "Hosted template"):
            verify_swop(self.target, template=True)
        shutil.rmtree(self.target / "hosted-runtime")
        (self.target / "index.html").unlink()
        (self.target / "local/hosted.js").write_text(SWOP["HOSTED_SCRIPT"])
        with self.assertRaisesRegex(SystemExit, "Hosted runtime graph"):
            verify_swop(self.target)

    def test_original_runtime_symlinks_cannot_enter_staged_graph(self):
        self.runtime()
        for relative in SWOP["GRAPH_ASSETS"]:
            path = self.target / relative
            backup = self.root / (relative.name + ".original")
            path.rename(backup)
            path.symlink_to(backup)
            with self.subTest(path=relative), self.assertRaises(SystemExit):
                stage_swop(self.target, self.source)
            self.assertFalse((self.target / "hosted-runtime").exists())
            path.unlink()
            backup.rename(path)


if __name__ == "__main__":
    unittest.main()
