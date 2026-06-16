"""GitHub Copilot provider.

Surfaces the Copilot *premium request* quota that the IDE clients show. There
is no public per-user quota API, so we read the same undocumented endpoint the
editors use::

    GET https://api.github.com/copilot_internal/user

whose ``quota_snapshots`` carries ``premium_interactions`` /``chat`` /
``completions``. Because the endpoint is internal and may change without
notice, every field is parsed defensively and any failure degrades to a calm
"last known value" rather than a crash — mirroring the hardening in
:func:`claude_usage.collector._fetch_oauth_usage`.

Authentication is zero-config: the OAuth token the editors already stored is
auto-detected from, in order, an explicit config token, the Copilot apps file,
the ``gh`` CLI, then ``GH_TOKEN`` / ``GITHUB_TOKEN``.
"""

from __future__ import annotations

import json
import math
import os
import random
import time
from datetime import datetime, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from claude_usage.history import append_sample, load_samples, prune
from claude_usage.providers.base import Meter, ProviderSnapshot

PROVIDER_ID = "copilot"
DISPLAY_NAME = "COPILOT"

USAGE_URL = "https://api.github.com/copilot_internal/user"

# Same tight retry budget as the Anthropic usage poll: this runs on every 30s
# refresh, so worst-case added latency must stay sub-second. Retry only on
# transient faults; never on 401 (bad token won't heal).
_MAX_RETRIES = 2
_BASE_DELAY = 0.2

# History file lives beside the widget's own config (NOT ~/.claude), since
# Copilot is unrelated to Claude Code's data dir. Premium utilization is stored
# in the generic "session" slot so history.aggregate(key="session") just works.
_HISTORY_FILENAME = "copilot-history.jsonl"
_HISTORY_KEEP_DAYS = 90


def _history_path() -> str:
    from claude_usage.config import user_config_path

    return os.path.join(os.path.dirname(user_config_path()), _HISTORY_FILENAME)


# --------------------------------------------------------------------------- auth

def _token_from_apps_json() -> str | None:
    """Read the OAuth token the Copilot editor plugins cache.

    ``~/.config/github-copilot/apps.json`` maps ``github.com:<appId>`` ->
    ``{user, oauth_token, githubAppId}``. Any entry with a token works; we take
    the first non-empty one.
    """
    path = os.path.expanduser("~/.config/github-copilot/apps.json")
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict):
        return None
    for entry in data.values():
        if isinstance(entry, dict):
            tok = entry.get("oauth_token")
            if tok:
                return str(tok)
    return None


def _token_from_gh_cli() -> str | None:
    """Read the ``gh`` CLI token: prefer the live ``gh auth token`` command,
    fall back to scraping ``~/.config/gh/hosts.yml`` (we avoid a hard PyYAML
    dependency by line-scanning for the ``oauth_token:`` key)."""
    import shutil
    import subprocess

    if shutil.which("gh"):
        try:
            res = subprocess.run(
                ["gh", "auth", "token"],
                capture_output=True, text=True, timeout=5,
            )
            if res.returncode == 0 and res.stdout.strip():
                return res.stdout.strip()
        except Exception:
            pass

    path = os.path.expanduser("~/.config/gh/hosts.yml")
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                stripped = line.strip()
                if stripped.startswith("oauth_token:"):
                    val = stripped.split(":", 1)[1].strip().strip('"\'')
                    if val:
                        return val
    except OSError:
        pass
    return None


def _token_from_env() -> str | None:
    for var in ("GH_TOKEN", "GITHUB_TOKEN"):
        val = os.environ.get(var)
        if val:
            return val
    return None


def load_token(config: dict[str, Any]) -> str | None:
    """Return a usable GitHub token, or ``None`` if none can be found.

    Precedence: explicit config token, Copilot apps file, ``gh`` CLI, env vars.
    """
    cfg_token = (config.get("providers", {}).get("copilot", {}) or {}).get("token")
    if cfg_token:
        return str(cfg_token)
    for source in (_token_from_apps_json, _token_from_gh_cli, _token_from_env):
        tok = source()
        if tok:
            return tok
    return None


# ------------------------------------------------------------------------- fetch

def _fetch_usage(token: str) -> dict[str, Any]:
    """Hit the internal usage endpoint, returning the raw decoded payload or an
    ``{"error": ..., "rate_limited"?: bool}`` dict."""
    req = Request(
        USAGE_URL,
        headers={
            "Authorization": f"token {token}",
            "Accept": "application/json",
            "User-Agent": "claude-usage-widget",
            "Editor-Version": "claude-usage-widget/1.0",
        },
    )
    for attempt in range(_MAX_RETRIES + 1):
        try:
            with urlopen(req, timeout=10) as resp:
                payload = json.loads(resp.read(65536).decode("utf-8", errors="replace"))
            if not isinstance(payload, dict):
                return {"error": "Unexpected Copilot usage payload"}
            return payload
        except HTTPError as e:
            if e.code in (401, 403):
                return {"error": "Copilot auth failed -- sign in to Copilot again"}
            if e.code == 404:
                # Endpoint gone or account lacks Copilot — don't keep retrying.
                return {"error": "Copilot usage unavailable for this account"}
            if e.code == 429:
                if attempt >= _MAX_RETRIES:
                    return {"error": "Rate limited -- using last known values",
                            "rate_limited": True}
                delay = _BASE_DELAY * (2 ** attempt) + random.uniform(0.0, 0.1)
                time.sleep(min(delay, 5.0))
                continue
            return {"error": f"Copilot usage error {e.code}"}
        except json.JSONDecodeError:
            if attempt >= _MAX_RETRIES:
                return {"error": "Copilot usage request failed"}
        except (URLError, OSError, TimeoutError):
            if attempt >= _MAX_RETRIES:
                return {"error": "Copilot usage request failed"}
        time.sleep(_BASE_DELAY * (2 ** attempt) + random.uniform(0.0, 0.1))
    return {"error": "Copilot usage request failed"}


