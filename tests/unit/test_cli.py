import io
import json
import os
import tomllib
from functools import partial
from pathlib import Path

import pytest
from httpx2 import MockTransport, Request, Response

import herdr_jev_router.cli as cli_module
import herdr_jev_router.router as router_module
from herdr_jev_router.cli import main
from herdr_jev_router.jev import JevRoutingResult, route_with_jev
from herdr_jev_router.quota import QuotaSnapshot, QuotaWindow, write_cache

_OPT_IN_ENVIRONMENT = {
    "HERDR_JEV_ROUTER_OPENCODE": (
        "provider=opencode-go,small=deepseek-v4.1-flash,balanced=glm-5.3,large=kimi-k3"
    ),
    "HERDR_JEV_ROUTER_PI": (
        "provider=opencode-go,small=deepseek-v4.1-flash,balanced=glm-5.3,large=kimi-k3"
    ),
}


def all_commands(name: str) -> str:
    """Return a path for every command name a test may look up."""

    return f"/usr/bin/{name}"


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
            "harness": {
                "claude": 0.2,
                "codex": 0.5,
                "opencode": 0.2,
                "pi": 0.1,
            },
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


def cache(
    path: Path,
    *,
    provider: str,
    source: str,
    remaining: float,
    now: float,
) -> None:
    write_cache(
        path,
        QuotaSnapshot(
            provider=provider,
            source=source,
            observed_at=now,
            captured_at=now,
            windows=(
                QuotaWindow(
                    "primary",
                    100 - remaining,
                    remaining,
                    3_600,
                    now + 1_800,
                ),
            ),
        ),
    )


def invoke_explain(
    tmp_path: Path,
    jev_callable,
    *,
    task: str = "Review the authentication change.",
    extra: tuple[str, ...] = (),
    environ: dict[str, str] | None = None,
    command_finder=None,
) -> tuple[int, str]:
    stdout = io.StringIO()
    environment = dict(_OPT_IN_ENVIRONMENT)
    if environ is None:
        environment["TYPESAFE_API_KEY"] = "test-key"
    else:
        environment.update(environ)
    code = main(
        [
            "explain",
            task,
            *extra,
            "--state-dir",
            str(tmp_path / "state"),
            "--audit-path",
            str(tmp_path / "audit.jsonl"),
        ],
        stdout=stdout,
        environ=environment,
        jev_callable=jev_callable,
        clock=lambda: 1_000.0,
        command_finder=all_commands if command_finder is None else command_finder,
    )
    return code, stdout.getvalue()


def test_help_documents_safe_configuration_defaults(capsys) -> None:
    with pytest.raises(SystemExit) as error:
        main(["spawn", "--help"])

    assert error.value.code == 0
    help_text = capsys.readouterr().out
    assert "TYPESAFE_API_KEY" in help_text
    assert "HERDR_JEV_ROUTER_STATE_DIR" in help_text
    assert "<state-dir>/routing.jsonl" in help_text


def test_explain_prints_human_readable_review_and_audits_without_starting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = tmp_path / "state"
    cache(
        state / "claude-quota.json",
        provider="claude",
        source="claude_status_line",
        remaining=0,
        now=1_000,
    )
    cache(
        state / "codex-quota.json",
        provider="codex",
        source="codex_app_server",
        remaining=90,
        now=1_000,
    )
    calls = 0

    async def fake_jev(**kwargs: object) -> JevRoutingResult:
        nonlocal calls
        calls += 1
        result = jev_result()
        result.probabilities["harness"] = {
            "codex": 0.6,
            "opencode": 0.3,
            "pi": 0.1,
        }
        return result

    def refuse_to_start(*args: object, **kwargs: object) -> None:
        raise AssertionError("explain must not run the Herdr CLI")

    monkeypatch.setattr(cli_module.subprocess, "run", refuse_to_start)

    code, output = invoke_explain(
        tmp_path,
        fake_jev,
        environ={"TYPESAFE_API_KEY": "SENTINEL_TYPESAFE_KEY"},
    )

    assert code == 0
    assert "recommended harness: codex" in output
    assert "selected model: terra" in output
    assert "effort: high" in output
    assert "capacity claude: exhausted" in output
    assert "capacity codex: surplus" in output
    assert "capacity opencode: unknown" in output
    assert "capacity pi: unknown" in output
    assert "SENTINEL_TYPESAFE_KEY" not in output
    assert "authorization" not in output.lower()
    assert "launch" not in output.lower()
    assert calls == 1
    with pytest.raises(json.JSONDecodeError):
        json.loads(output)
    audit = (tmp_path / "audit.jsonl").read_text()
    assert "SENTINEL_TYPESAFE_KEY" not in audit
    records = [json.loads(line) for line in audit.splitlines()]
    assert len(records) == 1
    assert records[0]["phase"] == "recommendation"


