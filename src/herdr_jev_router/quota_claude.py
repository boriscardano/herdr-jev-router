"""Capture Claude Code status-line quota without retaining session data."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from pathlib import Path

from herdr_jev_router.quota import (
    QuotaSnapshot,
    QuotaWindow,
    provider_lock,
    write_cache,
)

CLAUDE_CACHE_NAME = "claude-quota.json"
CLAUDE_REFRESH_INTERVAL_SECONDS = 30 * 60
_WINDOW_SECONDS = {"five_hour": 5 * 60 * 60, "seven_day": 7 * 24 * 60 * 60}


def parse_status_line(document: str, *, captured_at: float) -> QuotaSnapshot | None:
    """Extract only documented rate-limit fields from a status-line document."""

    try:
        payload = json.loads(document)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, Mapping):
        return None
    limits = payload.get("rate_limits")
    if limits is None:
        limits = {}
    if not isinstance(limits, Mapping):
        return None
    windows = tuple(
        window
        for name in ("five_hour", "seven_day", "spend_limit")
        if (window := _parse_window(name, limits.get(name), captured_at)) is not None
    )
    if not windows:
        # No usable window means no observation, not an empty one. Returning
        # None stops capture_status_line from overwriting a good cache, which a
        # brand-new session's first status line (no rate_limits) otherwise does.
        return None
    return QuotaSnapshot(
        provider="claude",
        source="claude_status_line",
        observed_at=captured_at,
        captured_at=captured_at,
        windows=windows,
    )


def _parse_window(name: str, value: object, captured_at: float) -> QuotaWindow | None:
    if not isinstance(value, Mapping):
        return None
    used = _number(value.get("used_percentage"))
    reset = _number(value.get("resets_at"))
    if used is None or reset is None or used < 0 or reset <= captured_at:
        return None
    if name == "spend_limit":
        used = min(used, 100)
        duration = max(1.0, reset - captured_at)
    elif used <= 100:
        duration = float(_WINDOW_SECONDS[name])
    else:
        return None
    return QuotaWindow(name, used, 100 - used, duration, reset)


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def capture_status_line(
    document: str, cache_path: Path, *, captured_at: float
) -> QuotaSnapshot | None:
    """Write a successful status-line observation without retaining its input."""

    snapshot = parse_status_line(document, captured_at=captured_at)
    if snapshot is not None:
        with provider_lock(cache_path, blocking=False) as acquired:
            if not acquired:
                return None
            write_cache(cache_path, snapshot)
    return snapshot
