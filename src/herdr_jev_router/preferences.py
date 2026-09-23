"""Load the user's routing preferences text and its built-in default.

The preferences are the user's plain-words steering for Jev. They are sent to
Jev verbatim in `state.preferences` and are never written to the audit; only
whether a file or the default was used, and its length, are recorded.
"""

from __future__ import annotations

import os
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

# One place for the default: plain words about cost, near-empty quota, and
# effort, so an absent preferences file still steers Jev sensibly.
DEFAULT_PREFERENCES = (
    "Prefer the cheapest model that can do the task well. Avoid a provider "
    "whose weekly or 5-hour quota is nearly used up unless nothing else fits. "
    "Use higher effort only for hard tasks."
)
MAX_PREFERENCES_BYTES = 8 * 1024
_FILE_SOURCE = "file"
_DEFAULT_SOURCE = "default"


class PreferencesError(ValueError):
    """Report an unusable preferences file with one stable denial code."""

    code = "invalid_preferences"

    def __init__(self, message: str) -> None:
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class Preferences:
    """Hold the preferences text sent to Jev and where it came from."""

    text: str
    source: str
    path: Path | None = None

    @property
    def length(self) -> int:
        """Return the number of characters in the preferences text."""

        return len(self.text)


def default_preferences() -> Preferences:
    """Return the built-in preferences used when no file is present."""

    return Preferences(DEFAULT_PREFERENCES, _DEFAULT_SOURCE)


@dataclass(frozen=True, slots=True)
class PreferencesResolution:
    """Describe the preferences in use, or why the file could not be used."""

    preferences: Preferences
    problem: str | None = None


def preferences_path(environment: Mapping[str, str]) -> Path:
    """Return the configured preferences file path under the config home."""

    configured = environment.get("XDG_CONFIG_HOME")
    root = Path(configured) if configured else Path.home() / ".config"
    return root / "herdr-jev-router" / "preferences.md"


def resolve_preferences(environment: Mapping[str, str]) -> PreferencesResolution:
    """Read the optional preferences file, or fall back to the default.

    A missing file is the normal case and yields the default. An unsafe path
    (a symlink, a non-regular file, another user's file, a writable directory,
    invalid UTF-8, or more than `MAX_PREFERENCES_BYTES`) yields a problem that
    the routing commands turn into a stable `invalid_preferences` denial.
    """

    return _read_preferences_file(preferences_path(environment))


def _read_preferences_file(path: Path) -> PreferencesResolution:
    """Read and validate the preferences file without following symlinks."""

    try:
        parent = path.parent.stat()
    except OSError:
        return PreferencesResolution(default_preferences())
    # The preferences are not secret, so any owner mode is fine, but another
    # user who can write the directory could swap the file the router reads.
    if (
        not stat.S_ISDIR(parent.st_mode)
        or parent.st_uid != os.geteuid()
        or parent.st_mode & 0o022
    ):
        return PreferencesResolution(
            default_preferences(),
            "preferences directory must be owned by you and not "
            "group- or world-writable",
        )
    try:
        # O_NOFOLLOW plus fstat close the race between a pre-check and the
        # open, matching the key-file read in cli.py. O_NONBLOCK keeps a FIFO
        # at the path from blocking the open before fstat can reject it.
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return PreferencesResolution(default_preferences())
    except OSError:
        return PreferencesResolution(
            default_preferences(),
            "preferences must be a regular file owned by you, not a symlink",
        )
    try:
        details = os.fstat(descriptor)
        if not stat.S_ISREG(details.st_mode) or details.st_uid != os.geteuid():
            return PreferencesResolution(
                default_preferences(),
                "preferences must be a regular file owned by you, not a symlink",
            )
        data = os.read(descriptor, MAX_PREFERENCES_BYTES + 1)
    except OSError:
        return PreferencesResolution(
            default_preferences(), "preferences could not be read safely"
        )
    finally:
        os.close(descriptor)
    if len(data) > MAX_PREFERENCES_BYTES:
        return PreferencesResolution(
            default_preferences(), "preferences file is too large"
        )
    try:
        text = data.decode("utf-8").strip()
    except UnicodeDecodeError:
        return PreferencesResolution(
            default_preferences(), "preferences must contain UTF-8 text"
        )
    if not text:
        return PreferencesResolution(default_preferences())
    return PreferencesResolution(Preferences(text, _FILE_SOURCE, path))
