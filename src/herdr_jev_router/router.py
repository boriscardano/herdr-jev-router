"""Compose one fail-closed advisory routing decision and audit it."""

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from math import fsum, isclose, isfinite
from pathlib import Path
from time import time
from typing import TypeGuard

from herdr_jev_router.audit import AuditError, append_audit_record
from herdr_jev_router.jev import (
    JevError,
    JevRoutingResult,
    probability_sum_tolerance,
    route_with_jev,
)
from herdr_jev_router.models import (
    ProviderCapacity,
    RoutingDecision,
)
from herdr_jev_router.policy import RoutingPolicyError, validate_decision

_ANSWER_NAMES = (
    "harness",
    "claude_model",
    "codex_model",
    "opencode_model",
    "pi_model",
    "effort",
)
_CONSTRAINT_NAMES = ("read_only", "worktree", "network_required")
_EXPECTED_LABELS = {
    "claude_model": frozenset({"haiku", "sonnet", "opus"}),
    "codex_model": frozenset({"luna", "terra", "sol"}),
    "opencode_model": frozenset({"deepseek", "glm", "kimi"}),
    "pi_model": frozenset({"deepseek", "glm", "kimi"}),
    "effort": frozenset({"low", "medium", "high", "xhigh", "max"}),
}

JevCallable = Callable[..., Awaitable[object]]
Clock = Callable[[], int | float]


class RouterError(RuntimeError):
    """Report a stable fail-closed orchestration failure."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class RecommendationResult:
    """Return the audited recommendation and its capacity snapshot."""

    recommended: RoutingDecision
    capacities: tuple[ProviderCapacity, ...]


async def recommend(
    *,
    request_id: str,
    task: str,
    role: str,
    constraints: Mapping[str, object],
    capacities: Sequence[ProviderCapacity],
    audit_path: Path,
    jev_callable: JevCallable = route_with_jev,
    clock: Clock = time,
) -> RecommendationResult:
    """Call Jev once and durably return the validated recommendation."""

    constraints_snapshot = dict(constraints)
    # The caller already removed harnesses that cannot be launched. Jev decides
    # among everything else, whatever the quota says.
    capacities_snapshot = tuple(capacities)
    record = _v2_record(
        phase="recommendation",
        request_id=request_id,
        timestamp=clock(),
        role=role,
        constraints=constraints_snapshot,
        capacities=capacities_snapshot,
    )
    if not capacities_snapshot:
        record["error_category"] = "capacity"
        _persist(audit_path, record)
        raise RouterError("no_eligible_provider", "no launchable provider")
    try:
        jev_result = await jev_callable(
            task=task,
            role=role,
            constraints=constraints_snapshot,
            capacities=capacities_snapshot,
        )
    except Exception as error:
        record["error_category"] = "jev"
        # Audit only Jev's stable code, never its message or response body, so
        # a recurring failure is diagnosable from the record alone.
        record["jev_error_code"] = error.code if isinstance(error, JevError) else None
        _persist(audit_path, record)
        raise RouterError("jev_failed", "Jev routing failed") from None

    if not isinstance(jev_result, JevRoutingResult):
        record["error_category"] = "validation"
        _persist(audit_path, record)
        raise RouterError("validation_failed", "Jev result validation failed")

    try:
        record["jev"] = _jev_audit(jev_result, capacities_snapshot)
        recommended = validate_decision(jev_result.answers, capacities_snapshot)
    except (KeyError, RoutingPolicyError, TypeError, ValueError):
        record["error_category"] = "validation"
        _persist(audit_path, record)
        raise RouterError("validation_failed", "Jev result validation failed") from None

    record["recommended_decision"] = _decision_audit(recommended)
    _persist(audit_path, record)
    return RecommendationResult(recommended, capacities_snapshot)


def _base_record(
    *,
    timestamp: int | float,
    role: str,
    constraints: Mapping[str, object],
    capacities: Sequence[ProviderCapacity],
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "timestamp": timestamp,
        "request": {
            "role": role,
            "constraints": {
                name: constraints[name]
                for name in _CONSTRAINT_NAMES
                if type(constraints.get(name)) is bool
            },
        },
        "capacity": {
            capacity.harness.value: capacity.state.value for capacity in capacities
        },
        "jev": None,
        "recommended_decision": None,
        "error_category": None,
    }


def _v2_record(
    *,
    phase: str,
    request_id: str,
    timestamp: int | float,
    role: str,
    constraints: Mapping[str, object],
    capacities: Sequence[ProviderCapacity],
) -> dict[str, object]:
    record = _base_record(
        timestamp=timestamp,
        role=role,
        constraints=constraints,
        capacities=capacities,
    )
    record.update(
        {
            "schema_version": 2,
            "phase": phase,
            "request_id": request_id,
            "capacity": {
                capacity.harness.value: {
                    "state": capacity.state.value,
                    "age_hours": capacity.age_hours,
                    **capacity.quota.to_dict(),
                    "reason": capacity.reason,
                }
                for capacity in capacities
            },
        }
    )
    return record


def _jev_audit(
    result: JevRoutingResult, capacities: Sequence[ProviderCapacity]
) -> dict[str, object]:
    if not isinstance(result.model, str) or not result.model:
        raise ValueError("invalid Jev model")
    if (
        set(result.answers) != set(_ANSWER_NAMES)
        or set(result.probabilities) != set(_ANSWER_NAMES)
        or set(result.confidences) != set(_ANSWER_NAMES)
    ):
        raise ValueError("invalid Jev answer metadata")

    answers: dict[str, object] = {}
    expected_labels = {
        **_EXPECTED_LABELS,
        "harness": frozenset(capacity.harness.value for capacity in capacities),
    }
    for answer_id in _ANSWER_NAMES:
        answer = result.answers[answer_id]
        probabilities = result.probabilities[answer_id]
        confidence = result.confidences[answer_id]
        if (
            set(answer) != {"type", "value"}
            or answer["type"] != "choice"
            or not isinstance(answer["value"], str)
            or not isinstance(probabilities, Mapping)
            or set(probabilities) != expected_labels[answer_id]
            or any(
                not _probability(label, probability)
                for label, probability in probabilities.items()
            )
            or not isclose(
                fsum(probabilities.values()),
                1.0,
                abs_tol=probability_sum_tolerance(len(expected_labels[answer_id])),
            )
            or answer["value"] not in probabilities
            or probabilities[answer["value"]] != max(probabilities.values())
            or not _finite_number(confidence)
            or not 0 <= confidence <= 1
        ):
            raise ValueError("invalid Jev answer metadata")
        answers[answer_id] = {
            "label": answer["value"],
            "probabilities": dict(probabilities),
            "confidence": confidence,
        }
    return {"model": result.model, "answers": answers}


def _probability(label: object, value: object) -> bool:
    return isinstance(label, str) and _finite_number(value) and 0 <= value <= 1


def _finite_number(value: object) -> TypeGuard[int | float]:
    return (
        isinstance(value, int | float)
        and not isinstance(value, bool)
        and isfinite(value)
    )


def _decision_audit(decision: RoutingDecision) -> dict[str, str]:
    return {
        "harness": decision.harness.value,
        "model": decision.model.value,
        "effort": decision.effort.value,
    }


def _persist(path: Path, record: Mapping[str, object]) -> None:
    try:
        append_audit_record(path, record)
    except AuditError:
        raise RouterError("audit_failed", "routing audit failed") from None
