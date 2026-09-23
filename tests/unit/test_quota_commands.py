from dataclasses import replace
from pathlib import Path

from herdr_jev_router.quota import QuotaSnapshot, QuotaWindow
from herdr_jev_router.quota_commands import (
    MAX_STATUS_LINE_CHARS,
    claude_main,
    codex_main,
)


def snapshot(provider: str) -> QuotaSnapshot:
    return QuotaSnapshot(
        provider=provider,
        source=f"{provider}_test",
        observed_at=1_800_000_000,
        captured_at=1_800_000_000,
        windows=(
            QuotaWindow("five_hour", 25, 75, 18_000, 1_800_018_000),
            QuotaWindow("seven_day", 40, 60, 604_800, 1_800_604_800),
        ),
    )


def test_codex_command_refreshes_requested_state_directory(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    observed: list[Path] = []

    async def fake_collect(state_dir: Path) -> QuotaSnapshot:
        observed.append(state_dir)
        return snapshot("codex")

    monkeypatch.setattr("herdr_jev_router.quota_commands.collect_codex", fake_collect)

    result = codex_main(["--state-dir", str(tmp_path)])

    assert result == 0
    assert observed == [tmp_path]
    assert capsys.readouterr().out == "Codex 5h 75% | 7d 60%\n"


def test_codex_command_reports_collection_failure(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    async def fake_collect(state_dir: Path) -> None:
        return None

    monkeypatch.setattr("herdr_jev_router.quota_commands.collect_codex", fake_collect)

    result = codex_main(["--state-dir", str(tmp_path)])

    assert result == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == "Codex quota unavailable\n"


def test_codex_command_treats_an_empty_window_snapshot_as_unavailable(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    async def fake_collect(state_dir: Path) -> QuotaSnapshot:
        return replace(snapshot("codex"), windows=())

    monkeypatch.setattr("herdr_jev_router.quota_commands.collect_codex", fake_collect)

    result = codex_main(["--state-dir", str(tmp_path)])

    assert result == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == "Codex quota unavailable\n"


def test_codex_command_marks_retained_stale_quota(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    async def fake_collect(state_dir: Path) -> QuotaSnapshot:
        return replace(snapshot("codex"), freshness="stale")

    monkeypatch.setattr("herdr_jev_router.quota_commands.collect_codex", fake_collect)

    result = codex_main(["--state-dir", str(tmp_path)])

    assert result == 0
    assert capsys.readouterr().out == "Codex 5h 75% | 7d 60% (stale)\n"


def test_claude_command_captures_stdin_and_prints_short_status(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    observed: list[tuple[str, Path]] = []

    def fake_capture(
        document: str, cache_path: Path, *, captured_at: float
    ) -> QuotaSnapshot:
        observed.append((document, cache_path))
        assert captured_at > 0
        return snapshot("claude")

    monkeypatch.setattr(
        "herdr_jev_router.quota_commands.capture_status_line", fake_capture
    )
    monkeypatch.setattr("sys.stdin.read", lambda *args: '{"rate_limits":{}}')

    result = claude_main(["--state-dir", str(tmp_path)])

    assert result == 0
    assert observed == [('{"rate_limits":{}}', tmp_path / "claude-quota.json")]
    assert capsys.readouterr().out == "Claude 5h 75% | 7d 60%\n"


def test_claude_command_supports_quiet_status_line(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    monkeypatch.setattr(
        "herdr_jev_router.quota_commands.capture_status_line",
        lambda document, cache_path, captured_at: snapshot("claude"),
    )
    monkeypatch.setattr("sys.stdin.read", lambda *args: "{}")

    result = claude_main(["--state-dir", str(tmp_path), "--quiet"])

    assert result == 0
    assert capsys.readouterr().out == ""


def test_claude_command_leaves_status_line_empty_on_invalid_input(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    monkeypatch.setattr(
        "herdr_jev_router.quota_commands.capture_status_line",
        lambda document, cache_path, captured_at: None,
    )
    monkeypatch.setattr("sys.stdin.read", lambda *args: "not json")

    result = claude_main(["--state-dir", str(tmp_path)])

    assert result == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == ""


def test_claude_command_rejects_an_oversized_status_line(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    observed: list[str] = []

    def fake_capture(document: str, cache_path: Path, *, captured_at: float):
        observed.append(document)
        return snapshot("claude")

    monkeypatch.setattr(
        "herdr_jev_router.quota_commands.capture_status_line", fake_capture
    )
    monkeypatch.setattr(
        "sys.stdin.read",
        lambda *args: "x" * (MAX_STATUS_LINE_CHARS + 1),
    )

    result = claude_main(["--state-dir", str(tmp_path)])

    assert result == 1
    assert observed == []
    assert capsys.readouterr().out == ""


def test_claude_command_treats_an_empty_window_snapshot_as_unavailable(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    monkeypatch.setattr(
        "herdr_jev_router.quota_commands.capture_status_line",
        lambda document, cache_path, captured_at: replace(
            snapshot("claude"), windows=()
        ),
    )
    monkeypatch.setattr("sys.stdin.read", lambda *args: "{}")

    result = claude_main(["--state-dir", str(tmp_path)])

    assert result == 1
    assert capsys.readouterr().out == ""
