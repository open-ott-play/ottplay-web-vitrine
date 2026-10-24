"""A reviewed demo MIME correction preserves bytes and every other control."""
import copy
import io
from pathlib import Path
import tempfile
import unittest

from retention_fixture import RETAIN, Reader, make_owner, make_stage, SLUG


class DemoMimeMigrationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.stage = self.root / "stock"
        make_stage(self.stage)
        self.original = RETAIN.snapshot(self.stage)
        self.version = "01M5NVXM5S6GNY3RFA11WGRJ3H"
        self.fixture = make_owner(self.stage, version=self.version)
        self.names = {"demo/pattern.m3u8"} | {f"demo/pattern{i}.ts" for i in range(12)}
        for item in self.fixture["listing"]["files"]:
            name = item["path"]
            if name in self.names:
                item["contentType"] = "text/plain" if name.endswith(".m3u8") else "application/octet-stream"
                self.fixture["bodies"]["/files/" + name] = (self.stage / name).read_bytes()
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
        self.assertEqual(RETAIN.snapshot(self.stage), self.original)

    def test_exact_13_mime_changes_verify_bytes_and_preserve_all_other_controls(self):
        result = self.prepare()
        changes = result["changedPublicationControls"]
        self.assertEqual(set(changes), self.names)
        self.assertEqual(len(result["protectedFiles"]), 6)
        for name in self.names:
            old_mime = "text/plain" if name.endswith(".m3u8") else "application/octet-stream"
            new_mime = "application/vnd.apple.mpegurl" if name.endswith(".m3u8") else "video/mp2t"
            identity = self.original["files"][name]
            self.assertEqual(changes[name], {"from": identity | {"contentType": old_mime},
                                           "to": identity | {"contentType": new_mime},
                                           "reviewedFromVersion": self.version})
            self.assertIn(("/files/" + name, identity["size"]), self.reader.calls)
            self.assertEqual((self.output / name).read_bytes(), (self.stage / name).read_bytes())
        self.assertEqual(RETAIN.snapshot(self.stage), self.original)
        self.assertEqual(RETAIN.snapshot(self.output)["files"], self.original["files"] | result["addedFiles"])

    def test_other_version_cannot_reuse_migration(self):
        self.version = "different-version-with-the-same-bytes"
        self.fixture["metadata"]["currentVersionId"] = self.version
        self.fixture["listing"]["currentVersionId"] = self.version
        self.rejected()
        self.assertEqual(len(self.reader.calls), 2)

    def test_each_predecessor_requires_exact_identity_and_body(self):
        baseline = copy.deepcopy(self.fixture)
        for name in sorted(self.names):
            for key, value in (("hash", "0" * 64), ("size", 1), ("contentType", "text/html")):
                with self.subTest(path=name, key=key):
                    self.fixture = copy.deepcopy(baseline)
                    entry = next(i for i in self.fixture["listing"]["files"] if i["path"] == name)
                    entry[key] = value
                    self.rejected()
                    self.assertEqual(len(self.reader.calls), 2)
            with self.subTest(path=name, key="body"):
                self.fixture = copy.deepcopy(baseline)
                raw = self.fixture["bodies"]["/files/" + name]
                self.fixture["bodies"]["/files/" + name] = bytes([raw[0] ^ 1]) + raw[1:]
                self.rejected()

    def test_partial_migration_missing_extra_or_unrelated_changes_are_rejected(self):
        baseline = copy.deepcopy(self.fixture)
        for mutation in ("missing", "extra", "partial", "unrelated"):
            with self.subTest(mutation=mutation):
                self.fixture = copy.deepcopy(baseline)
                files = self.fixture["listing"]["files"]
                entry = next(i for i in files if i["path"] == "demo/pattern0.ts")
                if mutation == "missing":
                    files.remove(entry)
                elif mutation == "extra":
                    files.append(entry | {"path": "demo/pattern12.ts"})
                elif mutation == "partial":
                    entry["contentType"] = "video/mp2t"
                else:
                    next(i for i in files if i["path"] == "local/swop.json")["contentType"] = "text/plain"
                self.rejected()
                self.assertEqual(len(self.reader.calls), 2)

    def test_changed_stock_destination_cannot_use_migration(self):
        stock = copy.deepcopy(self.original)
        stock["files"]["demo/pattern0.ts"]["hash"] = "0" * 64
        owner = {i["path"]: {key: i[key] for key in ("size", "hash", "contentType")}
                 for i in self.fixture["listing"]["files"]}
        reader = Reader(self.fixture)
        with self.assertRaisesRegex(ValueError, "publication controls"):
            RETAIN.check_protected(stock, owner, self.version, reader)
        self.assertEqual(reader.calls, [])

    def test_owner_drift_after_byte_checks_still_rejects_copy(self):
        self.reader = Reader(self.fixture)
        self.reader.metadata_changes[2] = self.fixture["metadata"] | {"currentVersionId": "changed"}
        with self.assertRaisesRegex(ValueError, "version changed"):
            RETAIN.create_copy(self.stage, self.output, self.receipt,
                               "ottplay", SLUG, self.version, self.reader)
        self.assertFalse(self.output.exists())
        self.assertFalse(self.receipt.exists())

    def test_already_corrected_owner_uses_normal_preservation_at_any_version(self):
        self.version = "later-accepted-version"
        self.fixture = make_owner(self.stage, version=self.version)
        result = self.prepare()
        self.assertEqual(result["changedPublicationControls"], {})
        self.assertEqual(len(result["protectedFiles"]), 19)
        self.assertFalse(any(path.startswith("/files/demo/") for path, _ in self.reader.calls))

    def test_owner_transport_only_allows_exact_demo_predecessor_paths(self):
        seen = []
        class Response(io.BytesIO):
            status = 200
            def geturl(self):
                return seen[-1].full_url
        class Opener:
            def open(self, req, timeout):
                seen.append(req)
                return Response(b"fixture")
        reader = RETAIN.OwnerReader("ottplay", SLUG, "offline-secret")
        reader.opener = Opener()
        for name in self.names:
            self.assertEqual(reader("/files/" + name, 7), b"fixture")
        for suffix in ("demo/pattern.mp4", "demo/pattern12.ts", "demo/../demo/pattern0.ts",
                       "demo/pattern0.ts?x=1", "demo/pattern.m3u8/extra", "demo/pattern00.ts"):
            with self.subTest(suffix=suffix), self.assertRaisesRegex(ValueError, "endpoint"):
                reader("/files/" + suffix, 7)
        self.assertEqual(len(seen), 13)
        self.assertTrue(all(req.get_method() == "GET" for req in seen))


if __name__ == "__main__":
    unittest.main()
