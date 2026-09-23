"""Apply deterministic policy to typed routing values."""

from collections.abc import Mapping, Sequence
from enum import StrEnum
from typing import TypeVar

from herdr_jev_router.models import (
    CapacityState,
    ClaudeModel,
    CodexModel,
    Effort,
    Harness,
    HarnessLaunch,
    LaunchProfile,
    OpenCodeModel,
    PiModel,
    ProviderCapacity,
    RoutingDecision,
)

UNKNOWN_CAPACITY_PENALTY = 1

_ANSWER_NAMES = frozenset(
    {
        "harness",
        "claude_model",
        "codex_model",
        "opencode_model",
        "pi_model",
        "effort",
    }
)
_CODEX_LAUNCH_MODELS = {
    CodexModel.LUNA: "gpt-5.6-luna",
    CodexModel.TERRA: "gpt-5.6-terra",
    CodexModel.SOL: "gpt-5.6-sol",
}

EnumValue = TypeVar("EnumValue", bound=StrEnum)


class RoutingPolicyError(ValueError):
    """Report a stable fail-closed routing policy error."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def validate_decision(
    answers: Mapping[str, object],
    capacities: Sequence[ProviderCapacity],
) -> RoutingDecision:
    """Validate strict choice answers against currently eligible providers."""

    if set(answers) != _ANSWER_NAMES:
        raise RoutingPolicyError(
            "invalid_answer_keys", "routing answers must contain exactly six choices"
        )

    harness = _choice(answers["harness"], Harness)
    claude_model = _choice(answers["claude_model"], ClaudeModel)
    codex_model = _choice(answers["codex_model"], CodexModel)
    opencode_model = _choice(answers["opencode_model"], OpenCodeModel)
    pi_model = _choice(answers["pi_model"], PiModel)
    effort = _choice(answers["effort"], Effort)

    capacity_by_harness = _capacity_by_harness(capacities)
    eligible = {
        harness
        for harness, capacity in capacity_by_harness.items()
        if capacity.state is not CapacityState.EXHAUSTED
    }
    if harness not in eligible:
        raise RoutingPolicyError(
            "ineligible_harness", "selected harness is not eligible"
        )

    return RoutingDecision(
        harness,
        claude_model,
        codex_model,
        opencode_model,
        pi_model,
        effort,
    )


def launch_profile(
    decision: RoutingDecision,
    *,
    harness_launch: Mapping[Harness, HarnessLaunch] | None = None,
) -> LaunchProfile:
    """Map a semantic decision to allowlisted Herdr launch arguments."""

    if decision.harness is Harness.CLAUDE:
        return LaunchProfile(
            kind=Harness.CLAUDE,
            args=(
                "--model",
                decision.claude_model.value,
                "--effort",
                decision.effort.value,
            ),
        )

    if decision.harness is Harness.CODEX:
        model = _CODEX_LAUNCH_MODELS[decision.codex_model]
        return LaunchProfile(
            kind=Harness.CODEX,
            args=(
                "-m",
                model,
                "-c",
                f'model_reasoning_effort="{decision.effort.value}"',
            ),
        )

    configuration = (harness_launch or {}).get(decision.harness)
    if configuration is None:
        raise RoutingPolicyError(
            "harness_not_configured",
            "selected harness has no opt-in configuration",
        )

    if decision.harness is Harness.OPENCODE:
        model = configuration.models[decision.opencode_model.value]
        return LaunchProfile(
            kind=Harness.OPENCODE,
            args=("--model", f"{configuration.provider}/{model}"),
        )

    return LaunchProfile(
        kind=Harness.PI,
        args=(
            "--provider",
            configuration.provider,
            "--model",
            configuration.models[decision.pi_model.value],
            "--thinking",
            decision.effort.value,
        ),
    )


def _choice(value: object, enum_type: type[EnumValue]) -> EnumValue:
    if not isinstance(value, Mapping) or set(value) != {"type", "value"}:
        raise RoutingPolicyError("invalid_answer", "answer must be one choice")
    if value["type"] != "choice" or not isinstance(value["value"], str):
        raise RoutingPolicyError("invalid_answer", "answer must be one choice")
    try:
        return enum_type(value["value"])
    except ValueError as error:
        raise RoutingPolicyError("invalid_answer", "unknown choice value") from error


def _capacity_by_harness(
    capacities: Sequence[ProviderCapacity],
) -> dict[Harness, ProviderCapacity]:
    by_harness: dict[Harness, ProviderCapacity] = {}
    for capacity in capacities:
        if capacity.harness in by_harness:
            raise RoutingPolicyError(
                "invalid_capacity", "capacity contains a duplicate harness"
            )
        by_harness[capacity.harness] = capacity
    return by_harness
