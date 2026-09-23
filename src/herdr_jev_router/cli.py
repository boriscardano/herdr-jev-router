"""Expose the advisory Herdr spawn and review commands."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import stat
import subprocess
import sys
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from time import time
from typing import Any, Never, TextIO, cast

from herdr_jev_router.harness import (
    OPT_IN_VARIABLES,
    HarnessAvailability,
    HarnessConfigurationError,
    harness_availability,
)
from herdr_jev_router.jev import route_with_jev
from herdr_jev_router.models import (
    CapacityState,
    Effort,
    Harness,
    ProviderCapacity,
)
from herdr_jev_router.policy import (
    UNKNOWN_CAPACITY_PENALTY,
    RoutingPolicyError,
    launch_profile,
)
from herdr_jev_router.quota import classify_capacity, read_cache
from herdr_jev_router.quota_claude import (
    CLAUDE_CACHE_NAME,
    CLAUDE_REFRESH_INTERVAL_SECONDS,
)
from herdr_jev_router.quota_codex import (
    CODEX_CACHE_NAME,
    CODEX_REFRESH_INTERVAL_SECONDS,
)
from herdr_jev_router.router import (
    RecommendationResult,
    RouterError,
    recommend,
)

_START_TIMEOUT_SECONDS = 60.0
_PROMPT_TIMEOUT_SECONDS = 30.0
_SPLIT_TIMEOUT_SECONDS = 30.0
_CAPTURED_HERDR_BYTES = 64 * 1024
_CONSTRAINT_NAMES = ("read_only", "worktree", "network_required")
_ROLES = frozenset({"worker", "reviewer", "debugger", "researcher"})

_CACHE_REFRESH_INTERVAL_SECONDS = {
    Harness.CLAUDE: CLAUDE_REFRESH_INTERVAL_SECONDS,
    Harness.CODEX: CODEX_REFRESH_INTERVAL_SECONDS,
    Harness.OPENCODE: 30 * 60,
    Harness.PI: 30 * 60,
}

JevCallable = Callable[..., Awaitable[object]]
Clock = Callable[[], int | float]
CommandFinder = Callable[[str], str | None]
RequestIdFactory = Callable[[], str]


def _default_request_id() -> str:
    """Return a fresh opaque request id for one advisory command."""

    return uuid.uuid4().hex


class _RequestError(ValueError):
    pass


class _UsageError(ValueError):
    pass


class _ConfigurationError(RuntimeError):
    pass


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> Never:
        raise _UsageError(message)


@dataclass(frozen=True, slots=True)
class _SpawnResult:
    """Summarize one started advisory child."""

    harness: Harness
    model: str
    effort: Effort
    pane: str
    name: str


def _parser(environment: Mapping[str, str]) -> argparse.ArgumentParser:
    state_default = environment.get("HERDR_JEV_ROUTER_STATE_DIR")
    if state_default is None:
        state_root = environment.get("XDG_STATE_HOME")
        state_default = os.fspath(
            Path(state_root) / "herdr-jev-router"
            if state_root
            else Path.home() / ".local" / "state" / "herdr-jev-router"
        )

    parser = _ArgumentParser(
        prog="herdr-jev-router", formatter_class=argparse.RawTextHelpFormatter
    )
    commands = parser.add_subparsers(dest="command", required=True)
    spawn_parser = commands.add_parser(
        "spawn",
        formatter_class=argparse.RawTextHelpFormatter,
        description=(
            "Ask Jev for one child-agent decision and start it with the stock "
            "`herdr agent start` CLI, then deliver TASK once with "
            "`herdr agent prompt`. Advisory only: it cannot stop a direct "
            "`herdr agent start`. Jev credentials come only from "
            "TYPESAFE_API_KEY."
        ),
    )
    explain_parser = commands.add_parser(
        "explain",
        formatter_class=argparse.RawTextHelpFormatter,
        description=(
            "Ask Jev for one child-agent decision and print a human-readable "
            "review. It audits the decision but never starts an agent. Jev "
            "credentials come only from TYPESAFE_API_KEY."
        ),
    )
    spawn_parser.add_argument("--name", required=True, help="Herdr agent name")
    spawn_parser.add_argument(
        "--pane",
        help=(
            "existing pane at its interactive shell prompt "
            "(default: split the current pane)"
        ),
    )
    for advisory_parser in (spawn_parser, explain_parser):
        advisory_parser.add_argument(
            "task", metavar="TASK", help="task text routed by Jev"
        )
        advisory_parser.add_argument(
            "--role", default="worker", help="routing role (default: worker)"
        )
        advisory_parser.add_argument(
            "--read-only", action="store_true", help="mark the task read-only"
        )
        advisory_parser.add_argument(
            "--worktree", action="store_true", help="require an isolated worktree"
        )
        advisory_parser.add_argument(
            "--network-required",
            action="store_true",
            help="mark network access required",
        )
        advisory_parser.add_argument(
            "--state-dir",
            type=Path,
            default=Path(state_default),
            help=(
                "owner-only quota cache directory (default: "
                "$HERDR_JEV_ROUTER_STATE_DIR, then $XDG_STATE_HOME/herdr-jev-router, "
                "then ~/.local/state/herdr-jev-router)"
            ),
        )
        advisory_parser.add_argument(
            "--audit-path",
            type=Path,
            help="owner-only audit JSONL path (default: <state-dir>/routing.jsonl)",
        )
    for name, description in (
        ("usage", "Show normalized current provider capacity."),
        ("doctor", "Validate the local router installation."),
    ):
        command_parser = commands.add_parser(name, description=description)
        command_parser.add_argument(
            "--state-dir",
            type=Path,
            default=Path(state_default),
            help="owner-only router state directory",
        )
        if name == "doctor":
            command_parser.add_argument(
                "--human",
                action="store_true",
                help="print a friendly first-run check instead of JSON",
            )
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    stdout: TextIO | None = None,
    environ: Mapping[str, str] | None = None,
    jev_callable: JevCallable = route_with_jev,
    clock: Clock = time,
    command_finder: CommandFinder = shutil.which,
    herdr_command: str | None = None,
    request_id_factory: RequestIdFactory = _default_request_id,
) -> int:
    """Run one fail-closed advisory command and return its process exit code."""

    output = sys.stdout if stdout is None else stdout
    environment = os.environ if environ is None else environ
    request_id: str | None = None
    try:
        arguments = _parser(environment).parse_args(
            list(sys.argv[1:] if argv is None else argv)
        )
        availability = harness_availability(command_finder, environment)
        if arguments.command == "usage":
            _write_json(
                output,
                _usage(
                    arguments.state_dir,
                    now=float(clock()),
                    availability=availability,
                ),
            )
            return 0
        if arguments.command == "doctor":
            result = _doctor(
                arguments.state_dir,
                environment=environment,
                command_finder=command_finder,
                now=float(clock()),
                availability=availability,
            )
            if arguments.human:
                _write_doctor_human(output, result)
            else:
                _write_json(output, result)
            return 0 if result["ok"] else 1
        if availability.configuration_error is not None:
            raise HarnessConfigurationError(
                availability.configuration_error,
                "invalid harness configuration",
            )
        request_id = request_id_factory()
        if arguments.command == "spawn":
            _write_spawn_summary(
                output,
                _spawn_command(
                    arguments,
                    environment=environment,
                    herdr_command=herdr_command,
                    command_finder=command_finder,
                    jev_callable=jev_callable,
                    clock=clock,
                    request_id=request_id,
                    availability=availability,
                ),
            )
            return 0
        _write_explain(
            output,
            _explain_command(
                arguments,
                environment=environment,
                jev_callable=jev_callable,
                clock=clock,
                request_id=request_id,
                availability=availability,
            ),
            availability,
        )
        return 0
    except _ConfigurationError:
        _write_denial(output, request_id, "configuration_failed")
        return 2
    except HarnessConfigurationError as error:
        _write_denial(output, request_id, error.code)
        return 2
    except (_RequestError, _UsageError):
        _write_denial(output, request_id, "invalid_request")
        return 2
    except RoutingPolicyError as error:
        _write_denial(output, request_id, error.code)
        return 1
    except RouterError as error:
        _write_denial(output, request_id, error.code)
        return 1
    except Exception:
        _write_denial(output, request_id, "routing_failed")
        return 1


def _usage(
    state_dir: Path, *, now: float, availability: HarnessAvailability
) -> dict[str, object]:
    result: dict[str, object] = {}
    for harness, refresh_interval in _CACHE_REFRESH_INTERVAL_SECONDS.items():
        snapshot = read_cache(
            state_dir / f"{harness.value}-quota.json",
            now=now,
            refresh_interval=refresh_interval,
        )
        result[harness.value] = {
            "state": classify_capacity(snapshot, now=now).value,
            "freshness": snapshot.freshness if snapshot is not None else None,
            "source": snapshot.source if snapshot is not None else None,
            "detected": harness in availability.detected,
            "enabled": harness in availability.enabled,
        }
    return result


def _doctor(
    state_dir: Path,
    *,
    environment: Mapping[str, str],
    command_finder: CommandFinder,
    now: float,
    availability: HarnessAvailability,
) -> dict[str, object]:
    credential_ok = bool(environment.get("TYPESAFE_API_KEY", "").strip())
    state_ok = _path_has_mode(state_dir, expected_mode=0o700, directory=True)
    router_files = {
        "claude_cache": _file_status(state_dir / CLAUDE_CACHE_NAME),
        "codex_cache": _file_status(state_dir / CODEX_CACHE_NAME),
        "audit": _file_status(state_dir / "routing.jsonl"),
    }
    router_files_ok = all(value != "insecure" for value in router_files.values())
    commands = {
        name: command_finder(name) is not None
        for name in ("herdr", "codex", "claude", "opencode", "pi")
    }
    configuration_ok = availability.configuration_error is None
    commands_ok = commands["herdr"] and bool(availability.enabled)
    ok = (
        credential_ok
        and state_ok
        and router_files_ok
        and commands_ok
        and configuration_ok
    )
    return {
        "ok": ok,
        "checks": {
            "credential": {"ok": credential_ok},
            "state_directory": {"ok": state_ok},
            "router_files": {"ok": router_files_ok, **router_files},
            "providers": _usage(state_dir, now=now, availability=availability),
            "commands": {"ok": commands_ok, **commands},
            "harness_configuration": {
                "ok": configuration_ok,
                "error": availability.configuration_error,
            },
        },
    }


_QUOTA_SETUP = {
    "claude": "configure the Claude status-line collector (herdr-jev-quota-claude)",
    "codex": "run herdr-jev-quota-codex to record a snapshot",
}


def _write_doctor_human(output: TextIO, result: Mapping[str, object]) -> None:
    """Print one actionable line per doctor check for a first run."""

    checks = cast("Mapping[str, Any]", result["checks"])
    providers = cast("Mapping[str, Mapping[str, Any]]", checks["providers"])
    lines: list[str] = []
    if checks["credential"]["ok"]:
        lines.append("TypeSafe key: ok")
    else:
        lines.append(
            "TypeSafe key: missing, set TYPESAFE_API_KEY in the environment you "
            "run herdr-jev-router from"
        )
    if checks["commands"]["herdr"]:
        lines.append("herdr: ok")
    else:
        lines.append("herdr: not found on PATH, install stock Herdr")
    configuration_error = checks["harness_configuration"]["error"]
    if configuration_error is not None:
        lines.append(f"harness configuration: {configuration_error}")
    for name, entry in providers.items():
        lines.append(_harness_line(name, entry))
    if not any(entry["enabled"] for entry in providers.values()):
        lines.append("harnesses: none enabled, install Claude Code or Codex")
    if checks["state_directory"]["ok"]:
        lines.append("state directory: ok")
    else:
        lines.append(
            "state directory: not owner-only, run chmod 700 on the state directory"
        )
    if checks["router_files"]["ok"]:
        lines.append("state files: ok")
    else:
        lines.append(
            "state files: insecure or missing, fix permissions or let the router "
            "recreate them"
        )
    lines.extend(
        line
        for name, entry in providers.items()
        if (line := _quota_line(name, entry)) is not None
    )
    _write_output(output, "\n".join(lines) + "\n")


def _harness_line(name: str, entry: Mapping[str, Any]) -> str:
    """Describe one harness as enabled, missing, or needing an opt-in."""

    if entry["enabled"]:
        return f"harness {name}: enabled"
    if not entry["detected"]:
        return f"harness {name}: not installed"
    variable = OPT_IN_VARIABLES.get(Harness(name))
    if variable is None:
        return f"harness {name}: detected but not enabled"
    return f"harness {name}: detected but not enabled, set {variable}"


def _quota_line(name: str, entry: Mapping[str, Any]) -> str | None:
    """Describe one enabled harness's quota, with setup guidance when empty."""

    if not entry["enabled"]:
        return None
    if entry["source"] is not None:
        return f"quota {name}: {entry['state']}"
    setup = _QUOTA_SETUP.get(name)
    if setup is None:
        return f"quota {name}: no local quota source, capacity stays unknown"
    return f"quota {name}: no data yet, {setup}"


