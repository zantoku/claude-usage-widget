"""The provider contract shared by the UI and every concrete provider.

The UI deals only in :class:`Meter` (one gauge) and :class:`ProviderSnapshot`
(one service's worth of gauges). Anything service-specific — Anthropic's cost
breakdowns, heatmaps, ticker tape, Copilot's plan name — rides along in
:attr:`ProviderSnapshot.rich` and is only understood by that service's own
renderer.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass
class Meter:
    """A single usage gauge.

    ``utilization`` is the fraction *used* (0.0–1.0), matching the existing OSD
    convention so the shared colour ramp (``overlay._bar_color``) applies
    unchanged. Providers that report "percent remaining" must convert.
    """

    key: str                       # stable id within a provider: "session", "premium"
    label: str                     # human label: "Session (5h)", "Premium"
    utilization: float = 0.0       # fraction USED, clamped to [0, 1]
    reset_ts: int = 0              # unix seconds until the window resets; 0 = unknown
    unlimited: bool = False        # render as an "∞" badge rather than a gauge
    detail: str = ""              # short caption, e.g. "37 / 300 left"

    def __post_init__(self) -> None:
        # Clamp defensively — provider math (1 - pct/100) and dodgy payloads
        # can land just outside [0, 1], which would overflow the gauge paint.
        try:
            u = float(self.utilization)
        except (TypeError, ValueError):
            u = 0.0
        if u != u or u in (float("inf"), float("-inf")):  # NaN / Inf
            u = 0.0
        self.utilization = max(0.0, min(1.0, u))


@dataclass
class ProviderSnapshot:
    """One refresh of one provider's usage."""

    provider_id: str                          # "anthropic", "copilot"
    display_name: str                         # "CLAUDE", "COPILOT"
    meters: list[Meter] = field(default_factory=list)
    error: str = ""                          # non-fatal message shown dim in the section
    available: bool = True                    # False -> section hidden entirely
    # Provider-specific payload for the detail popup. Anthropic stashes its full
    # UsageStats here; Copilot a small dict. The OSD ignores it except for the
    # Anthropic-only ticker/news/live-activity extras.
    rich: Any = None

    def to_public_dict(self) -> dict[str, Any]:
        """Serialise meters + headline fields for the JSON API / CLI.

        ``rich`` is deliberately omitted — it is provider-internal and, for
        Anthropic, already exposed via the legacy top-level fields. Callers that
        need the rich payload reach for it directly.
        """
        return {
            "provider_id": self.provider_id,
            "display_name": self.display_name,
            "available": self.available,
            "error": self.error,
            "meters": [asdict(m) for m in self.meters],
        }


@runtime_checkable
class Provider(Protocol):
    """What every provider must implement.

    ``collect`` must never raise — transient failures belong in
    ``ProviderSnapshot.error`` and missing credentials in ``available=False`` —
    but the registry wraps it defensively regardless.
    """

    id: str
    display_name: str

    def is_available(self, config: dict[str, Any]) -> bool: ...

    def collect(self, config: dict[str, Any]) -> ProviderSnapshot: ...
