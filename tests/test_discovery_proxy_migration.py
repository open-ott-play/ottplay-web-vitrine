"""Only the reviewed owner may add the credential-free discovery GET route."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from retention_fixture import RETAIN, ROOT, SWOP, Reader, make_owner, make_stage, SLUG


class DiscoveryProxyMigrationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.stage = self.root / "stock"
        make_stage(self.stage)
        # Exercise the historical cutover against its exact reviewed destination.
        # Reconstruct the exact historical destination from its retained
        # predecessor and the single reviewed route, independent of future stock.
        before = json.loads((ROOT / "tests/discovery-proxy-before.json").read_bytes())
        after = (json.dumps({"proxies": {
            "/ott-control/api/discovery": {
                "upstream": "https://www.2560801.xyz/ott-control/api/discovery",
                "method": "GET",
                "headers": {"Accept": "application/json",
                            "User-Agent": "OTT-play-control-discovery/1.0"},
                "rateLimit": "1200/hour/ip",
            }, **before["proxies"]}}, indent=2) + "\n").encode()
        self.assertEqual(len(after), RETAIN.DISCOVERY_PROXY_TO["size"])
        self.assertEqual(RETAIN.sha256(after), RETAIN.DISCOVERY_PROXY_TO["hash"])
        (self.stage / RETAIN.EPG_PROXY_PATH).write_bytes(after)
        self.proxy_patch = patch.object(SWOP, "EXPECTED_PROXY", json.loads(after))
        self.proxy_patch.start()
        self.addCleanup(self.proxy_patch.stop)
        self.original = RETAIN.snapshot(self.stage)
        self.version = RETAIN.DISCOVERY_PROXY_FROM_VERSION
        self.fixture = make_owner(self.stage, version=self.version)
        self.before = (ROOT / "tests/discovery-proxy-before.json").read_bytes()
        self.assertEqual(len(self.before), RETAIN.DISCOVERY_PROXY_FROM["size"])
        self.assertEqual(RETAIN.sha256(self.before), RETAIN.DISCOVERY_PROXY_FROM["hash"])
        self.entry = next(item for item in self.fixture["listing"]["files"]
                          if item["path"] == RETAIN.EPG_PROXY_PATH)
        self.entry.update(RETAIN.DISCOVERY_PROXY_FROM)
        self.fixture["bodies"]["/files/" + RETAIN.EPG_PROXY_PATH] = self.before
        self.output, self.receipt = self.root / "copy", self.root / "receipt.json"

    def prepare(self):
        self.reader = Reader(self.fixture)
        return RETAIN.create_copy(self.stage, self.output, self.receipt,
                                  "ottplay", SLUG, self.version, self.reader)

    def rejected(self):
        with self.assertRaises((ValueError, SystemExit)):
            self.prepare()
        self.assertFalse(self.output.exists())
        self.assertFalse(self.receipt.exists())

    def test_only_reviewed_discovery_route_is_added(self):
        self.assertEqual(self.version, "01M6HME985RD3JN6T1X5BH0JDG")
        self.assertEqual(RETAIN.DISCOVERY_PROXY_FROM, {
            "size": 2217,
            "hash": "edba89271adfc29cc9a3ab8b2efa274269c739fb06729e89b6749bb637735294",
            "contentType": "application/json; charset=utf-8",
        })
        self.assertEqual(RETAIN.DISCOVERY_PROXY_TO, {
            "size": 2509,
            "hash": "1db5fd88531bf0e6844c99ba5dd4fcd8f2b6b843c408ff5d6c4e6a45571ca625",
            "contentType": "application/json; charset=utf-8",
        })
        self.assertEqual(sum(RETAIN.protected(item["path"])
                             for item in self.fixture["listing"]["files"]), 19)
        before = json.loads(self.before)
        after = json.loads((self.stage / RETAIN.EPG_PROXY_PATH).read_bytes())
        route = after["proxies"].pop("/ott-control/api/discovery")
        self.assertEqual(route["method"], "GET")
        self.assertEqual(route["upstream"], "https://www.2560801.xyz/ott-control/api/discovery")
        self.assertEqual(before, after)
        result = self.prepare()
        self.assertEqual(result["changedPublicationControls"], {
            RETAIN.EPG_PROXY_PATH: {"from": RETAIN.DISCOVERY_PROXY_FROM, "to": RETAIN.DISCOVERY_PROXY_TO,
                                   "reviewedFromVersion": self.version}})
        self.assertNotIn(RETAIN.EPG_PROXY_PATH, result["protectedFiles"])
        self.assertEqual(len(result["protectedFiles"]), 18)
        self.assertEqual(RETAIN.snapshot(self.stage), self.original)
        self.assertEqual(RETAIN.snapshot(self.output)["files"], self.original["files"] | result["addedFiles"])

    def test_different_live_version_cannot_reuse_this_migration(self):
        for version in ("01M5TMTKNGZ2VBMX21423J3FKM", "another-version-with-the-same-legacy-proxy"):
            with self.subTest(version=version):
                self.version = version
                self.fixture["metadata"]["currentVersionId"] = version
                self.fixture["listing"]["currentVersionId"] = version
                self.rejected()
                self.assertFalse(any(path.startswith("/files/") for path, _ in self.reader.calls))

    def test_old_proxy_requires_exact_metadata_and_actual_body(self):
        for key, value in (("hash", "0" * 64), ("size", 2216),
                           ("contentType", "text/plain"), ("contentType", "application/json")):
            with self.subTest(key=key):
                previous = self.entry[key]
                self.entry[key] = value
                self.rejected()
                self.entry[key] = previous
        for body in (self.before[:-1] + b"x", self.before[:-1]):
            with self.subTest(body_size=len(body)):
                self.fixture["bodies"]["/files/" + RETAIN.EPG_PROXY_PATH] = body
                self.rejected()

    def test_unrelated_control_change_or_extra_file_cannot_hitchhike(self):
        unrelated = next(item for item in self.fixture["listing"]["files"]
                         if item["path"] == "local/swop.json")
        saved = unrelated["hash"]
        unrelated["hash"] = "0" * 64
        self.rejected()
        unrelated["hash"] = saved
        self.fixture["listing"]["files"].append({"path": ".herenow/extra.json", "size": 2,
                                                "hash": RETAIN.sha256(b"{}"),
                                                "contentType": "application/json; charset=utf-8"})
        self.rejected()

    def test_even_semantically_identical_destination_requires_reviewed_bytes(self):
        path = self.stage / RETAIN.EPG_PROXY_PATH
        path.write_text(json.dumps(json.loads(path.read_text())))
        self.rejected()

    def test_already_migrated_owner_uses_normal_preservation_at_any_version(self):
        self.version = "later-accepted-version"
        self.fixture = make_owner(self.stage, version=self.version)
        result = self.prepare()
        self.assertEqual(result["changedPublicationControls"], {})
        self.assertEqual(len(result["protectedFiles"]), 19)


if __name__ == "__main__":
    unittest.main()
