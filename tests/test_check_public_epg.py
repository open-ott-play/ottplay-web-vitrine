"""Public readiness uses the approved client route and rejects old snapshots."""
import copy
import json
from pathlib import Path
import runpy
import tempfile
import unittest
from unittest.mock import Mock, patch

from retention_fixture import ROOT, make_stage


PUBLIC = runpy.run_path(str(ROOT / "scripts/check-public-epg.py"))
check_public = PUBLIC["check_public"]


class PublicEpgTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.stage = Path(temporary.name) / "dist"
        make_stage(self.stage)
        self.result = {"source": "epg-one", "generation": "current-generation", "fetchedAt": 10000000,
                       "stale": False, "programmes": 3, "archived": 2, "current": "Current"}

    def run_check(self, result=None):
        probe = Mock(return_value=self.result if result is None else result)
        with patch.dict(PUBLIC["EPG"], {"check": probe}), patch("time.time", return_value=10000):
            value = check_public(self.stage)
        probe.assert_called_once_with("https://player.ottplay.here.now")
        return value

    def test_uses_fixed_public_origin_and_does_not_change_stock_or_upstream(self):
        before = {p.relative_to(self.stage): p.read_bytes() for p in self.stage.rglob("*") if p.is_file()}
        result = self.run_check()
        self.assertEqual(result["checkedOrigin"], "https://player.ottplay.here.now")
        self.assertEqual(result["generation"], self.result["generation"])
        self.assertEqual(before, {p.relative_to(self.stage): p.read_bytes() for p in self.stage.rglob("*") if p.is_file()})

    def test_rejects_stale_expired_and_future_generations(self):
        for fields in ({"stale": True}, {"fetchedAt": 2799000}, {"fetchedAt": 2800000},
                       {"fetchedAt": 10301000}):
            with self.subTest(fields=fields), self.assertRaisesRegex(ValueError, "stale, expired or future"):
                self.run_check({**self.result, **fields})
        self.run_check({**self.result, "fetchedAt": 2800001})
        self.run_check({**self.result, "fetchedAt": 10300000})

    def test_wrong_proxy_target_method_limit_or_extra_route_fails_before_network(self):
        path = self.stage / ".herenow/proxy.json"
        original = json.loads(path.read_text())
        variants = []
        for field, value in (("upstream", "https://player.ottplay.here.now/epg/v1/match"),
                             ("method", "GET"), ("rateLimit", "999999/hour/ip")):
            variant = copy.deepcopy(original)
            variant["proxies"]["/epg/v1/match"][field] = value
            variants.append(variant)
        variant = copy.deepcopy(original)
        variant["proxies"]["/epg/*"] = variant["proxies"]["/epg/v1/match"]
        variants.append(variant)
        for variant in variants:
            path.write_text(json.dumps(variant))
            probe = Mock()
            with self.subTest(variant=variant), patch.dict(PUBLIC["EPG"], {"check": probe}):
                with self.assertRaisesRegex(SystemExit, "approved exact"):
                    check_public(self.stage)
            probe.assert_not_called()

    def test_http_failure_propagates_without_an_alternative_origin_or_retry(self):
        error = PUBLIC["EPG"]["EpgHttpError"]({"http_status": 403, "reason": "access_denied"})
        probe = Mock(side_effect=error)
        with patch.dict(PUBLIC["EPG"], {"check": probe}), self.assertRaises(type(error)):
            check_public(self.stage)
        probe.assert_called_once_with("https://player.ottplay.here.now")


if __name__ == "__main__":
    unittest.main()
