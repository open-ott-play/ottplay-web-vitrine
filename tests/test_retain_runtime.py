"""Preserve only authenticated owner graph bytes without weakening stock checks."""
import copy
import io
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch
from urllib import error, request

from retention_fixture import RETAIN, ROOT, Reader, SLUG, SWOP, VERSION, make_owner, make_stage


class RetainedRuntimeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.stage = self.root / "stock"
        make_stage(self.stage)
        self.original = RETAIN.snapshot(self.stage)
        self.owner = Reader(make_owner(self.stage))
        self.output, self.receipt = self.root / "copy", self.root / "receipt.json"

    def prepare(self):
        return RETAIN.create_copy(self.stage, self.output, self.receipt, "ottplay", SLUG, VERSION, self.owner)

    def rejected(self, message=None):
        with self.assertRaises((ValueError, SystemExit)) as caught:
            self.prepare()
        if message:
            self.assertIn(message, str(caught.exception))
        self.assertFalse(self.receipt.exists())
        self.assertFalse(self.output.exists())
        self.assertEqual(RETAIN.snapshot(self.stage), self.original)

    def test_exact_union_retains_both_graph_orders_and_leaves_stock_strict(self):
        result = self.prepare()
        self.assertEqual({item["algorithm"] for item in result["graphs"]}, {"canonical", "sorted-path"})
        self.assertEqual(len(result["addedFiles"]), 14)
        self.assertEqual(RETAIN.snapshot(self.stage), self.original)
        SWOP.verify_swop(self.stage)
        with self.assertRaises(SystemExit):
            SWOP.verify_swop(self.output)
        copied = RETAIN.snapshot(self.output)
        self.assertEqual(copied["files"], self.original["files"] | result["addedFiles"])
        self.assertEqual(result["publicationSha256"], RETAIN.sha256(RETAIN.canonical(copied)))
        self.assertEqual(json.loads(self.receipt.read_text()), result)
        self.assertEqual(self.receipt.stat().st_mode & 0o777, 0o600)
        self.assertEqual(sum(path == "" for path, _ in self.owner.calls), 3)
        self.assertEqual(sum(path == "/files" for path, _ in self.owner.calls), 2)

    def test_owner_graph_already_in_stock_is_checked_without_replacement(self):
        graph, _ = SWOP.runtime_graph(self.stage)
        fixture = make_owner(self.stage, ())
        for relative in SWOP.GRAPH_ASSETS:
            name = f"hosted-runtime/{graph}/{relative.as_posix()}"
            raw = (self.stage / name).read_bytes()
            fixture["listing"]["files"].append({"path": name, "size": len(raw), "hash": RETAIN.sha256(raw), "contentType": RETAIN.JS_MIME})
            fixture["bodies"]["/files/" + name] = raw
        self.owner = Reader(fixture)
        result = self.prepare()
        self.assertEqual(result["addedFiles"], {})
        self.assertEqual(RETAIN.snapshot(self.output), self.original)

    def test_each_owner_version_read_rejects_drift_and_pending(self):
        for count in (1, 2, 3):
            for update in ({"currentVersionId": "changed"}, {"pendingVersionId": "pending"}):
                with self.subTest(count=count, update=update):
                    self.owner = Reader(make_owner(self.stage))
                    self.owner.metadata_changes[count] = self.owner.fixture["metadata"] | update
                    self.rejected("version changed")

    def test_listing_version_pending_duplicate_path_and_duplicate_json_keys(self):
        base = make_owner(self.stage)
        for update in ({"currentVersionId": "changed"}, {"pendingVersionId": "pending"},
                       {"files": base["listing"]["files"] + [base["listing"]["files"][0]]}):
            self.owner = Reader(base)
            self.owner.fixture["listing"].update(update)
            self.rejected()
        with self.assertRaises(ValueError):
            RETAIN.decode_json(b'{"currentVersionId":"old","currentVersionId":"new"}')

    def test_final_inventory_drift_fails(self):
        original = self.owner
        count = 0
        def reader(suffix, limit):
            nonlocal count
            if suffix == "/files":
                count += 1
                if count == 2:
                    original.fixture["listing"]["files"][0]["hash"] = "0" * 64
            return original(suffix, limit)
        self.owner = reader
        self.rejected("inventory changed")

    def test_changed_or_extra_protected_file_or_mime_fails_before_download(self):
        base = make_owner(self.stage)
        for key, value in (("hash", "0" * 64), ("size", 1), ("contentType", "text/plain")):
            self.owner = Reader(base)
            self.owner.fixture["listing"]["files"][0][key] = value
            self.rejected("publication controls")
            self.assertEqual(len(self.owner.calls), 2)
        self.owner = Reader(base)
        self.owner.fixture["listing"]["files"].append({"path": "demo/extra.mp4", "size": 1, "hash": "0" * 64, "contentType": "video/mp4"})
        self.rejected("publication controls")

    def test_graph_identity_path_mime_and_size_rejections(self):
        base = make_owner(self.stage)
        index = next(i for i, item in enumerate(base["listing"]["files"]) if item["path"].startswith("hosted-runtime/"))
        for key, value in (("path", "hosted-runtime"), ("path", "hosted-runtime/../escape.js"),
                           ("path", "hosted-runtime/" + "a" * 64 + "/unknown.js"),
                           ("contentType", "text/html"), ("size", RETAIN.MAX_FILE_BYTES + 1), ("size", True)):
            self.owner = Reader(base)
            self.owner.fixture["listing"]["files"][index][key] = value
            self.rejected()
        self.owner = Reader(base)
        self.owner.fixture["listing"]["files"].pop(index)
        self.rejected("seven")

    def test_graph_byte_and_directory_hash_rejections(self):
        self.owner.fixture["bodies"][next(iter(self.owner.fixture["bodies"]))] += b"tampered"
        self.rejected("byte digest")
        base = make_owner(self.stage)
        original = base["graphs"][0]
        for item in base["listing"]["files"]:
            item["path"] = item["path"].replace(original, "0" * 64)
        base["bodies"] = {name.replace(original, "0" * 64): raw for name, raw in base["bodies"].items()}
        self.owner = Reader(base)
        self.rejected("name does not match")

    def test_graph_import_rules_are_reused(self):
        fixture = make_owner(self.stage)
        name = next(name for name in fixture["bodies"] if name.endswith("epg-server.js"))
        raw = b'importScripts("/mutable.js");'
        fixture["bodies"][name] = raw
        entry = next(item for item in fixture["listing"]["files"] if "/files/" + item["path"] == name)
        entry.update(size=len(raw), hash=RETAIN.sha256(raw))
        self.owner = Reader(fixture)
        self.rejected("self-contained")

    def test_graph_count_and_byte_caps_fail_closed(self):
        for name, value in (("MAX_GRAPHS", 1), ("MAX_GRAPH_BYTES", 1), ("MAX_TOTAL_BYTES", 1)):
            with patch.object(RETAIN, name, value):
                self.rejected("bound")

    def test_stock_symlink_and_special_entries_fail_before_owner_request(self):
        link = self.stage / "other.js"
        link.symlink_to(self.stage / "dist/player.js")
        with self.assertRaisesRegex(ValueError, "symlink"):
            self.prepare()
        link.unlink()
        if hasattr(os, "mkfifo"):
            os.mkfifo(link)
            with self.assertRaisesRegex(ValueError, "special"):
                self.prepare()
            link.unlink()
        self.assertEqual(self.owner.calls, [])

    def test_copy_failure_removes_partial_owned_output(self):
        def fail_copy(source, destination, **kwargs):
            (destination / "partial").write_text("partial")
            raise OSError("injected copy failure")
        with patch.object(RETAIN.shutil, "copytree", fail_copy), self.assertRaises(OSError):
            self.prepare()
        self.assertFalse(self.output.exists())
        self.assertFalse(self.receipt.exists())
        self.assertEqual(RETAIN.snapshot(self.stage), self.original)

    def test_copy_cannot_add_an_unattested_file(self):
        original = shutil.copytree
        def inject(source, destination, *args, **kwargs):
            original(source, destination, *args, **kwargs)
            if Path(destination) == self.output:
                (Path(destination) / "unattested.js").write_text("extra")
        with patch.object(RETAIN.shutil, "copytree", inject):
            self.rejected("attested graph additions")

    def test_existing_output_and_receipt_inside_stage_are_rejected(self):
        self.output.mkdir()
        with self.assertRaisesRegex(ValueError, "new paths"):
            self.prepare()
        self.output.rmdir()
        self.receipt = self.stage / "receipt.json"
        with self.assertRaisesRegex(ValueError, "outside"):
            self.prepare()
        self.assertEqual(self.owner.calls, [])


