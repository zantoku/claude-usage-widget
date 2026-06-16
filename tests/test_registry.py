"""Tests for provider enable/disable resolution and isolated collection."""

from __future__ import annotations

import unittest

from claude_usage.providers import registry
from claude_usage.providers.base import ProviderSnapshot


class TestEnabledProviders(unittest.TestCase):
    def test_default_is_anthropic_only(self) -> None:
        ids = [p.id for p in registry.enabled_providers({})]
        self.assertEqual(ids, ["anthropic"])

    def test_copilot_opt_in(self) -> None:
        cfg = {"providers": {"copilot": {"enabled": True}}}
        ids = [p.id for p in registry.enabled_providers(cfg)]
        self.assertIn("copilot", ids)
        self.assertIn("anthropic", ids)

    def test_anthropic_can_be_disabled(self) -> None:
        cfg = {"providers": {"anthropic": {"enabled": False}}}
        ids = [p.id for p in registry.enabled_providers(cfg)]
        self.assertNotIn("anthropic", ids)

    def test_malformed_providers_block_falls_back_to_default(self) -> None:
        cfg = {"providers": "not-a-dict"}
        ids = [p.id for p in registry.enabled_providers(cfg)]
        self.assertEqual(ids, ["anthropic"])


class _BoomProvider:
    id = "boom"
    display_name = "BOOM"

    def is_available(self, config):
        return True

    def collect(self, config):
        raise RuntimeError("kaboom")


class _OKProvider:
    id = "ok"
    display_name = "OK"

    def is_available(self, config):
        return True

    def collect(self, config):
        return ProviderSnapshot("ok", "OK")


class TestCollectIsolation(unittest.TestCase):
    def test_one_provider_failure_does_not_drop_others(self) -> None:
        original = registry._ALL_PROVIDERS
        registry._ALL_PROVIDERS = [_BoomProvider(), _OKProvider()]
        try:
            cfg = {"providers": {"boom": {"enabled": True}, "ok": {"enabled": True}}}
            snaps = registry.collect_snapshots(cfg)
        finally:
            registry._ALL_PROVIDERS = original

        by_id = {s.provider_id: s for s in snaps}
        self.assertIn("boom", by_id)
        self.assertIn("ok", by_id)
        self.assertIn("Collection failed", by_id["boom"].error)
        self.assertEqual(by_id["ok"].error, "")


if __name__ == "__main__":
    unittest.main()
