"""Anthropic provider — a thin adapter over the existing collector.

All of the Anthropic richness (cost, heatmaps, ticker, forecasts, news) still
lives in :mod:`claude_usage.collector`; this module only maps the two headline
utilization numbers onto :class:`Meter`s and carries the full ``UsageStats``
through ``snapshot.rich`` for the detail popup and legacy consumers.
"""

from __future__ import annotations

from typing import Any

from claude_usage.collector import UsageStats, collect_all
from claude_usage.providers.base import Meter, ProviderSnapshot

PROVIDER_ID = "anthropic"
DISPLAY_NAME = "CLAUDE"


class AnthropicProvider:
    id = PROVIDER_ID
    display_name = DISPLAY_NAME

    def is_available(self, config: dict[str, Any]) -> bool:
        # Anthropic is the widget's reason to exist; always show its section even
        # if credentials are missing (the section then surfaces the auth error).
        return True

    def collect(self, config: dict[str, Any]) -> ProviderSnapshot:
        stats = collect_all(config)
        return snapshot_from_stats(stats)


def snapshot_from_stats(stats: UsageStats) -> ProviderSnapshot:
    """Project a collected ``UsageStats`` onto a :class:`ProviderSnapshot`.

    Split out from ``collect`` so tests can exercise the mapping without a live
    ``collect_all`` filesystem/network pass.
    """
    meters = [
        Meter(
            key="session",
            label="Session (5h)",
            utilization=stats.session_utilization,
            reset_ts=stats.session_reset,
        ),
        Meter(
            key="weekly",
            label="Weekly (7d)",
            utilization=stats.weekly_utilization,
            reset_ts=stats.weekly_reset,
        ),
    ]
    return ProviderSnapshot(
        provider_id=PROVIDER_ID,
        display_name=DISPLAY_NAME,
        meters=meters,
        error=stats.rate_limit_error,
        available=True,
        rich=stats,
    )
