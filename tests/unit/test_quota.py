import json
import math
import os
import stat
from dataclasses import FrozenInstanceError

import pytest

from herdr_jev_router.models import CapacityState, QuotaDetail
from herdr_jev_router.quota import (
    CACHE_MAX_AGE_SECONDS,
    QuotaSnapshot,
    QuotaWindow,
    assess_capacity,
    classify_capacity,
    owner_only_directory,
    provider_lock,
    read_cache,
    write_cache,
)


def snapshot(*, observed_at: float = 1_000, reached: bool = False) -> QuotaSnapshot:
    return QuotaSnapshot(
        provider="codex",
        source="codex_app_server",
        observed_at=observed_at,
        captured_at=observed_at + 1,
        windows=(QuotaWindow("primary", 25, 75, 3_600, observed_at + 3_600),),
        reached=reached,
    )


def test_normalized_quota_records_are_immutable_and_minimal() -> None:
    value = snapshot()

    with pytest.raises(FrozenInstanceError):
        value.provider = "claude"  # type: ignore[misc]

    assert value.to_dict() == {
        "schema_version": 1,
        "provider": "codex",
        "source": "codex_app_server",
        "observed_at": 1_000,
        "captured_at": 1_001,
        "freshness": "fresh",
        "reached": False,
        "windows": [
            {
                "name": "primary",
                "used_percent": 25,
                "remaining_percent": 75,
                "window_seconds": 3_600,
                "resets_at": 4_600,
            }
        ],
    }


def test_owner_only_atomic_cache_marks_old_data_stale_then_expires(tmp_path) -> None:
    path = tmp_path / "codex-quota.json"
    write_cache(path, snapshot())

    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert read_cache(path, now=1_100, refresh_interval=300).freshness == "fresh"
    assert read_cache(path, now=1_300, refresh_interval=300).freshness == "stale"
    assert read_cache(path, now=1_000 + CACHE_MAX_AGE_SECONDS) is None


def test_cache_rejects_unknown_fields_and_never_classifies_bad_data_as_exhausted(
    tmp_path,
) -> None:
    path = tmp_path / "codex-quota.json"
    payload = snapshot().to_dict() | {"credential": "SENTINEL_CREDENTIAL"}
    path.write_text(json.dumps(payload))
    path.chmod(0o600)

    assert read_cache(path, now=1_100, refresh_interval=300) is None
    assert classify_capacity(None, now=1_100) is CapacityState.UNKNOWN


def test_cache_reader_rejects_a_symlink_even_with_a_valid_target(tmp_path) -> None:
    cache = tmp_path / "codex-quota.json"
    write_cache(cache, snapshot())
    target = tmp_path / "real-codex.json"
    target.write_bytes(cache.read_bytes())
    target.chmod(0o600)
    cache.unlink()
    cache.symlink_to(target)

    assert cache.is_symlink()
    assert read_cache(cache, now=1_100, refresh_interval=300) is None


def test_provider_lock_rejects_a_symlinked_lock_file(tmp_path) -> None:
    target = tmp_path / "target"
    target.touch(mode=0o600)
    (tmp_path / "codex-quota.json.lock").symlink_to(target)

    with provider_lock(tmp_path / "codex-quota.json") as acquired:
        assert acquired is False


