"""Detect installed harnesses and resolve their opt-in launch configuration."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from herdr_jev_router.models import TIER_NAMES, Harness, HarnessLaunch

CommandFinder = Callable[[str], str | None]

HARNESS_EXECUTABLES: Mapping[Harness, str] = {
    Harness.CLAUDE: "claude",
    Harness.CODEX: "codex",
    Harness.OPENCODE: "opencode",
    Harness.PI: "pi",
}

OPT_IN_VARIABLES: Mapping[Harness, str] = {
    Harness.OPENCODE: "HERDR_JEV_ROUTER_OPENCODE",
    Harness.PI: "HERDR_JEV_ROUTER_PI",
}

# Claude Code and Codex need no provider or model configuration.
_CONFIGURATION_FREE_HARNESSES = (Harness.CLAUDE, Harness.CODEX)

# Generic user-facing keys, in the order of the internal deepseek/glm/kimi
# launch tiers. A user's small model might be any brand, so the opt-in value
# never names the author's tiers.
_OPT_IN_TIER_KEYS = ("small", "balanced", "large")


class HarnessConfigurationError(ValueError):
    """Report one malformed opt-in harness configuration."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class HarnessAvailability:
    """Hold detected harnesses, the enabled subset, and opt-in launch data."""

    detected: frozenset[Harness]
    enabled: frozenset[Harness]
    launch: Mapping[Harness, HarnessLaunch]
    configuration_error: str | None = None


def detect_harnesses(command_finder: CommandFinder) -> frozenset[Harness]:
    """Return every harness whose executable the command finder resolves."""

    return frozenset(
        harness
        for harness, executable in HARNESS_EXECUTABLES.items()
        if command_finder(executable) is not None
    )


def harness_availability(
    command_finder: CommandFinder, environment: Mapping[str, str]
) -> HarnessAvailability:
    """Return the detected, enabled and opt-in launch state of every harness."""

    detected = detect_harnesses(command_finder)
    enabled = {
        harness for harness in _CONFIGURATION_FREE_HARNESSES if harness in detected
    }
    launch: dict[Harness, HarnessLaunch] = {}
    configuration_error: str | None = None
    for harness, variable in OPT_IN_VARIABLES.items():
        value = environment.get(variable)
        if value is None or not value.strip():
            continue
        try:
            launch[harness] = parse_launch_config(value)
        except HarnessConfigurationError as error:
            configuration_error = configuration_error or error.code
            continue
        if harness in detected:
            enabled.add(harness)
    return HarnessAvailability(
        frozenset(detected), frozenset(enabled), launch, configuration_error
    )


def parse_launch_config(value: str) -> HarnessLaunch:
    """Parse one opt-in value into a validated provider and tier mapping."""

    fields: dict[str, str] = {}
    for entry in value.split(","):
        name, separator, item = entry.partition("=")
        key = name.strip()
        if not separator or key in fields:
            raise _invalid_configuration()
        fields[key] = item.strip()
    if set(fields) != {"provider", *_OPT_IN_TIER_KEYS}:
        raise _invalid_configuration()
    for item in fields.values():
        _require_token(item)
    return HarnessLaunch(
        provider=fields["provider"],
        models={
            tier: fields[key]
            for tier, key in zip(TIER_NAMES, _OPT_IN_TIER_KEYS, strict=True)
        },
    )


def _invalid_configuration() -> HarnessConfigurationError:
    return HarnessConfigurationError(
        "invalid_harness_configuration", "opt-in harness configuration is invalid"
    )


def _require_token(value: str) -> None:
    if (
        not value
        or value.startswith("-")
        or "/" in value
        or any(
            character.isspace() or ord(character) < 0x20 or ord(character) == 0x7F
            for character in value
        )
    ):
        raise _invalid_configuration()
