"""Normalize, store, and classify provider quota without source payloads."""

from __future__ import annotations

import fcntl
import json
import math
import os
import stat
import tempfile
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path

from herdr_jev_router.models import CapacityState, Harness

CACHE_MAX_AGE_SECONDS = 6 * 60 * 60
SCHEMA_VERSION = 1
_FRESHNESS = frozenset({"fresh", "stale"})
_SNAPSHOT_FIELDS = frozenset(
    {
        "schema_version",
        "provider",
        "source",
        "observed_at",
        "captured_at",
        "freshness",
        "reached",
        "windows",
    }
)
_WINDOW_FIELDS = frozenset(
    {"name", "used_percent", "remaining_percent", "window_seconds", "resets_at"}
)


def _number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(f"{name} must be numeric")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite")
    return number


@dataclass(frozen=True, slots=True)
class QuotaWindow:
    """Represent one validated provider quota window."""

    name: str
    used_percent: float
    remaining_percent: float
    window_seconds: float
    resets_at: float

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name:
            raise TypeError("name must be a non-empty string")
        # The name is printed to a terminal by the quota commands, so keep it to
        # a short label with no control characters or escape sequences.
        if len(self.name) > 64 or any(
            not (character.isascii() and (character.isalnum() or character in "_-"))
            for character in self.name
        ):
            raise ValueError("name must be a short safe label")
        used = _number(self.used_percent, "used_percent")
        remaining = _number(self.remaining_percent, "remaining_percent")
        duration = _number(self.window_seconds, "window_seconds")
        reset = _number(self.resets_at, "resets_at")
        if not 0 <= used <= 100 or not 0 <= remaining <= 100:
            raise ValueError("quota percentages must be between zero and 100")
        if used + remaining != 100:
            raise ValueError("quota percentages must add to 100")
        if duration <= 0:
            raise ValueError("window_seconds must be positive")
        object.__setattr__(self, "used_percent", used)
        object.__setattr__(self, "remaining_percent", remaining)
        object.__setattr__(self, "window_seconds", duration)
        object.__setattr__(self, "resets_at", reset)

    def to_dict(self) -> dict[str, object]:
        """Serialize the normalized window for the owner-only cache."""

        return {
            "name": self.name,
            "used_percent": _compact_number(self.used_percent),
            "remaining_percent": _compact_number(self.remaining_percent),
            "window_seconds": _compact_number(self.window_seconds),
            "resets_at": _compact_number(self.resets_at),
        }


@dataclass(frozen=True, slots=True)
class QuotaSnapshot:
    """Represent a normalized, provider-specific quota observation."""

    provider: str
    source: str
    observed_at: float
    captured_at: float
    windows: tuple[QuotaWindow, ...]
    reached: bool = False
    freshness: str = "fresh"

    def __post_init__(self) -> None:
        if self.provider not in {harness.value for harness in Harness}:
            raise ValueError("provider must be a supported harness")
        if not isinstance(self.source, str) or not self.source:
            raise TypeError("source must be a non-empty string")
        object.__setattr__(
            self, "observed_at", _number(self.observed_at, "observed_at")
        )
        object.__setattr__(
            self, "captured_at", _number(self.captured_at, "captured_at")
        )
        if not isinstance(self.windows, tuple) or not all(
            isinstance(window, QuotaWindow) for window in self.windows
        ):
            raise TypeError("windows must be a tuple of QuotaWindow values")
        if type(self.reached) is not bool:
            raise TypeError("reached must be a boolean")
        if self.freshness not in _FRESHNESS:
            raise ValueError("freshness must be fresh or stale")

    def to_dict(self) -> dict[str, object]:
        """Serialize only normalized, non-sensitive quota fields."""

        return {
            "schema_version": SCHEMA_VERSION,
            "provider": self.provider,
            "source": self.source,
            "observed_at": _compact_number(self.observed_at),
            "captured_at": _compact_number(self.captured_at),
            "freshness": self.freshness,
            "reached": self.reached,
            "windows": [window.to_dict() for window in self.windows],
        }


def _compact_number(value: int | float) -> int | float:
    if isinstance(value, int):
        return value
    return int(value) if value.is_integer() else value


def snapshot_from_dict(value: object) -> QuotaSnapshot:
    """Validate a cache document and return its normalized snapshot."""

    if not isinstance(value, Mapping) or set(value) != _SNAPSHOT_FIELDS:
        raise ValueError("invalid quota cache schema")
    if value["schema_version"] != SCHEMA_VERSION or not isinstance(
        value["windows"], list
    ):
        raise ValueError("invalid quota cache schema")
    windows = tuple(_window_from_dict(item) for item in value["windows"])
    return QuotaSnapshot(
        provider=_string(value["provider"], "provider"),
        source=_string(value["source"], "source"),
        observed_at=_number(value["observed_at"], "observed_at"),
        captured_at=_number(value["captured_at"], "captured_at"),
        windows=windows,
        reached=_bool(value["reached"], "reached"),
        freshness=_string(value["freshness"], "freshness"),
    )


