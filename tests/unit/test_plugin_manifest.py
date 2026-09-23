"""Encode the stock Herdr 0.9.1 plugin-manifest rules for this repository.

The rules mirror `src/app/api/plugins/manifest.rs` in stock Herdr 0.9.1:
`id`, `name`, `version` and `min_herdr_version` are required; `id` is limited
to `[A-Za-z0-9:._-]`; `platforms` entries are `linux`, `macos` or `windows`;
and every build command is a non-empty argv. No extra field is required.
"""

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).parents[2]
MANIFEST = ROOT / "herdr-plugin.toml"
_IDENTIFIER = re.compile(r"[A-Za-z0-9:._-]{1,120}\Z")
_PLATFORMS = frozenset({"linux", "macos", "windows"})


def load_manifest() -> dict[str, object]:
    """Return the parsed plugin manifest for this repository."""

    return tomllib.loads(MANIFEST.read_text(encoding="utf-8"))


def test_manifest_declares_the_fields_the_stock_parser_requires() -> None:
    manifest = load_manifest()

    for field in ("id", "name", "version", "min_herdr_version"):
        value = manifest.get(field)
        assert isinstance(value, str)
        assert value.strip()
    assert _IDENTIFIER.fullmatch(str(manifest["id"]).strip())
    platforms = manifest.get("platforms")
    assert isinstance(platforms, list) and platforms
    assert all(platform in _PLATFORMS for platform in platforms)


def test_manifest_min_herdr_version_is_not_newer_than_the_stock_target() -> None:
    manifest = load_manifest()

    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", str(manifest["min_herdr_version"]))
    assert match is not None
    assert tuple(int(part) for part in match.groups()) <= (0, 9, 1)


def test_manifest_build_commands_are_non_empty_argv() -> None:
    manifest = load_manifest()

    build = manifest.get("build")
    assert isinstance(build, list) and build
    for entry in build:
        assert isinstance(entry, dict)
        command = entry.get("command")
        assert isinstance(command, list) and command
        assert all(isinstance(argument, str) and argument for argument in command)
