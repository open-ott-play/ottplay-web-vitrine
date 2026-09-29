"""Operator smoke checks must use the bounded v1 API, never the old full feed."""
import copy
import io
import json
from pathlib import Path
import runpy
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit


EPG = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts/check-epg.py"))
check = EPG["check"]


class Response(io.BytesIO):
    def __init__(self, value, content_type="application/json"):
        super().__init__(json.dumps(value).encode())
        self.headers = {"Content-Type": content_type}


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


if __name__ == "__main__":
    unittest.main()
