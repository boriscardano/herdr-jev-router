"""Cover harness detection, opt-in configuration, and the CLI report surface."""

from __future__ import annotations

import io
import json
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from herdr_jev_router.cli import main
from herdr_jev_router.harness import (
    HarnessConfigurationError,
    detect_harnesses,
    harness_availability,
    parse_launch_config,
)
from herdr_jev_router.jev import JevRoutingResult
from herdr_jev_router.models import Harness, HarnessLaunch

LAUNCH_MODELS = {
    "deepseek": "deepseek-v4.1-flash",
    "glm": "glm-5.3",
    "kimi": "kimi-k3",
}

OPT_IN_TIER_KEYS = ("small", "balanced", "large")

_FAKE_HERDR_BODY = """
import json
import sys

with open(sys.argv[0] + ".log", "a", encoding="utf-8") as log:
    print(json.dumps(sys.argv[1:]), file=log)
"""


def opted_in(provider: str = "opencode-go", models: str = "") -> str:
    """Return one opt-in value that names a provider and three tier models."""

    if not models:
        models = ",".join(
            f"{key}={model}"
            for key, model in zip(OPT_IN_TIER_KEYS, LAUNCH_MODELS.values(), strict=True)
        )
    return f"provider={provider},{models}"


def finder(*installed: str) -> Callable[[str], str | None]:
    """Return a command finder that reports exactly the named commands."""

    return lambda name: f"/usr/bin/{name}" if name in installed else None


def all_commands(name: str) -> str:
    """Return a path for every command name a test may look up."""

    return f"/usr/bin/{name}"