def _path_has_mode(path: Path, *, expected_mode: int, directory: bool) -> bool:
    try:
        details = path.lstat()
    except OSError:
        return False
    expected_type = stat.S_ISDIR if directory else stat.S_ISREG
    return (
        expected_type(details.st_mode)
        and stat.S_IMODE(details.st_mode) == expected_mode
    )


def _file_status(path: Path) -> str:
    try:
        path.lstat()
    except OSError:
        return "missing"
    return (
        "secure"
        if _path_has_mode(path, expected_mode=0o600, directory=False)
        else "insecure"
    )


def _task_text(value: object) -> str:
    """Validate one advisory task before it reaches Jev or the Herdr CLI."""

    task = _bounded_string(value, maximum=256 * 1024)
    # Newline and tab are legitimate task text. Every other C0 character, and
    # DEL, can drive the child terminal instead. Stock Herdr delivers the task
    # as a bracketed paste, so an embedded `ESC[201~` would close the paste
    # region early and deliver the rest as raw keystrokes.
    if any(
        (ord(character) < 0x20 and character not in "\n\t") or ord(character) == 0x7F
        for character in task
    ):
        raise _RequestError
    return task


def _role(value: object) -> str:
    """Validate one routing role against the closed role set."""

    if not isinstance(value, str) or value not in _ROLES:
        raise _RequestError
    return value


