"""Expose the local quota collectors as small command-line programs."""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from collections.abc import Sequence
from pathlib import Path

from herdr_jev_router.quota import QuotaSnapshot, QuotaWindow
from herdr_jev_router.quota_claude import CLAUDE_CACHE_NAME, capture_status_line
from herdr_jev_router.quota_codex import collect_codex

# Claude passes a small status-line document on standard input. Bound the read
# so a broken or hostile producer cannot buffer unbounded text.
MAX_STATUS_LINE_CHARS = 1024 * 1024


def codex_main(argv: Sequence[str] | None = None) -> int:
    """Refresh Codex quota once and return a process exit status."""

    arguments = _parser("Refresh normalized Codex quota once").parse_args(argv)
    snapshot = asyncio.run(collect_codex(arguments.state_dir))
    if snapshot is None or not snapshot.windows:
        if not arguments.quiet:
            print("Codex quota unavailable", file=sys.stderr)
        return 1
    if not arguments.quiet:
        print(_short_status("Codex", snapshot))
    return 0


def claude_main(argv: Sequence[str] | None = None) -> int:
    """Capture one Claude status-line document from standard input."""

    arguments = _parser("Capture normalized Claude quota from stdin").parse_args(argv)
    document = sys.stdin.read(MAX_STATUS_LINE_CHARS + 1)
    if len(document) > MAX_STATUS_LINE_CHARS:
        return 1
    snapshot = capture_status_line(
        document,
        arguments.state_dir / CLAUDE_CACHE_NAME,
        captured_at=time.time(),
    )
    if snapshot is None or not snapshot.windows:
        return 1
    if not arguments.quiet:
        print(_short_status("Claude", snapshot))
    return 0


def _parser(description: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument(
        "--state-dir",
        required=True,
        type=Path,
        help="owner-only directory containing normalized quota caches",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="write no status text",
    )
    return parser


def _short_status(provider: str, snapshot: QuotaSnapshot) -> str:
    windows = " | ".join(_window_status(window) for window in snapshot.windows)
    status = f"{provider} {windows}" if windows else f"{provider} quota unavailable"
    suffix = " (stale)" if snapshot.freshness == "stale" else ""
    return f"{status}{suffix}"


def _window_status(window: QuotaWindow) -> str:
    labels = {
        5 * 60 * 60: "5h",
        7 * 24 * 60 * 60: "7d",
    }
    label = labels.get(int(window.window_seconds), window.name.replace("_", " "))
    remaining = f"{window.remaining_percent:g}%"
    return f"{label} {remaining}"