def jev_result(selected: Harness, eligible: tuple[Harness, ...]) -> JevRoutingResult:
    """Return one valid Jev result for a selected harness and eligible set."""

    return JevRoutingResult(
        model="jev-test-1",
        answers={
            "harness": {"type": "choice", "value": selected.value},
            "claude_model": {"type": "choice", "value": "sonnet"},
            "codex_model": {"type": "choice", "value": "terra"},
            "opencode_model": {"type": "choice", "value": "deepseek"},
            "pi_model": {"type": "choice", "value": "deepseek"},
            "effort": {"type": "choice", "value": "high"},
        },
        probabilities={
            "harness": {
                harness.value: (1.0 if harness is selected else 0.0)
                for harness in eligible
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
            "harness": 1.0,
            "claude_model": 0.6,
            "codex_model": 0.7,
            "opencode_model": 0.6,
            "pi_model": 0.6,
            "effort": 0.6,
        },
        usage={"input_tokens": 10, "output_tokens": 5},
    )


def fake_herdr(tmp_path: Path) -> Path:
    """Install a herdr stand-in that records argv and exits zero."""

    executable = tmp_path / "fake-herdr"
    executable.write_text(f"#!{sys.executable}\n{_FAKE_HERDR_BODY}", encoding="utf-8")
    executable.chmod(0o755)
    return executable


def run_spawn(
    tmp_path: Path,
    *,
    command_finder: Callable[[str], str | None],
    environ: dict[str, str],
    jev_callable,
) -> tuple[int, str, list[dict[str, object]], list[list[str]]]:
    """Run `spawn` against a herdr stand-in and capture Jev calls and argv."""

    executable = fake_herdr(tmp_path)
    calls: list[dict[str, object]] = []

    async def recording_jev(**kwargs: object) -> object:
        calls.append(kwargs)
        return await jev_callable(**kwargs)

    stdout = io.StringIO()
    code = main(
        [
            "spawn",
            "Review the authentication change.",
            "--name",
            "worker",
            "--pane",
            "w1:p2",
            "--state-dir",
            str(tmp_path / "state"),
            "--audit-path",
            str(tmp_path / "audit.jsonl"),
        ],
        stdout=stdout,
        environ=environ,
        jev_callable=recording_jev,
        clock=lambda: 1_000.0,
        command_finder=command_finder,
        herdr_command=str(executable),
        request_id_factory=lambda: "harness-request-1",
    )
    log = executable.with_suffix(".log")
    argv = (
        [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
        if log.exists()
        else []
    )
    return code, stdout.getvalue(), calls, argv


def run_explain(
    tmp_path: Path,
    *,
    command_finder: Callable[[str], str | None],
    environ: dict[str, str],
    jev_callable,
) -> tuple[int, str, list[dict[str, object]]]:
    """Run `explain` and capture its human-readable review and Jev calls."""

    calls: list[dict[str, object]] = []

    async def recording_jev(**kwargs: object) -> object:
        calls.append(kwargs)
        return await jev_callable(**kwargs)

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
        environ=environ,
        jev_callable=recording_jev,
        clock=lambda: 1_000.0,
        command_finder=command_finder,
    )
    return code, stdout.getvalue(), calls


def test_detect_harnesses_uses_the_command_finder() -> None:
    assert detect_harnesses(finder("claude", "pi")) == frozenset(
        {Harness.CLAUDE, Harness.PI}
    )


def test_claude_and_codex_are_enabled_without_configuration() -> None:
    availability = harness_availability(finder("claude", "codex"), {})

    assert availability.detected == frozenset({Harness.CLAUDE, Harness.CODEX})
    assert availability.enabled == frozenset({Harness.CLAUDE, Harness.CODEX})
    assert availability.launch == {}
    assert availability.configuration_error is None


def test_opencode_and_pi_are_disabled_without_opt_in() -> None:
    availability = harness_availability(finder("claude", "codex", "opencode", "pi"), {})

    assert availability.enabled == frozenset({Harness.CLAUDE, Harness.CODEX})
    assert Harness.OPENCODE not in availability.enabled
    assert Harness.PI not in availability.enabled
    assert availability.launch == {}


def test_opt_in_enables_a_detected_harness_and_supplies_its_launch() -> None:
    availability = harness_availability(
        finder("claude", "opencode"),
        {"HERDR_JEV_ROUTER_OPENCODE": opted_in()},
    )

    assert availability.enabled == frozenset({Harness.CLAUDE, Harness.OPENCODE})
    assert availability.launch[Harness.OPENCODE] == HarnessLaunch(
        provider="opencode-go",
        models=LAUNCH_MODELS,
    )


def test_opt_in_does_not_enable_an_absent_harness() -> None:
    availability = harness_availability(
        finder("claude"),
        {"HERDR_JEV_ROUTER_PI": opted_in()},
    )

    assert availability.enabled == frozenset({Harness.CLAUDE})
    assert Harness.PI not in availability.enabled
    assert availability.launch[Harness.PI].provider == "opencode-go"


def test_launch_configuration_tolerates_spacing() -> None:
    config = parse_launch_config(
        " provider = opencode-go , small = a , balanced = b , large = c "
    )

    assert config == HarnessLaunch(
        provider="opencode-go",
        models={"deepseek": "a", "glm": "b", "kimi": "c"},
    )


def test_generic_tier_keys_map_in_jev_tier_order() -> None:
    config = parse_launch_config("provider=p,small=a,balanced=b,large=c")

    assert dict(config.models) == {
        "deepseek": "a",
        "glm": "b",
        "kimi": "c",
    }


@pytest.mark.parametrize(
    "value",
    [
        "provider=opencode-go",
        "provider=opencode-go,small=a,balanced=b",
        "provider=opencode-go,small=a,balanced=b,large=c,extra=d",
        "provider=,,small=a,balanced=b,large=c",
        "provider=-bad,small=a,balanced=b,large=c",
        "provider=a/b,small=a,balanced=b,large=c",
        "provider=opencode-go,small=a/b,balanced=b,large=c",
        "provider=opencode-go,small=a,balanced=b,large=c,small=d",
        "provider=opencode-go,small=a,balanced=b,large=c\ttab",
        "provider=opencode-go,deepseek=a,glm=b,kimi=c",
    ],
)
def test_malformed_opt_in_records_a_stable_configuration_error(value: str) -> None:
    availability = harness_availability(
        finder("claude", "pi"),
        {"HERDR_JEV_ROUTER_PI": value},
    )

    assert availability.configuration_error == "invalid_harness_configuration"
    assert Harness.PI not in availability.enabled


def test_parse_launch_config_rejects_a_malformed_value() -> None:
    with pytest.raises(HarnessConfigurationError) as error:
        parse_launch_config("provider=opencode-go")

    assert error.value.code == "invalid_harness_configuration"


def test_usage_reports_detected_and_enabled_harnesses(tmp_path: Path) -> None:
    stdout = io.StringIO()

    code = main(
        ["usage", "--state-dir", str(tmp_path / "state")],
        stdout=stdout,
        environ={"HERDR_JEV_ROUTER_OPENCODE": opted_in()},
        command_finder=finder("claude", "opencode"),
        clock=lambda: 1_000.0,
    )

    assert code == 0
    usage = json.loads(stdout.getvalue())
    assert usage["claude"]["detected"] is True
    assert usage["claude"]["enabled"] is True
    assert usage["codex"]["detected"] is False
    assert usage["codex"]["enabled"] is False
    assert usage["opencode"]["detected"] is True
    assert usage["opencode"]["enabled"] is True
    assert usage["pi"]["detected"] is False
    assert usage["pi"]["enabled"] is False


def test_doctor_is_healthy_with_only_claude_installed(tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    stdout = io.StringIO()

    code = main(
        ["doctor", "--state-dir", str(state)],
        stdout=stdout,
        environ={"TYPESAFE_API_KEY": "test-key"},
        command_finder=finder("herdr", "claude"),
    )

    assert code == 0
    result = json.loads(stdout.getvalue())
    assert result["ok"] is True
    assert result["checks"]["commands"]["herdr"] is True
    assert result["checks"]["commands"]["codex"] is False
    assert result["checks"]["providers"]["claude"]["enabled"] is True
    assert result["checks"]["providers"]["codex"]["enabled"] is False
    assert result["checks"]["harness_configuration"] == {"ok": True, "error": None}


def test_doctor_reports_a_malformed_opt_in_as_unhealthy(tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    stdout = io.StringIO()

    code = main(
        ["doctor", "--state-dir", str(state)],
        stdout=stdout,
        environ={
            "TYPESAFE_API_KEY": "test-key",
            "HERDR_JEV_ROUTER_PI": "provider=opencode-go",
        },
        command_finder=finder("herdr", "claude", "pi"),
    )

    assert code == 1
    result = json.loads(stdout.getvalue())
    assert result["ok"] is False
    assert result["checks"]["harness_configuration"] == {
        "ok": False,
        "error": "invalid_harness_configuration",
    }
    assert result["checks"]["providers"]["pi"]["detected"] is True
    assert result["checks"]["providers"]["pi"]["enabled"] is False


def test_spawn_denies_without_calling_jev_when_no_harness_is_installed(
    tmp_path: Path,
) -> None:
    called = False

    async def fake_jev(**kwargs: object) -> JevRoutingResult:
        nonlocal called
        called = True
        return jev_result(Harness.CLAUDE, (Harness.CLAUDE,))

    code, output, calls, argv = run_spawn(
        tmp_path,
        command_finder=finder("herdr"),
        environ={"TYPESAFE_API_KEY": "test-key"},
        jev_callable=fake_jev,
    )

    assert code == 1
    assert json.loads(output)["denial"]["code"] == "no_eligible_provider"
    assert calls == []
    assert argv == []
    assert called is False


@pytest.mark.parametrize(
    "installed, expected_states",
    [
        (
            ("claude",),
            {
                "claude": "unknown",
                "codex": "exhausted",
                "opencode": "exhausted",
                "pi": "exhausted",
            },
        ),
        (
            ("codex",),
            {
                "claude": "exhausted",
                "codex": "unknown",
                "opencode": "exhausted",
                "pi": "exhausted",
            },
        ),
    ],
)
def test_spawn_excludes_every_unavailable_harness_before_jev(
    tmp_path: Path, installed: tuple[str, ...], expected_states: dict[str, str]
) -> None:
    async def fake_jev(**kwargs: object) -> JevRoutingResult:
        raise RuntimeError("stop after capturing capacities")

    code, output, calls, argv = run_spawn(
        tmp_path,
        command_finder=finder("herdr", *installed),
        environ={"TYPESAFE_API_KEY": "test-key"},
        jev_callable=fake_jev,
    )

    assert code == 1
    assert json.loads(output)["denial"]["code"] == "jev_failed"
    assert len(calls) == 1
    capacities = calls[0]["capacities"]
    assert {value.harness.value: value.state.value for value in capacities} == (
        expected_states
    )
    assert argv == []


def test_spawn_denies_opencode_and_pi_present_but_not_opted_in(
    tmp_path: Path,
) -> None:
    async def fake_jev(**kwargs: object) -> JevRoutingResult:
        raise AssertionError("opted-out harnesses must not reach Jev")

    code, output, calls, argv = run_spawn(
        tmp_path,
        command_finder=finder("herdr", "opencode", "pi"),
        environ={"TYPESAFE_API_KEY": "test-key"},
        jev_callable=fake_jev,
    )

    assert code == 1
    assert json.loads(output)["denial"]["code"] == "no_eligible_provider"
    assert calls == []
    assert argv == []


@pytest.mark.parametrize(
    "harness, executable",
    [(Harness.CLAUDE, "claude"), (Harness.CODEX, "codex")],
)
def test_spawn_works_with_only_one_harness_installed(
    tmp_path: Path, harness: Harness, executable: str
) -> None:
    async def fake_jev(**kwargs: object) -> JevRoutingResult:
        return jev_result(harness, (harness,))

    code, output, calls, argv = run_spawn(
        tmp_path,
        command_finder=finder("herdr", executable),
        environ={"TYPESAFE_API_KEY": "test-key"},
        jev_callable=fake_jev,
    )

    assert code == 0
    assert argv[0][:5] == ["agent", "start", "worker", "--kind", harness.value]
    assert output.startswith(f"started harness={harness.value} ")
    assert len(calls) == 1


def test_spawn_uses_the_configured_pi_provider(tmp_path: Path) -> None:
    environment = {
        "TYPESAFE_API_KEY": "test-key",
        "HERDR_JEV_ROUTER_PI": opted_in("pi-provider"),
    }

    async def fake_jev(**kwargs: object) -> JevRoutingResult:
        return jev_result(
            Harness.PI,
            (Harness.CLAUDE, Harness.CODEX, Harness.PI),
        )

    code, output, calls, argv = run_spawn(
        tmp_path,
        command_finder=all_commands,
        environ=environment,
        jev_callable=fake_jev,
    )

    assert code == 0
    assert output == (
        "started harness=pi model=deepseek effort=high pane=w1:p2 name=worker\n"
    )
    assert argv[0] == [
        "agent",
        "start",
        "worker",
        "--kind",
        "pi",
        "--pane",
        "w1:p2",
        "--",
        "--provider",
        "pi-provider",
        "--model",
        "deepseek-v4.1-flash",
        "--thinking",
        "high",
    ]
    assert len(calls) == 1


def test_spawn_fails_closed_on_a_malformed_opt_in(tmp_path: Path) -> None:
    async def fake_jev(**kwargs: object) -> JevRoutingResult:
        raise AssertionError("a malformed opt-in must not reach Jev")

    code, output, calls, argv = run_spawn(
        tmp_path,
        command_finder=finder("herdr", "claude", "pi"),
        environ={
            "TYPESAFE_API_KEY": "test-key",
            "HERDR_JEV_ROUTER_PI": "provider=opencode-go",
        },
        jev_callable=fake_jev,
    )

    assert code == 2
    assert json.loads(output)["denial"]["code"] == "invalid_harness_configuration"
    assert calls == []
    assert argv == []


def test_spawn_uses_the_configured_provider_and_models_when_opted_in(
    tmp_path: Path,
) -> None:
    environment = {
        "TYPESAFE_API_KEY": "test-key",
        "HERDR_JEV_ROUTER_OPENCODE": opted_in("my-provider"),
    }

    async def fake_jev(**kwargs: object) -> JevRoutingResult:
        return jev_result(
            Harness.OPENCODE,
            (Harness.CLAUDE, Harness.CODEX, Harness.OPENCODE),
        )

    code, output, calls, argv = run_spawn(
        tmp_path,
        command_finder=all_commands,
        environ=environment,
        jev_callable=fake_jev,
    )

    assert code == 0
    assert output == (
        "started harness=opencode model=deepseek effort=high pane=w1:p2 name=worker\n"
    )
    assert argv[0] == [
        "agent",
        "start",
        "worker",
        "--kind",
        "opencode",
        "--pane",
        "w1:p2",
        "--",
        "--model",
        "my-provider/deepseek-v4.1-flash",
    ]
    assert len(calls) == 1


def test_explain_says_why_a_harness_is_unavailable(tmp_path: Path) -> None:
    async def fake_jev(**kwargs: object) -> JevRoutingResult:
        return jev_result(Harness.CLAUDE, (Harness.CLAUDE,))

    code, output, calls = run_explain(
        tmp_path,
        command_finder=finder("claude", "opencode"),
        environ={"TYPESAFE_API_KEY": "test-key"},
        jev_callable=fake_jev,
    )

    assert code == 0
    assert "capacity claude: unknown" in output
    assert "capacity codex: not installed" in output
    assert "capacity opencode: not enabled (set HERDR_JEV_ROUTER_OPENCODE)" in output
    assert "capacity pi: not installed" in output
    assert "capacity opencode: exhausted" not in output
    assert "capacity pi: exhausted" not in output
    assert len(calls) == 1


def test_explain_reports_the_state_of_an_opted_in_harness(tmp_path: Path) -> None:
    async def fake_jev(**kwargs: object) -> JevRoutingResult:
        return jev_result(Harness.CLAUDE, (Harness.CLAUDE, Harness.OPENCODE))

    code, output, calls = run_explain(
        tmp_path,
        command_finder=finder("claude", "opencode"),
        environ={
            "TYPESAFE_API_KEY": "test-key",
            "HERDR_JEV_ROUTER_OPENCODE": opted_in(),
        },
        jev_callable=fake_jev,
    )

    assert code == 0
    assert "capacity claude: unknown" in output
    assert "capacity opencode: unknown" in output
    assert "capacity opencode: not" not in output
    assert len(calls) == 1
