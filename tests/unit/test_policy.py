import pytest

from herdr_jev_router.models import (
    CapacityState,
    ClaudeModel,
    CodexModel,
    Effort,
    Harness,
    HarnessLaunch,
    OpenCodeModel,
    PiModel,
    ProviderCapacity,
    RoutingDecision,
)
from herdr_jev_router.policy import (
    RoutingPolicyError,
    launch_profile,
    validate_decision,
)


def choice(value: str) -> dict[str, str]:
    return {"type": "choice", "value": value}


def valid_answers(**changes: object) -> dict[str, object]:
    answers: dict[str, object] = {
        "harness": choice("codex"),
        "claude_model": choice("sonnet"),
        "codex_model": choice("terra"),
        "opencode_model": choice("deepseek"),
        "pi_model": choice("deepseek"),
        "effort": choice("high"),
    }
    answers.update(changes)
    return answers


def all_capacities() -> tuple[ProviderCapacity, ...]:
    return (
        ProviderCapacity(Harness.CLAUDE, CapacityState.ON_PACE),
        ProviderCapacity(Harness.CODEX, CapacityState.SURPLUS),
        ProviderCapacity(Harness.OPENCODE, CapacityState.ON_PACE),
        ProviderCapacity(Harness.PI, CapacityState.ON_PACE),
    )


LAUNCH_CONFIGURATION = {
    Harness.OPENCODE: HarnessLaunch(
        provider="opencode-go",
        models={
            "deepseek": "deepseek-v4.1-flash",
            "glm": "glm-5.3",
            "kimi": "kimi-k3",
        },
    ),
    Harness.PI: HarnessLaunch(
        provider="opencode-go",
        models={
            "deepseek": "deepseek-v4.1-flash",
            "glm": "glm-5.3",
            "kimi": "kimi-k3",
        },
    ),
}


def routing_decision(
    harness: Harness = Harness.CODEX,
    claude_model: ClaudeModel = ClaudeModel.SONNET,
    codex_model: CodexModel = CodexModel.TERRA,
    opencode_model: OpenCodeModel = OpenCodeModel.DEEPSEEK,
    pi_model: PiModel = PiModel.DEEPSEEK,
    effort: Effort = Effort.MEDIUM,
) -> RoutingDecision:
    return RoutingDecision(
        harness, claude_model, codex_model, opencode_model, pi_model, effort
    )


@pytest.mark.parametrize(
    "answers, code",
    [
        ({}, "invalid_answer_keys"),
        (valid_answers(extra=choice("value")), "invalid_answer_keys"),
        (valid_answers(harness={"type": "text", "value": "codex"}), "invalid_answer"),
        (
            valid_answers(harness={"type": "choice", "value": "codex", "extra": 1}),
            "invalid_answer",
        ),
        (valid_answers(harness=choice("other")), "invalid_answer"),
        (valid_answers(effort=choice("ultra")), "invalid_answer"),
    ],
)
def test_decision_validation_rejects_malformed_or_unknown_answers(
    answers: dict[str, object], code: str
) -> None:
    with pytest.raises(RoutingPolicyError) as error:
        validate_decision(answers, all_capacities())

    assert error.value.code == code


def test_decision_validation_rejects_an_ineligible_harness() -> None:
    capacities = (ProviderCapacity(Harness.CODEX, CapacityState.ON_PACE),)

    with pytest.raises(RoutingPolicyError) as error:
        validate_decision(valid_answers(harness=choice("claude")), capacities)

    assert error.value.code == "ineligible_harness"


@pytest.mark.parametrize(
    "capacities",
    [
        (
            ProviderCapacity(Harness.CODEX, CapacityState.ON_PACE),
            ProviderCapacity(Harness.CODEX, CapacityState.EXHAUSTED),
        ),
        (
            ProviderCapacity(Harness.CODEX, CapacityState.EXHAUSTED),
            ProviderCapacity(Harness.CODEX, CapacityState.ON_PACE),
        ),
    ],
)
def test_decision_validation_rejects_duplicate_capacity_records(
    capacities: tuple[ProviderCapacity, ...],
) -> None:
    with pytest.raises(RoutingPolicyError) as error:
        validate_decision(valid_answers(), capacities)

    assert error.value.code == "invalid_capacity"