def _constraints(arguments: argparse.Namespace) -> dict[str, bool]:
    return {
        "read_only": arguments.read_only,
        "worktree": arguments.worktree,
        "network_required": arguments.network_required,
    }


def _recommend_decision(
    *,
    request_id: str,
    task: str,
    role: str,
    constraints: Mapping[str, bool],
    state_dir: Path,
    audit_path: Path | None,
    environment: Mapping[str, str],
    jev_callable: JevCallable,
    clock: Clock,
    availability: HarnessAvailability,
) -> RecommendationResult:
    """Ask Jev for one audited advisory decision without starting anything."""

    api_key = environment.get("TYPESAFE_API_KEY")
    if not api_key:
        raise _ConfigurationError
    capacities = _capacity_snapshot_for(state_dir, clock, enabled=availability.enabled)

    async def configured_jev(**kwargs: object) -> object:
        return await jev_callable(api_key=api_key, **kwargs)

    return asyncio.run(
        recommend(
            request_id=request_id,
            task=task,
            role=role,
            constraints=constraints,
            capacities=capacities,
            audit_path=audit_path or state_dir / "routing.jsonl",
            jev_callable=configured_jev,
            clock=clock,
        )
    )


def _spawn_command(
    arguments: argparse.Namespace,
    *,
    environment: Mapping[str, str],
    herdr_command: str | None,
    command_finder: CommandFinder,
    jev_callable: JevCallable,
    clock: Clock,
    request_id: str,
    availability: HarnessAvailability,
) -> _SpawnResult:
    """Route one advisory spawn request and start the child exactly once."""

    task = _task_text(arguments.task)
    name = _spawn_identifier(arguments.name)
    pane = None if arguments.pane is None else _spawn_identifier(arguments.pane)
    role = _role(arguments.role)
    executable = herdr_command or command_finder("herdr")
    if not executable:
        raise _ConfigurationError
    result = _recommend_decision(
        request_id=request_id,
        task=task,
        role=role,
        constraints=_constraints(arguments),
        state_dir=arguments.state_dir,
        audit_path=arguments.audit_path,
        environment=environment,
        jev_callable=jev_callable,
        clock=clock,
        availability=availability,
    )
    try:
        profile = launch_profile(result.recommended, harness_launch=availability.launch)
    except (RoutingPolicyError, TypeError, ValueError):
        raise RouterError("launch_profile_failed", "launch profile failed") from None

    if pane is None:
        # Split only after Jev succeeds, so a denied route never leaves an
        # empty pane behind.
        pane = _split_pane(executable)

    _run_herdr(
        [
            executable,
            "agent",
            "start",
            name,
            "--kind",
            profile.kind.value,
            "--pane",
            pane,
            "--",
            *profile.args,
        ],
        timeout=_START_TIMEOUT_SECONDS,
        failure_code="launch_failed",
    )
    _run_herdr(
        [executable, "agent", "prompt", name, task],
        timeout=_PROMPT_TIMEOUT_SECONDS,
        failure_code="task_delivery_failed",
    )
    return _SpawnResult(
        harness=profile.kind,
        model=result.recommended.model.value,
        effort=result.recommended.effort,
        pane=pane,
        name=name,
    )


