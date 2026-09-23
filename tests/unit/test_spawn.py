"""Cover the advisory `spawn` command against a fake Herdr executable."""

import io
import json
import sys
from pathlib import Path

import pytest

import herdr_jev_router.cli as cli_module
import herdr_jev_router.router as router_module
from herdr_jev_router.cli import main
from herdr_jev_router.jev import JevRoutingResult
from herdr_jev_router.quota import QuotaSnapshot, QuotaWindow, write_cache

_FAKE_HERDR_BODY = """
import json
import os
import sys
import time

record = {
    "argv": sys.argv[1:],
    "has_typesafe_key": "TYPESAFE_API_KEY" in os.environ,
}
with open(os.environ["FAKE_HERDR_LOG"], "a", encoding="utf-8") as log:
    print(json.dumps(record), file=log)

time.sleep(float(os.environ.get("FAKE_HERDR_SLEEP", "0")))

stderr_bytes = os.environ.get("FAKE_HERDR_STDERR_BYTES", "")
if stderr_bytes:
    sys.stderr.buffer.write(bytes.fromhex(stderr_bytes))
    sys.stderr.buffer.flush()

stdout_bytes = int(os.environ.get("FAKE_HERDR_STDOUT_BYTES", "0"))
if stdout_bytes:
    sys.stdout.write("x" * stdout_bytes)
    sys.stdout.flush()

argv = sys.argv[1:]
if argv[:2] == ["pane", "split"]:
    split_bytes = int(os.environ.get("FAKE_HERDR_SPLIT_BYTES", "0"))
    if split_bytes:
        sys.stdout.write("x" * split_bytes)
    response = os.environ.get("FAKE_HERDR_SPLIT_RESPONSE", "")
    status = int(os.environ.get("FAKE_HERDR_SPLIT_STATUS", "0"))
elif argv[:2] == ["agent", "start"]:
    response = os.environ.get("FAKE_HERDR_START_RESPONSE", "")
    status = int(os.environ.get("FAKE_HERDR_START_STATUS", "0"))
elif argv[:2] == ["agent", "prompt"]:
    response = os.environ.get("FAKE_HERDR_PROMPT_RESPONSE", "")
    status = int(os.environ.get("FAKE_HERDR_PROMPT_STATUS", "0"))
else:
    response = ""
    status = 2
if response:
    sys.stdout.write(response)
sys.exit(status)
"""

SPLIT_RESPONSE = (
    '{"id":"cli:pane:split","result":{"type":"pane_info","pane":{"pane_id":"w9:p9"}}}'
)


def jev_result() -> JevRoutingResult:
    """Return one validated Jev result that selects Claude/opus/max."""

    return JevRoutingResult(
        model="jev-test-1",
        answers={
            "harness": {"type": "choice", "value": "claude"},
            "claude_model": {"type": "choice", "value": "opus"},
            "codex_model": {"type": "choice", "value": "terra"},
            "opencode_model": {"type": "choice", "value": "deepseek"},
            "pi_model": {"type": "choice", "value": "deepseek"},
            "effort": {"type": "choice", "value": "max"},
        },
        probabilities={
            "harness": {"claude": 0.7, "codex": 0.1, "opencode": 0.1, "pi": 0.1},
            "claude_model": {"haiku": 0.1, "sonnet": 0.2, "opus": 0.7},
            "codex_model": {"luna": 0.2, "terra": 0.7, "sol": 0.1},
            "opencode_model": {"deepseek": 0.6, "glm": 0.3, "kimi": 0.1},
            "pi_model": {"deepseek": 0.6, "glm": 0.3, "kimi": 0.1},
            "effort": {
                "low": 0.05,
                "medium": 0.15,
                "high": 0.2,
                "xhigh": 0.2,
                "max": 0.4,
            },
        },
        confidences={
            "harness": 0.7,
            "claude_model": 0.7,
            "codex_model": 0.7,
            "opencode_model": 0.6,
            "pi_model": 0.6,
            "effort": 0.4,
        },
        usage={"input_tokens": 10, "output_tokens": 5},
    )