# ----------------------------------------------------------------------- mapping

def _iso_to_epoch(v: Any) -> int:
    """Parse an ISO date/datetime string to unix seconds; 0 on failure.

    The reset field is often a bare date ("2025-07-01"); treat that as midnight
    UTC so the OSD's "resets in N days" countdown is sane.
    """
    if not isinstance(v, str) or not v:
        return 0
    s = v.strip()
    try:
        if len(s) == 10:  # YYYY-MM-DD
            dt = datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        else:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    except (ValueError, TypeError):
        return 0


def _premium_meter(payload: dict[str, Any]) -> Meter | None:
    """Build the premium-requests meter from a usage payload, or None if absent."""
    snapshots = payload.get("quota_snapshots")
    if not isinstance(snapshots, dict):
        return None
    snap = snapshots.get("premium_interactions")
    if not isinstance(snap, dict):
        return None

    unlimited = bool(snap.get("unlimited"))
    reset_ts = _iso_to_epoch(payload.get("quota_reset_date"))

    if unlimited:
        return Meter(key="premium", label="Premium", utilization=0.0,
                     reset_ts=reset_ts, unlimited=True, detail="unlimited")

    # Prefer the server's percent_remaining; fall back to remaining/entitlement.
    pct_remaining = snap.get("percent_remaining")
    entitlement = snap.get("entitlement")
    remaining = snap.get("remaining")
    if remaining is None:
        remaining = snap.get("quota_remaining")

    util = 0.0
    if isinstance(pct_remaining, (int, float)) and not math.isnan(float(pct_remaining)):
        util = 1.0 - float(pct_remaining) / 100.0
    elif isinstance(entitlement, (int, float)) and entitlement:
        try:
            util = 1.0 - float(remaining) / float(entitlement)
        except (TypeError, ValueError, ZeroDivisionError):
            util = 0.0

    detail = ""
    if isinstance(remaining, (int, float)) and isinstance(entitlement, (int, float)) and entitlement:
        detail = f"{int(remaining)}/{int(entitlement)} left"

    # Meter.__post_init__ clamps util into [0, 1].
    return Meter(key="premium", label="Premium", utilization=util,
                 reset_ts=reset_ts, unlimited=False, detail=detail)


# ---------------------------------------------------------------------- provider

class CopilotProvider:
    id = PROVIDER_ID
    display_name = DISPLAY_NAME

    def is_available(self, config: dict[str, Any]) -> bool:
        return load_token(config) is not None

    def collect(self, config: dict[str, Any]) -> ProviderSnapshot:
        token = load_token(config)
        if not token:
            # No credentials -> hide the section entirely, no scary error.
            return ProviderSnapshot(PROVIDER_ID, DISPLAY_NAME, available=False)

        payload = _fetch_usage(token)
        now_ts = datetime.now().timestamp()

        if "error" in payload:
            return self._snapshot_from_last_known(payload["error"], now_ts)

        meter = _premium_meter(payload)
        if meter is None:
            return ProviderSnapshot(
                PROVIDER_ID, DISPLAY_NAME, available=True,
                error="No premium quota reported",
                rich={"plan": payload.get("copilot_plan", "")},
            )

        # Persist real (non-unlimited) utilization for the sparkline + fallback.
        if not meter.unlimited:
            self._record(meter.utilization, now_ts)

        return ProviderSnapshot(
            PROVIDER_ID, DISPLAY_NAME, meters=[meter], available=True,
            rich={
                "plan": payload.get("copilot_plan", ""),
                "history": self._history_series(now_ts),
            },
        )

    # -- history helpers ---------------------------------------------------

    def _record(self, util: float, now_ts: float) -> None:
        path = _history_path()
        try:
            append_sample(path, now_ts, session_util=util, weekly_util=0.0)
            prune(path, keep_seconds=_HISTORY_KEEP_DAYS * 86400, now=now_ts)
        except OSError:
            pass

    def _history_series(self, now_ts: float) -> list[dict]:
        try:
            return load_samples(_history_path(), since_ts=now_ts - 7 * 86400)
        except OSError:
            return []

    def _snapshot_from_last_known(self, error: str, now_ts: float) -> ProviderSnapshot:
        """On a transient failure, paint the last recorded utilization (dim
        error caption) instead of blanking the gauge to 0%."""
        try:
            recent = load_samples(_history_path())
        except OSError:
            recent = []
        meters: list[Meter] = []
        if recent:
            last_util = float(recent[-1].get("session", 0.0) or 0.0)
            meters = [Meter(key="premium", label="Premium", utilization=last_util)]
        return ProviderSnapshot(
            PROVIDER_ID, DISPLAY_NAME, meters=meters, available=True, error=error,
            rich={"history": recent[-200:] if recent else []},
        )
