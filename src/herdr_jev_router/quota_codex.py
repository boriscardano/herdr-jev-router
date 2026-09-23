"""Collect Codex subscription quota through the local app-server protocol."""

from __future__ import annotations

import asyncio
import json
import math
import time
from collections.abc import Mapping, Sequence
from pathlib import Path

from herdr_jev_router.quota import (
    QuotaSnapshot,
    QuotaWindow,
    provider_lock,
    read_cache,
    write_cache,
)

CODEX_CACHE_NAME = "codex-quota.json"
CODEX_REFRESH_INTERVAL_SECONDS = 5 * 60
# Reported to the local Codex app-server during the JSON-RPC initialize
# handshake. Keep it in sync with the release version (test_release_version.py).
CLIENT_VERSION = "0.1.4"
_COMMAND = ("codex", "app-server", "--listen", "stdio://")


def parse_app_server_jsonl(
    document: str, *, observed_at: float, captured_at: float
) -> QuotaSnapshot | None:
    """Parse the minimum app-server response sequence into a quota snapshot."""

    responses: dict[int, object] = {}
    for line in document.splitlines():
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            return None
        if not isinstance(message, Mapping):
            return None
        identifier = message.get("id")
        if type(identifier) is int and identifier in {2, 3}:
            if "error" in message or "result" not in message:
                return None
            responses[identifier] = message["result"]
    account = responses.get(2)
    limits = responses.get(3)
    if not isinstance(account, Mapping) or not isinstance(limits, Mapping):
        return None
    account_details = account.get("account")
    if not isinstance(account_details, Mapping) or account_details.get("type") not in {
        "chatgpt",
        "chatgptAuthTokens",
    }:
        return None
    return parse_rate_limits(limits, observed_at=observed_at, captured_at=captured_at)


def parse_rate_limits(
    result: Mapping[str, object], *, observed_at: float, captured_at: float
) -> QuotaSnapshot | None:
    """Normalize only the documented compatible Codex rate-limit view."""

    raw_limits = result.get("rateLimits")
    if raw_limits is None:
        buckets = result.get("rateLimitsByLimitId")
        raw_limits = buckets.get("codex") if isinstance(buckets, Mapping) else None
    if not isinstance(raw_limits, Mapping):
        return None

    windows = tuple(
        window
        for name in ("primary", "secondary")
        if (window := _parse_window(name, raw_limits.get(name))) is not None
    )
    reached_type = raw_limits.get("rateLimitReachedType")
    if not isinstance(reached_type, str | None):
        return None
    reached = reached_type not in (None, "")
    return QuotaSnapshot(
        provider="codex",
        source="codex_app_server",
        observed_at=observed_at,
        captured_at=captured_at,
        windows=windows,
        reached=reached,
    )


def _parse_window(name: str, value: object) -> QuotaWindow | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        return None
    used = _number(value.get("usedPercent"))
    duration = _number(value.get("windowDurationMins"))
    reset = _number(value.get("resetsAt"))
    if used is None or duration is None or reset is None:
        return None
    if not 0 <= used <= 100 or duration <= 0:
        return None
    return QuotaWindow(name, used, 100 - used, duration * 60, reset)


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


async def collect_codex(
    state_dir: Path,
    *,
    now: float | None = None,
    command: Sequence[str] = _COMMAND,
    timeout: float = 15,
) -> QuotaSnapshot | None:
    """Refresh the Codex cache once, retaining stale normalized data on failure."""

    if not command or not all(isinstance(part, str) and part for part in command):
        raise ValueError("command must contain non-empty argv strings")
    if timeout <= 0:
        raise ValueError("timeout must be positive")
    observed_at = time.time() if now is None else now
    cache_path = state_dir / CODEX_CACHE_NAME
    cached = read_cache(
        cache_path, now=observed_at, refresh_interval=CODEX_REFRESH_INTERVAL_SECONDS
    )
    if cached is not None and cached.freshness == "fresh":
        return cached

    with provider_lock(cache_path, blocking=False) as acquired:
        if not acquired:
            return cached
        cached = read_cache(
            cache_path,
            now=observed_at,
            refresh_interval=CODEX_REFRESH_INTERVAL_SECONDS,
        )
        if cached is not None and cached.freshness == "fresh":
            return cached
        result = await _query_app_server(command, observed_at, timeout)
        if result is None:
            return cached
        write_cache(cache_path, result)
        return result


async def _query_app_server(
    command: Sequence[str], observed_at: float, timeout: float
) -> QuotaSnapshot | None:
    process: asyncio.subprocess.Process | None = None
    try:
        async with asyncio.timeout(timeout):
            process = await asyncio.create_subprocess_exec(
                *command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await _send(
                process,
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "clientInfo": {
                            "name": "herdr-jev-router",
                            "version": CLIENT_VERSION,
                        },
                        "capabilities": {},
                    },
                },
            )
            if await _response(process, 1) is None:
                return None
            await _send(process, {"jsonrpc": "2.0", "method": "initialized"})
            await _send(
                process,
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "account/read",
                    "params": {"refreshToken": False},
                },
            )
            account = await _response(process, 2)
            if account is None:
                return None
            await _send(
                process,
                {"jsonrpc": "2.0", "id": 3, "method": "account/rateLimits/read"},
            )
            limits = await _response(process, 3)
            if limits is None:
                return None
            transcript = "\n".join(
                (
                    json.dumps({"id": 2, "result": account}),
                    json.dumps({"id": 3, "result": limits}),
                )
            )
            return parse_app_server_jsonl(
                transcript, observed_at=observed_at, captured_at=time.time()
            )
    except (OSError, TimeoutError, ValueError, asyncio.IncompleteReadError):
        return None
    finally:
        if process is not None:
            await _stop_process(process)


async def _send(
    process: asyncio.subprocess.Process, message: Mapping[str, object]
) -> None:
    if process.stdin is None:
        raise OSError("app-server stdin is unavailable")
    process.stdin.write(json.dumps(message, separators=(",", ":")).encode() + b"\n")
    await process.stdin.drain()


async def _response(
    process: asyncio.subprocess.Process, identifier: int
) -> object | None:
    if process.stdout is None:
        raise OSError("app-server stdout is unavailable")
    while line := await process.stdout.readline():
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            return None
        if not isinstance(message, Mapping) or message.get("id") != identifier:
            continue
        if "error" in message or "result" not in message:
            return None
        return message["result"]
    return None


async def _stop_process(process: asyncio.subprocess.Process) -> None:
    if process.returncode is None:
        try:
            process.terminate()
        except ProcessLookupError:
            pass
    try:
        await asyncio.wait_for(process.wait(), timeout=2)
    except TimeoutError:
        try:
            process.kill()
        except ProcessLookupError:
            pass
        await process.wait()
