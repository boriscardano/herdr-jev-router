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


@pytest.mark.parametrize(
    "state",
    [CapacityState.SURPLUS, CapacityState.UNKNOWN, CapacityState.CRITICAL],
)
def test_provider_capacity_carries_no_penalty(state: CapacityState) -> None:
    capacity = ProviderCapacity(harness=Harness.CODEX, state=state)

    assert not hasattr(capacity, "penalty")


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


def test_quota_detail_is_immutable_and_serializes_four_numbers() -> None:
    from herdr_jev_router.models import QuotaDetail

    detail = QuotaDetail(85, 2, 35, 100)

    with pytest.raises(FrozenInstanceError):
        detail.weekly_remaining_percent = 1  # type: ignore[misc]

    assert detail.to_dict() == {
        "five_hour_remaining_percent": 85,
        "five_hour_resets_in_hours": 2,
        "weekly_remaining_percent": 35,
        "weekly_resets_in_hours": 100,
    }


@pytest.mark.parametrize(
    "field, value",
    [
        ("five_hour_remaining_percent", -0.1),
        ("five_hour_remaining_percent", 100.1),
        ("weekly_remaining_percent", 101),
        ("five_hour_resets_in_hours", -1),
        ("weekly_resets_in_hours", -0.5),
    ],
)
def test_quota_detail_rejects_out_of_range_numbers(field: str, value: float) -> None:
    from herdr_jev_router.models import QuotaDetail

    values: dict[str, object] = {
        "five_hour_remaining_percent": 85,
        "five_hour_resets_in_hours": 2,
        "weekly_remaining_percent": 35,
        "weekly_resets_in_hours": 100,
    }
    values[field] = value

    with pytest.raises(ValueError):
        QuotaDetail(**values)  # type: ignore[arg-type]


def test_quota_detail_requires_a_known_window_to_carry_both_numbers() -> None:
    from herdr_jev_router.models import QuotaDetail

    with pytest.raises(ValueError, match="known window"):
        QuotaDetail(five_hour_remaining_percent=85)

    with pytest.raises(ValueError, match="known window"):
        QuotaDetail(weekly_resets_in_hours=100)


def test_quota_detail_rejects_non_numeric_or_non_finite_numbers() -> None:
    from herdr_jev_router.models import QuotaDetail

    with pytest.raises(TypeError, match="must be numeric"):
        QuotaDetail(five_hour_remaining_percent="85", five_hour_resets_in_hours=2)

    with pytest.raises(ValueError, match="must be finite"):
        QuotaDetail(
            five_hour_remaining_percent=float("nan"),
            five_hour_resets_in_hours=2,
        )


def test_provider_capacity_rejects_a_non_quota_detail_and_bad_reason() -> None:
    with pytest.raises(TypeError, match="quota must be a QuotaDetail"):
        ProviderCapacity(
            harness=Harness.CODEX,
            state=CapacityState.ON_PACE,
            quota=object(),  # type: ignore[arg-type]
        )

    with pytest.raises(TypeError, match="reason must be a non-empty string"):
        ProviderCapacity(
            harness=Harness.CODEX,
            state=CapacityState.CRITICAL,
            reason="",
        )


def test_reason_is_only_allowed_for_critical_capacity() -> None:
    with pytest.raises(ValueError, match="reason is only for critical capacity"):
        ProviderCapacity(
            harness=Harness.CODEX,
            state=CapacityState.ON_PACE,
            reason="not critical",
        )

    assert (
        ProviderCapacity(
            harness=Harness.CODEX,
            state=CapacityState.CRITICAL,
            reason="codex weekly 6% left, resets in 38h",
        ).reason
        == "codex weekly 6% left, resets in 38h"
    )


def test_provider_capacity_age_hours_is_optional_and_non_negative() -> None:
    assert ProviderCapacity(Harness.CODEX, CapacityState.ON_PACE).age_hours is None
    assert (
        ProviderCapacity(Harness.CODEX, CapacityState.ON_PACE, age_hours=1.5).age_hours
        == 1.5
    )

    with pytest.raises(ValueError, match="age_hours must not be negative"):
        ProviderCapacity(Harness.CODEX, CapacityState.ON_PACE, age_hours=-0.1)

    with pytest.raises(TypeError, match="age_hours must be numeric"):
        ProviderCapacity(
            Harness.CODEX,
            CapacityState.ON_PACE,
            age_hours="1",  # type: ignore[arg-type]
        )
