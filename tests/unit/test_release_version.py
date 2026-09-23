"""Keep every current-version statement in sync for a release.

A release bumps `pyproject.toml`, the plugin manifest, the Codex app-server
clientInfo, the changelog, and the README install pin. This guard fails when
one of them is missed, which is otherwise invisible until an install or a
Codex handshake reports the wrong version.

The README trailer is release-facing too: both of its links must follow the
latest release so they never point at a superseded asset.
"""

import tomllib
from pathlib import Path

import herdr_jev_router
from herdr_jev_router import quota_codex

ROOT = Path(__file__).parents[2]

_TRAILER_URL = (
    "https://github.com/boriscardano/herdr-jev-router/releases/"
    "latest/download/herdr-jev-router-trailer.mp4"
)
_TRAILER_CAPTION = (
    "Real run on stock Herdr: asked in plain language to do three things in "
    "parallel, a master agent found the skill on its own and every child was "
    "routed by Jev."
)


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


def test_readme_trailer_links_follow_the_latest_release() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert readme.count(_TRAILER_URL) == 2
    assert "releases/download/v0.1.0/herdr-jev-router-trailer.mp4" not in readme


def test_readme_trailer_caption_is_the_plain_release_caption() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    flattened = " ".join(readme.split())

    assert flattened.count(_TRAILER_CAPTION) == 1
    assert not any(mark in _TRAILER_CAPTION for mark in ("\u2014", ";", "**"))