def fake_herdr(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    start_response: str = "",
    start_status: int = 0,
    prompt_response: str = "",
    prompt_status: int = 0,
    split_response: str = SPLIT_RESPONSE,
    split_status: int = 0,
) -> tuple[Path, Path]:
    """Install a fake `herdr` script that records argv and returns canned JSON."""

    executable = tmp_path / "fake-herdr"
    executable.write_text(f"#!{sys.executable}\n{_FAKE_HERDR_BODY}", encoding="utf-8")
    executable.chmod(0o755)
    log = tmp_path / "herdr-argv.jsonl"
    monkeypatch.setenv("FAKE_HERDR_LOG", str(log))
    monkeypatch.setenv("FAKE_HERDR_START_RESPONSE", start_response)
    monkeypatch.setenv("FAKE_HERDR_START_STATUS", str(start_status))
    monkeypatch.setenv("FAKE_HERDR_PROMPT_RESPONSE", prompt_response)
    monkeypatch.setenv("FAKE_HERDR_PROMPT_STATUS", str(prompt_status))
    monkeypatch.setenv("FAKE_HERDR_SPLIT_RESPONSE", split_response)
    monkeypatch.setenv("FAKE_HERDR_SPLIT_STATUS", str(split_status))
    return executable, log


def spawn_argv(
    task: str = "Review the authentication change.",
    *,
    name: str = "worker",
    pane: str | None = "w1:p2",
    extra: tuple[str, ...] = (),
) -> list[str]:
    argv = ["spawn", task, "--name", name]
    if pane is not None:
        argv += ["--pane", pane]
    return [*argv, *extra]


def all_commands(name: str) -> str:
    """Return a path for every command name a test may look up."""

    return f"/usr/bin/{name}"


def run_spawn(
    argv: list[str],
    tmp_path: Path,
    *,
    herdr_command: Path | None,
    jev_callable,
    environ: dict[str, str] | None = None,
    command_finder=None,
) -> tuple[int, str]:
    optional: dict[str, object] = {}
    if herdr_command is not None:
        optional["herdr_command"] = str(herdr_command)
    optional["command_finder"] = (
        all_commands if command_finder is None else command_finder
    )
    environment = {
        "HERDR_JEV_ROUTER_OPENCODE": (
            "provider=opencode-go,small=deepseek-v4.1-flash,"
            "balanced=glm-5.3,large=kimi-k3"
        ),
        "HERDR_JEV_ROUTER_PI": (
            "provider=opencode-go,small=deepseek-v4.1-flash,"
            "balanced=glm-5.3,large=kimi-k3"
        ),
    }
    if environ is None:
        environment["TYPESAFE_API_KEY"] = "test-key"
    else:
        environment.update(environ)
    stdout = io.StringIO()
    code = main(
        [
            *argv,
            "--state-dir",
            str(tmp_path / "state"),
            "--audit-path",
            str(tmp_path / "audit.jsonl"),
        ],
        stdout=stdout,
        environ=environment,
        jev_callable=jev_callable,
        clock=lambda: 1_000.0,
        request_id_factory=lambda: "spawn-request-1",
        **optional,
    )
    return code, stdout.getvalue()


def read_log(log: Path) -> list[dict[str, object]]:
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]


def cache(
    path: Path, *, provider: str, source: str, remaining: float, now: float
) -> None:
    write_cache(
        path,
        QuotaSnapshot(
            provider=provider,
            source=source,
            observed_at=now,
            captured_at=now,
            windows=(
                QuotaWindow("primary", 100 - remaining, remaining, 3_600, now + 1_800),
            ),
        ),
    )


def denial(output: str) -> dict[str, object]:
    return json.loads(output)["denial"]  # type: ignore[no-any-return]


def test_spawn_starts_recommended_agent_and_delivers_task_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable, log = fake_herdr(tmp_path, monkeypatch)
    calls: list[dict[str, object]] = []

    async def fake_jev(**kwargs: object) -> JevRoutingResult:
        calls.append(kwargs)
        return jev_result()

    code, output = run_spawn(
        spawn_argv(), tmp_path, herdr_command=executable, jev_callable=fake_jev
    )

    assert code == 0
    assert output == (
        "started harness=claude model=opus effort=max pane=w1:p2 name=worker\n"
    )
    records = read_log(log)
    assert [record["argv"] for record in records] == [
        [
            "agent",
            "start",
            "worker",
            "--kind",
            "claude",
            "--pane",
            "w1:p2",
            "--",
            "--model",
            "opus",
            "--effort",
            "max",
        ],
        ["agent", "prompt", "worker", "Review the authentication change."],
    ]
    assert all(record["has_typesafe_key"] is False for record in records)
    assert len(calls) == 1
    assert calls[0]["api_key"] == "test-key"
    audit = (tmp_path / "audit.jsonl").read_text(encoding="utf-8")
    assert "Review the authentication change" not in audit
    assert json.loads(audit.splitlines()[-1])["request_id"] == "spawn-request-1"


