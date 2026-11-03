"""Offline checks for the fixed, credential-free discovery publication probe."""
import contextlib
from email.message import Message
import importlib.util
import io
import json
from pathlib import Path
import shutil
import ssl
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
SPEC = importlib.util.spec_from_file_location("control_discovery_probe", ROOT / "scripts/check-control-discovery.py")
PROBE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROBE)


class Response:
    def __init__(self, body, *, status=200, url=None, content_type="application/json; charset=utf-8"):
        self.body = body
        self.status = status
        self.url = url or PROBE.CONTROL_PUBLIC_DISCOVERY_URL
        self.headers = Message()
        if content_type is not None:
            self.headers["Content-Type"] = content_type
        self.read_limits = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def geturl(self):
        return self.url

    def read(self, limit):
        self.read_limits.append(limit)
        return self.body[:limit]


class ControlDiscoveryProbeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.stage = Path(temporary.name)
        self.catalog_path = self.stage / PROBE.CONTROL_DISCOVERY_PATH
        self.catalog_path.parent.mkdir()
        shutil.copyfile(ROOT / "static" / PROBE.CONTROL_DISCOVERY_PATH, self.catalog_path)
        self.catalog = json.loads(self.catalog_path.read_bytes())
        self.value = self.catalog | {"pairing_url": PROBE.CONTROL_API_BASE + "/api/pairings"}

    def raw(self, value=None):
        return json.dumps(self.value if value is None else value).encode()

    def rejects(self, response, reason, *, upstream=False):
        transport = Mock(side_effect=response if isinstance(response, Exception) else None,
                         return_value=response)
        with self.assertRaises(PROBE.ProbeError) as caught:
            PROBE.check(self.stage, upstream=upstream, open_url=transport)
        self.assertEqual(caught.exception.diagnostic["reason"], reason)
        self.assertEqual(transport.call_count, 1, "failed GET must not trigger retries or other operations")
        return caught.exception

    def test_public_and_upstream_are_fixed_credential_free_gets(self):
        before = self.catalog_path.read_bytes()
        for upstream in (False, True):
            with self.subTest(upstream=upstream):
                url = PROBE.CONTROL_API_BASE + "/api/discovery" if upstream else PROBE.CONTROL_PUBLIC_DISCOVERY_URL
                response = Response(self.raw(), url=url)
                transport = Mock(return_value=response)
                result = PROBE.check(self.stage, upstream=upstream, open_url=transport)
                self.assertEqual(result, {"status": "ok", "endpoint": "upstream" if upstream else "public",
                                         "catalog_sha256": PROBE.CONTROL_DISCOVERY_SHA256,
                                         "response_bytes": len(self.raw()), "server_count": 1})
                request = transport.call_args.args[0]
                self.assertEqual(request.full_url, url)
                self.assertEqual(request.get_method(), "GET")
                self.assertIsNone(request.data)
                self.assertEqual({key.lower(): value for key, value in request.header_items()}, {
                    "accept": "application/json", "user-agent": "OTT-play-control-discovery/1.0"})
                self.assertEqual(transport.call_args.kwargs, {"timeout": PROBE.HTTP_TIMEOUT_SECONDS})
                self.assertEqual(response.read_limits, [8193])
                self.assertEqual(transport.call_count, 1)
        self.assertEqual(self.catalog_path.read_bytes(), before)

    def test_former_html_success_is_rejected(self):
        self.rejects(Response(b"<html>player</html>", content_type="text/html"), "unexpected_content_type")
        self.rejects(Response(b"<html>player</html>"), "invalid_schema")

    def test_content_type_must_be_one_json_header(self):
        for value in (None, "text/plain", "application/jsonp", "text/html; application/json", "application/problem+json"):
            with self.subTest(value=value):
                self.rejects(Response(self.raw(), content_type=value), "unexpected_content_type")
        response = Response(self.raw())
        response.headers["Content-Type"] = "application/json"
        self.rejects(response, "unexpected_content_type")

    def test_non_success_and_upstream_503_are_rejected_without_body_read(self):
        for status in (201, 204, 400, 401, 403, 404, 429, 500, 503):
            with self.subTest(status=status):
                response = Response(b"private upstream details", status=status)
                error = self.rejects(response, "http_status", upstream=True)
                self.assertEqual(error.diagnostic["http_status"], status)
                self.assertEqual(response.read_limits, [])

    def test_redirect_status_and_changed_final_url_are_rejected(self):
        for status in (301, 302, 303, 307, 308):
            self.rejects(Response(b"", status=status), "redirect_rejected")
        self.rejects(Response(self.raw(), url="https://untrusted.invalid/?secret=private"), "redirect_rejected")
        self.assertIsNone(PROBE.NoRedirect().redirect_request(None, None, 302, "", {}, "https://untrusted.invalid"))

    def test_http_errors_are_closed_and_do_not_expose_bodies_or_redirect_urls(self):
        for status, reason in ((302, "redirect_rejected"), (503, "http_status")):
            stream = io.BytesIO(b"secret-body")
            error = HTTPError("https://private.invalid/?token=secret", status, "private-message", Message(), stream)
            caught = self.rejects(error, reason)
            self.assertTrue(stream.closed)
            self.assertNotIn("secret", json.dumps(caught.diagnostic))
            self.assertNotIn("private", str(caught))

    def test_transport_failure_is_sanitized(self):
        for error in (URLError("secret upstream URL"), TimeoutError("private endpoint"), ssl.SSLError("secret")):
            caught = self.rejects(error, "transport_failed")
            self.assertEqual(caught.diagnostic, {"status": "failed", "reason": "transport_failed"})

    def test_response_byte_limit_and_empty_body(self):
        for raw in (b"", b" " * 8193):
            self.rejects(Response(raw), "response_size")
        padded = self.raw() + b" " * (8192 - len(self.raw()))
        self.assertEqual(PROBE.check(self.stage, open_url=Mock(return_value=Response(padded)))["response_bytes"], 8192)

    def test_duplicate_keys_invalid_utf8_constants_and_trailing_json_are_rejected(self):
        duplicate = self.raw().replace(b'"version": 1', b'"version": 1, "version": 1')
        nested = self.raw().replace(b'"domain": "alvit.cf"', b'"domain": "alvit.cf", "domain": "alvit.cf"')
        for raw in (duplicate, nested, b'{"version":NaN}', b'{"version":Infinity}'):
            self.rejects(Response(raw), "invalid_json")
        for raw in (b"\xff", self.raw() + b"{}"):
            self.rejects(Response(raw), "invalid_schema")

    def test_exact_schema_and_version_are_required(self):
        for value in ([], self.catalog, self.value | {"extra": 1},
                      self.value | {"version": True}, self.value | {"version": 1.0},
                      self.value | {"version": 2}, self.value | {"servers": None}):
            with self.subTest(value=value):
                self.rejects(Response(self.raw(value)), "invalid_schema")

    def test_server_descriptor_must_match_pinned_catalog(self):
        for key, value in (("address", "https://player.ottplay.here.now/ott-control"),
                           ("domain", "example.com"), ("id", "other._ottplay-ctrl._tcp.alvit.cf.")):
            changed = json.loads(self.raw())
            changed["servers"][0][key] = value
            reason = "invalid_schema" if key == "domain" else "catalog_mismatch"
            self.rejects(Response(self.raw(changed)), reason)
        self.rejects(Response(self.raw(self.value | {"servers": []})), "catalog_mismatch")
        self.rejects(Response(self.raw(self.value | {"servers": self.value["servers"] * 2})), "invalid_schema")

    def test_pairing_url_is_exact_canonical_endpoint(self):
        for url in (None, 1, PROBE.CONTROL_PUBLIC_DISCOVERY_URL.replace("discovery", "pairings"),
                    PROBE.CONTROL_API_BASE + "/api/pairings?token=private",
                    PROBE.CONTROL_API_BASE + "/api/pairings/", "https://untrusted.invalid/api/pairings"):
            self.rejects(Response(self.raw(self.value | {"pairing_url": url})), "pairing_url_mismatch")

    def test_invalid_stage_is_rejected_before_any_http(self):
        transport = Mock()
        self.catalog_path.write_bytes(self.catalog_path.read_bytes() + b" ")
        with self.assertRaisesRegex(PROBE.ProbeError, "invalid_stage_catalog"):
            PROBE.check(self.stage, open_url=transport)
        transport.assert_not_called()

    def test_missing_or_extra_stage_catalog_is_rejected_before_any_http(self):
        transport = Mock()
        extra = self.catalog_path.parent / ("control-discovery." + "a" * 64 + ".json")
        extra.write_bytes(b"{}")
        with self.assertRaisesRegex(PROBE.ProbeError, "invalid_stage_catalog"):
            PROBE.check(self.stage, open_url=transport)
        extra.unlink()
        self.catalog_path.unlink()
        with self.assertRaisesRegex(PROBE.ProbeError, "invalid_stage_catalog"):
            PROBE.check(self.stage, open_url=transport)
        transport.assert_not_called()

    def test_default_opener_verifies_tls_and_disables_redirects(self):
        response = Response(self.raw())
        opener = Mock()
        opener.open.return_value = response
        with patch.object(PROBE, "build_opener", return_value=opener) as build:
            PROBE.check(self.stage)
        handlers = build.call_args.args
        self.assertIsInstance(handlers[0], PROBE.NoRedirect)
        context = handlers[1]._context
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)
        self.assertGreaterEqual(context.minimum_version, ssl.TLSVersion.TLSv1_2)

    def test_cli_reports_only_safe_metadata_and_rejects_abbreviations(self):
        success = {"status": "ok", "endpoint": "upstream"}
        output = io.StringIO()
        with patch.object(PROBE, "check", return_value=success) as check, contextlib.redirect_stdout(output):
            self.assertEqual(PROBE.main([str(self.stage), "--upstream"]), 0)
        check.assert_called_once_with(self.stage, upstream=True)
        self.assertEqual(json.loads(output.getvalue()), success)
        error_output = io.StringIO()
        with patch.object(PROBE, "check", side_effect=PROBE.ProbeError("http_status", 503)), contextlib.redirect_stderr(error_output):
            self.assertEqual(PROBE.main([str(self.stage)]), 1)
        self.assertEqual(json.loads(error_output.getvalue()), {"status": "failed", "reason": "http_status", "http_status": 503})
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
            PROBE.main([str(self.stage), "--up"])
        self.assertEqual(caught.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
