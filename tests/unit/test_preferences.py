"""Cover the optional preferences file and the built-in default."""

from pathlib import Path

from herdr_jev_router.preferences import (
    DEFAULT_PREFERENCES,
    MAX_PREFERENCES_BYTES,
    PreferencesResolution,
    preferences_path,
    resolve_preferences,
)


def _directory(tmp_path: Path) -> Path:
    directory = tmp_path / "herdr-jev-router"
    directory.mkdir(mode=0o755, exist_ok=True)
    directory.chmod(0o755)
    return directory


def _write(tmp_path: Path, text: str) -> Path:
    path = _directory(tmp_path) / "preferences.md"
    path.write_text(text, encoding="utf-8")
    path.chmod(0o644)
    return path


def _resolve(tmp_path: Path) -> PreferencesResolution:
    return resolve_preferences({"XDG_CONFIG_HOME": str(tmp_path)})


def test_missing_file_uses_the_built_in_default(tmp_path: Path) -> None:
    resolution = _resolve(tmp_path)

    assert resolution.problem is None
    assert resolution.preferences.text == DEFAULT_PREFERENCES
    assert resolution.preferences.source == "default"
    assert resolution.preferences.length == len(DEFAULT_PREFERENCES)


def test_preferences_path_prefers_xdg_then_home(tmp_path: Path, monkeypatch) -> None:
    assert preferences_path({"XDG_CONFIG_HOME": str(tmp_path)}) == (
        tmp_path / "herdr-jev-router" / "preferences.md"
    )

    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    assert preferences_path({}) == (
        home / ".config" / "herdr-jev-router" / "preferences.md"
    )


def test_owner_owned_file_is_read_verbatim_at_mode_0644(tmp_path: Path) -> None:
    path = _write(tmp_path, "Use Pi deepseek as the workhorse.\n")

    resolution = _resolve(tmp_path)

    assert resolution.problem is None
    assert resolution.preferences.text == "Use Pi deepseek as the workhorse."
    assert resolution.preferences.source == "file"
    assert resolution.preferences.path == path
    assert resolution.preferences.length == len("Use Pi deepseek as the workhorse.")


def test_empty_file_falls_back_to_the_default(tmp_path: Path) -> None:
    _write(tmp_path, "  \n")

    resolution = _resolve(tmp_path)

    assert resolution.problem is None
    assert resolution.preferences.source == "default"


def test_symlinked_file_is_rejected(tmp_path: Path) -> None:
    target = tmp_path / "elsewhere.md"
    target.write_text("hidden", encoding="utf-8")
    (_directory(tmp_path) / "preferences.md").symlink_to(target)

    resolution = _resolve(tmp_path)

    assert resolution.problem is not None
    assert resolution.preferences.source == "default"


def test_oversized_file_is_rejected(tmp_path: Path) -> None:
    _write(tmp_path, "x" * (MAX_PREFERENCES_BYTES + 1))

    resolution = _resolve(tmp_path)

    assert resolution.problem == "preferences file is too large"
    assert resolution.preferences.source == "default"


def test_non_utf8_file_is_rejected(tmp_path: Path) -> None:
    path = _directory(tmp_path) / "preferences.md"
    path.write_bytes(b"\xff\xfe\x00")

    resolution = _resolve(tmp_path)

    assert resolution.problem == "preferences must contain UTF-8 text"


def test_group_writable_directory_is_rejected(tmp_path: Path) -> None:
    _write(tmp_path, "steer")
    (tmp_path / "herdr-jev-router").chmod(0o775)

    resolution = _resolve(tmp_path)

    assert resolution.problem is not None
    assert resolution.preferences.source == "default"
