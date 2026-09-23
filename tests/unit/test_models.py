from dataclasses import FrozenInstanceError

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


def test_closed_routing_values_match_the_launch_contract() -> None:
    assert {item.value for item in Harness} == {
        "claude",
        "codex",
        "opencode",
        "pi",
    }
    assert {item.value for item in ClaudeModel} == {"haiku", "sonnet", "opus"}
    assert {item.value for item in CodexModel} == {"luna", "terra", "sol"}
    assert {item.value for item in OpenCodeModel} == {"deepseek", "glm", "kimi"}
    assert {item.value for item in PiModel} == {"deepseek", "glm", "kimi"}
    assert {item.value for item in Effort} == {
        "low",
        "medium",
        "high",
        "xhigh",
        "max",
    }
    assert {item.value for item in CapacityState} == {
        "surplus",
        "on_pace",
        "conserve",
        "unknown",
        "critical",
        "exhausted",
    }


def test_provider_capacity_is_immutable() -> None:
    capacity = ProviderCapacity(
        harness=Harness.CODEX,
        state=CapacityState.ON_PACE,
    )

    with pytest.raises(FrozenInstanceError):
        capacity.state = CapacityState.CONSERVE  # type: ignore[misc]


def harness_launch(**changes: object) -> HarnessLaunch:
    kwargs: dict[str, object] = {
        "provider": "opencode-go",
        "models": {"deepseek": "d", "glm": "g", "kimi": "k"},
    }
    kwargs.update(changes)
    return HarnessLaunch(**kwargs)  # type: ignore[arg-type]


def test_harness_launch_is_immutable() -> None:
    launch = harness_launch()

    with pytest.raises(TypeError):
        launch.models["deepseek"] = "other"  # type: ignore[index]


def test_harness_launch_requires_every_tier() -> None:
    with pytest.raises(ValueError, match="models must name every launch tier"):
        harness_launch(models={"deepseek": "d", "glm": "g"})


def test_harness_launch_requires_a_non_empty_provider() -> None:
    with pytest.raises(TypeError, match="provider must be a non-empty string"):
        harness_launch(provider="")


def test_harness_launch_requires_non_empty_models() -> None:
    with pytest.raises(
        TypeError, match="every launch model must be a non-empty string"
    ):
        harness_launch(models={"deepseek": "", "glm": "g", "kimi": "k"})


def test_unknown_capacity_requires_a_positive_penalty() -> None:
    with pytest.raises(ValueError, match="unknown capacity requires a penalty"):
        ProviderCapacity(
            harness=Harness.CODEX,
            state=CapacityState.UNKNOWN,
        )


def test_known_capacity_rejects_a_penalty() -> None:
    with pytest.raises(
        ValueError, match="only unknown or critical capacity may have a penalty"
    ):
        ProviderCapacity(
            harness=Harness.CLAUDE,
            state=CapacityState.SURPLUS,
            penalty=1,
        )


@pytest.mark.parametrize("penalty", [True, 1.5])
def test_capacity_penalty_must_be_an_exact_integer(penalty: object) -> None:
    with pytest.raises(TypeError, match="penalty must be an integer"):
        ProviderCapacity(
            harness=Harness.CODEX,
            state=CapacityState.UNKNOWN,
            penalty=penalty,  # type: ignore[arg-type]
        )


def decision_kwargs(**changes: object) -> dict[str, object]:
    kwargs: dict[str, object] = {
        "harness": Harness.CLAUDE,
        "claude_model": ClaudeModel.SONNET,
        "codex_model": CodexModel.TERRA,
        "opencode_model": OpenCodeModel.DEEPSEEK,
        "pi_model": PiModel.DEEPSEEK,
        "effort": Effort.HIGH,
    }
    kwargs.update(changes)
    return kwargs


@pytest.mark.parametrize(
    "field, value, message",
    [
        ("claude_model", CodexModel.SOL, "claude_model must be a ClaudeModel"),
        ("codex_model", ClaudeModel.SONNET, "codex_model must be a CodexModel"),
        (
            "opencode_model",
            PiModel.GLM,
            "opencode_model must be an OpenCodeModel",
        ),
        ("pi_model", OpenCodeModel.GLM, "pi_model must be a PiModel"),
    ],
)
def test_routing_decision_requires_each_branch_model_type(
    field: str, value: object, message: str
) -> None:
    with pytest.raises(TypeError, match=message):
        RoutingDecision(**decision_kwargs(**{field: value}))  # type: ignore[arg-type]
