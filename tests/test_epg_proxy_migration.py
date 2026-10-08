"""Only the reviewed source/destination/version tuple may change EPG controls."""
import json
from pathlib import Path
import tempfile
import unittest

from retention_fixture import RETAIN, ROOT, Reader, make_owner, make_stage, SLUG


class EpgProxyMigrationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.stage = self.root / "stock"
        make_stage(self.stage)
        self.original = RETAIN.snapshot(self.stage)
        self.version = RETAIN.EPG_PROXY_FROM_VERSION
        self.fixture = make_owner(self.stage, version=self.version)
        self.before = (ROOT / "tests/epg-proxy-before.json").read_bytes()
        self.assertEqual(len(self.before), RETAIN.EPG_PROXY_FROM["size"])
        self.assertEqual(RETAIN.sha256(self.before), RETAIN.EPG_PROXY_FROM["hash"])
        self.entry = next(item for item in self.fixture["listing"]["files"]
                          if item["path"] == RETAIN.EPG_PROXY_PATH)
        self.entry.update(RETAIN.EPG_PROXY_FROM)
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

    def test_only_two_upstreams_change_and_all_other_controls_are_preserved(self):
        before = json.loads(self.before)
        after = json.loads((self.stage / RETAIN.EPG_PROXY_PATH).read_bytes())
        for route in ("/epg/v1/match", "/epg/v1/programmes"):
            self.assertNotEqual(before["proxies"][route]["upstream"], after["proxies"][route]["upstream"])
            before["proxies"][route]["upstream"] = after["proxies"][route]["upstream"]
        self.assertEqual(before, after)
        result = self.prepare()
        self.assertEqual(result["changedPublicationControls"], {
            RETAIN.EPG_PROXY_PATH: {"from": RETAIN.EPG_PROXY_FROM, "to": RETAIN.EPG_PROXY_TO,
                                   "reviewedFromVersion": self.version}})
        self.assertNotIn(RETAIN.EPG_PROXY_PATH, result["protectedFiles"])
        self.assertEqual(len(result["protectedFiles"]), 18)
        self.assertEqual(RETAIN.snapshot(self.stage), self.original)
        self.assertEqual(RETAIN.snapshot(self.output)["files"], self.original["files"] | result["addedFiles"])

    def test_different_live_version_cannot_reuse_this_migration(self):
        self.version = "another-version-with-the-same-legacy-proxy"
        self.fixture["metadata"]["currentVersionId"] = self.version
        self.fixture["listing"]["currentVersionId"] = self.version
        self.rejected()
        self.assertFalse(any(path.startswith("/files/") for path, _ in self.reader.calls))

    def test_old_proxy_requires_exact_metadata_and_actual_body(self):
        for key, value in (("hash", "0" * 64), ("size", 804), ("contentType", "text/plain")):
            with self.subTest(key=key):
                previous = self.entry[key]
                self.entry[key] = value
                self.rejected()
                self.entry[key] = previous
        self.fixture["bodies"]["/files/" + RETAIN.EPG_PROXY_PATH] = self.before[:-1] + b"x"
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
