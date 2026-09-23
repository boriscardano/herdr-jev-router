"""Cover the owner-only key-file fallback and doctor source reporting."""

import io
import json
import os
import stat
from pathlib import Path

import pytest

import herdr_jev_router.cli as cli_module
from herdr_jev_router.cli import main
from herdr_jev_router.jev import JevRoutingResult

_KEY_FILE_MAX_BYTES = 4096
_SENTINEL = "SENTINEL_KEY_FILE_SECRET"


def key_path(config_home: Path) -> Path:
    return config_home / "herdr-jev-router" / "key"


def write_key(config_home: Path, value: str, *, mode: int = 0o600) -> Path:
    path = key_path(config_home)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")
    path.chmod(mode)
    return path


def resolve(environment: dict[str, str]) -> cli_module._KeyResolution:
    return cli_module._resolve_key(environment)


def test_environment_key_wins_over_the_key_file(tmp_path: Path) -> None:
    config_home = tmp_path / "config"
    write_key(config_home, "file-key")

    result = resolve(
        {
            "TYPESAFE_API_KEY": "environment-key",
            "XDG_CONFIG_HOME": os.fspath(config_home),
        }
    )

    assert result.key == "environment-key"
    assert result.source == "environment"
    assert result.problem is None


def test_key_file_is_used_when_the_environment_is_unset(tmp_path: Path) -> None:
    config_home = tmp_path / "config"
    write_key(config_home, f"  {_SENTINEL}\n")

    result = resolve({"XDG_CONFIG_HOME": os.fspath(config_home)})

    assert result.key == _SENTINEL
    assert result.source == "key file"
    assert result.problem is None


def test_blank_environment_key_falls_back_to_the_key_file(tmp_path: Path) -> None:
    config_home = tmp_path / "config"
    write_key(config_home, "file-key")

    result = resolve(
        {
            "TYPESAFE_API_KEY": "   ",
            "XDG_CONFIG_HOME": os.fspath(config_home),
        }
    )

    assert result.key == "file-key"
    assert result.source == "key file"


def test_missing_key_file_reports_no_key(tmp_path: Path) -> None:
    result = resolve({"XDG_CONFIG_HOME": os.fspath(tmp_path / "config")})

    assert result.key is None
    assert result.source is None
    assert result.problem is None


def test_empty_key_file_reports_no_key(tmp_path: Path) -> None:
    config_home = tmp_path / "config"
    write_key(config_home, "  \n")

    result = resolve({"XDG_CONFIG_HOME": os.fspath(config_home)})

    assert result.key is None
    assert result.source is None
    assert result.problem is None


def test_symlinked_key_file_is_rejected(tmp_path: Path) -> None:
    config_home = tmp_path / "config"
    target = write_key(config_home, "file-key")
    link = key_path(config_home)
    link.unlink()
    link.symlink_to(target)

    result = resolve({"XDG_CONFIG_HOME": os.fspath(config_home)})

    assert result.key is None
    assert result.problem is not None


def test_wrong_mode_key_file_is_rejected_with_a_fix(tmp_path: Path) -> None:
    config_home = tmp_path / "config"
    write_key(config_home, "file-key", mode=0o644)

    result = resolve({"XDG_CONFIG_HOME": os.fspath(config_home)})

    assert result.key is None
    assert result.problem is not None
    assert "chmod 600" in result.problem


def test_key_file_owned_by_another_user_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_home = tmp_path / "config"
    path = write_key(config_home, "file-key")
    real = os.stat(path)
    foreign = os.stat_result(
        (
            real.st_mode,
            real.st_ino,
            real.st_dev,
            real.st_nlink,
            424242,
            real.st_gid,
            real.st_size,
            real.st_atime,
            real.st_mtime,
            real.st_ctime,
        )
    )
    monkeypatch.setattr(cli_module.os, "fstat", lambda descriptor: foreign)

    result = resolve({"XDG_CONFIG_HOME": os.fspath(config_home)})

    assert result.key is None
    assert result.problem is not None
    assert "owner" in result.problem