def test_explain_rejects_injected_launch_fields(tmp_path: Path) -> None:
    called = False

    async def fake_jev(**kwargs: object) -> JevRoutingResult:
        nonlocal called
        called = True
        return jev_result()

    code, output = invoke_explain(tmp_path, fake_jev, extra=("--harness", "codex"))

    assert code == 2
    assert json.loads(output) == {
        "version": 2,
        "request_id": None,
        "denial": {"code": "invalid_request", "message": "routing denied"},
    }
    assert called is False


def test_explain_rejects_an_undocumented_role(tmp_path: Path) -> None:
    called = False

    async def fake_jev(**kwargs: object) -> JevRoutingResult:
        nonlocal called
        called = True
        return jev_result()

    code, output = invoke_explain(tmp_path, fake_jev, extra=("--role", "boss"))

    assert code == 2
    assert json.loads(output)["denial"]["code"] == "invalid_request"
    assert called is False


class _BrokenPipeOutput(io.StringIO):
    """Stand in for a stdout whose reader has closed the pipe."""

    def write(self, *args: object, **kwargs: object) -> int:
        raise BrokenPipeError


@pytest.mark.parametrize(
    "task",
    [
        "escape\x1b[201~task",
        "eight-bit-csi\x9b201~task",
        "padding\x80task",
        "nel\x85task",
        "osc\x9d0;titletask",
        "unit-separator\x9ftask",
    ],
)
def test_explain_rejects_control_characters_in_the_task(
    tmp_path: Path, task: str
) -> None:
    called = False

    async def fake_jev(**kwargs: object) -> JevRoutingResult:
        nonlocal called
        called = True
        return jev_result()

    code, output = invoke_explain(tmp_path, fake_jev, task=task)

    assert code == 2
    assert json.loads(output)["denial"]["code"] == "invalid_request"
    assert called is False


@pytest.mark.parametrize(
    "task",
    [
        "a\ud800b",
        "lone-high\ud800",
        "lone-low\udcff",
    ],
)
def test_explain_rejects_lone_surrogates_in_the_task(tmp_path: Path, task: str) -> None:
    called = False

    async def fake_jev(**kwargs: object) -> JevRoutingResult:
        nonlocal called
        called = True
        return jev_result()

    code, output = invoke_explain(tmp_path, fake_jev, task=task)

    assert code == 2
    assert json.loads(output)["denial"]["code"] == "invalid_request"
    assert called is False


@pytest.mark.parametrize(
    "task",
    [
        "\u200b",
        "\u200b\u200c\u200d",
        "\u2060",
        "\ufeff",
        " \u200b\t\u200c ",
    ],
)
def test_explain_rejects_invisible_only_tasks(tmp_path: Path, task: str) -> None:
    called = False

    async def fake_jev(**kwargs: object) -> JevRoutingResult:
        nonlocal called
        called = True
        return jev_result()

    code, output = invoke_explain(tmp_path, fake_jev, task=task)

    assert code == 2
    assert json.loads(output)["denial"]["code"] == "invalid_request"
    assert called is False


def test_explain_allows_format_characters_inside_visible_text(tmp_path: Path) -> None:
    calls = 0

    async def fake_jev(**kwargs: object) -> JevRoutingResult:
        nonlocal calls
        calls += 1
        return jev_result()

    code, output = invoke_explain(tmp_path, fake_jev, task="\u200bFix the test\u2060")

    assert code == 0
    assert "recommended harness:" in output
    assert calls == 1


