"""Tests for the GitHub Copilot provider: auth discovery, quota mapping,
and the hardened fetch path."""

from __future__ import annotations

import io
import json
import os
import unittest
from unittest.mock import patch

import claude_usage.providers.copilot as cop
from claude_usage.providers.copilot import (
    CopilotProvider,
    _premium_meter,
    load_token,
)


class _CtxResp:
    """Minimal context-manager stand-in for urlopen's response object."""

    def __init__(self, body: bytes) -> None:
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self, n: int = -1) -> bytes:
        return self._body


def _http_error(code: int):
    from urllib.error import HTTPError
    return HTTPError("https://api", code, "err", {}, io.BytesIO(b"{}"))


# --------------------------------------------------------------------------- auth


class TestTokenDiscovery(unittest.TestCase):
    def test_config_token_takes_precedence(self) -> None:
        cfg = {"providers": {"copilot": {"token": "cfg-tok"}}}
        with patch.object(cop, "_token_from_apps_json", lambda: "apps-tok"):
            self.assertEqual(load_token(cfg), "cfg-tok")

    def test_apps_json_before_gh_and_env(self) -> None:
        with patch.object(cop, "_token_from_apps_json", lambda: "apps-tok"), \
             patch.object(cop, "_token_from_gh_cli", lambda: "gh-tok"), \
             patch.object(cop, "_token_from_env", lambda: "env-tok"):
            self.assertEqual(load_token({}), "apps-tok")

    def test_gh_before_env(self) -> None:
        with patch.object(cop, "_token_from_apps_json", lambda: None), \
             patch.object(cop, "_token_from_gh_cli", lambda: "gh-tok"), \
             patch.object(cop, "_token_from_env", lambda: "env-tok"):
            self.assertEqual(load_token({}), "gh-tok")

    def test_env_fallback(self) -> None:
        with patch.object(cop, "_token_from_apps_json", lambda: None), \
             patch.object(cop, "_token_from_gh_cli", lambda: None), \
             patch.dict(os.environ, {"GH_TOKEN": "env-tok"}, clear=False):
            self.assertEqual(load_token({}), "env-tok")

    def test_none_when_nothing_found(self) -> None:
        with patch.object(cop, "_token_from_apps_json", lambda: None), \
             patch.object(cop, "_token_from_gh_cli", lambda: None), \
             patch.object(cop, "_token_from_env", lambda: None):
            self.assertIsNone(load_token({}))

    def test_apps_json_parsed_from_disk(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "apps.json")
            with open(path, "w") as f:
                json.dump({
                    "github.com:Iv1.abc": {"user": "x", "oauth_token": "gho_xyz"},
                }, f)
            with patch.object(cop.os.path, "expanduser", lambda p: path):
                self.assertEqual(cop._token_from_apps_json(), "gho_xyz")


# ------------------------------------------------------------------------ mapping


class TestPremiumMeter(unittest.TestCase):
    def _payload(self, premium: dict, reset: str = "2099-07-01") -> dict:
        return {"quota_reset_date": reset, "quota_snapshots": {"premium_interactions": premium}}

    def test_percent_remaining_to_utilization(self) -> None:
        m = _premium_meter(self._payload(
            {"percent_remaining": 37.0, "remaining": 111, "entitlement": 300},
        ))
        self.assertAlmostEqual(m.utilization, 0.63, places=5)
        self.assertEqual(m.detail, "111/300 left")
        self.assertFalse(m.unlimited)
        self.assertGreater(m.reset_ts, 0)

    def test_remaining_entitlement_fallback(self) -> None:
        m = _premium_meter(self._payload({"remaining": 25, "entitlement": 100}))
        self.assertAlmostEqual(m.utilization, 0.75, places=5)

    def test_unlimited(self) -> None:
        m = _premium_meter(self._payload({"unlimited": True}))
        self.assertTrue(m.unlimited)
        self.assertEqual(m.utilization, 0.0)

    def test_missing_snapshot_returns_none(self) -> None:
        self.assertIsNone(_premium_meter({"quota_snapshots": {}}))
        self.assertIsNone(_premium_meter({}))