def test_validated_decision_retains_both_models_and_selects_one_for_launch() -> None:
    decision = validate_decision(
        valid_answers(
            harness=choice("claude"),
            claude_model=choice("opus"),
            codex_model=choice("luna"),
            effort=choice("max"),
        ),
        all_capacities(),
    )

    assert decision == RoutingDecision(
        harness=Harness.CLAUDE,
        claude_model=ClaudeModel.OPUS,
        codex_model=CodexModel.LUNA,
        opencode_model=OpenCodeModel.DEEPSEEK,
        pi_model=PiModel.DEEPSEEK,
        effort=Effort.MAX,
    )
    assert decision.model is ClaudeModel.OPUS


@pytest.mark.parametrize(
    "decision, expected_kind, expected_args",
    [
        (
            routing_decision(Harness.CLAUDE, ClaudeModel.HAIKU, effort=Effort.LOW),
            Harness.CLAUDE,
            ("--model", "haiku", "--effort", "low"),
        ),
        (
            routing_decision(Harness.CLAUDE),
            Harness.CLAUDE,
            ("--model", "sonnet", "--effort", "medium"),
        ),
        (
            routing_decision(Harness.CLAUDE, ClaudeModel.OPUS, effort=Effort.MAX),
            Harness.CLAUDE,
            ("--model", "opus", "--effort", "max"),
        ),
        (
            routing_decision(codex_model=CodexModel.LUNA, effort=Effort.LOW),
            Harness.CODEX,
            ("-m", "gpt-5.6-luna", "-c", 'model_reasoning_effort="low"'),
        ),
        (
            routing_decision(effort=Effort.XHIGH),
            Harness.CODEX,
            ("-m", "gpt-5.6-terra", "-c", 'model_reasoning_effort="xhigh"'),
        ),
        (
            routing_decision(codex_model=CodexModel.SOL, effort=Effort.HIGH),
            Harness.CODEX,
            ("-m", "gpt-5.6-sol", "-c", 'model_reasoning_effort="high"'),
        ),
        (
            routing_decision(
                Harness.OPENCODE,
                opencode_model=OpenCodeModel.DEEPSEEK,
                effort=Effort.LOW,
            ),
            Harness.OPENCODE,
            ("--model", "opencode-go/deepseek-v4.1-flash"),
        ),
        (
            routing_decision(
                Harness.OPENCODE,
                opencode_model=OpenCodeModel.KIMI,
                effort=Effort.HIGH,
            ),
            Harness.OPENCODE,
            ("--model", "opencode-go/kimi-k3"),
        ),
        (
            routing_decision(
                Harness.OPENCODE,
                opencode_model=OpenCodeModel.GLM,
                effort=Effort.MEDIUM,
            ),
            Harness.OPENCODE,
            ("--model", "opencode-go/glm-5.3"),
        ),
        (
            routing_decision(
                Harness.PI,
                pi_model=PiModel.GLM,
                effort=Effort.XHIGH,
            ),
            Harness.PI,
            (
                "--provider",
                "opencode-go",
                "--model",
                "glm-5.3",
                "--thinking",
                "xhigh",
            ),
        ),
        (
            routing_decision(
                Harness.PI,
                pi_model=PiModel.DEEPSEEK,
                effort=Effort.LOW,
            ),
            Harness.PI,
            (
                "--provider",
                "opencode-go",
                "--model",
                "deepseek-v4.1-flash",
                "--thinking",
                "low",
            ),
        ),
        (
            routing_decision(
                Harness.PI,
                pi_model=PiModel.KIMI,
                effort=Effort.MAX,
            ),
            Harness.PI,
            (
                "--provider",
                "opencode-go",
                "--model",
                "kimi-k3",
                "--thinking",
                "max",
            ),
        ),
    ],
)
def test_launch_mapping_is_deterministic(
    decision: RoutingDecision,
    expected_kind: Harness,
    expected_args: tuple[str, ...],
) -> None:
    profile = launch_profile(decision, harness_launch=LAUNCH_CONFIGURATION)

    assert profile.kind == expected_kind
    assert profile.args == expected_args


def test_opt_in_harness_without_configuration_fails_closed() -> None:
    with pytest.raises(RoutingPolicyError) as error:
        launch_profile(routing_decision(Harness.PI))

    assert error.value.code == "harness_not_configured"