class OwnerReaderTests(unittest.TestCase):
    def test_redirect_handler_refuses_every_target(self):
        handler = RETAIN.NoRedirect()
        self.assertIsNone(handler.redirect_request(None, None, 302, "redirect", {}, "https://untrusted.example/asset"))

    def test_get_only_exact_owner_endpoint_and_bounded_read(self):
        seen = []
        class Response(io.BytesIO):
            status = 200
            def geturl(self):
                return seen[-1].full_url
        class Opener:
            def open(self, req, timeout):
                seen.append(req)
                self_timeout = timeout
                if self_timeout != 30:
                    raise AssertionError("wrong timeout")
                return Response(b"12345")
        reader = RETAIN.OwnerReader("ottplay", SLUG, "offline-secret")
        reader.opener = Opener()
        self.assertEqual(reader("/files/hosted-runtime/" + "a" * 64 + "/hosted/sax.js", 5), b"12345")
        self.assertEqual(seen[0].get_method(), "GET")
        self.assertTrue(seen[0].full_url.startswith("https://here.now/api/v1/publish/" + SLUG + "/files/hosted-runtime/"))
        self.assertEqual(seen[0].get_header("Authorization"), "Bearer offline-secret")
        with self.assertRaisesRegex(ValueError, "size bound"):
            reader("", 4)
        with self.assertRaisesRegex(ValueError, "endpoint"):
            reader("https://untrusted.example", 5)

    def test_owner_exception_cannot_expose_credentials(self):
        reader = RETAIN.OwnerReader("ottplay", SLUG, "offline-secret")
        with patch.object(reader.opener, "open", side_effect=error.URLError("offline-secret")):
            with self.assertRaises(ValueError) as caught:
                reader("", 1)
        self.assertNotIn("offline-secret", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