# -------------------------------------------------------------------------- fetch


class TestFetchUsage(unittest.TestCase):
    def test_success(self) -> None:
        body = json.dumps({"copilot_plan": "business"}).encode()
        with patch.object(cop, "urlopen", lambda req, timeout=10: _CtxResp(body)):
            out = cop._fetch_usage("tok")
        self.assertEqual(out.get("copilot_plan"), "business")

    def test_401_reports_auth_failure(self) -> None:
        def always_401(req, timeout=10):
            raise _http_error(401)
        with patch.object(cop, "urlopen", always_401):
            out = cop._fetch_usage("tok")
        self.assertIn("auth", out["error"].lower())

    def test_429_retries_then_rate_limited(self) -> None:
        calls = {"n": 0}

        def always_429(req, timeout=10):
            calls["n"] += 1
            raise _http_error(429)

        with patch.object(cop, "urlopen", always_429), \
             patch.object(cop.time, "sleep", lambda s: None):
            out = cop._fetch_usage("tok")
        self.assertTrue(out.get("rate_limited"))
        self.assertGreater(calls["n"], 1)

    def test_network_error_non_fatal(self) -> None:
        from urllib.error import URLError

        def boom(req, timeout=10):
            raise URLError("down")

        with patch.object(cop, "urlopen", boom), \
             patch.object(cop.time, "sleep", lambda s: None):
            out = cop._fetch_usage("tok")
        self.assertIn("error", out)


# ----------------------------------------------------------------------- collect


class TestCollect(unittest.TestCase):
    def test_no_token_means_unavailable(self) -> None:
        with patch.object(cop, "load_token", lambda cfg: None):
            snap = CopilotProvider().collect({})
        self.assertFalse(snap.available)
        self.assertEqual(snap.meters, [])

    def test_success_builds_premium_meter(self) -> None:
        payload = {
            "copilot_plan": "business",
            "quota_reset_date": "2099-07-01",
            "quota_snapshots": {
                "premium_interactions": {
                    "percent_remaining": 50.0, "remaining": 50, "entitlement": 100,
                },
            },
        }
        with patch.object(cop, "load_token", lambda cfg: "tok"), \
             patch.object(cop, "_fetch_usage", lambda tok: payload), \
             patch.object(CopilotProvider, "_record", lambda *a, **k: None), \
             patch.object(CopilotProvider, "_history_series", lambda *a, **k: []):
            snap = CopilotProvider().collect({})
        self.assertTrue(snap.available)
        self.assertEqual(len(snap.meters), 1)
        self.assertAlmostEqual(snap.meters[0].utilization, 0.5)
        self.assertEqual(snap.rich["plan"], "business")

    def test_error_payload_uses_last_known(self) -> None:
        with patch.object(cop, "load_token", lambda cfg: "tok"), \
             patch.object(cop, "_fetch_usage",
                          lambda tok: {"error": "Rate limited", "rate_limited": True}), \
             patch.object(cop, "load_samples", lambda *a, **k: [{"session": 0.4}]):
            snap = CopilotProvider().collect({})
        self.assertTrue(snap.available)
        self.assertEqual(snap.error, "Rate limited")
        self.assertEqual(len(snap.meters), 1)
        self.assertAlmostEqual(snap.meters[0].utilization, 0.4)

    def test_is_available_reflects_token(self) -> None:
        with patch.object(cop, "load_token", lambda cfg: "tok"):
            self.assertTrue(CopilotProvider().is_available({}))
        with patch.object(cop, "load_token", lambda cfg: None):
            self.assertFalse(CopilotProvider().is_available({}))


if __name__ == "__main__":
    unittest.main()
