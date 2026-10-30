"""Preserve only authenticated owner graph bytes without weakening stock checks."""
import copy
import io
import json
import os
from pathlib import Path
import shutil
import ssl
import tempfile
import traceback
import unittest
from unittest.mock import patch
from urllib import error, request

from retention_fixture import RETAIN, ROOT, Reader, SLUG, SWOP, VERSION, add_discovery, make_owner, make_stage


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

    def add_current_graph(self, fixture):
        graph, _ = SWOP.runtime_graph(self.stage)
        for relative in SWOP.GRAPH_ASSETS:
            name = f"hosted-runtime/{graph}/{relative.as_posix()}"
            raw = (self.stage / name).read_bytes()
            fixture["listing"]["files"].append({"path": name, "size": len(raw), "hash": RETAIN.sha256(raw), "contentType": RETAIN.JS_MIME})
            fixture["bodies"]["/files/" + name] = raw

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
        fixture = make_owner(self.stage, ())
        self.add_current_graph(fixture)
        self.owner = Reader(fixture)
        graph_bytes = sum(item["size"] for item in fixture["listing"]["files"] if item["path"].startswith("hosted-runtime/"))
        with patch.object(RETAIN, "MAX_TOTAL_BYTES", graph_bytes):
            result = self.prepare()
        self.assertEqual(result["addedFiles"], {})
        self.assertEqual(RETAIN.snapshot(self.output), self.original)

    def test_final_graph_count_accepts_32_with_new_or_shared_stock_graph(self):
        for shared in (False, True):
            with self.subTest(shared=shared):
                fixture = make_owner(self.stage, ("canonical",) * 31)
                if shared:
                    self.add_current_graph(fixture)
                self.owner = Reader(fixture)
                self.output = self.root / f"copy-{shared}"
                self.receipt = self.root / f"receipt-{shared}.json"
                result = self.prepare()
                graphs = {name.split("/")[1] for name in result["files"] if name.startswith("hosted-runtime/")}
                self.assertEqual(len(graphs), 32)
                self.assertEqual(len(result["addedFiles"]), 31 * len(SWOP.GRAPH_ASSETS))
                self.assertEqual(RETAIN.snapshot(self.stage), self.original)

    def test_new_stock_graph_over_count_limit_fails_before_download_or_directory_creation(self):
        self.owner = Reader(make_owner(self.stage, ("canonical",) * 32))
        self.output = self.root / "not-created" / "copy"
        self.rejected("count exceeds")
        self.assertEqual(len(self.owner.calls), 2)
        self.assertFalse(self.output.parent.exists())

    def test_final_graph_byte_limit_includes_new_stock_and_accepts_exact_boundary(self):
        owner_bytes = sum(item["size"] for item in self.owner.fixture["listing"]["files"] if item["path"].startswith("hosted-runtime/"))
        stock_bytes = sum(item["size"] for name, item in self.original["files"].items() if name.startswith("hosted-runtime/"))
        self.output = self.root / "not-created" / "copy"
        with patch.object(RETAIN, "MAX_TOTAL_BYTES", owner_bytes + stock_bytes - 1):
            self.rejected("total byte bound")
        self.assertEqual(len(self.owner.calls), 2)
        self.assertFalse(self.output.parent.exists())
        with patch.object(RETAIN, "MAX_TOTAL_BYTES", owner_bytes + stock_bytes):
            result = self.prepare()
        self.assertEqual(len(result["addedFiles"]), 2 * len(SWOP.GRAPH_ASSETS))

    def test_shared_graph_identity_mismatch_fails_before_download(self):
        fixture = make_owner(self.stage, ())
        self.add_current_graph(fixture)
        fixture["listing"]["files"][-1]["hash"] = "0" * 64
        self.owner = Reader(fixture)
        self.rejected("collides with the new release bytes")
        self.assertEqual(len(self.owner.calls), 2)

    def test_old_and_current_discovery_are_verified_and_only_old_is_added(self):
        fixture = make_owner(self.stage)
        previous = add_discovery(fixture)
        current = add_discovery(fixture, raw=(self.stage / SWOP.CONTROL_DISCOVERY_PATH).read_bytes())
        self.owner = Reader(fixture)
        result = self.prepare()
        self.assertEqual(len(result["addedFiles"]), 15)
        self.assertEqual({item["path"] for item in result["discoveryManifests"]}, {previous["path"], current["path"]})
        self.assertIn(previous["path"], result["addedFiles"])
        self.assertNotIn(current["path"], result["addedFiles"])
        self.assertEqual((self.output / previous["path"]).read_bytes(), fixture["bodies"]["/files/" + previous["path"]])
        self.assertEqual((self.output / current["path"]).read_bytes(), (self.stage / current["path"]).read_bytes())
        self.assertEqual(RETAIN.snapshot(self.stage), self.original)
        self.assertEqual(RETAIN.snapshot(self.output)["files"], self.original["files"] | result["addedFiles"])
        self.assertIn(("/files/" + previous["path"], previous["size"]), self.owner.calls)

    def test_invalid_discovery_inventory_is_rejected_before_file_downloads(self):
        fixture = make_owner(self.stage)
        add_discovery(fixture)
        for key, value in (("path", "local/control-discovery.json"), ("path", "local/control-discovery." + "A" * 64 + ".json"),
                           ("path", "local/control-discovery." + "a" * 64 + ".json/extra"),
                           ("contentType", "text/html"), ("size", SWOP.MAX_DISCOVERY_BYTES + 1),
                           ("hash", "0" * 64)):
            self.owner = Reader(fixture)
            self.owner.fixture["listing"]["files"][-1][key] = value
            with self.subTest(key=key, value=value):
                self.rejected()
                self.assertEqual(len(self.owner.calls), 2)

    def test_retained_discovery_bytes_schema_and_canonical_format_are_checked(self):
        invalid = [b'{"version":1,"servers":[]}', b'{"version":1,"version":1,"servers":[]}']
        invalid.append((json.dumps({"version": 1, "servers": [], "token": "must-not-publish"}, sort_keys=True, indent=2) + "\n").encode())
        invalid.append((json.dumps({"version": 1, "servers": [{"id": "home._ottplay-ctrl._tcp.home.example.",
                        "domain": "home.example", "address": "https://secret@controller.example.com"}]}, sort_keys=True, indent=2) + "\n").encode())
        for raw in invalid:
            fixture = make_owner(self.stage, ())
            add_discovery(fixture, raw=raw)
            self.owner = Reader(fixture)
            with self.subTest(raw=raw):
                self.rejected("Control discovery")
        fixture = make_owner(self.stage, ())
        item = add_discovery(fixture)
        fixture["bodies"]["/files/" + item["path"]] = b" " * item["size"]
        self.owner = Reader(fixture)
        self.rejected("content digest")

    def test_retained_discovery_count_includes_new_current_and_never_prunes(self):
        fixture = make_owner(self.stage, ())
        for count in range(RETAIN.MAX_DISCOVERIES):
            add_discovery(fixture, {"version": 1, "servers": [{
                "id": f"previous{count}._ottplay-ctrl._tcp.home.example.", "domain": "home.example",
                "address": f"https://controller{count}.example.com/ott-control"}]})
        self.owner = Reader(fixture)
        self.rejected("count exceeds")
        self.assertEqual(len(self.owner.calls), 2)

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
    def test_discovery_owner_reads_only_accept_exact_fingerprinted_path(self):
        seen = []
        class Response(io.BytesIO):
            status = 200
            def geturl(self):
                return seen[-1].full_url
        class Opener:
            def open(self, req, timeout):
                seen.append(req)
                return Response(b"{}")
        reader = RETAIN.OwnerReader("ottplay", SLUG, "offline-secret")
        reader.opener = Opener()
        suffix = "/files/local/control-discovery." + "a" * 64 + ".json"
        self.assertEqual(reader(suffix, 2), b"{}")
        for path in (suffix + "?token=other", suffix + "/extra", suffix.replace("a" * 64, "A" * 64),
                     "/files/local/control-discovery.json", "/files/local/../control-discovery." + "a" * 64 + ".json"):
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, "endpoint"):
                reader(path, 2)
        self.assertEqual(len(seen), 1)

    def test_only_the_exact_proxy_predecessor_can_be_read_for_migration(self):
        seen = []
        class Response(io.BytesIO):
            status = 200
            def geturl(self):
                return seen[-1].full_url
        class Opener:
            def open(self, req, timeout):
                seen.append(req)
                return Response(b"proxy-bytes")
        reader = RETAIN.OwnerReader("ottplay", SLUG, "offline-secret")
        reader.opener = Opener()
        self.assertEqual(reader("/files/.herenow/proxy.json", 11), b"proxy-bytes")
        self.assertEqual(seen[0].get_method(), "GET")
        self.assertEqual(seen[0].full_url, reader.base + "/files/.herenow/proxy.json")
        for path in ("/files/.herenow/data.json", "/files/.herenow/proxy.json?other=1",
                     "/files/../.herenow/proxy.json", "/files/.herenow/proxy.json/extra"):
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, "endpoint"):
                reader(path, 11)
        self.assertEqual(len(seen), 1)

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

    def test_timeout_retry_keeps_exact_request_and_discards_partial_body(self):
        for failure in (TimeoutError("offline-secret"), error.URLError(TimeoutError("offline-secret"))):
            for partial in (False, True):
                with self.subTest(failure=type(failure).__name__, partial=partial):
                    reader = RETAIN.OwnerReader("ottplay", SLUG, "offline-secret")
                    seen, responses = [], []
                    class Response(io.BytesIO):
                        status = 200
                        def geturl(self):
                            return seen[-1].full_url
                        def read(self, size):
                            if partial and len(seen) == 1:
                                super().read(2)
                                raise failure
                            return super().read(size)
                    class Opener:
                        def open(self, req, timeout):
                            seen.append(req)
                            if timeout != 30:
                                raise AssertionError("wrong timeout")
                            if not partial and len(seen) == 1:
                                raise failure
                            response = Response(b"fresh")
                            responses.append(response)
                            return response
                    reader.opener = Opener()
                    with patch.object(RETAIN.time, "sleep") as sleep:
                        self.assertEqual(reader("", 5), b"fresh")
                    self.assertEqual(len(seen), 2)
                    self.assertIs(seen[0], seen[1])
                    self.assertEqual(seen[0].get_method(), "GET")
                    self.assertEqual(seen[0].full_url, reader.base)
                    self.assertEqual(seen[0].get_header("Authorization"), "Bearer offline-secret")
                    self.assertTrue(all(response.closed for response in responses))
                    sleep.assert_called_once_with(0.25)

    def test_timeout_retries_are_bounded_and_sanitized(self):
        reader = RETAIN.OwnerReader("ottplay", SLUG, "offline-secret")
        with patch.object(reader.opener, "open", side_effect=TimeoutError("offline-secret")) as opened:
            with patch.object(RETAIN.time, "sleep") as sleep:
                with self.assertRaises(ValueError) as caught:
                    reader("", 1)
        self.assertEqual(opened.call_count, 3)
        self.assertEqual([call.args for call in sleep.call_args_list], [(0.25,), (0.5,)])
        self.assertNotIn("offline-secret", str(caught.exception))

    def test_interrupted_backoff_does_not_expose_timeout_context(self):
        reader = RETAIN.OwnerReader("ottplay", SLUG, "offline-secret")
        with patch.object(reader.opener, "open", side_effect=TimeoutError("offline-secret")) as opened:
            with patch.object(RETAIN.time, "sleep", side_effect=KeyboardInterrupt):
                with self.assertRaises(KeyboardInterrupt) as caught:
                    reader("", 1)
        self.assertEqual(opened.call_count, 1)
        self.assertIsNone(caught.exception.__context__)
        self.assertNotIn("offline-secret", "".join(traceback.format_exception(caught.exception)))

    def test_non_timeout_failures_never_retry(self):
        failures = [
            error.HTTPError("https://here.now/owner", code, "offline-secret", {}, None)
            for code in (301, 302, 307, 401, 403, 429, 500, 503)
        ] + [error.URLError(ssl.SSLCertVerificationError("offline-secret")),
             error.URLError("timed out: offline-secret"),
             ConnectionResetError("offline-secret"), ValueError("offline-secret")]
        for failure in failures:
            with self.subTest(failure=type(failure).__name__, code=getattr(failure, "code", None)):
                reader = RETAIN.OwnerReader("ottplay", SLUG, "offline-secret")
                with patch.object(reader.opener, "open", side_effect=failure) as opened:
                    with patch.object(RETAIN.time, "sleep") as sleep:
                        with self.assertRaises(ValueError) as caught:
                            reader("", 1)
                self.assertEqual(opened.call_count, 1)
                sleep.assert_not_called()
                self.assertNotIn("offline-secret", str(caught.exception))

    def test_owner_exception_cannot_expose_credentials(self):
        reader = RETAIN.OwnerReader("ottplay", SLUG, "offline-secret")
        with patch.object(reader.opener, "open", side_effect=error.URLError("offline-secret")):
            with self.assertRaises(ValueError) as caught:
                reader("", 1)
        self.assertNotIn("offline-secret", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