def test_explain_allows_non_ascii_task_text(tmp_path: Path) -> None:
    calls = 0

    async def fake_jev(**kwargs: object) -> JevRoutingResult:
        nonlocal calls
        calls += 1
        return jev_result()

    code, output = invoke_explain(
        tmp_path, fake_jev, task="Überprüfe die README\u00a0🚀"
    )

    assert code == 0
    assert "recommended harness:" in output
    assert calls == 1


def test_a_broken_stdout_pipe_does_not_escape_as_a_traceback(
    tmp_path: Path,
) -> None:
    usage_code = main(
        ["usage", "--state-dir", str(tmp_path / "state")],
        stdout=_BrokenPipeOutput(),
        environ={},
        command_finder=all_commands,
    )
    spawn_code = main(
        [
            "spawn",
            "Review the authentication change.",
            "--name",
            "worker",
            "--pane",
            "w1:p2",
            "--state-dir",
            str(tmp_path / "state"),
        ],
        stdout=_BrokenPipeOutput(),
        environ={},
        command_finder=all_commands,
    )

    assert usage_code == 0
    assert spawn_code == 2


def test_explain_fails_closed_with_router_error_code_and_no_secret(
    tmp_path: Path,
) -> None:
    async def fake_jev(**kwargs: object) -> JevRoutingResult:
        raise RuntimeError("SENTINEL_PROVIDER_CREDENTIAL")

    code, output = invoke_explain(
        tmp_path,
        fake_jev,
        environ={"TYPESAFE_API_KEY": "SENTINEL_TYPESAFE_KEY"},
    )

    assert code == 1
    assert json.loads(output)["denial"] == {
        "code": "jev_failed",
        "message": "routing denied",
    }
    assert "SENTINEL" not in output
    assert "SENTINEL" not in (tmp_path / "audit.jsonl").read_text()


def test_explain_fails_closed_when_the_audit_write_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(*args: object) -> None:
        raise router_module.AuditError("audit_write_failed", "private detail")

    monkeypatch.setattr(router_module, "append_audit_record", fail)

    async def fake_jev(**kwargs: object) -> JevRoutingResult:
        return jev_result()

    code, output = invoke_explain(tmp_path, fake_jev)

    assert code != 0
    assert json.loads(output)["denial"]["code"] == "audit_failed"
    assert "recommended harness:" not in output


