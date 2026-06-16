"""Pluggable usage providers.

Each provider knows how to authenticate against one metered AI subscription
(Anthropic, GitHub Copilot, ...) and collect its usage into a uniform
:class:`~claude_usage.providers.base.ProviderSnapshot`. The UI (OSD overlay,
detail popup) renders snapshots without knowing which service produced them,
so adding a new service is a matter of dropping in one module and registering
it in :mod:`claude_usage.providers.registry`.
"""

from claude_usage.providers.base import Meter, Provider, ProviderSnapshot
from claude_usage.providers.registry import collect_snapshots, enabled_providers

__all__ = [
    "Meter",
    "Provider",
    "ProviderSnapshot",
    "collect_snapshots",
    "enabled_providers",
]
