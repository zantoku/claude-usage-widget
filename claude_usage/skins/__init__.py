"""Pixel-accurate OSD skins from the Claude Design handoff bundle.

Each submodule exposes:
  THEME       — palette dict (same shape as claude_usage.themes entries,
                plus a few direction-specific keys)
  METRICS     — pixel sizes at scale=1.0
  FONTS       — family + size hints per text role
  paint_osd(p, rect, data, scale=1.0) — the full OSD renderer

See the README packaged with the handoff bundle under
``skins-themes/handoff/README.md`` for the design contract and the
nuances to preserve (letter-spacing, ASCII glyph widths, arc angles).
"""

from __future__ import annotations

from . import brutalist, dashboard, hud, receipt, strip, terminal
from ._adapter import SkinData, SkinTickerItem, from_usage_stats
from ._paint import draw_provider_strip, measure_provider_strip

# style-name → module map used by the overlay to dispatch paint.
SKIN_MODULES = {
    brutalist.THEME["style"]: brutalist,
    dashboard.THEME["style"]: dashboard,
    hud.THEME["style"]:       hud,
    receipt.THEME["style"]:   receipt,
    strip.THEME["style"]:     strip,
    terminal.THEME["style"]:  terminal,
}

# Bar idiom each skin uses for its extra-provider strips, mirroring the bar the
# skin draws for its own session/weekly rows. Anything unlisted gets a rounded
# block bar.
_EXTRA_BAR_STYLE = {
    "terminal":  "ascii",   # █░ ASCII cells
    "brutalist": "hard",    # sharp-cornered rectangle
    "receipt":   "hard",
}


def extra_bar_style(style: str) -> str:
    """Return the extra-provider bar idiom for a skin style name."""
    return _EXTRA_BAR_STYLE.get(style, "block")


__all__ = [
    "brutalist", "dashboard", "hud", "receipt", "strip", "terminal",
    "SKIN_MODULES", "SkinData", "SkinTickerItem", "from_usage_stats",
    "draw_provider_strip", "measure_provider_strip", "extra_bar_style",
]