def test_group_writable_parent_directory_is_rejected(tmp_path: Path) -> None:
    config_home = tmp_path / "config"
    write_key(config_home, "file-key")
    key_path(config_home).parent.chmod(0o770)

    result = resolve({"XDG_CONFIG_HOME": os.fspath(config_home)})

    assert result.key is None
    assert result.problem is not None
    assert "group" in result.problem or "world" in result.problem


def test_oversized_key_file_is_rejected(tmp_path: Path) -> None:
    config_home = tmp_path / "config"
    write_key(config_home, "k" * (_KEY_FILE_MAX_BYTES + 1))

    result = resolve({"XDG_CONFIG_HOME": os.fspath(config_home)})

    assert result.key is None
    assert result.problem is not None


def test_owner_only_parent_directory_is_accepted(tmp_path: Path) -> None:
    config_home = tmp_path / "config"
    write_key(config_home, "file-key")
    key_path(config_home).parent.chmod(0o700)

    result = resolve({"XDG_CONFIG_HOME": os.fspath(config_home)})

    assert result.key == "file-key"
    assert result.problem is None


def jev_result() -> JevRoutingResult:
    return JevRoutingResult(
        model="jev-test-1",
        answers={
            "harness": {"type": "choice", "value": "codex"},
            "claude_model": {"type": "choice", "value": "sonnet"},
            "codex_model": {"type": "choice", "value": "terra"},
            "opencode_model": {"type": "choice", "value": "deepseek"},
            "pi_model": {"type": "choice", "value": "deepseek"},
            "effort": {"type": "choice", "value": "high"},
        },
        probabilities={
            "harness": {"claude": 0.2, "codex": 0.5, "opencode": 0.2, "pi": 0.1},
            "claude_model": {"haiku": 0.2, "sonnet": 0.6, "opus": 0.2},
            "codex_model": {"luna": 0.2, "terra": 0.7, "sol": 0.1},
            "opencode_model": {"deepseek": 0.6, "glm": 0.3, "kimi": 0.1},
            "pi_model": {"deepseek": 0.6, "glm": 0.3, "kimi": 0.1},
            "effort": {
                "low": 0.05,
                "medium": 0.15,
                "high": 0.6,
                "xhigh": 0.15,
                "max": 0.05,
            },
        },
        confidences={
            "harness": 0.5,
            "claude_model": 0.4,
            "codex_model": 0.5,
            "opencode_model": 0.5,
            "pi_model": 0.5,
            "effort": 0.5,
        },
        usage={"input_tokens": 10, "output_tokens": 5},
    )


def test_explain_uses_the_key_file_and_never_leaks_it(tmp_path: Path) -> None:
    config_home = tmp_path / "config"
    write_key(config_home, _SENTINEL)
    calls: list[dict[str, object]] = []

    async def fake_jev(**kwargs: object) -> JevRoutingResult:
        calls.append(kwargs)
        return jev_result()

    stdout = io.StringIO()
    code = main(
        [
            "explain",
            "Review the authentication change.",
            "--state-dir",
            str(tmp_path / "state"),
            "--audit-path",
            str(tmp_path / "audit.jsonl"),
        ],
        stdout=stdout,
        environ={
            "XDG_CONFIG_HOME": os.fspath(config_home),
            "HERDR_JEV_ROUTER_OPENCODE": (
                "provider=opencode-go,small=deepseek-v4.1-flash,"
                "balanced=glm-5.3,large=kimi-k3"
            ),
            "HERDR_JEV_ROUTER_PI": (
                "provider=opencode-go,small=deepseek-v4.1-flash,"
                "balanced=glm-5.3,large=kimi-k3"
            ),
        },
        jev_callable=fake_jev,
        clock=lambda: 1_000.0,
        command_finder=lambda name: f"/usr/bin/{name}",
    )

    assert code == 0
    assert calls and calls[0]["api_key"] == _SENTINEL
    assert _SENTINEL not in stdout.getvalue()
    audit = (tmp_path / "audit.jsonl").read_text(encoding="utf-8")
    assert _SENTINEL not in audit


def test_explain_fails_closed_without_any_key(tmp_path: Path) -> None:
    stdout = io.StringIO()
    code = main(
        [
            "explain",
            "Review the authentication change.",
            "--state-dir",
            str(tmp_path / "state"),
        ],
        stdout=stdout,
        environ={"XDG_CONFIG_HOME": os.fspath(tmp_path / "config")},
        command_finder=lambda name: f"/usr/bin/{name}",
    )

    assert code == 2
    assert json.loads(stdout.getvalue())["denial"]["code"] == "configuration_failed"


