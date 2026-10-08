"""Operator smoke checks must use the bounded v1 API, never the old full feed."""
import copy
from email.message import Message
import io
import json
from pathlib import Path
import runpy
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit
from urllib.request import HTTPSHandler, build_opener
from urllib.response import addinfourl


EPG = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts/check-epg.py"))
check = EPG["check"]
check_current = EPG["check_current"]
EpgHttpError = EPG["EpgHttpError"]


class Response(io.BytesIO):
    def __init__(self, value, content_type="application/json"):
        super().__init__(json.dumps(value).encode())
        self.headers = {"Content-Type": content_type}
        self.status = 200


class EpgSmokeTests(unittest.TestCase):
    def setUp(self):
        self.requests = []
        metadata = {"version": 1, "source": "epg-one", "generation": "opaque-generation",
                    "fetchedAt": 100000, "refreshMs": 7200000, "stale": False}
        self.match = {**metadata, "mappings": {"ren-hd": {"channelId": "18", "shift": 0, "logo": ""}}}
        self.guide = {**metadata, "rows": [
            {"time": 10, "time_to": 90, "name": "Archive", "descr": "Old programme", "icon": ""},
            {"time": 90, "time_to": 110, "name": "Current", "descr": "Current description", "icon": ""},
        ]}

    def run_check(self, content_type="application/json"):
        def open_request(request, timeout):
            self.requests.append(request)
            self.assertEqual(timeout, 30)
            return Response(self.match if request.get_method() == "POST" else self.guide, content_type)
        with patch.dict(check.__globals__, {"urlopen": open_request}), patch("time.time", return_value=100):
            return check("https://player.example")

    def test_only_name_metadata_is_posted_and_generation_is_bound_to_guide(self):
        result = self.run_check()
        self.assertEqual(result["programmes"], 2)
        self.assertEqual(result["archived"], 1)
        self.assertEqual(result["current"], "Current")
        match, guide = self.requests
        self.assertEqual(match.get_header("Cache-control"), "no-cache")
        self.assertEqual(guide.get_header("Cache-control"), "no-cache")
        self.assertEqual(match.full_url, "https://player.example/epg/v1/match")
        self.assertEqual(json.loads(match.data), {"version": 1, "source": "epg-one", "channels": [
            {"id": "ren-hd", "tvgId": "hlsproxy-382", "tvgName": "", "name": "РЕН ТВ HD"}]})
        self.assertEqual(urlsplit(guide.full_url).path, "/epg/v1/programmes")
        self.assertEqual(parse_qs(urlsplit(guide.full_url).query),
                         {"channelId": ["18"], "shift": ["0"], "hours": ["0"], "generation": ["opaque-generation"]})

    def test_rejects_html_generation_mismatch_and_incomplete_current_guide(self):
        with self.assertRaisesRegex(ValueError, "did not return JSON"):
            self.run_check("text/html")
        original = copy.deepcopy(self.guide)
        for changed, message in (({**original, "generation": "other"}, "generation changed"),
                                 ({**original, "rows": []}, "empty"),
                                 ({**original, "rows": [original["rows"][0]]}, "no current"),
                                 ({**original, "rows": [original["rows"][1]]}, "no archived")):
            self.guide = changed
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                self.run_check()

    def test_does_not_send_requests_to_credentialed_or_non_origin_urls(self):
        for base in ("http://player.example", "https://user:password@player.example",
                     "https://player.example/path", "https://player.example?source=feed"):
            with self.subTest(base=base), self.assertRaisesRegex(ValueError, "HTTPS origin"):
                check(base)

    def test_rejects_stale_match_and_stale_programmes(self):
        for response in (self.match, self.guide):
            response["stale"] = True
            with self.subTest(response=response), self.assertRaisesRegex(ValueError, "stale generation"):
                self.run_check()
            response["stale"] = False

    def fail_check(self, status, headers, body, stage="match"):
        self.requests = []
        stream = io.BytesIO(body)
        error = HTTPError("https://redirect.invalid/private?token=DO_NOT_LOG", status,
                          "DO_NOT_LOG", headers, stream)
        reads = []
        original_read = error.read

        def bounded_read(size):
            reads.append(size)
            return original_read(size)

        error.read = bounded_read

        def open_request(request, timeout):
            self.requests.append(request)
            self.assertEqual(timeout, 30)
            if stage == "programmes" and request.get_method() == "POST":
                return Response(self.match)
            raise error

        with patch.dict(check.__globals__, {"urlopen": open_request}):
            with self.assertRaises(EpgHttpError) as raised:
                check("https://player.example")
        self.assertTrue(stream.closed)
        self.assertNotIn("DO_NOT_LOG", str(raised.exception))
        self.assertNotIn("redirect.invalid", str(raised.exception))
        self.assertNotIn("opaque-generation", str(raised.exception))
        self.assertLess(len(str(raised.exception)), 2048)
        return raised.exception, reads

    def test_match_403_reports_stage_without_retry_or_private_response_data(self):
        headers = Message()
        for key, value in {"Content-Type": "text/html; token=DO_NOT_LOG",
                           "Server": "cloudflare", "Set-Cookie": "DO_NOT_LOG",
                           "Authorization": "Bearer DO_NOT_LOG",
                           "Location": "https://secret.invalid/?token=DO_NOT_LOG"}.items():
            headers[key] = value
        error, reads = self.fail_check(403, headers, b"<html>DO_NOT_LOG</html>")
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(reads, [])
        self.assertEqual(error.diagnostic, {
            "stage": "match", "method": "POST", "host": "player.example",
            "path": "/epg/v1/match", "http_status": 403, "reason": "access_denied",
            "headers": {"Content-Type": "text/html", "Server": "cloudflare"}})

    def test_programmes_403_reports_original_path_without_query_or_retry(self):
        error, _ = self.fail_check(403, {}, b"DO_NOT_LOG", stage="programmes")
        self.assertEqual(len(self.requests), 2)
        self.assertEqual(error.diagnostic["stage"], "programmes")
        self.assertEqual(error.diagnostic["method"], "GET")
        self.assertEqual(error.diagnostic["path"], "/epg/v1/programmes")
        self.assertNotIn("?", str(error))
        self.assertNotIn("generation=", str(error))

    def test_cloudflare_challenge_has_only_bounded_recognized_headers(self):
        error, _ = self.fail_check(403, {
            "Content-Type": "text/html", "Server": "cloudflare",
            "CF-Ray": "a474af630ba32c2d-SJC", "CF-Mitigated": "challenge",
            "Retry-After": "5", "CF-Access-Jwt-Assertion": "DO_NOT_LOG",
        }, b"DO_NOT_LOG")
        self.assertEqual(error.diagnostic["reason"], "access_denied")
        self.assertEqual(error.diagnostic["headers"], {
            "Content-Type": "text/html", "Server": "cloudflare",
            "CF-Ray": "a474af630ba32c2d-SJC", "CF-Mitigated": "challenge", "Retry-After": "5"})

    def test_json_readiness_and_generation_codes_are_recognized_without_raw_body(self):
        for status, code, reason in ((503, "EPG_NOT_READY", "epg_not_ready"),
                                     (409, "EPG_GENERATION", "epg_generation_changed")):
            with self.subTest(status=status):
                body = json.dumps({"version": 1, "source": "epg-one",
                                   "error": {"code": code, "private": "DO_NOT_LOG"}}).encode()
                error, reads = self.fail_check(status, {"Content-Type": "application/json"}, body)
                self.assertEqual(error.diagnostic["epg_code"], code)
                self.assertEqual(error.diagnostic["reason"], reason)
                self.assertEqual(reads, [EPG["MAX_ERROR_RESPONSE_BYTES"] + 1])
                self.assertEqual(len(self.requests), 1)

    def test_error_code_parser_rejects_ambiguous_unknown_mismatched_and_large_json(self):
        valid = {"version": 1, "source": "epg-one", "error": {"code": "EPG_NOT_READY"}}
        bad_bodies = [
            {**valid, "error": {"code": "DO_NOT_LOG"}},
            {**valid, "source": "DO_NOT_LOG"},
            {**valid, "version": True},
            {**valid, "error": ["EPG_NOT_READY"]},
            b'{"version":1,"source":"epg-one","error":{"code":"DO_NOT_LOG","code":"EPG_NOT_READY"}}',
            b'{"version":1,"source":"epg-one","error":{"code":"EPG_NOT_READY"},"private":NaN}',
            b'{"version":1,"source":"epg-one","error":{"code":"EPG_NOT_READY"},"private":"' + b"x" * 4096 + b'"}',
            b'\xff',
        ]
        for body in bad_bodies:
            if not isinstance(body, bytes):
                body = json.dumps(body).encode()
            with self.subTest(body_length=len(body)):
                error, reads = self.fail_check(503, {"Content-Type": "application/json"}, body)
                self.assertNotIn("epg_code", error.diagnostic)
                self.assertEqual(error.diagnostic["reason"], "http_error")
                self.assertEqual(reads, [4097])
        error, _ = self.fail_check(403, {"Content-Type": "application/json"}, json.dumps(valid).encode())
        self.assertNotIn("epg_code", error.diagnostic)
        self.assertEqual(error.diagnostic["reason"], "access_denied")

    def test_untrusted_oversized_duplicate_and_control_headers_are_omitted(self):
        headers = Message()
        headers["Content-Type"] = "application/json"
        headers["Content-Type"] = "text/html"
        headers["Server"] = "cloudflare;token=DO_NOT_LOG"
        headers["CF-Ray"] = "a" * 1000
        headers["CF-Mitigated"] = "challenge\r\nDO_NOT_LOG"
        headers["Retry-After"] = "https://secret.invalid/?token=DO_NOT_LOG"
        error, reads = self.fail_check(403, headers, b"DO_NOT_LOG")
        self.assertEqual(error.diagnostic["headers"], {})
        self.assertEqual(reads, [])

    def test_retry_after_date_is_metadata_not_an_instruction_to_retry(self):
        error, _ = self.fail_check(503, {"Retry-After": "Thu, 08 Oct 2026 11:06:35 GMT"}, b"")
        self.assertEqual(error.diagnostic["headers"]["Retry-After"], "Thu, 08 Oct 2026 11:06:35 GMT")
        self.assertEqual(len(self.requests), 1)

    def test_http_failure_still_exits_nonzero_with_safe_cli_diagnostic(self):
        path = str(Path(__file__).resolve().parents[1] / "scripts/check-epg.py")
        error = HTTPError("https://secret.invalid/?token=DO_NOT_LOG", 403, "DO_NOT_LOG",
                          {"Content-Type": "text/html"}, io.BytesIO(b"DO_NOT_LOG"))
        with patch("sys.argv", [path, "https://player.example"]), patch("urllib.request.OpenerDirector.open", side_effect=error) as opener:
            with self.assertRaises(SystemExit) as raised:
                runpy.run_path(path, run_name="__main__")
        self.assertIsInstance(raised.exception.code, str)
        self.assertIn('"reason":"access_denied"', raised.exception.code)
        self.assertNotIn("DO_NOT_LOG", raised.exception.code)
        self.assertEqual(opener.call_count, 1)