def _explain_command(
    arguments: argparse.Namespace,
    *,
    environment: Mapping[str, str],
    jev_callable: JevCallable,
    clock: Clock,
    request_id: str,
    availability: HarnessAvailability,
) -> RecommendationResult:
    """Route one advisory review request without starting an agent."""

    return _recommend_decision(
        request_id=request_id,
        task=_task_text(arguments.task),
        role=_role(arguments.role),
        constraints=_constraints(arguments),
        state_dir=arguments.state_dir,
        audit_path=arguments.audit_path,
        environment=environment,
        jev_callable=jev_callable,
        clock=clock,
        availability=availability,
    )


def _spawn_identifier(value: object) -> str:
    """Validate one agent name or pane id before it reaches the Herdr CLI."""

    identifier = _bounded_string(value, maximum=256)
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in identifier):
        raise _RequestError
    return identifier


def _run_herdr(argv: Sequence[str], *, timeout: float, failure_code: str) -> None:
    """Run one stock Herdr CLI command without a shell or captured output.

    The router never reads Herdr's output, so both streams are discarded
    instead of captured. This bounds router memory even if `herdr` writes
    without limit, and it keeps `subprocess.run`'s timeout semantics.
    """

    try:
        completed = subprocess.run(
            list(argv),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            env=_child_environment(),
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise RouterError(failure_code, "herdr command failed") from None
    if completed.returncode != 0:
        raise RouterError(failure_code, "herdr command failed")


def _run_herdr_capture(
    argv: Sequence[str], *, timeout: float, failure_code: str
) -> str:
    """Run one stock Herdr CLI command and return its bounded stdout.

    `pane split` needs its small JSON reply, so this path captures stdout. A
    reply longer than `_CAPTURED_HERDR_BYTES` is treated as a failure.
    """

    try:
        completed = subprocess.run(
            list(argv),
            capture_output=True,
            check=False,
            env=_child_environment(),
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise RouterError(failure_code, "herdr command failed") from None
    if completed.returncode != 0 or len(completed.stdout) > _CAPTURED_HERDR_BYTES:
        raise RouterError(failure_code, "herdr command failed")
    return completed.stdout.decode("utf-8", errors="replace")


def _split_pane(executable: str) -> str:
    """Split the caller's pane and return the new pane id, or fail closed.

    The new pane goes to the right and keeps the caller visible, and
    `--no-focus` leaves focus in the caller so the advisory command keeps its
    own pane.
    """

    response = _run_herdr_capture(
        [
            executable,
            "pane",
            "split",
            "--current",
            "--direction",
            "right",
            "--no-focus",
        ],
        timeout=_SPLIT_TIMEOUT_SECONDS,
        failure_code="pane_split_failed",
    )
    try:
        document = json.loads(response)
        pane_id = document["result"]["pane"]["pane_id"]
    except (KeyError, TypeError, ValueError):
        raise RouterError(
            "pane_split_failed", "herdr pane split returned no pane id"
        ) from None
    try:
        return _spawn_identifier(pane_id)
    except _RequestError:
        raise RouterError(
            "pane_split_failed", "herdr pane split returned an invalid pane id"
        ) from None


def _child_environment() -> dict[str, str]:
    """Return a child Herdr CLI environment with no router credential."""

    environment = dict(os.environ)
    environment.pop("TYPESAFE_API_KEY", None)
    return environment


def _write_spawn_summary(output: TextIO, result: _SpawnResult) -> None:
    """Print one short human-readable line describing the started child."""

    _write_output(
        output,
        f"started harness={result.harness.value} model={result.model} "
        f"effort={result.effort.value} pane={result.pane} name={result.name}\n",
    )


def _provider_states(state_dir: Path, *, now: float) -> dict[Harness, CapacityState]:
    """Read every provider cache and classify its current capacity state."""

    return {
        harness: classify_capacity(
            read_cache(
                state_dir / f"{harness.value}-quota.json",
                now=now,
                refresh_interval=refresh_interval,
            ),
            now=now,
        )
        for harness, refresh_interval in _CACHE_REFRESH_INTERVAL_SECONDS.items()
    }


def _capacity_snapshot(
    states: Mapping[Harness, CapacityState], enabled: frozenset[Harness]
) -> tuple[ProviderCapacity, ...]:
    """Attach penalties and force every disabled harness to exhausted."""

    return tuple(
        ProviderCapacity(
            harness,
            state if harness in enabled else CapacityState.EXHAUSTED,
            (
                UNKNOWN_CAPACITY_PENALTY
                if harness in enabled and state is CapacityState.UNKNOWN
                else 0
            ),
        )
        for harness, state in states.items()
    )


def _capacity_snapshot_for(
    state_dir: Path, clock: Clock, *, enabled: frozenset[Harness]
) -> tuple[ProviderCapacity, ...]:
    """Read provider caches and force disabled harnesses to exhausted."""

    return _capacity_snapshot(_provider_states(state_dir, now=float(clock())), enabled)


def _bounded_string(value: object, *, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise _RequestError
    return value


def _write_explain(
    output: TextIO,
    result: RecommendationResult,
    availability: HarnessAvailability,
) -> None:
    """Print one human-readable recommendation without starting anything."""

    decision = result.recommended
    lines = [
        f"recommended harness: {decision.harness.value}",
        f"selected model: {decision.model.value}",
        f"effort: {decision.effort.value}",
    ]
    lines.extend(
        f"capacity {capacity.harness.value}: {_capacity_label(capacity, availability)}"
        for capacity in result.capacities
    )
    _write_output(output, "\n".join(lines) + "\n")


def _capacity_label(
    capacity: ProviderCapacity, availability: HarnessAvailability
) -> str:
    """Explain one capacity line, distinguishing an unavailable harness."""

    harness = capacity.harness
    if harness in availability.enabled:
        return capacity.state.value
    if harness not in availability.detected:
        return "not installed"
    variable = OPT_IN_VARIABLES.get(harness)
    if variable is None:
        return "not enabled"
    return f"not enabled (set {variable})"


def _write_denial(output: TextIO, request_id: str | None, code: str) -> None:
    _write_json(
        output,
        {
            "version": 2,
            "request_id": request_id,
            "denial": {"code": code, "message": "routing denied"},
        },
    )


def _write_json(output: TextIO, value: Mapping[str, object]) -> None:
    _write_output(
        output, json.dumps(value, allow_nan=False, separators=(",", ":")) + "\n"
    )


def _write_output(output: TextIO, text: str) -> None:
    """Write one text block, tolerating a reader that closed the pipe."""

    try:
        output.write(text)
        output.flush()
    except OSError:
        _detach_output(output)


def _detach_output(output: TextIO) -> None:
    """Point later stdout flushes at devnull once the reader is gone."""

    if output is not sys.stdout:
        return
    try:
        devnull = os.open(os.devnull, os.O_WRONLY)
        try:
            os.dup2(devnull, sys.stdout.fileno())
        finally:
            os.close(devnull)
    except OSError:
        return


if __name__ == "__main__":
    raise SystemExit(main())