def test_spawn_without_pane_splits_the_current_pane_and_uses_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable, log = fake_herdr(tmp_path, monkeypatch)

    async def fake_jev(**kwargs: object) -> JevRoutingResult:
        return jev_result()

    code, output = run_spawn(
        spawn_argv(pane=None), tmp_path, herdr_command=executable, jev_callable=fake_jev
    )

    assert code == 0
    assert output == (
        "started harness=claude model=opus effort=max pane=w9:p9 name=worker\n"
    )
    records = read_log(log)
    assert records[0]["argv"] == [
        "pane",
        "split",
        "--current",
        "--direction",
        "right",
        "--no-focus",
    ]
    assert records[1]["argv"][:7] == [
        "agent",
        "start",
        "worker",
        "--kind",
        "claude",
        "--pane",
        "w9:p9",
    ]
    assert records[2]["argv"] == [
        "agent",
        "prompt",
        "worker",
        "Review the authentication change.",
    ]


def test_spawn_without_pane_fails_closed_when_the_split_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable, log = fake_herdr(
        tmp_path, monkeypatch, split_response="", split_status=1
    )

    async def fake_jev(**kwargs: object) -> JevRoutingResult:
        return jev_result()

    code, output = run_spawn(
        spawn_argv(pane=None), tmp_path, herdr_command=executable, jev_callable=fake_jev
    )

    assert code == 1
    assert denial(output)["code"] == "pane_split_failed"
    assert [record["argv"][:2] for record in read_log(log)] == [["pane", "split"]]


def test_spawn_without_pane_fails_closed_when_the_split_response_is_invalid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable, log = fake_herdr(tmp_path, monkeypatch, split_response="not json")

    async def fake_jev(**kwargs: object) -> JevRoutingResult:
        return jev_result()

    code, output = run_spawn(
        spawn_argv(pane=None), tmp_path, herdr_command=executable, jev_callable=fake_jev
    )

    assert code == 1
    assert denial(output)["code"] == "pane_split_failed"
    assert [record["argv"][:2] for record in read_log(log)] == [["pane", "split"]]


