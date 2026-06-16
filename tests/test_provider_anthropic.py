"""Tests for the Anthropic provider adapter."""

from __future__ import annotations

import unittest

from claude_usage.collector import UsageStats
from claude_usage.providers.anthropic import AnthropicProvider, snapshot_from_stats


class TestAnthropicAdapter(unittest.TestCase):
    def test_maps_stats_to_two_meters(self) -> None:
        stats = UsageStats(
            session_utilization=0.4, session_reset=111,
            weekly_utilization=0.7, weekly_reset=222,
        )
        snap = snapshot_from_stats(stats)
        self.assertEqual(snap.provider_id, "anthropic")
        self.assertEqual(snap.display_name, "CLAUDE")
        self.assertEqual([m.key for m in snap.meters], ["session", "weekly"])
        self.assertAlmostEqual(snap.meters[0].utilization, 0.4)
        self.assertEqual(snap.meters[0].reset_ts, 111)
        self.assertAlmostEqual(snap.meters[1].utilization, 0.7)
        self.assertEqual(snap.meters[1].reset_ts, 222)

    def test_rich_carries_full_stats(self) -> None:
        stats = UsageStats(today_cost=12.0)
        snap = snapshot_from_stats(stats)
        self.assertIs(snap.rich, stats)

    def test_rate_limit_error_propagates(self) -> None:
        stats = UsageStats(rate_limit_error="boom")
        self.assertEqual(snapshot_from_stats(stats).error, "boom")

    def test_provider_always_available(self) -> None:
        self.assertTrue(AnthropicProvider().is_available({}))


if __name__ == "__main__":
    unittest.main()
