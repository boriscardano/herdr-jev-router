import json
from pathlib import Path

from herdr_jev_router.models import CapacityState
from herdr_jev_router.quota import classify_capacity, read_cache
from herdr_jev_router.quota_claude import (
    CLAUDE_REFRESH_INTERVAL_SECONDS,
    capture_status_line,
    parse_status_line,
)

FIXTURES = Path(__file__).parents[1] / "fixtures" / "quota" / "claude"


def test_status_line_keeps_only_normalized_windows_and_discards_sentinels() -> None:
    result = parse_status_line(
        (FIXTURES / "statusline-success.json").read_text(), captured_at=1_800_000_000
    )

    assert result is not None
    assert [(item.name, item.remaining_percent) for item in result.windows] == [
        ("five_hour", 76.5),
        ("seven_day", 58.8),
        ("spend_limit", 0),
    ]
    assert classify_capacity(result, now=1_800_000_001) is CapacityState.EXHAUSTED
    serialized = json.dumps(result.to_dict())
    for sentinel in (
        "SENTINEL_SESSION_ID",
        "SENTINEL_ACCESS_TOKEN",
        "SENTINEL_TRANSCRIPT_PATH",
        "SENTINEL_CWD",
        "SENTINEL_PROMPT",
    ):
        assert sentinel not in serialized


def test_partial_or_malformed_status_line_is_unknown_not_exhausted(tmp_path) -> None:
    partial = parse_status_line(
        (FIXTURES / "statusline-partial.json").read_text(), captured_at=1_800_000_000
    )

    assert partial is None
    assert classify_capacity(partial, now=1_800_000_001) is CapacityState.UNKNOWN
    assert parse_status_line("not json", captured_at=1_800_000_000) is None

    cache = tmp_path / "claude-quota.json"
    capture_status_line(
        (FIXTURES / "statusline-success.json").read_text(),
        cache,
        captured_at=1_800_000_000,
    )
    assert capture_status_line("not json", cache, captured_at=1_800_000_000) is None
    retained = read_cache(
        cache,
        now=1_800_000_000 + CLAUDE_REFRESH_INTERVAL_SECONDS,
        refresh_interval=CLAUDE_REFRESH_INTERVAL_SECONDS,
    )
    assert retained is not None
    assert retained.freshness == "stale"


def test_invalid_status_line_windows_are_unknown_not_exhausted() -> None:
    invalid = parse_status_line(
        (FIXTURES / "statusline-invalid.json").read_text(), captured_at=1_800_000_000
    )

    assert invalid is None
    assert classify_capacity(invalid, now=1_800_000_001) is CapacityState.UNKNOWN


def test_status_line_with_all_invalid_windows_does_not_overwrite_the_cache(
    tmp_path,
) -> None:
    cache = tmp_path / "claude-quota.json"
    capture_status_line(
        (FIXTURES / "statusline-success.json").read_text(),
        cache,
        captured_at=1_800_000_000,
    )
    before = cache.read_text()

    document = json.dumps(
        {
            "rate_limits": {
                "five_hour": {"used_percentage": 150, "resets_at": 1_800_003_600},
                "seven_day": {"used_percentage": 10},
                "spend_limit": "not-a-window",
            }
        }
    )
    assert parse_status_line(document, captured_at=1_800_000_000) is None
    assert capture_status_line(document, cache, captured_at=1_800_000_100) is None
    assert cache.read_text() == before


def test_valid_zero_remaining_window_is_still_exhausted() -> None:
    document = json.dumps(
        {
            "rate_limits": {
                "five_hour": {"used_percentage": 100, "resets_at": 1_800_003_600}
            }
        }
    )

    result = parse_status_line(document, captured_at=1_800_000_000)

    assert result is not None
    assert [window.remaining_percent for window in result.windows] == [0]
    assert classify_capacity(result, now=1_800_000_001) is CapacityState.EXHAUSTED


def test_status_line_without_rate_limits_does_not_overwrite_the_cache(
    tmp_path,
) -> None:
    cache = tmp_path / "claude-quota.json"
    capture_status_line(
        (FIXTURES / "statusline-success.json").read_text(),
        cache,
        captured_at=1_800_000_000,
    )
    before = cache.read_text()

    # A brand-new session reports its first status line without rate_limits.
    fresh = json.dumps({"session_id": "SENTINEL_SESSION_ID", "cwd": "/tmp"})
    assert parse_status_line(fresh, captured_at=1_800_000_100) is None
    assert capture_status_line(fresh, cache, captured_at=1_800_000_100) is None
    assert cache.read_text() == before