def test_owner_only_directory_requires_the_current_owner(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = tmp_path / "state"
    state.mkdir(mode=0o700)

    assert owner_only_directory(state) is True

    monkeypatch.setattr(os, "geteuid", lambda: os.getuid() + 1)

    assert owner_only_directory(state) is False


def test_owner_only_directory_rejects_a_loose_mode(tmp_path) -> None:
    state = tmp_path / "state"
    state.mkdir(mode=0o755)

    assert owner_only_directory(state) is False


def test_cache_reader_rejects_an_insecure_state_directory(tmp_path) -> None:
    state = tmp_path / "state"
    path = state / "codex-quota.json"
    write_cache(path, snapshot())
    state.chmod(0o770)

    assert read_cache(path, now=1_100, refresh_interval=300) is None


def test_cache_reader_rejects_a_file_not_owned_by_the_current_user(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "codex-quota.json"
    write_cache(path, snapshot())
    # Keep the directory check green so the file-owner branch is the one under
    # test. Faking geteuid alone would fail on the directory first.
    monkeypatch.setattr(
        "herdr_jev_router.quota.owner_only_directory", lambda directory: True
    )
    monkeypatch.setattr(os, "geteuid", lambda: os.getuid() + 1)

    assert read_cache(path, now=1_100, refresh_interval=300) is None


def test_cache_reader_rejects_a_symlink_when_a_pre_check_was_fooled(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = tmp_path / "codex-quota.json"
    write_cache(cache, snapshot())
    target = tmp_path / "real-codex.json"
    target.write_bytes(cache.read_bytes())
    target.chmod(0o600)
    target_details = os.lstat(target)
    cache.unlink()
    cache.symlink_to(target)

    # Keep the directory check green so only the cache path is under test.
    monkeypatch.setattr(
        "herdr_jev_router.quota.owner_only_directory", lambda directory: True
    )

    def fooled_lstat(path: object) -> os.stat_result:
        # A reader that trusted an lstat pre-check would be fooled here: lstat
        # reports the regular file from before the swap while the path on disk
        # is now a symlink. Only an open-time O_NOFOLLOW rejects it.
        if str(path) == str(cache):
            return target_details
        return os.lstat(str(path))

    monkeypatch.setattr(os, "lstat", fooled_lstat)

    assert read_cache(cache, now=1_100, refresh_interval=300) is None


def test_cache_reader_rejects_a_symlinked_state_directory(tmp_path) -> None:
    real = tmp_path / "real-state"
    real.mkdir(mode=0o700)
    path = real / "codex-quota.json"
    write_cache(path, snapshot())
    link = tmp_path / "state"
    link.symlink_to(real)

    assert (
        read_cache(link / "codex-quota.json", now=1_100, refresh_interval=300) is None
    )


def test_cache_writer_refuses_a_symlinked_state_directory(tmp_path) -> None:
    real = tmp_path / "real-state"
    real.mkdir(mode=0o700)
    link = tmp_path / "state"
    link.symlink_to(real)

    with pytest.raises(PermissionError, match="owner-only"):
        write_cache(link / "codex-quota.json", snapshot())

    assert not (real / "codex-quota.json").exists()


def test_cache_writer_refuses_an_insecure_state_directory(tmp_path) -> None:
    state = tmp_path / "state"
    state.mkdir(mode=0o755)

    with pytest.raises(PermissionError, match="owner-only"):
        write_cache(state / "codex-quota.json", snapshot())

    assert not (state / "codex-quota.json").exists()


def test_provider_lock_refuses_an_insecure_state_directory(tmp_path) -> None:
    state = tmp_path / "state"
    state.mkdir(mode=0o755)

    with provider_lock(state / "codex-quota.json") as acquired:
        assert acquired is False


def test_cache_writer_never_chmods_the_published_path(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(*args: object, **kwargs: object) -> None:
        raise AssertionError("write_cache must not chmod the published path")

    monkeypatch.setattr(os, "chmod", fail)
    path = tmp_path / "codex-quota.json"

    write_cache(path, snapshot())

    assert stat.S_IMODE(path.stat().st_mode) == 0o600


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
@pytest.mark.parametrize("field", ["observed_at", "captured_at"])
def test_snapshot_rejects_non_finite_timestamps(field: str, value: float) -> None:
    values = {
        "provider": "codex",
        "source": "codex_app_server",
        "observed_at": 1_000.0,
        "captured_at": 1_001.0,
        "windows": (),
    }
    values[field] = value

    with pytest.raises(ValueError, match=f"{field} must be finite"):
        QuotaSnapshot(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
@pytest.mark.parametrize("field", ["window_seconds", "resets_at"])
def test_window_rejects_non_finite_times(field: str, value: float) -> None:
    values = {
        "name": "primary",
        "used_percent": 25.0,
        "remaining_percent": 75.0,
        "window_seconds": 3_600.0,
        "resets_at": 4_600.0,
    }
    values[field] = value

    with pytest.raises(ValueError, match=f"{field} must be finite"):
        QuotaWindow(**values)  # type: ignore[arg-type]


def test_cache_rejects_provider_mismatched_to_filename(tmp_path) -> None:
    path = tmp_path / "codex-quota.json"
    write_cache(path, snapshot())
    payload = json.loads(path.read_text())
    payload["provider"] = "claude"
    path.write_text(json.dumps(payload))
    path.chmod(0o600)

    assert read_cache(path, now=1_100) is None


def test_cache_writer_rejects_provider_mismatched_to_filename(tmp_path) -> None:
    with pytest.raises(ValueError, match="filename must match"):
        write_cache(tmp_path / "claude-quota.json", snapshot())

    assert not (tmp_path / "claude-quota.json").exists()


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
@pytest.mark.parametrize(
    ("scope", "field"),
    [
        ("snapshot", "observed_at"),
        ("snapshot", "captured_at"),
        ("window", "window_seconds"),
        ("window", "resets_at"),
    ],
)
def test_cache_rejects_every_non_finite_time(
    tmp_path, scope: str, field: str, value: float
) -> None:
    path = tmp_path / "codex-quota.json"
    payload = snapshot().to_dict()
    target = payload if scope == "snapshot" else payload["windows"][0]
    target[field] = value
    path.write_text(json.dumps(payload))
    path.chmod(0o600)

    assert read_cache(path, now=1_100) is None


@pytest.mark.parametrize(
    "name",
    ["has space", "x" * 65, "esc\x1b]0;pwned\x07", "zero\u200bwidth"],
)
def test_window_rejects_an_unsafe_name(name: str) -> None:
    with pytest.raises(ValueError, match="name"):
        QuotaWindow(name, 25, 75, 3_600, 4_600)


def test_window_rejects_an_empty_name() -> None:
    with pytest.raises(TypeError, match="name"):
        QuotaWindow("", 25, 75, 3_600, 4_600)


def test_only_valid_zero_or_explicit_reached_signal_is_exhausted() -> None:
    zero = QuotaSnapshot(
        provider="claude",
        source="claude_status_line",
        observed_at=1_000,
        captured_at=1_000,
        windows=(QuotaWindow("five_hour", 100, 0, 18_000, 2_000),),
    )

    assert classify_capacity(zero, now=1_100) is CapacityState.EXHAUSTED
    assert (
        classify_capacity(snapshot(reached=True), now=1_100) is CapacityState.EXHAUSTED
    )


def test_capacity_classification_distinguishes_on_pace_and_conserve() -> None:
    on_pace = QuotaSnapshot(
        provider="codex",
        source="codex_app_server",
        observed_at=1_000,
        captured_at=1_000,
        windows=(QuotaWindow("primary", 40, 60, 3_600, 3_160),),
    )
    conserve = QuotaSnapshot(
        provider="codex",
        source="codex_app_server",
        observed_at=1_000,
        captured_at=1_000,
        windows=(QuotaWindow("primary", 60, 40, 3_600, 3_160),),
    )

    assert classify_capacity(on_pace, now=1_000) is CapacityState.ON_PACE
    assert classify_capacity(conserve, now=1_000) is CapacityState.CONSERVE


def test_expired_snapshot_is_unknown_even_outside_the_cache_reader() -> None:
    expired = QuotaSnapshot(
        provider="codex",
        source="codex_app_server",
        observed_at=1_000,
        captured_at=1_000,
        windows=(QuotaWindow("primary", 25, 75, 604_800, 605_800),),
    )

    assert (
        classify_capacity(expired, now=1_000 + CACHE_MAX_AGE_SECONDS)
        is CapacityState.UNKNOWN
    )


def _snapshot_with_windows(
    provider: str,
    windows: tuple[QuotaWindow, ...],
    *,
    observed_at: float = 1_000,
    freshness: str = "fresh",
) -> QuotaSnapshot:
    return QuotaSnapshot(
        provider=provider,
        source="test",
        observed_at=observed_at,
        captured_at=observed_at,
        windows=windows,
        freshness=freshness,
    )


def test_window_mapping_is_by_length_not_provider_specific_names() -> None:
    now = 1_000.0
    # Claude's names are five_hour/seven_day; Codex's are primary/secondary.
    # The Codex fixture puts the weekly window in `primary` on purpose, so a
    # name-based mapping would swap the two numbers and fail this test.
    claude = _snapshot_with_windows(
        "claude",
        (
            QuotaWindow("five_hour", 15, 85, 18_000, now + 2 * 3_600),
            QuotaWindow("seven_day", 65, 35, 604_800, now + 100 * 3_600),
        ),
    )
    codex = _snapshot_with_windows(
        "codex",
        (
            QuotaWindow("primary", 94, 6, 604_800, now + 38 * 3_600),
            QuotaWindow("secondary", 15, 85, 18_000, now + 2 * 3_600),
        ),
    )

    assert assess_capacity(claude, now=now).quota == QuotaDetail(85, 2, 35, 100)
    assert assess_capacity(codex, now=now).quota == QuotaDetail(85, 2, 6, 38)


def test_missing_or_expired_windows_give_null_quota_never_a_guess() -> None:
    now = 1_000.0
    empty = QuotaDetail()

    assert assess_capacity(None, now=now).quota == empty
    assert assess_capacity(_snapshot_with_windows("codex", ()), now=now).quota == empty
    # A window whose length is neither about 5 hours nor about 7 days is not
    # mapped to either slot, even though it is a valid known window.
    assert (
        assess_capacity(
            _snapshot_with_windows(
                "codex",
                (QuotaWindow("primary", 50, 50, 3_600, now + 1_800),),
            ),
            now=now,
        ).quota
        == empty
    )
    # An expired cache is discarded, not used, and reports no age.
    expired = _snapshot_with_windows(
        "codex",
        (QuotaWindow("primary", 94, 6, 604_800, now + 38 * 3_600),),
        observed_at=now - CACHE_MAX_AGE_SECONDS,
        freshness="stale",
    )
    expired_assessment = assess_capacity(expired, now=now)
    assert expired_assessment.state is CapacityState.UNKNOWN
    assert expired_assessment.quota == empty
    assert expired_assessment.age_hours is None
    assert expired_assessment.reason is None


def test_stale_but_unexpired_windows_are_used_and_reported_with_their_age() -> None:
    now = 1_000.0
    # A weekly window cannot recover in minutes, so a stale-but-unexpired Codex
    # cache still yields its numbers and the critical rule. The collector skips
    # refreshes under five minutes, so this is the common live case.
    stale = _snapshot_with_windows(
        "codex",
        (QuotaWindow("primary", 94, 6, 604_800, now + 38 * 3_600),),
        observed_at=now - 3_600,
        freshness="stale",
    )

    assessment = assess_capacity(stale, now=now)

    assert assessment.state is CapacityState.CRITICAL
    assert assessment.quota == QuotaDetail(None, None, 6, 38)
    assert assessment.age_hours == 1
    assert assessment.reason == "codex weekly 6% left, resets in 38h"


def test_fresh_windows_report_a_rounded_age() -> None:
    now = 1_000.0
    fresh = _snapshot_with_windows(
        "codex",
        (QuotaWindow("primary", 25, 75, 604_800, now + 100 * 3_600),),
        observed_at=now - 90,
    )

    assert assess_capacity(fresh, now=now).age_hours == 0


def test_critical_when_a_known_window_is_low_and_resets_far_out() -> None:

    now = 1_000.0
    codex = _snapshot_with_windows(
        "codex",
        (
            QuotaWindow("primary", 94, 6, 604_800, now + 38 * 3_600),
            QuotaWindow("secondary", 15, 85, 18_000, now + 2 * 3_600),
        ),
    )

    assessment = assess_capacity(codex, now=now)

    assert assessment.state is CapacityState.CRITICAL
    assert assessment.reason == "codex weekly 6% left, resets in 38h"


@pytest.mark.parametrize(
    "remaining, resets_in_hours, expected",
    [
        (9.9, 12.1, CapacityState.CRITICAL),
        (10.0, 38.0, CapacityState.CONSERVE),
        (6.0, 12.0, CapacityState.CONSERVE),
        (6.0, 11.9, CapacityState.CONSERVE),
        (6.0, 48.0, CapacityState.CRITICAL),
    ],
)
def test_critical_needs_both_low_remaining_and_a_far_reset(
    remaining: float, resets_in_hours: float, expected: CapacityState
) -> None:

    now = 1_000.0
    snapshot = _snapshot_with_windows(
        "codex",
        (
            QuotaWindow(
                "primary",
                100 - remaining,
                remaining,
                604_800,
                now + resets_in_hours * 3_600,
            ),
        ),
    )

    assert assess_capacity(snapshot, now=now).state is expected


def test_exhausted_takes_precedence_over_critical() -> None:

    now = 1_000.0
    snapshot = _snapshot_with_windows(
        "codex",
        (QuotaWindow("primary", 100, 0, 604_800, now + 38 * 3_600),),
    )

    assessment = assess_capacity(snapshot, now=now)

    assert assessment.state is CapacityState.EXHAUSTED
    assert assessment.reason is None


def test_quota_slot_prefers_the_window_closest_to_the_nominal_length() -> None:

    now = 1_000.0
    # Claude's spend_limit window stores `resets_at - captured_at` as its
    # synthetic length, so it can look about 5 hours long. It must not shadow
    # the real five_hour window in the 5-hour slot.
    snapshot = _snapshot_with_windows(
        "claude",
        (
            QuotaWindow("five_hour", 15, 85, 18_000, now + 2 * 3_600),
            QuotaWindow("spend_limit", 97, 3, 17_280, now + 17_280),
        ),
    )

    assessment = assess_capacity(snapshot, now=now)

    assert assessment.quota == QuotaDetail(85, 2, None, None)