class EpgCurrentSmokeTests(unittest.TestCase):
    def setUp(self):
        self.requests = []
        self.current = {"version": 1, "source": "epg-one", "generation": "opaque-generation",
                        "fetchedAt": 100000, "refreshMs": 7200000, "stale": False,
                        "asOf": 100, "checked": 1, "total": 1,
                        "programs": [{"id": "ren-hd", "start": 90, "end": 110, "title": "Current"}]}

    def run_check(self, expected_generation=None):
        def open_request(request, timeout):
            self.requests.append(request)
            self.assertEqual(timeout, 30)
            return Response(self.current)
        with patch.dict(check_current.__globals__, {"urlopen": open_request}), patch("time.time", return_value=100):
            return check_current("https://upstream.example", expected_generation)

    def test_current_is_one_bounded_metadata_request_with_generation_bound_summary(self):
        result = self.run_check("opaque-generation")
        self.assertEqual(result, {"source": "epg-one", "generation": "opaque-generation",
                                 "fetchedAt": 100000, "stale": False, "asOf": 100,
                                 "checked": 1, "total": 1, "programmes": 1, "current": "Current"})
        self.assertEqual(len(self.requests), 1)
        request = self.requests[0]
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.full_url, "https://upstream.example/epg/v1/current")
        self.assertEqual(request.get_header("Cache-control"), "no-cache")
        self.assertIsNone(request.get_header("Authorization"))
        self.assertIsNone(request.get_header("Cookie"))
        self.assertEqual(json.loads(request.data), {"version": 1, "source": "epg-one", "search": "",
                         "channels": [{"id": "ren-hd", "tvgId": "hlsproxy-382", "tvgName": "",
                                       "name": "РЕН ТВ HD", "shift": 0}]})

    def test_rejects_wrong_metadata_coverage_and_shape_without_retry(self):
        original = copy.deepcopy(self.current)
        for change in ({"version": True}, {"version": 2}, {"source": "other"},
                       {"generation": ""}, {"fetchedAt": False}, {"stale": True}, {"stale": 0},
                       {"checked": True}, {"checked": 0}, {"total": 2}, {"total": True},
                       {"programs": []}, {"programs": {}}, {"programs": [None]},
                       {"programs": original["programs"] * 2}):
            self.current = {**original, **change}
            self.requests = []
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.run_check()
            self.assertEqual(len(self.requests), 1)

    def test_rejects_stale_clock_and_wrong_current_interval(self):
        original = copy.deepcopy(self.current)
        for as_of in (True, 100.5, "100", None, 39, 161):
            self.current = {**original, "asOf": as_of}
            with self.subTest(as_of=as_of), self.assertRaisesRegex(ValueError, "invalid time"):
                self.run_check()
        self.current = copy.deepcopy(original)
        for change in ({"id": "other"}, {"title": ""}, {"title": "   "}, {"title": None},
                       {"title": "x" * 16385}, {"start": True}, {"end": 110.5},
                       {"start": 101}, {"end": 100}, {"end": 80}):
            self.current["programs"] = [{**original["programs"][0], **change}]
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, "programme is missing or invalid"):
                self.run_check()
        self.current["programs"] = [{**original["programs"][0], "start": 100}]
        self.assertEqual(self.run_check()["current"], "Current")

    def test_generation_change_fails_without_a_rematch_or_retry(self):
        with self.assertRaisesRegex(ValueError, "generation changed"):
            self.run_check("previous-generation")
        self.assertEqual(len(self.requests), 1)

    def test_current_http_error_keeps_safe_stage_and_does_not_retry(self):
        stream = io.BytesIO(b"DO_NOT_LOG")
        error = HTTPError("https://private.invalid/?token=DO_NOT_LOG", 403, "DO_NOT_LOG",
                          {"Content-Type": "text/html", "Server": "cloudflare"}, stream)
        opener = Mock(side_effect=error)
        with patch.dict(check_current.__globals__, {"urlopen": opener}):
            with self.assertRaises(EpgHttpError) as raised:
                check_current("https://upstream.example")
        self.assertEqual(opener.call_count, 1)
        self.assertTrue(stream.closed)
        self.assertEqual(raised.exception.diagnostic["stage"], "current")
        self.assertEqual(raised.exception.diagnostic["path"], "/epg/v1/current")
        self.assertEqual(raised.exception.diagnostic["method"], "POST")
        self.assertNotIn("DO_NOT_LOG", str(raised.exception))
        self.assertNotIn("private.invalid", str(raised.exception))

    def test_current_rejects_credentials_and_non_origin_without_network(self):
        opener = Mock()
        with patch.dict(check_current.__globals__, {"urlopen": opener}):
            for base in ("http://upstream.example", "https://user:password@upstream.example",
                         "https://upstream.example/path", "https://upstream.example?token=private"):
                with self.subTest(base=base), self.assertRaisesRegex(ValueError, "HTTPS origin"):
                    check_current(base)
        opener.assert_not_called()

    def test_redirect_handlers_never_follow_another_origin_or_rewrite_post(self):
        requests = []

        class RedirectTransport(HTTPSHandler):
            def https_open(self, request):
                requests.append(request)
                headers = Message()
                headers["Location"] = "https://private.invalid/?token=DO_NOT_LOG"
                response = addinfourl(io.BytesIO(b""), headers, request.full_url, status)
                response.msg = "Redirect"
                return response

        for status in (301, 302, 303, 307, 308):
            requests.clear()
            opener = build_opener(RedirectTransport(), EPG["NoRedirect"]())
            with self.subTest(status=status), patch.dict(check_current.__globals__, {"urlopen": opener.open}):
                with self.assertRaises(EpgHttpError) as raised:
                    check_current("https://upstream.example")
            self.assertEqual(len(requests), 1)
            self.assertEqual(requests[0].get_method(), "POST")
            self.assertEqual(raised.exception.diagnostic["http_status"], status)
            self.assertEqual(raised.exception.diagnostic["path"], "/epg/v1/current")
            self.assertNotIn("private.invalid", str(raised.exception))
            self.assertNotIn("DO_NOT_LOG", str(raised.exception))

    def test_non_200_json_is_not_accepted(self):
        response = Response(self.current)
        response.status = 202
        opener = Mock(return_value=response)
        with patch.dict(check_current.__globals__, {"urlopen": opener}):
            with self.assertRaisesRegex(ValueError, "HTTP 200"):
                check_current("https://upstream.example")
        self.assertEqual(opener.call_count, 1)


if __name__ == "__main__":
    unittest.main()