def test_explain_fails_closed_on_an_unsafe_key_file(tmp_path: Path) -> None:
    config_home = tmp_path / "config"
    write_key(config_home, _SENTINEL, mode=0o644)
    stdout = io.StringIO()
    code = main(
        [
            "explain",
            "Review the authentication change.",
            "--state-dir",
            str(tmp_path / "state"),
        ],
        stdout=stdout,
        environ={"XDG_CONFIG_HOME": os.fspath(config_home)},
        command_finder=lambda name: f"/usr/bin/{name}",
    )

    assert code == 2
    assert json.loads(stdout.getvalue())["denial"]["code"] == "configuration_failed"
    assert _SENTINEL not in stdout.getvalue()


def doctor(tmp_path: Path, environment: dict[str, str]) -> tuple[int, dict]:
    stdout = io.StringIO()
    code = main(
        ["doctor", "--state-dir", str(tmp_path / "state")],
        stdout=stdout,
        environ=environment,
        command_finder=lambda name: f"/usr/bin/{name}",
    )
    return code, json.loads(stdout.getvalue())


def test_doctor_reports_the_environment_source(tmp_path: Path) -> None:
    _, result = doctor(tmp_path, {"TYPESAFE_API_KEY": _SENTINEL})

    credential = result["checks"]["credential"]
    assert credential["ok"] is True
    assert credential["source"] == "environment"
    assert _SENTINEL not in json.dumps(result)


def test_doctor_reports_the_key_file_source(tmp_path: Path) -> None:
    config_home = tmp_path / "config"
    write_key(config_home, _SENTINEL)

    _, result = doctor(tmp_path, {"XDG_CONFIG_HOME": os.fspath(config_home)})

    credential = result["checks"]["credential"]
    assert credential["ok"] is True
    assert credential["source"] == "key file"
    assert _SENTINEL not in json.dumps(result)


def test_doctor_human_reports_the_key_file_source(tmp_path: Path) -> None:
    config_home = tmp_path / "config"
    write_key(config_home, _SENTINEL)
    (tmp_path / "state").mkdir(mode=0o700)
    stdout = io.StringIO()

    code = main(
        ["doctor", "--human", "--state-dir", str(tmp_path / "state")],
        stdout=stdout,
        environ={"XDG_CONFIG_HOME": os.fspath(config_home)},
        command_finder=lambda name: f"/usr/bin/{name}",
    )

    assert code == 0
    output = stdout.getvalue()
    assert "TypeSafe key: ok (from key file)" in output
    assert _SENTINEL not in output


def test_doctor_human_reports_an_unsafe_key_file_with_a_fix(tmp_path: Path) -> None:
    config_home = tmp_path / "config"
    write_key(config_home, _SENTINEL, mode=0o644)
    stdout = io.StringIO()

    code = main(
        ["doctor", "--human", "--state-dir", str(tmp_path / "state")],
        stdout=stdout,
        environ={"XDG_CONFIG_HOME": os.fspath(config_home)},
        command_finder=lambda name: f"/usr/bin/{name}",
    )

    assert code == 1
    output = stdout.getvalue()
    assert "chmod 600" in output
    assert _SENTINEL not in output


def test_doctor_json_reports_an_unsafe_key_file(tmp_path: Path) -> None:
    config_home = tmp_path / "config"
    write_key(config_home, _SENTINEL, mode=0o644)

    code, result = doctor(tmp_path, {"XDG_CONFIG_HOME": os.fspath(config_home)})

    assert code == 1
    credential = result["checks"]["credential"]
    assert credential["ok"] is False
    assert credential["source"] == "key file"
    assert "chmod 600" in credential["problem"]
    assert _SENTINEL not in json.dumps(result)


def test_key_file_mode_is_owner_only(tmp_path: Path) -> None:
    config_home = tmp_path / "config"
    path = write_key(config_home, "file-key")

    assert stat.S_IMODE(path.stat().st_mode) == 0o600