def _window_from_dict(value: object) -> QuotaWindow:
    if not isinstance(value, Mapping) or set(value) != _WINDOW_FIELDS:
        raise ValueError("invalid quota window schema")
    return QuotaWindow(
        name=_string(value["name"], "name"),
        used_percent=_number(value["used_percent"], "used_percent"),
        remaining_percent=_number(value["remaining_percent"], "remaining_percent"),
        window_seconds=_number(value["window_seconds"], "window_seconds"),
        resets_at=_number(value["resets_at"], "resets_at"),
    )


def _string(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    return value


def _bool(value: object, name: str) -> bool:
    if type(value) is not bool:
        raise TypeError(f"{name} must be a boolean")
    return value


def owner_only_directory(path: Path) -> bool:
    """Return True when path is a directory owned by this user with mode 0700."""

    try:
        details = os.lstat(path)
    except OSError:
        return False
    return (
        stat.S_ISDIR(details.st_mode)
        and stat.S_IMODE(details.st_mode) == 0o700
        and details.st_uid == os.geteuid()
    )


def write_cache(path: Path, snapshot: QuotaSnapshot) -> None:
    """Atomically persist one provider's normalized snapshot with mode 0600."""

    if path.name != f"{snapshot.provider}-quota.json":
        raise ValueError("cache filename must match snapshot provider")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not owner_only_directory(path.parent):
        raise PermissionError("state directory must be owner-only")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(snapshot.to_dict(), handle, separators=(",", ":"), sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def read_cache(
    path: Path, *, now: float, refresh_interval: float = 5 * 60
) -> QuotaSnapshot | None:
    """Load a valid cache snapshot, marking it stale or expired by age."""

    if refresh_interval <= 0:
        raise ValueError("refresh_interval must be positive")
    if not owner_only_directory(path.parent):
        return None
    descriptor: int | None = None
    try:
        # O_NOFOLLOW plus fstat close the race between a pre-check and the open:
        # the opened inode must be our own 0600 regular file, not whatever the
        # path resolves to now.
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        details = os.fstat(descriptor)
        if (
            not stat.S_ISREG(details.st_mode)
            or stat.S_IMODE(details.st_mode) != 0o600
            or details.st_uid != os.geteuid()
        ):
            return None
        with os.fdopen(descriptor, encoding="utf-8") as handle:
            descriptor = None
            snapshot = snapshot_from_dict(json.load(handle))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return None
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
    if path.name != f"{snapshot.provider}-quota.json":
        return None
    age = now - snapshot.observed_at
    if age < 0 or age >= CACHE_MAX_AGE_SECONDS:
        return None
    freshness = "fresh" if age < refresh_interval else "stale"
    return replace(snapshot, freshness=freshness)


@contextmanager
def provider_lock(path: Path, *, blocking: bool = True) -> Iterator[bool]:
    """Serialize refreshes for the single provider cache path."""

    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not owner_only_directory(path.parent):
        yield False
        return
    lock_path = path.with_suffix(f"{path.suffix}.lock")
    try:
        descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    except OSError:
        yield False
        return
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            yield False
            return
        os.fchmod(descriptor, 0o600)
        flags = fcntl.LOCK_EX if blocking else fcntl.LOCK_EX | fcntl.LOCK_NB
        try:
            fcntl.flock(descriptor, flags)
        except BlockingIOError:
            yield False
        else:
            try:
                yield True
            finally:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


def classify_capacity(snapshot: QuotaSnapshot | None, *, now: float) -> CapacityState:
    """Convert valid normalized windows into one deterministic capacity state."""

    if snapshot is None:
        return CapacityState.UNKNOWN
    age = now - snapshot.observed_at
    if age < 0 or age >= CACHE_MAX_AGE_SECONDS:
        return CapacityState.UNKNOWN
    valid_windows = tuple(
        window for window in snapshot.windows if window.resets_at > now
    )
    if snapshot.reached or any(
        window.remaining_percent == 0 for window in valid_windows
    ):
        return CapacityState.EXHAUSTED
    if not valid_windows:
        return CapacityState.UNKNOWN

    expected = tuple(
        max(
            0.0,
            min(100.0, 100.0 * (window.resets_at - now) / window.window_seconds),
        )
        for window in valid_windows
    )
    if any(window.remaining_percent <= 20 for window in valid_windows) or any(
        window.remaining_percent < pace
        for window, pace in zip(valid_windows, expected, strict=True)
    ):
        return CapacityState.CONSERVE
    if all(
        window.remaining_percent >= pace + 20
        for window, pace in zip(valid_windows, expected, strict=True)
    ):
        return CapacityState.SURPLUS
    return CapacityState.ON_PACE
