"""Provider discovery and safe collection.

Knows which providers exist and which the user has enabled. Adding OpenAI later
is: write ``providers/openai.py`` and append one line to ``_ALL_PROVIDERS``.
"""

from __future__ import annotations

from typing import Any

from claude_usage.providers.anthropic import AnthropicProvider
from claude_usage.providers.base import Provider, ProviderSnapshot
from claude_usage.providers.copilot import CopilotProvider

# Order here is the order sections stack in the OSD. Anthropic stays first.
_ALL_PROVIDERS: list[Provider] = [
    AnthropicProvider(),
    CopilotProvider(),
]

# Providers enabled by default when the config says nothing. Anthropic is the
# widget's reason to exist; everything else is opt-in so existing installs are
# unaffected until the user turns them on.
_DEFAULT_ENABLED = {"anthropic"}


def _is_enabled(provider: Provider, config: dict[str, Any]) -> bool:
    providers_cfg = config.get("providers")
    if not isinstance(providers_cfg, dict):
        return provider.id in _DEFAULT_ENABLED
    entry = providers_cfg.get(provider.id)
    if not isinstance(entry, dict):
        return provider.id in _DEFAULT_ENABLED
    return bool(entry.get("enabled", provider.id in _DEFAULT_ENABLED))


def enabled_providers(config: dict[str, Any]) -> list[Provider]:
    """Return the provider instances the user has enabled, in display order."""
    return [p for p in _ALL_PROVIDERS if _is_enabled(p, config)]


def collect_snapshots(config: dict[str, Any]) -> list[ProviderSnapshot]:
    """Collect every enabled provider, isolating failures.

    A provider that raises (despite the contract) becomes a snapshot carrying
    the error rather than taking down the whole refresh — one flaky service
    must never blank the others.
    """
    snapshots: list[ProviderSnapshot] = []
    for provider in enabled_providers(config):
        try:
            snapshots.append(provider.collect(config))
        except Exception as exc:  # noqa: BLE001 — last-resort isolation
            snapshots.append(ProviderSnapshot(
                provider_id=provider.id,
                display_name=getattr(provider, "display_name", provider.id.upper()),
                available=True,
                error=f"Collection failed: {exc}",
            ))
    return snapshots
