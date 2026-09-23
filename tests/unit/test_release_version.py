"""Keep every current-version statement in sync for a release.

A release bumps `pyproject.toml`, the plugin manifest, the Codex app-server
clientInfo, the changelog, and the README install pin. This guard fails when
one of them is missed, which is otherwise invisible until an install or a
Codex handshake reports the wrong version.
"""

import tomllib
from pathlib import Path

import herdr_jev_router
from herdr_jev_router import quota_codex

ROOT = Path(__file__).parents[2]


def _pyproject_version() -> str:
    document = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return document["project"]["version"]


def test_every_current_version_statement_matches_pyproject() -> None:
    version = _pyproject_version()
    manifest = tomllib.loads((ROOT / "herdr-plugin.toml").read_text(encoding="utf-8"))
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")

    assert manifest["version"] == version
    assert quota_codex.CLIENT_VERSION == version
    assert herdr_jev_router.__version__ == version
    assert f"herdr-jev-router@v{version}" in readme
    assert f"## [{version}]" in changelog
