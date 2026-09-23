import asyncio
import json
import os
from pathlib import Path

import pytest
from httpx2 import MockTransport, Response

from herdr_jev_router.jev import JevError, JevRoutingResult, route_with_jev
from herdr_jev_router.models import (
    CapacityState,
    ClaudeModel,
    CodexModel,
    Effort,
    Harness,
    OpenCodeModel,
    PiModel,
    ProviderCapacity,
    QuotaDetail,
    RoutingDecision,
)
from herdr_jev_router.policy import (
    CRITICAL_CAPACITY_PENALTY,
    apply_critical_fallback,
    route_eligible,
)
from herdr_jev_router.router import RouterError, recommend


def jev_result(
    *,
    harness: str = "codex",
    harness_probabilities: dict[str, float] | None = None,
) -> JevRoutingResult:
    return JevRoutingResult(
        model="jev-test-1",
        answers={
            "harness": {"type": "choice", "value": harness},
            "claude_model": {"type": "choice", "value": "sonnet"},
            "codex_model": {"type": "choice", "value": "terra"},
            "opencode_model": {"type": "choice", "value": "deepseek"},
            "pi_model": {"type": "choice", "value": "deepseek"},
            "effort": {"type": "choice", "value": "high"},
        },
        probabilities={
            "harness": harness_probabilities
            or {"claude": 0.2, "codex": 0.5, "opencode": 0.2, "pi": 0.1},
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


def matrix_jev_result(
    *,
    harness: Harness = Harness.CODEX,
    claude_model: ClaudeModel = ClaudeModel.SONNET,
    codex_model: CodexModel = CodexModel.TERRA,
    opencode_model: OpenCodeModel = OpenCodeModel.DEEPSEEK,
    pi_model: PiModel = PiModel.DEEPSEEK,
    effort: Effort = Effort.HIGH,
    eligible_harnesses: tuple[Harness, ...] = (Harness.CLAUDE, Harness.CODEX),
) -> JevRoutingResult:
    harness_probabilities = {
        eligible: (1.0 if eligible is harness else 0.0)
        for eligible in eligible_harnesses
    }
    return JevRoutingResult(
        model="jev-test-1",
        answers={
            "harness": {"type": "choice", "value": harness.value},
            "claude_model": {"type": "choice", "value": claude_model.value},
            "codex_model": {"type": "choice", "value": codex_model.value},
            "opencode_model": {"type": "choice", "value": opencode_model.value},
            "pi_model": {"type": "choice", "value": pi_model.value},
            "effort": {"type": "choice", "value": effort.value},
        },
        probabilities={
            "harness": harness_probabilities,
            "claude_model": {
                model.value: (1.0 if model is claude_model else 0.0)
                for model in ClaudeModel
            },
            "codex_model": {
                model.value: (1.0 if model is codex_model else 0.0)
                for model in CodexModel
            },
            "opencode_model": {
                model.value: (1.0 if model is opencode_model else 0.0)
                for model in OpenCodeModel
            },
            "pi_model": {
                model.value: (1.0 if model is pi_model else 0.0) for model in PiModel
            },
            "effort": {
                selected.value: (1.0 if selected is effort else 0.0)
                for selected in Effort
            },
        },
        confidences={
            answer_id: 1.0
            for answer_id in (
                "harness",
                "claude_model",
                "codex_model",
                "opencode_model",
                "pi_model",
                "effort",
            )
        },
        usage={"input_tokens": 10, "output_tokens": 5},
    )


def capacities(*, claude: CapacityState = CapacityState.ON_PACE):
    return (
        ProviderCapacity(Harness.CLAUDE, claude),
        ProviderCapacity(Harness.CODEX, CapacityState.SURPLUS),
        ProviderCapacity(Harness.OPENCODE, CapacityState.ON_PACE),
        ProviderCapacity(Harness.PI, CapacityState.ON_PACE),
    )


def run_recommend(
    tmp_path: Path,
    jev_callable,
    **changes: object,
):
    kwargs = {
        "request_id": "rec-request",
        "task": "private task text must not be audited",
        "role": "reviewer",
        "constraints": {
            "read_only": True,
            "worktree": False,
            "network_required": False,
        },
        "capacities": capacities(),
        "audit_path": tmp_path / "routing.jsonl",
        "jev_callable": jev_callable,
        "clock": lambda: 1234.5,
    }
    kwargs.update(changes)
    return asyncio.run(recommend(**kwargs))  # type: ignore[arg-type]


def audit_record(tmp_path: Path) -> dict[str, object]:
    lines = (tmp_path / "routing.jsonl").read_text().splitlines()
    assert len(lines) == 1
    return json.loads(lines[0])


def test_recommend_calls_jev_once_and_persists_audit_before_returning(
    tmp_path: Path,
) -> None:
    calls: list[dict[str, object]] = []

    async def fake_jev(**kwargs: object) -> JevRoutingResult:
        calls.append(kwargs)
        return jev_result()

    result = run_recommend(tmp_path, fake_jev)

    assert len(calls) == 1
    assert calls[0] == {
        "task": "private task text must not be audited",
        "role": "reviewer",
        "constraints": {
            "read_only": True,
            "worktree": False,
            "network_required": False,
        },
        "capacities": capacities(),
    }
    assert result.recommended == RoutingDecision(
        Harness.CODEX,
        ClaudeModel.SONNET,
        CodexModel.TERRA,
        OpenCodeModel.DEEPSEEK,
        PiModel.DEEPSEEK,
        Effort.HIGH,
    )
    assert result.capacities == capacities()

    record = audit_record(tmp_path)
    assert record == {
        "schema_version": 2,
        "phase": "recommendation",
        "request_id": "rec-request",
        "timestamp": 1234.5,
        "request": {
            "role": "reviewer",
            "constraints": {
                "read_only": True,
                "worktree": False,
                "network_required": False,
            },
        },
        "capacity": {
            "claude": _audited_capacity(
                ProviderCapacity(Harness.CLAUDE, CapacityState.ON_PACE)
            ),
            "codex": _audited_capacity(
                ProviderCapacity(Harness.CODEX, CapacityState.SURPLUS)
            ),
            "opencode": _audited_capacity(
                ProviderCapacity(Harness.OPENCODE, CapacityState.ON_PACE)
            ),
            "pi": _audited_capacity(
                ProviderCapacity(Harness.PI, CapacityState.ON_PACE)
            ),
        },
        "jev": {
            "model": "jev-test-1",
            "answers": {
                answer_id: {
                    "label": jev_result().answers[answer_id]["value"],
                    "probabilities": jev_result().probabilities[answer_id],
                    "confidence": jev_result().confidences[answer_id],
                }
                for answer_id in (
                    "harness",
                    "claude_model",
                    "codex_model",
                    "opencode_model",
                    "pi_model",
                    "effort",
                )
            },
        },
        "recommended_decision": {
            "harness": "codex",
            "model": "terra",
            "effort": "high",
        },
        "error_category": None,
    }
    assert "effective_decision" not in record
    serialized = json.dumps(record)
    assert "private task text" not in serialized
    assert "cwd" not in serialized
    assert "input_tokens" not in serialized


@pytest.mark.parametrize("claude_state", tuple(CapacityState))
@pytest.mark.parametrize("codex_state", tuple(CapacityState))
def test_recommend_matrix_accepts_and_audits_every_capacity_state(
    tmp_path: Path,
    claude_state: CapacityState,
    codex_state: CapacityState,
) -> None:
    capacities_snapshot = (
        ProviderCapacity(
            Harness.CLAUDE,
            claude_state,
            1 if claude_state is CapacityState.UNKNOWN else 0,
        ),
        ProviderCapacity(
            Harness.CODEX, codex_state, 1 if codex_state is CapacityState.UNKNOWN else 0
        ),
    )
    eligible = tuple(
        capacity.harness for capacity in route_eligible(capacities_snapshot)
    )
    if not eligible:
        called = False

        async def no_provider_jev(**kwargs: object) -> JevRoutingResult:
            nonlocal called
            called = True
            return matrix_jev_result(eligible_harnesses=())

        with pytest.raises(RouterError) as error:
            run_recommend(
                tmp_path,
                no_provider_jev,
                capacities=capacities_snapshot,
            )
        assert error.value.code == "no_eligible_provider"
        assert called is False
        assert audit_record(tmp_path)["error_category"] == "capacity"
        return
    selected = eligible[0]

    async def fake_jev(**kwargs: object) -> JevRoutingResult:
        return matrix_jev_result(
            harness=selected,
            eligible_harnesses=eligible,
        )

    result = run_recommend(tmp_path, fake_jev, capacities=capacities_snapshot)

    effective = {
        capacity.harness: capacity
        for capacity in apply_critical_fallback(capacities_snapshot)
    }
    assert result.recommended.harness is selected
    assert audit_record(tmp_path)["capacity"] == {
        "claude": _audited_capacity(effective[Harness.CLAUDE]),
        "codex": _audited_capacity(effective[Harness.CODEX]),
    }


def _audited_capacity(capacity: ProviderCapacity) -> dict[str, object]:
    return {
        "state": capacity.state.value,
        "penalty": capacity.penalty,
        "age_hours": capacity.age_hours,
        "five_hour_remaining_percent": None,
        "five_hour_resets_in_hours": None,
        "weekly_remaining_percent": None,
        "weekly_resets_in_hours": None,
        "reason": None,
    }


@pytest.mark.parametrize(
    "jev_exception",
    tuple(
        JevError(category, f"secret {category} detail")
        for category in (
            "authentication",
            "invalid_request",
            "rate_limit",
            "service_unavailable",
            "invalid_response",
            "timeout",
            "connection",
            "api_error",
            "sdk_error",
        )
    ),
)
def test_recommend_audits_every_jev_error_category_without_leaking_details(
    tmp_path: Path, jev_exception: JevError
) -> None:
    async def failing_jev(**kwargs: object) -> JevRoutingResult:
        raise jev_exception

    with pytest.raises(RouterError) as error:
        run_recommend(tmp_path, failing_jev)

    assert error.value.code == "jev_failed"
    record = audit_record(tmp_path)
    assert record["error_category"] == "jev"
    # The stable Jev code explains the failure; the raw message must not leak.
    assert record["jev_error_code"] == jev_exception.code
    assert jev_exception.args[0] not in json.dumps(record)


@pytest.mark.parametrize(
    "mutate",
    (
        lambda result: result.answers.pop("effort"),
        lambda result: result.probabilities["codex_model"].update({"other": 1.0}),
        lambda result: result.probabilities["effort"].update({"low": 0.5}),
        lambda result: result.answers["harness"].update({"type": "text"}),
        lambda result: result.answers["codex_model"].update({"value": "other"}),
        lambda result: result.confidences.update({"effort": float("nan")}),
    ),
)
def test_recommend_rejects_each_malformed_answer_shape_and_audits_validation(
    tmp_path: Path, mutate
) -> None:
    result = jev_result()
    mutate(result)

    async def malformed_jev(**kwargs: object) -> JevRoutingResult:
        return result

    with pytest.raises(RouterError) as error:
        run_recommend(tmp_path, malformed_jev)

    assert error.value.code == "validation_failed"
    assert audit_record(tmp_path)["error_category"] == "validation"


@pytest.mark.parametrize("failed_call", ["write", "fsync"])
def test_audit_io_failure_denies_recommend_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failed_call: str,
) -> None:
    async def fake_jev(**kwargs: object) -> JevRoutingResult:
        return jev_result()

    def fail(*args: object) -> None:
        raise OSError("private operating-system detail")

    monkeypatch.setattr(os, failed_call, fail)

    with pytest.raises(RouterError) as error:
        run_recommend(tmp_path, fake_jev)

    assert error.value.code == "audit_failed"
    assert "private operating-system detail" not in str(error.value)


def test_malformed_jev_result_fails_closed_with_a_stable_audit_category(
    tmp_path: Path,
) -> None:
    async def malformed_jev(**kwargs: object) -> object:
        return object()

    with pytest.raises(RouterError) as error:
        run_recommend(tmp_path, malformed_jev)

    assert error.value.code == "validation_failed"
    record = audit_record(tmp_path)
    assert record["jev"] is None
    assert record["error_category"] == "validation"


def test_malformed_jev_probability_map_fails_closed(tmp_path: Path) -> None:
    result = jev_result()
    del result.probabilities["harness"]["claude"]

    async def malformed_jev(**kwargs: object) -> JevRoutingResult:
        return result

    with pytest.raises(RouterError) as error:
        run_recommend(tmp_path, malformed_jev)

    assert error.value.code == "validation_failed"
    record = audit_record(tmp_path)
    assert record["jev"] is None
    assert record["error_category"] == "validation"


def test_jev_failure_is_audited_without_raw_exception_data(tmp_path: Path) -> None:
    async def failing_jev(**kwargs: object) -> JevRoutingResult:
        raise JevError("connection", "secret provider body")

    with pytest.raises(RouterError) as error:
        run_recommend(tmp_path, failing_jev)

    assert error.value.code == "jev_failed"
    record = audit_record(tmp_path)
    serialized = json.dumps(record)
    assert '"error_category": "jev"' in serialized
    assert record["jev_error_code"] == "connection"
    assert "secret provider body" not in serialized


def test_recommend_accepts_two_decimal_rounded_jev_probabilities(
    tmp_path: Path,
) -> None:
    body = {
        "model": "jev-test-1",
        "answers": {
            "harness": {
                "type": "choice",
                "choice": "codex",
                "probabilities": {
                    "claude": 0.2,
                    "codex": 0.5,
                    "opencode": 0.2,
                    "pi": 0.1,
                },
                "confidence": 0.5,
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
                "probabilities": {"luna": 0.33, "terra": 0.33, "sol": 0.33},
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
        "usage": {"input_tokens": 123, "output_tokens": 45},
    }

    def handler(request: object) -> Response:
        return Response(
            200,
            headers={"content-type": "application/json"},
            content=json.dumps(body).encode(),
        )

    async def jev(**kwargs: object) -> JevRoutingResult:
        return await route_with_jev(
            api_key="dummy-test-key",
            transport=MockTransport(handler),
            **kwargs,  # type: ignore[arg-type]
        )

    result = run_recommend(tmp_path, jev)

    assert result.recommended.codex_model is CodexModel.TERRA
    record = audit_record(tmp_path)
    assert record["error_category"] is None
    jev_audit = record["jev"]
    assert isinstance(jev_audit, dict)
    assert jev_audit["answers"]["codex_model"] == {
        "label": "terra",
        "probabilities": {"luna": 0.33, "terra": 0.33, "sol": 0.33},
        "confidence": 0.5,
    }


def test_recommend_fails_without_calling_jev_when_all_capacity_is_exhausted(
    tmp_path: Path,
) -> None:
    called = False

    async def fake_jev(**kwargs: object) -> JevRoutingResult:
        nonlocal called
        called = True
        return matrix_jev_result(eligible_harnesses=())

    with pytest.raises(RouterError) as error:
        run_recommend(
            tmp_path,
            fake_jev,
            capacities=(
                ProviderCapacity(Harness.CLAUDE, CapacityState.EXHAUSTED),
                ProviderCapacity(Harness.CODEX, CapacityState.EXHAUSTED),
            ),
        )

    assert error.value.code == "no_eligible_provider"
    assert called is False
    record = audit_record(tmp_path)
    assert record["phase"] == "recommendation"
    assert record["request_id"] == "rec-request"
    assert record["error_category"] == "capacity"


def test_audit_records_the_same_quota_numbers_sent_to_jev(tmp_path: Path) -> None:

    capacities_snapshot = (
        ProviderCapacity(
            Harness.CLAUDE,
            CapacityState.ON_PACE,
            0,
            QuotaDetail(85, 2, 35, 100),
        ),
        ProviderCapacity(Harness.CODEX, CapacityState.SURPLUS),
        ProviderCapacity(Harness.OPENCODE, CapacityState.ON_PACE),
        ProviderCapacity(Harness.PI, CapacityState.ON_PACE),
    )

    async def fake_jev(**kwargs: object) -> JevRoutingResult:
        return jev_result()

    run_recommend(tmp_path, fake_jev, capacities=capacities_snapshot)

    capacity = audit_record(tmp_path)["capacity"]
    assert capacity["claude"] == {
        "state": "on_pace",
        "penalty": 0,
        "age_hours": None,
        "five_hour_remaining_percent": 85,
        "five_hour_resets_in_hours": 2,
        "weekly_remaining_percent": 35,
        "weekly_resets_in_hours": 100,
        "reason": None,
    }
    assert capacity["codex"] == {
        "state": "surplus",
        "penalty": 0,
        "age_hours": None,
        "five_hour_remaining_percent": None,
        "five_hour_resets_in_hours": None,
        "weekly_remaining_percent": None,
        "weekly_resets_in_hours": None,
        "reason": None,
    }


def test_audit_explains_a_removed_critical_provider(tmp_path: Path) -> None:

    capacities_snapshot = (
        ProviderCapacity(Harness.CLAUDE, CapacityState.ON_PACE),
        ProviderCapacity(
            Harness.CODEX,
            CapacityState.CRITICAL,
            0,
            QuotaDetail(None, None, 6, 38),
            "codex weekly 6% left, resets in 38h",
        ),
    )

    async def fake_jev(**kwargs: object) -> JevRoutingResult:
        return matrix_jev_result(
            harness=Harness.CLAUDE, eligible_harnesses=(Harness.CLAUDE,)
        )

    result = run_recommend(tmp_path, fake_jev, capacities=capacities_snapshot)

    assert result.recommended.harness is Harness.CLAUDE
    assert audit_record(tmp_path)["capacity"]["codex"] == {
        "state": "critical",
        "penalty": 0,
        "age_hours": None,
        "five_hour_remaining_percent": None,
        "five_hour_resets_in_hours": None,
        "weekly_remaining_percent": 6,
        "weekly_resets_in_hours": 38,
        "reason": "codex weekly 6% left, resets in 38h",
    }


def test_recommend_rejects_jev_selecting_a_removed_critical_provider(
    tmp_path: Path,
) -> None:
    capacities_snapshot = (
        ProviderCapacity(Harness.CLAUDE, CapacityState.ON_PACE),
        ProviderCapacity(Harness.CODEX, CapacityState.CRITICAL),
    )

    async def fake_jev(**kwargs: object) -> JevRoutingResult:
        return matrix_jev_result(
            harness=Harness.CODEX,
            eligible_harnesses=(Harness.CLAUDE, Harness.CODEX),
        )

    with pytest.raises(RouterError) as error:
        run_recommend(tmp_path, fake_jev, capacities=capacities_snapshot)

    assert error.value.code == "validation_failed"
    assert audit_record(tmp_path)["error_category"] == "validation"


def test_recommend_applies_the_critical_fallback_penalty(tmp_path: Path) -> None:
    capacities_snapshot = (
        ProviderCapacity(
            Harness.CODEX,
            CapacityState.CRITICAL,
            0,
            QuotaDetail(None, None, 6, 38),
            "codex weekly 6% left, resets in 38h",
        ),
    )

    async def fake_jev(**kwargs: object) -> JevRoutingResult:
        return matrix_jev_result(
            harness=Harness.CODEX, eligible_harnesses=(Harness.CODEX,)
        )

    result = run_recommend(tmp_path, fake_jev, capacities=capacities_snapshot)

    assert result.capacities[0].penalty == CRITICAL_CAPACITY_PENALTY
    assert (
        audit_record(tmp_path)["capacity"]["codex"]["penalty"]
        == CRITICAL_CAPACITY_PENALTY
    )
