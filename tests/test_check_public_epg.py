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

    def test_cutover_checks_fixed_new_origin_and_current_route_without_using_old_public_route(self):
        probe = Mock(return_value=self.result)
        current = Mock(return_value=self.result)
        with patch.dict(PUBLIC["EPG"], {"check": probe, "check_current": current}), \
                patch("time.time", return_value=10000):
            value = PUBLIC["check_upstream"](self.stage)
        origin = PUBLIC["EPG_API_URL"].removesuffix("/epg/v1")
        probe.assert_called_once_with(origin)
        current.assert_called_once_with(origin, expected_generation="current-generation")
        self.assertNotEqual(origin, PUBLIC["PUBLIC_ORIGIN"])
        self.assertEqual(value["checkedOrigin"], origin)
        self.assertTrue(value["currentEndpointVerified"])

    def test_cutover_rejects_bad_stock_before_either_network_call(self):
        path = self.stage / ".herenow/proxy.json"
        changed = json.loads(path.read_text())
        changed["proxies"]["/epg/v1/match"]["upstream"] = "https://unreviewed.example/epg/v1/match"
        path.write_text(json.dumps(changed))
        probe, current = Mock(), Mock()
        with patch.dict(PUBLIC["EPG"], {"check": probe, "check_current": current}), \
                self.assertRaises(SystemExit):
            PUBLIC["check_upstream"](self.stage)
        probe.assert_not_called()
        current.assert_not_called()

    def test_cutover_requires_both_fresh_results_and_propagates_current_failure(self):
        for endpoint in ("check", "check_current"):
            for invalid in ({"stale": True}, {"fetchedAt": 2800000}, {"fetchedAt": 10301000}):
                probes = {key: Mock(return_value=self.result) for key in ("check", "check_current")}
                probes[endpoint].return_value = {**self.result, **invalid}
                with self.subTest(endpoint=endpoint, invalid=invalid), \
                        patch.dict(PUBLIC["EPG"], probes), patch("time.time", return_value=10000), \
                        self.assertRaises(ValueError):
                    PUBLIC["check_upstream"](self.stage)
        current = Mock(side_effect=ValueError("generation changed"))
        with patch.dict(PUBLIC["EPG"], {"check": Mock(return_value=self.result), "check_current": current}), \
                patch("time.time", return_value=10000), self.assertRaisesRegex(ValueError, "generation changed"):
            PUBLIC["check_upstream"](self.stage)
        current.assert_called_once()


if __name__ == "__main__":
    unittest.main()
