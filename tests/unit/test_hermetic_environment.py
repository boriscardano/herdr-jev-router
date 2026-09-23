"""Guard the suite against the developer's real credential and config.

`main` reads `TYPESAFE_API_KEY` and, when it is unset, the owner-only key file
under `$XDG_CONFIG_HOME` or, when that is unset, `~/.config`. `Path.home()` reads
`HOME` from the process environment, so a test cannot isolate the key file
through `main`'s `environ` argument alone. This guard fails on a developer
machine that has a real key file when the autouse fixture in
`tests/conftest.py` is removed.
"""

import os

import herdr_jev_router.cli as cli_module


def test_the_ambient_environment_has_no_router_credential() -> None:
    assert cli_module._resolve_key(os.environ).key is None
    assert cli_module._resolve_key({}).key is None