def test_spawn_fails_closed_when_the_split_pane_id_has_a_c1_control_character(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    split_response = (
        '{"id":"cli:pane:split","result":{"type":"pane_info",'
        '"pane":{"pane_id":"w9:p\u009b9"}}}'
    )
    executable, log = fake_herdr(tmp_path, monkeypatch, split_response=split_response)

    async def fake_jev(**kwargs: object) -> JevRoutingResult:
        return jev_result()

    code, output = run_spawn(
        spawn_argv(pane=None), tmp_path, herdr_command=executable, jev_callable=fake_jev
    )

    assert code == 1
    assert denial(output)["code"] == "pane_split_failed"
    assert [record["argv"][:2] for record in read_log(log)] == [["pane", "split"]]


def test_spawn_without_pane_fails_closed_when_the_split_response_is_too_large(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable, log = fake_herdr(tmp_path, monkeypatch, split_response="")
    monkeypatch.setenv("FAKE_HERDR_SPLIT_BYTES", str(128 * 1024))

    async def fake_jev(**kwargs: object) -> JevRoutingResult:
        return jev_result()

    code, output = run_spawn(
        spawn_argv(pane=None), tmp_path, herdr_command=executable, jev_callable=fake_jev
    )

    assert code == 1
    assert denial(output)["code"] == "pane_split_failed"
    assert [record["argv"][:2] for record in read_log(log)] == [["pane", "split"]]


def test_spawn_forwards_role_and_constraints_to_jev_and_audit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable, _ = fake_herdr(tmp_path, monkeypatch)
    calls: list[dict[str, object]] = []

    async def fake_jev(**kwargs: object) -> JevRoutingResult:
        calls.append(kwargs)
        return jev_result()

    code, _ = run_spawn(
        spawn_argv(extra=("--role", "reviewer", "--read-only", "--worktree")),
        tmp_path,
        herdr_command=executable,
        jev_callable=fake_jev,
    )

    assert code == 0
    assert calls[0]["role"] == "reviewer"
    assert calls[0]["constraints"] == {
        "read_only": True,
        "worktree": True,
        "network_required": False,
    }
    record = json.loads(
        (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()[-1]
    )
    assert record["request"]["role"] == "reviewer"
    assert record["request"]["constraints"] == {
        "read_only": True,
        "worktree": True,
        "network_required": False,
    }


def test_spawn_delivers_task_verbatim_without_parsing_injection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable, log = fake_herdr(tmp_path, monkeypatch)
    task = "review --model opus --effort max --kind codex\nsecond line"

    async def fake_jev(**kwargs: object) -> JevRoutingResult:
        return jev_result()

    code, _ = run_spawn(
        spawn_argv(task), tmp_path, herdr_command=executable, jev_callable=fake_jev
    )

    assert code == 0
    records = read_log(log)
    start = records[0]["argv"]
    assert isinstance(start, list)
    assert start[:7] == [
        "agent",
        "start",
        "worker",
        "--kind",
        "claude",
        "--pane",
        "w1:p2",
    ]
    assert start[7:] == ["--", "--model", "opus", "--effort", "max"]
    assert records[1]["argv"] == ["agent", "prompt", "worker", task]


@pytest.mark.parametrize(
    "extra",
    [
        ("--harness", "codex"),
        ("--model", "opus"),
        ("--effort", "max"),
        ("--cwd", "/tmp"),
        ("--args", "--model opus"),
        ("--timeout", "1000"),
        ("--", "--model", "opus"),
    ],
)
def test_spawn_rejects_injected_launch_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, extra: tuple[str, ...]
) -> None:
    executable, log = fake_herdr(tmp_path, monkeypatch)
    called = False

    async def fake_jev(**kwargs: object) -> JevRoutingResult:
        nonlocal called
        called = True
        return jev_result()

    code, output = run_spawn(
        spawn_argv(extra=extra),
        tmp_path,
        herdr_command=executable,
        jev_callable=fake_jev,
    )

    assert code == 2
    assert json.loads(output) == {
        "version": 2,
        "request_id": None,
        "denial": {"code": "invalid_request", "message": "routing denied"},
    }
    assert read_log(log) == []
    assert called is False


@pytest.mark.parametrize(
    "argv",
    [
        ["spawn", "   ", "--name", "worker", "--pane", "w1:p2"],
        ["spawn", "task", "--name", "", "--pane", "w1:p2"],
        ["spawn", "task", "--name", "worker", "--pane", "w1:p2", "--role", "boss"],
    ],
)
def test_spawn_rejects_invalid_arguments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, argv: list[str]
) -> None:
    executable, log = fake_herdr(tmp_path, monkeypatch)
    called = False

    async def fake_jev(**kwargs: object) -> JevRoutingResult:
        nonlocal called
        called = True
        return jev_result()

    code, output = run_spawn(
        argv, tmp_path, herdr_command=executable, jev_callable=fake_jev
    )

    assert code == 2
    assert denial(output)["code"] == "invalid_request"
    assert read_log(log) == []
    assert called is False


def test_spawn_rejects_a_null_task_before_starting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable, log = fake_herdr(tmp_path, monkeypatch)
    called = False

    async def fake_jev(**kwargs: object) -> JevRoutingResult:
        nonlocal called
        called = True
        return jev_result()

    code, output = run_spawn(
        ["spawn", "bad\x00task", "--name", "worker", "--pane", "w1:p2"],
        tmp_path,
        herdr_command=executable,
        jev_callable=fake_jev,
    )

    assert code == 2
    assert denial(output)["code"] == "invalid_request"
    assert read_log(log) == []
    assert called is False


@pytest.mark.parametrize(
    "task",
    [
        "escape\x1b[201~task",
        "bell\x07task",
        "delete\x7ftask",
        "vertical\x0btab",
        "form\x0cfeed",
        "eight-bit-csi\x9b201~task",
        "padding\x80task",
        "nel\x85task",
        "osc\x9d0;titletask",
        "unit-separator\x9ftask",
    ],
)
def test_spawn_rejects_control_characters_in_the_task(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, task: str
) -> None:
    executable, log = fake_herdr(tmp_path, monkeypatch)
    called = False

    async def fake_jev(**kwargs: object) -> JevRoutingResult:
        nonlocal called
        called = True
        return jev_result()

    code, output = run_spawn(
        spawn_argv(task), tmp_path, herdr_command=executable, jev_callable=fake_jev
    )

    assert code == 2
    assert denial(output)["code"] == "invalid_request"
    assert read_log(log) == []
    assert called is False


def test_spawn_allows_newline_and_tab_in_the_task(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable, log = fake_herdr(tmp_path, monkeypatch)
    task = "first line\nsecond\tline"

    async def fake_jev(**kwargs: object) -> JevRoutingResult:
        return jev_result()

    code, _ = run_spawn(
        spawn_argv(task), tmp_path, herdr_command=executable, jev_callable=fake_jev
    )

    assert code == 0
    assert read_log(log)[1]["argv"] == ["agent", "prompt", "worker", task]


@pytest.mark.parametrize(
    ("name", "pane"),
    [
        ("work\x9ber", "w1:p2"),
        ("worker", "w1:p\x852"),
        ("worker", "w1:p\x9d2"),
    ],
)
def test_spawn_rejects_c1_control_characters_in_name_and_pane(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    pane: str,
) -> None:
    executable, log = fake_herdr(tmp_path, monkeypatch)
    called = False

    async def fake_jev(**kwargs: object) -> JevRoutingResult:
        nonlocal called
        called = True
        return jev_result()

    code, output = run_spawn(
        spawn_argv(name=name, pane=pane),
        tmp_path,
        herdr_command=executable,
        jev_callable=fake_jev,
    )

    assert code == 2
    assert denial(output)["code"] == "invalid_request"
    assert read_log(log) == []
    assert called is False


@pytest.mark.parametrize(
    "task",
    [
        "a\ud800b",
        "lone-high\ud800",
        "lone-low\udcff",
    ],
)
def test_spawn_rejects_lone_surrogates_in_the_task(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, task: str
) -> None:
    executable, log = fake_herdr(tmp_path, monkeypatch)
    called = False

    async def fake_jev(**kwargs: object) -> JevRoutingResult:
        nonlocal called
        called = True
        return jev_result()

    code, output = run_spawn(
        spawn_argv(task), tmp_path, herdr_command=executable, jev_callable=fake_jev
    )

    assert code == 2
    assert denial(output)["code"] == "invalid_request"
    assert read_log(log) == []
    assert called is False


@pytest.mark.parametrize("field", ["name", "pane"])
def test_spawn_rejects_lone_surrogates_in_name_and_pane(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, field: str
) -> None:
    executable, log = fake_herdr(tmp_path, monkeypatch)
    called = False

    async def fake_jev(**kwargs: object) -> JevRoutingResult:
        nonlocal called
        called = True
        return jev_result()

    code, output = run_spawn(
        spawn_argv(
            name="a\ud800b" if field == "name" else "worker",
            pane="w1:p\ud8002" if field == "pane" else "w1:p2",
        ),
        tmp_path,
        herdr_command=executable,
        jev_callable=fake_jev,
    )

    assert code == 2
    assert denial(output)["code"] == "invalid_request"
    assert read_log(log) == []
    assert called is False


@pytest.mark.parametrize(
    "task",
    [
        "\u200b",
        "\u200b\u200c\u200d",
        "\u2060",
        " \u200b\t\u200c ",
    ],
)
def test_spawn_rejects_invisible_only_tasks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, task: str
) -> None:
    executable, log = fake_herdr(tmp_path, monkeypatch)
    called = False

    async def fake_jev(**kwargs: object) -> JevRoutingResult:
        nonlocal called
        called = True
        return jev_result()

    code, output = run_spawn(
        spawn_argv(task), tmp_path, herdr_command=executable, jev_callable=fake_jev
    )

    assert code == 2
    assert denial(output)["code"] == "invalid_request"
    assert read_log(log) == []
    assert called is False


def test_spawn_allows_non_ascii_task_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable, log = fake_herdr(tmp_path, monkeypatch)
    task = "Überprüfe die README\u00a0🚀"

    async def fake_jev(**kwargs: object) -> JevRoutingResult:
        return jev_result()

    code, _ = run_spawn(
        spawn_argv(task), tmp_path, herdr_command=executable, jev_callable=fake_jev
    )

    assert code == 0
    assert read_log(log)[1]["argv"] == ["agent", "prompt", "worker", task]


def test_spawn_allows_format_characters_inside_visible_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable, log = fake_herdr(tmp_path, monkeypatch)
    task = "Fix\u200bthe\u2060test"

    async def fake_jev(**kwargs: object) -> JevRoutingResult:
        return jev_result()

    code, _ = run_spawn(
        spawn_argv(task), tmp_path, herdr_command=executable, jev_callable=fake_jev
    )

    assert code == 0
    assert read_log(log)[1]["argv"] == ["agent", "prompt", "worker", task]


def test_spawn_allows_non_ascii_name_and_pane(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable, log = fake_herdr(tmp_path, monkeypatch)
    name = "wörker-Ü"
    pane = "w1:p2"

    async def fake_jev(**kwargs: object) -> JevRoutingResult:
        return jev_result()

    code, _ = run_spawn(
        spawn_argv(name=name, pane=pane),
        tmp_path,
        herdr_command=executable,
        jev_callable=fake_jev,
    )

    assert code == 0
    started = read_log(log)[0]["argv"]
    assert started[:3] == ["agent", "start", name]
    pane_flag = started.index("--pane")
    assert started[pane_flag + 1] == pane


def test_spawn_tolerates_large_herdr_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable, log = fake_herdr(tmp_path, monkeypatch)
    monkeypatch.setenv("FAKE_HERDR_STDOUT_BYTES", str(8 * 1024 * 1024))

    async def fake_jev(**kwargs: object) -> JevRoutingResult:
        return jev_result()

    code, _ = run_spawn(
        spawn_argv(), tmp_path, herdr_command=executable, jev_callable=fake_jev
    )

    assert code == 0
    assert len(read_log(log)) == 2


def test_spawn_fails_closed_when_jev_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable, log = fake_herdr(tmp_path, monkeypatch)

    async def fake_jev(**kwargs: object) -> JevRoutingResult:
        raise RuntimeError("SENTINEL_PROVIDER_CREDENTIAL")

    code, output = run_spawn(
        spawn_argv(),
        tmp_path,
        herdr_command=executable,
        jev_callable=fake_jev,
        environ={"TYPESAFE_API_KEY": "SENTINEL_TYPESAFE_KEY"},
    )

    assert code == 1
    assert denial(output)["code"] == "jev_failed"
    assert read_log(log) == []
    assert "SENTINEL" not in output
    assert "SENTINEL" not in (tmp_path / "audit.jsonl").read_text(encoding="utf-8")


def test_spawn_fails_closed_when_audit_write_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable, log = fake_herdr(tmp_path, monkeypatch)

    def fail(*args: object) -> None:
        raise router_module.AuditError("audit_write_failed", "private detail")

    monkeypatch.setattr(router_module, "append_audit_record", fail)

    async def fake_jev(**kwargs: object) -> JevRoutingResult:
        return jev_result()

    code, output = run_spawn(
        spawn_argv(), tmp_path, herdr_command=executable, jev_callable=fake_jev
    )

    assert code == 1
    assert denial(output)["code"] == "audit_failed"
    assert read_log(log) == []


def test_spawn_fails_closed_when_every_provider_is_exhausted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable, log = fake_herdr(tmp_path, monkeypatch)
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

    code, output = run_spawn(
        spawn_argv(), tmp_path, herdr_command=executable, jev_callable=fake_jev
    )

    assert code == 1
    assert denial(output)["code"] == "no_eligible_provider"
    assert read_log(log) == []
    assert called is False


def test_spawn_does_not_send_task_when_start_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable, log = fake_herdr(
        tmp_path,
        monkeypatch,
        start_response='{"error":{"code":"agent_pane_busy","message":"busy"}}',
        start_status=1,
    )

    async def fake_jev(**kwargs: object) -> JevRoutingResult:
        return jev_result()

    code, output = run_spawn(
        spawn_argv(), tmp_path, herdr_command=executable, jev_callable=fake_jev
    )

    assert code == 1
    assert json.loads(output) == {
        "version": 2,
        "request_id": "spawn-request-1",
        "denial": {"code": "launch_failed", "message": "routing denied"},
    }
    assert len(read_log(log)) == 1


def test_spawn_maps_a_nonzero_start_exit_to_launch_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable, log = fake_herdr(tmp_path, monkeypatch, start_status=1)
    # Arbitrary bytes on the discarded stderr stream must not change the result.
    monkeypatch.setenv("FAKE_HERDR_STDERR_BYTES", "fffe")

    async def fake_jev(**kwargs: object) -> JevRoutingResult:
        return jev_result()

    code, output = run_spawn(
        spawn_argv(), tmp_path, herdr_command=executable, jev_callable=fake_jev
    )

    assert code == 1
    assert denial(output)["code"] == "launch_failed"
    assert len(read_log(log)) == 1


def test_spawn_reports_delivery_failure_after_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable, log = fake_herdr(
        tmp_path,
        monkeypatch,
        prompt_response='{"error":{"code":"agent_blocked","message":"blocked"}}',
        prompt_status=1,
    )

    async def fake_jev(**kwargs: object) -> JevRoutingResult:
        return jev_result()

    code, output = run_spawn(
        spawn_argv(), tmp_path, herdr_command=executable, jev_callable=fake_jev
    )

    assert code == 1
    assert denial(output)["code"] == "task_delivery_failed"
    assert len(read_log(log)) == 2
    assert (tmp_path / "audit.jsonl").exists()


def test_spawn_fails_closed_when_herdr_start_times_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable, log = fake_herdr(tmp_path, monkeypatch)
    monkeypatch.setenv("FAKE_HERDR_SLEEP", "5")
    # Under load a freshly spawned Python process can need more than the old
    # fifth of a second to start and write its log, which failed this test for
    # a reason other than the timeout path it covers. Two seconds stays below
    # FAKE_HERDR_SLEEP so the start still times out.
    monkeypatch.setattr(cli_module, "_START_TIMEOUT_SECONDS", 2.0)

    async def fake_jev(**kwargs: object) -> JevRoutingResult:
        return jev_result()

    code, output = run_spawn(
        spawn_argv(), tmp_path, herdr_command=executable, jev_callable=fake_jev
    )

    assert code == 1
    assert denial(output)["code"] == "launch_failed"
    assert len(read_log(log)) == 1


def test_spawn_fails_closed_without_credential_or_herdr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable, log = fake_herdr(tmp_path, monkeypatch)
    called = False

    async def fake_jev(**kwargs: object) -> JevRoutingResult:
        nonlocal called
        called = True
        return jev_result()

    no_key_code, no_key_output = run_spawn(
        spawn_argv(),
        tmp_path,
        herdr_command=executable,
        jev_callable=fake_jev,
        environ={},
    )
    assert no_key_code == 2
    assert denial(no_key_output)["code"] == "configuration_failed"
    assert read_log(log) == []

    no_herdr_code, no_herdr_output = run_spawn(
        spawn_argv(),
        tmp_path,
        herdr_command=None,
        jev_callable=fake_jev,
        command_finder=lambda name: None,
    )
    assert no_herdr_code == 2
    assert denial(no_herdr_output)["code"] == "configuration_failed"
    assert read_log(log) == []
    assert called is False


def test_spawn_keeps_the_credential_out_of_output_audit_and_child_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable, log = fake_herdr(tmp_path, monkeypatch)
    monkeypatch.setenv("TYPESAFE_API_KEY", "SENTINEL_TYPESAFE_KEY")

    async def fake_jev(**kwargs: object) -> JevRoutingResult:
        return jev_result()

    code, output = run_spawn(
        spawn_argv(),
        tmp_path,
        herdr_command=executable,
        jev_callable=fake_jev,
        environ={"TYPESAFE_API_KEY": "SENTINEL_TYPESAFE_KEY"},
    )

    assert code == 0
    assert "SENTINEL" not in output
    assert "SENTINEL" not in (tmp_path / "audit.jsonl").read_text(encoding="utf-8")
    assert all(record["has_typesafe_key"] is False for record in read_log(log))
