"""Keep the test suite away from the developer's real credential and config.

The router reads `TYPESAFE_API_KEY` and, when it is unset, the owner-only key
file at `$XDG_CONFIG_HOME/herdr-jev-router/key` or, when that is unset,
`~/.config/herdr-jev-router/key`. `Path.home()` reads `HOME` from the process
environment, so a test cannot isolate the key file through `main`'s `environ`
argument alone. A developer machine with a real key file would otherwise leak
that key into the suite. A test that sets any of these variables itself still
wins, because its own setup runs after this fixture.
"""

import os
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolate_developer_credential_and_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Point HOME and XDG_CONFIG_HOME at a per-test directory and drop the key."""

    monkeypatch.setenv("HOME", os.fspath(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", os.fspath(tmp_path))
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
