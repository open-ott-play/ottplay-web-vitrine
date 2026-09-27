"""Security regression checks for the published SWOP relay boundary."""
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
        for relative in ("local/swop.json", ".herenow/proxy.json"):
            destination = self.source / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / "static" / relative, destination)
        self.target = self.root / "dist"
        self.target.mkdir()

    def test_stages_only_relative_config_and_fixed_server_side_secret_references(self):
        stage_swop(self.target, self.source)
        verify_swop(self.target)
        self.assertEqual({p.relative_to(self.target).as_posix() for p in self.target.rglob("*") if p.is_file()},
                         {"local/swop.json", ".herenow/proxy.json"})

    def test_rejects_leaked_credentials_arbitrary_routes_and_changed_upstreams(self):
        path = self.source / ".herenow/proxy.json"
        original = json.loads(path.read_text())
        invalid = []
        for field, value in (("headers", {"Authorization": "Bearer accidentally-pasted-secret"}),
                             ("upstream", "https://attacker.example/session"),
                             ("method", "GET"), ("rateLimit", "999999/hour/ip")):
            mutated = copy.deepcopy(original)
            mutated["proxies"]["/swop/session"][field] = value
            invalid.append(mutated)
        wildcard = copy.deepcopy(original)
        wildcard["proxies"]["/swop/*"] = wildcard["proxies"].pop("/swop/session")
        invalid.append(wildcard)
        extra = copy.deepcopy(original)
        extra["proxies"]["/admin/*"] = {"upstream": "https://swop.2560801.xyz/admin"}
        invalid.append(extra)
        invalid.append(original["proxies"])
        for index, value in enumerate(invalid):
            path.write_text(json.dumps(value))
            with self.subTest(index=index), self.assertRaisesRegex(SystemExit, "SWOP proxy manifest"):
                verify_swop(self.source)

    def test_rejects_direct_service_urls_shared_identity_and_public_secret_fields(self):
        path = self.source / "local/swop.json"
        for value in ({"swopBaseUrl": "https://swop.2560801.xyz"},
                      {"swopBaseUrl": "/swop", "clientId": "shared-device"},
                      {"swopBaseUrl": "/swop", "token": "accidentally-pasted-secret"}):
            path.write_text(json.dumps(value))
            with self.subTest(fields=list(value)), self.assertRaisesRegex(SystemExit, "SWOP public configuration"):
                verify_swop(self.source)

    def test_rejects_duplicate_json_keys(self):
        (self.source / "local/swop.json").write_text('{"swopBaseUrl":"https://wrong.example","swopBaseUrl":"/swop"}')
        with self.assertRaisesRegex(SystemExit, "duplicate keys"):
            verify_swop(self.source)

    def test_rejects_additional_publication_control_files(self):
        (self.source / ".herenow/credentials").write_text("must-not-publish")
        with self.assertRaisesRegex(SystemExit, "unexpected publication controls"):
            verify_swop(self.source)

    def test_rejects_file_and_parent_symlinks(self):
        for relative in ("local/swop.json", ".herenow/proxy.json", "local", ".herenow"):
            path = self.source / relative
            original = path.with_name(path.name + ".original")
            path.rename(original)
            path.symlink_to(original)
            with self.subTest(path=relative), self.assertRaises(SystemExit):
                verify_swop(self.source)
            path.unlink()
            original.rename(path)

    def test_staging_rejects_existing_hidden_controls_and_dangling_symlinks(self):
        path = self.target / ".herenow"
        path.mkdir()
        with self.assertRaisesRegex(SystemExit, "conflicts"):
            stage_swop(self.target, self.source)
        path.rmdir()
        path.symlink_to(self.root / "missing")
        with self.assertRaisesRegex(SystemExit, "conflicts"):
            stage_swop(self.target, self.source)
        self.assertFalse((self.target / "local").exists())

    def test_invalid_repository_configuration_is_rejected_before_staging(self):
        (self.source / ".herenow/proxy.json").write_text("{}")
        with self.assertRaisesRegex(SystemExit, "SWOP proxy manifest"):
            stage_swop(self.target, self.source)
        self.assertEqual(list(self.target.iterdir()), [])

    def test_runtime_guard_rejects_missing_legacy_and_symlinked_bundles(self):
        runtime = self.target / "player.js"
        with self.assertRaisesRegex(SystemExit, "compatible player.js"):
            verify_swop_runtime(self.target)
        for script in ('var otherToken="";', 'var nosessionToken="";',
                       'var sessionToken=""; alert("Allowlist this Device ID");'):
            runtime.write_text(script)
            with self.subTest(script=script), self.assertRaisesRegex(SystemExit, "sessionToken support"):
                verify_swop_runtime(self.target)
        runtime.write_text('function poll(session){return JSON.stringify({sessionToken:session.sessionToken});}')
        verify_swop_runtime(self.target)
        original = self.target / "original.js"
        runtime.rename(original)
        runtime.symlink_to(original)
        with self.assertRaisesRegex(SystemExit, "compatible player.js"):
            verify_swop_runtime(self.target)


if __name__ == "__main__":
    unittest.main()
