"""Guard the suite against the developer's real credential and config.

`main` reads `TYPESAFE_API_KEY` and, when it is unset, the owner-only key file
under `$XDG_CONFIG_HOME` or, when that is unset, `~/.config`. `Path.home()` reads
`HOME` from the process environment, so a test cannot isolate the key file
through `main`'s `environ` argument alone. The autouse fixture in
`tests/conftest.py` points both variables at a per-test directory and drops the
key. The first assertions fail when that fixture is removed, even on a machine
with no real key, because the derived paths leave the per-test directory. The
last assertion keeps the other half of the contract: a test that sets the
variables itself still wins over the fixture.
"""

import os
from pathlib import Path

import pytest

import herdr_jev_router.cli as cli_module


def test_the_ambient_environment_has_no_router_credential(tmp_path: Path) -> None:
    assert "TYPESAFE_API_KEY" not in os.environ
    assert cli_module._key_file_path(os.environ).is_relative_to(tmp_path)
    assert cli_module._key_file_path({}).is_relative_to(tmp_path)
    assert cli_module._resolve_key(os.environ).key is None
    assert cli_module._resolve_key({}).key is None


def test_a_test_can_still_set_the_variables_itself(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    configured = tmp_path / "own-config"
    monkeypatch.setenv("XDG_CONFIG_HOME", os.fspath(configured))
    monkeypatch.setenv("TYPESAFE_API_KEY", "own-key")

    assert os.environ["XDG_CONFIG_HOME"] == os.fspath(configured)
    assert cli_module._resolve_key(os.environ).key == "own-key"