def test_explain_audits_all_exhausted_denial_without_calling_jev(
    tmp_path: Path,
) -> None:
    state = tmp_path / "state"
    for provider in ("claude", "codex", "opencode", "pi"):
        cache(
            state / f"{provider}-quota.json",
            provider=provider,
            source="test",
            remaining=0,
            now=1_000,
        )
    called = False

    async def fake_jev(**kwargs: object) -> JevRoutingResult:
        nonlocal called
        called = True
        return jev_result()

    code, output = invoke_explain(tmp_path, fake_jev)

    assert code != 0
    assert json.loads(output)["denial"]["code"] == "no_eligible_provider"
    assert called is False
    record = json.loads(
        (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()[-1]
    )
    assert record["error_category"] == "capacity"


def test_usage_reports_normalized_current_provider_capacity(tmp_path: Path) -> None:
    state = tmp_path / "state"
    cache(
        state / "claude-quota.json",
        provider="claude",
        source="claude_status_line",
        remaining=10,
        now=1_000,
    )
    cache(
        state / "codex-quota.json",
        provider="codex",
        source="codex_app_server",
        remaining=90,
        now=1_000,
    )
    stdout = io.StringIO()

    code = main(
        ["usage", "--state-dir", str(state)],
        stdout=stdout,
        environ={},
        clock=lambda: 1_000.0,
        command_finder=all_commands,
    )

    assert code == 0
    assert json.loads(stdout.getvalue()) == {
        "claude": {
            "state": "conserve",
            "reason": None,
            "freshness": "fresh",
            "source": "claude_status_line",
            "detected": True,
            "enabled": True,
        },
        "codex": {
            "state": "surplus",
            "reason": None,
            "freshness": "fresh",
            "source": "codex_app_server",
            "detected": True,
            "enabled": True,
        },
        "opencode": {
            "state": "unknown",
            "reason": None,
            "freshness": None,
            "source": None,
            "detected": True,
            "enabled": False,
        },
        "pi": {
            "state": "unknown",
            "reason": None,
            "freshness": None,
            "source": None,
            "detected": True,
            "enabled": False,
        },
    }


def test_usage_reports_missing_or_invalid_cache_as_unknown(tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir()
    invalid = state / "claude-quota.json"
    invalid.write_text("SENTINEL_RAW_PROVIDER_BODY")
    invalid.chmod(0o600)
    stdout = io.StringIO()

    code = main(
        ["usage", "--state-dir", str(state)],
        stdout=stdout,
        environ={},
        clock=lambda: 1_000.0,
        command_finder=all_commands,
    )

    assert code == 0
    assert json.loads(stdout.getvalue()) == {
        "claude": {
            "state": "unknown",
            "reason": None,
            "freshness": None,
            "source": None,
            "detected": True,
            "enabled": True,
        },
        "codex": {
            "state": "unknown",
            "reason": None,
            "freshness": None,
            "source": None,
            "detected": True,
            "enabled": True,
        },
        "opencode": {
            "state": "unknown",
            "reason": None,
            "freshness": None,
            "source": None,
            "detected": True,
            "enabled": False,
        },
        "pi": {
            "state": "unknown",
            "reason": None,
            "freshness": None,
            "source": None,
            "detected": True,
            "enabled": False,
        },
    }
    assert "SENTINEL" not in stdout.getvalue()


def test_doctor_validates_credentials_permissions_files_and_commands(
    tmp_path: Path,
) -> None:
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    for name in ("claude-quota.json", "codex-quota.json", "routing.jsonl"):
        path = state / name
        path.touch(mode=0o600)
    stdout = io.StringIO()

    code = main(
        ["doctor", "--state-dir", str(state)],
        stdout=stdout,
        environ={"TYPESAFE_API_KEY": "SENTINEL_SECRET"},
        command_finder=lambda name: f"/usr/local/bin/{name}",
    )

    assert code == 0
    result = json.loads(stdout.getvalue())
    assert result["ok"] is True
    assert result["checks"] == {
        "credential": {"ok": True, "source": "environment"},
        "state_directory": {"ok": True},
        "router_files": {
            "ok": True,
            "claude_cache": "secure",
            "codex_cache": "secure",
            "audit": "secure",
        },
        "providers": {
            "claude": {
                "state": "unknown",
                "reason": None,
                "freshness": None,
                "source": None,
                "detected": True,
                "enabled": True,
            },
            "codex": {
                "state": "unknown",
                "reason": None,
                "freshness": None,
                "source": None,
                "detected": True,
                "enabled": True,
            },
            "opencode": {
                "state": "unknown",
                "reason": None,
                "freshness": None,
                "source": None,
                "detected": True,
                "enabled": False,
            },
            "pi": {
                "state": "unknown",
                "reason": None,
                "freshness": None,
                "source": None,
                "detected": True,
                "enabled": False,
            },
        },
        "commands": {
            "ok": True,
            "herdr": True,
            "codex": True,
            "claude": True,
            "opencode": True,
            "pi": True,
        },
        "harness_configuration": {"ok": True, "error": None},
    }
    assert "SENTINEL" not in stdout.getvalue()


def test_doctor_fails_safely_and_never_prints_secret_or_paths(tmp_path: Path) -> None:
    state = tmp_path / "SENTINEL_PRIVATE_STATE"
    state.mkdir(mode=0o755)
    (state / "claude-quota.json").symlink_to(state / "missing-target")
    (state / "codex-quota.json").touch(mode=0o644)
    stdout = io.StringIO()

    code = main(
        ["doctor", "--state-dir", str(state)],
        stdout=stdout,
        environ={"TYPESAFE_API_KEY": "SENTINEL_SECRET"},
        command_finder=lambda name: None if name == "claude" else f"/{name}",
    )

    assert code == 1
    result = json.loads(stdout.getvalue())
    assert result["ok"] is False
    assert result["checks"]["credential"] == {
        "ok": True,
        "source": "environment",
    }
    assert result["checks"]["state_directory"] == {"ok": False}
    assert result["checks"]["router_files"] == {
        "ok": False,
        "claude_cache": "insecure",
        "codex_cache": "insecure",
        "audit": "missing",
    }
    assert result["checks"]["commands"]["claude"] is False
    assert "SENTINEL" not in stdout.getvalue()


def test_doctor_reports_missing_and_stale_provider_data_without_calling_it_broken(
    tmp_path: Path,
) -> None:
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    cache(
        state / "codex-quota.json",
        provider="codex",
        source="codex_app_server",
        remaining=90,
        now=1_000,
    )
    stdout = io.StringIO()

    code = main(
        ["doctor", "--state-dir", str(state)],
        stdout=stdout,
        environ={"TYPESAFE_API_KEY": "test-key"},
        command_finder=lambda name: f"/{name}",
        clock=lambda: 1_400,
    )

    assert code == 0
    result = json.loads(stdout.getvalue())
    assert result["checks"]["router_files"] == {
        "ok": True,
        "claude_cache": "missing",
        "codex_cache": "secure",
        "audit": "missing",
    }
    assert result["checks"]["providers"]["claude"]["state"] == "unknown"
    assert result["checks"]["providers"]["codex"]["freshness"] == "stale"


def test_doctor_human_explains_every_failing_first_run_check(tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir(mode=0o755)
    stdout = io.StringIO()

    code = main(
        ["doctor", "--human", "--state-dir", str(state)],
        stdout=stdout,
        environ={},
        command_finder=lambda name: None,
    )

    assert code == 1
    output = stdout.getvalue()
    assert "TYPESAFE_API_KEY" in output
    assert "herdr: not found on PATH" in output
    assert "harness claude: not installed" in output
    assert "harnesses: none enabled" in output
    assert "chmod 700" in output
    assert "state files:" in output
    with pytest.raises(json.JSONDecodeError):
        json.loads(output)


def test_doctor_human_shows_the_opt_in_variable_and_quota_setup(
    tmp_path: Path,
) -> None:
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    stdout = io.StringIO()

    code = main(
        ["doctor", "--human", "--state-dir", str(state)],
        stdout=stdout,
        environ={"TYPESAFE_API_KEY": "SENTINEL_SECRET"},
        command_finder=lambda name: f"/{name}",
    )

    assert code == 0
    output = stdout.getvalue()
    assert "TypeSafe key: ok" in output
    assert "herdr: ok" in output
    assert "harness claude: enabled" in output
    assert (
        "harness opencode: detected but not enabled, set HERDR_JEV_ROUTER_OPENCODE"
        in output
    )
    assert "harness pi: detected but not enabled, set HERDR_JEV_ROUTER_PI" in output
    assert "quota claude: no data yet" in output
    assert "herdr-jev-quota-claude" in output
    assert "quota codex: no data yet" in output
    assert "herdr-jev-quota-codex" in output
    assert "SENTINEL" not in output


def test_doctor_human_reports_recorded_capacity(tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    cache(
        state / "claude-quota.json",
        provider="claude",
        source="claude_status_line",
        remaining=10,
        now=1_000,
    )
    cache(
        state / "codex-quota.json",
        provider="codex",
        source="codex_app_server",
        remaining=90,
        now=1_000,
    )
    stdout = io.StringIO()

    code = main(
        ["doctor", "--human", "--state-dir", str(state)],
        stdout=stdout,
        environ={"TYPESAFE_API_KEY": "test-key"},
        command_finder=lambda name: f"/{name}",
        clock=lambda: 1_000.0,
    )

    assert code == 0
    output = stdout.getvalue()
    assert "state directory: ok" in output
    assert "state files: ok" in output
    assert "quota claude: conserve" in output
    assert "quota codex: surplus" in output
    assert "not found" not in output
    assert "chmod 700" not in output


def test_doctor_human_reports_a_malformed_opt_in(tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    stdout = io.StringIO()

    code = main(
        ["doctor", "--human", "--state-dir", str(state)],
        stdout=stdout,
        environ={
            "TYPESAFE_API_KEY": "test-key",
            "HERDR_JEV_ROUTER_PI": "provider=pi-only",
        },
        command_finder=lambda name: f"/{name}",
    )

    assert code == 1
    output = stdout.getvalue()
    assert "harness configuration: invalid_harness_configuration" in output
    assert "harness pi: detected but not enabled, set HERDR_JEV_ROUTER_PI" in output


def test_doctor_human_notes_a_harness_without_a_quota_source(tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    stdout = io.StringIO()

    code = main(
        ["doctor", "--human", "--state-dir", str(state)],
        stdout=stdout,
        environ={**_OPT_IN_ENVIRONMENT, "TYPESAFE_API_KEY": "test-key"},
        command_finder=lambda name: f"/{name}",
    )

    assert code == 0
    output = stdout.getvalue()
    assert "harness opencode: enabled" in output
    assert "quota opencode: no local quota source, capacity stays unknown" in output
    assert "quota pi: no local quota source, capacity stays unknown" in output


def test_plugin_manifest_is_metadata_only_and_installs_the_console_scripts() -> None:
    manifest = tomllib.loads(
        (Path(__file__).parents[2] / "herdr-plugin.toml").read_text()
    )

    assert manifest["id"] == "herdr.jev-router"
    assert manifest["min_herdr_version"] == "0.9.1"
    assert manifest["platforms"] == ["linux", "macos"]
    assert manifest["build"] == [{"command": ["uv", "tool", "install", "--force", "."]}]
    assert "events" not in manifest
    assert "startup" not in manifest
    assert "actions" not in manifest
    assert "enforce" not in manifest["description"].lower()


def test_default_state_directory_uses_configured_environment(tmp_path: Path) -> None:
    configured = tmp_path / "configured-state"
    calls: list[dict[str, object]] = []

    async def fake_jev(**kwargs: object) -> JevRoutingResult:
        calls.append(kwargs)
        return jev_result()

    stdout = io.StringIO()
    code = main(
        ["explain", "Review the authentication change."],
        stdout=stdout,
        environ={
            "TYPESAFE_API_KEY": "test-key",
            "HERDR_JEV_ROUTER_STATE_DIR": os.fspath(configured),
            **_OPT_IN_ENVIRONMENT,
        },
        jev_callable=fake_jev,
        clock=lambda: 1_000,
        command_finder=all_commands,
    )

    assert code == 0
    assert calls
    assert (configured / "routing.jsonl").is_file()


def _quota_response(harnesses: tuple[str, ...], selected: str = "claude"):
    return Response(
        200,
        headers={"content-type": "application/json"},
        json={
            "model": "jev-test-1",
            "answers": {
                "harness": {
                    "type": "choice",
                    "choice": selected,
                    "probabilities": {
                        name: (1.0 if name == selected else 0.0) for name in harnesses
                    },
                    "confidence": 1.0,
                },
                "claude_model": {
                    "type": "choice",
                    "choice": "sonnet",
                    "probabilities": {"haiku": 0.2, "sonnet": 0.6, "opus": 0.2},
                    "confidence": 0.4,
                },
                "codex_model": {
                    "type": "choice",
                    "choice": "terra",
                    "probabilities": {"luna": 0.2, "terra": 0.7, "sol": 0.1},
                    "confidence": 0.5,
                },
                "opencode_model": {
                    "type": "choice",
                    "choice": "deepseek",
                    "probabilities": {"deepseek": 0.6, "glm": 0.3, "kimi": 0.1},
                    "confidence": 0.5,
                },
                "pi_model": {
                    "type": "choice",
                    "choice": "deepseek",
                    "probabilities": {"deepseek": 0.6, "glm": 0.3, "kimi": 0.1},
                    "confidence": 0.5,
                },
                "effort": {
                    "type": "choice",
                    "choice": "high",
                    "probabilities": {
                        "low": 0.05,
                        "medium": 0.15,
                        "high": 0.6,
                        "xhigh": 0.15,
                        "max": 0.05,
                    },
                    "confidence": 0.5,
                },
            },
            "usage": {"input_tokens": 10, "output_tokens": 5},
        },
    )


def _claude_and_critical_codex(state: Path, *, now: float = 1_000) -> None:
    write_cache(
        state / "claude-quota.json",
        QuotaSnapshot(
            provider="claude",
            source="claude_status_line",
            observed_at=now,
            captured_at=now,
            windows=(
                QuotaWindow("five_hour", 15, 85, 18_000, now + 2 * 3_600),
                QuotaWindow("seven_day", 65, 35, 604_800, now + 100 * 3_600),
            ),
        ),
    )
    write_cache(
        state / "codex-quota.json",
        QuotaSnapshot(
            provider="codex",
            source="codex_app_server",
            observed_at=now,
            captured_at=now,
            windows=(QuotaWindow("primary", 94, 6, 604_800, now + 38 * 3_600),),
        ),
    )


def _advisory_commands(name: str) -> str | None:
    return f"/usr/bin/{name}" if name in {"herdr", "claude", "codex"} else None


def test_explain_removes_critical_codex_and_sends_claude_numbers_to_jev(
    tmp_path: Path,
) -> None:
    state = tmp_path / "state"
    _claude_and_critical_codex(state)
    payloads: list[dict[str, object]] = []

    def handler(request: Request):
        payloads.append(json.loads(request.content))
        return _quota_response(("claude",))

    code, output = invoke_explain(
        tmp_path,
        partial(route_with_jev, transport=MockTransport(handler)),
        command_finder=_advisory_commands,
    )

    assert code == 0
    assert len(payloads) == 1
    capacity = payloads[0]["state"]["capacity"]
    assert set(capacity) == {"claude"}
    assert capacity["claude"] == {
        "state": "conserve",
        "penalty": 0,
        "age_hours": 0,
        "five_hour_remaining_percent": 85,
        "five_hour_resets_in_hours": 2,
        "weekly_remaining_percent": 35,
        "weekly_resets_in_hours": 100,
    }
    assert "capacity codex: critical (codex weekly 6% left, resets in 38h)" in output
    record = json.loads(
        (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()[-1]
    )
    assert record["capacity"]["codex"] == {
        "state": "critical",
        "penalty": 0,
        "age_hours": 0,
        "five_hour_remaining_percent": None,
        "five_hour_resets_in_hours": None,
        "weekly_remaining_percent": 6,
        "weekly_resets_in_hours": 38,
        "reason": "codex weekly 6% left, resets in 38h",
    }
    assert record["capacity"]["claude"]["five_hour_remaining_percent"] == 85
    assert record["capacity"]["claude"]["weekly_remaining_percent"] == 35


def test_usage_reports_critical_with_the_reason(tmp_path: Path) -> None:
    state = tmp_path / "state"
    _claude_and_critical_codex(state)
    stdout = io.StringIO()

    code = main(
        ["usage", "--state-dir", str(state)],
        stdout=stdout,
        environ={},
        clock=lambda: 1_000.0,
        command_finder=_advisory_commands,
    )

    assert code == 0
    data = json.loads(stdout.getvalue())
    assert data["codex"]["state"] == "critical"
    assert data["codex"]["reason"] == "codex weekly 6% left, resets in 38h"
    assert data["claude"]["reason"] is None


def test_doctor_human_reports_critical_with_the_reason(tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    _claude_and_critical_codex(state)
    stdout = io.StringIO()

    code = main(
        ["doctor", "--human", "--state-dir", str(state)],
        stdout=stdout,
        environ={"TYPESAFE_API_KEY": "test-key"},
        command_finder=_advisory_commands,
        clock=lambda: 1_000.0,
    )

    assert code == 0
    assert "quota codex: critical (codex weekly 6% left, resets in 38h)" in (
        stdout.getvalue()
    )


def test_explain_keeps_the_only_critical_provider_with_a_penalty(
    tmp_path: Path,
) -> None:
    state = tmp_path / "state"
    write_cache(
        state / "codex-quota.json",
        QuotaSnapshot(
            provider="codex",
            source="codex_app_server",
            observed_at=1_000,
            captured_at=1_000,
            windows=(QuotaWindow("primary", 94, 6, 604_800, 1_000 + 38 * 3_600),),
        ),
    )
    payloads: list[dict[str, object]] = []

    def handler(request: Request):
        payloads.append(json.loads(request.content))
        return _quota_response(("codex",), selected="codex")

    def only_codex(name: str) -> str | None:
        return f"/usr/bin/{name}" if name in {"herdr", "codex"} else None

    code, output = invoke_explain(
        tmp_path,
        partial(route_with_jev, transport=MockTransport(handler)),
        command_finder=only_codex,
    )

    assert code == 0
    assert payloads[0]["state"]["capacity"]["codex"]["penalty"] == 2
    assert "capacity codex: critical (codex weekly 6% left, resets in 38h)" in output
    record = json.loads(
        (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()[-1]
    )
    assert record["capacity"]["codex"]["state"] == "critical"
    assert record["capacity"]["codex"]["penalty"] == 2
    assert record["capacity"]["claude"]["state"] == "exhausted"


def test_usage_calls_a_stale_near_empty_window_critical_with_the_reason(
    tmp_path: Path,
) -> None:
    state = tmp_path / "state"
    write_cache(
        state / "codex-quota.json",
        QuotaSnapshot(
            provider="codex",
            source="codex_app_server",
            observed_at=1_000,
            captured_at=1_000,
            windows=(QuotaWindow("primary", 94, 6, 604_800, 1_400 + 38 * 3_600),),
        ),
    )
    stdout = io.StringIO()

    code = main(
        ["usage", "--state-dir", str(state)],
        stdout=stdout,
        environ={},
        clock=lambda: 1_400.0,
        command_finder=_advisory_commands,
    )

    assert code == 0
    data = json.loads(stdout.getvalue())
    assert data["codex"]["freshness"] == "stale"
    assert data["codex"]["state"] == "critical"
    assert data["codex"]["reason"] == "codex weekly 6% left, resets in 38h"


def test_explain_removes_a_stale_near_empty_provider(tmp_path: Path) -> None:
    state = tmp_path / "state"
    # Codex was observed at 600 and the test clock is 1000, so it is stale
    # (refresh floor 300) but still within the 6-hour maximum. A stale weekly
    # window at 6% is still critical and must be removed.
    write_cache(
        state / "codex-quota.json",
        QuotaSnapshot(
            provider="codex",
            source="codex_app_server",
            observed_at=600,
            captured_at=600,
            windows=(QuotaWindow("primary", 94, 6, 604_800, 1_000 + 38 * 3_600),),
        ),
    )
    write_cache(
        state / "claude-quota.json",
        QuotaSnapshot(
            provider="claude",
            source="claude_status_line",
            observed_at=1_000,
            captured_at=1_000,
            windows=(QuotaWindow("seven_day", 10, 90, 604_800, 1_000 + 160 * 3_600),),
        ),
    )
    payloads: list[dict[str, object]] = []

    def handler(request: Request):
        payloads.append(json.loads(request.content))
        return _quota_response(("claude",))

    code, output = invoke_explain(
        tmp_path,
        partial(route_with_jev, transport=MockTransport(handler)),
        command_finder=_advisory_commands,
    )

    assert code == 0
    capacity = payloads[0]["state"]["capacity"]
    assert set(capacity) == {"claude"}
    assert capacity["claude"]["age_hours"] == 0
    assert "capacity codex: critical (codex weekly 6% left, resets in 38h)" in output
    record = json.loads(
        (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()[-1]
    )
    assert record["capacity"]["codex"]["state"] == "critical"
    assert record["capacity"]["codex"]["age_hours"] == 0.1
    assert record["capacity"]["codex"]["weekly_remaining_percent"] == 6
