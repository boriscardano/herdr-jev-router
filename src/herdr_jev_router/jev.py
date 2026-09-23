"""Call Jev once for the six typed routing judgments."""

import logging
from asyncio import timeout as async_timeout
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from math import fsum, isclose, isfinite
from typing import cast

from httpx2 import AsyncBaseTransport
from typesafe_sdk import (
    AsyncTypeSafeClient,
    Choice,
    JSONContent,
    RetryPolicy,
    TypeSafeAPIConnectionError,
    TypeSafeAPIError,
    TypeSafeAPIResponseValidationError,
    TypeSafeAPITimeoutError,
    TypeSafeAuthenticationError,
    TypeSafeError,
    TypeSafeInternalServerError,
    TypeSafeRateLimitError,
    TypeSafeUnprocessableEntityError,
)

from herdr_jev_router.models import ProviderCapacity
from herdr_jev_router.policy import route_eligible

_MODEL = "jev-latest"
# Pin the TypeSafe endpoint so TYPESAFE_BASE_URL cannot redirect the key to a
# host the user did not choose. Proxy support is deliberately kept: the SDK
# builds a default httpx2 client with trust_env enabled.
_BASE_URL = "https://api.typesafe.ai"
_CLAUDE_CRITERIA = {
    "haiku": "small and fast",
    "sonnet": "balanced",
    "opus": "highest capability",
}
_CODEX_CRITERIA = {
    "luna": "small and fast",
    "terra": "balanced",
    "sol": "highest capability",
}
_OPENCODE_CRITERIA = {
    "deepseek": "small and fast",
    "glm": "balanced",
    "kimi": "highest capability",
}
_PI_CRITERIA = {
    "deepseek": "small and fast",
    "glm": "balanced",
    "kimi": "highest capability",
}
_EFFORT_CRITERIA = {
    "low": None,
    "medium": None,
    "high": None,
    "xhigh": None,
    "max": None,
}
_HARNESS_DESCRIPTIONS = {
    "claude": "Claude Code",
    "codex": "Codex",
    "opencode": "OpenCode",
    "pi": "Pi",
}
_CONSTRAINT_NAMES = frozenset({"read_only", "worktree", "network_required"})
# Jev rounds each probability to two decimals, so every option can miss its
# true value by up to 0.005 and an n-option sum by up to 0.005 * n. Accepting
# that bound keeps rounded answers valid without accepting a genuinely
# different distribution (0.8 or 1.2 stays rejected).
_ROUNDING_TOLERANCE_PER_OPTION = 0.005


_SECRET_HEADER_MARKERS = ("authorization", "api-key", "token", "secret")


def _is_secret_header(name: str) -> bool:
    lowered = name.lower()
    return any(marker in lowered for marker in _SECRET_HEADER_MARKERS)


class _RedactSdkBodies(logging.Filter):
    """Remove request and response bodies and credential headers from SDK wire logs."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not isinstance(record.args, dict):
            return True
        arguments = dict(record.args)
        if "body" in arguments:
            arguments["body"] = b"[redacted]"
        headers = arguments.get("headers")
        if isinstance(headers, dict):
            arguments["headers"] = {
                name: "***" if _is_secret_header(name) else value
                for name, value in headers.items()
            }
        record.args = arguments
        return True


logging.getLogger("typesafe_sdk").addFilter(_RedactSdkBodies())


def probability_sum_tolerance(option_count: int) -> float:
    """Return the two-decimal rounding slack for an `option_count`-way choice."""

    return _ROUNDING_TOLERANCE_PER_OPTION * option_count


class JevError(RuntimeError):
    """Report a stable, secret-safe failure at the Jev boundary."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class JevRoutingResult:
    """Expose validated Jev metadata and policy-compatible answers."""

    model: str
    answers: dict[str, dict[str, str]]
    probabilities: dict[str, dict[str, float]]
    confidences: dict[str, float]
    usage: dict[str, int | None]


@dataclass(frozen=True, slots=True)
class JevRequest:
    """Hold the exact state and six questions sent to one Jev routing call."""

    state: JSONContent
    questions: dict[str, Choice]


def build_jev_request(
    *,
    task: str,
    role: str,
    constraints: Mapping[str, object],
    capacities: Sequence[ProviderCapacity],
) -> JevRequest:
    """Build the exact state and six Choice questions for one Jev call."""

    raw_constraints = dict(constraints)
    if set(raw_constraints) != _CONSTRAINT_NAMES or any(
        type(value) is not bool for value in raw_constraints.values()
    ):
        raise JevError("invalid_state", "Jev state is not normalized")
    constraints_snapshot = {
        name: cast(bool, raw_constraints[name]) for name in _CONSTRAINT_NAMES
    }

    capacities_snapshot = tuple(capacities)
    if len({capacity.harness for capacity in capacities_snapshot}) != len(
        capacities_snapshot
    ):
        raise JevError("invalid_capacity", "Jev capacity contains a duplicate harness")
    eligible_capacities = route_eligible(capacities_snapshot)
    if not eligible_capacities:
        raise JevError("no_eligible_provider", "Jev has no eligible provider")

    harness_criteria = {
        capacity.harness.value: _HARNESS_DESCRIPTIONS[capacity.harness.value]
        for capacity in eligible_capacities
    }
    questions: dict[str, Choice] = {
        "harness": Choice(
            instructions=(
                "Which harness is the better fit for this delegated task? When "
                "more than one harness fits, prefer the provider with more "
                "remaining quota."
            ),
            criteria=harness_criteria,
        ),
        "claude_model": Choice(
            instructions=(
                "Assuming Claude Code is selected, which model tier best fits?"
            ),
            criteria=_CLAUDE_CRITERIA,
        ),
        "codex_model": Choice(
            instructions="Assuming Codex is selected, which model tier best fits?",
            criteria=_CODEX_CRITERIA,
        ),
        "opencode_model": Choice(
            instructions="Assuming OpenCode is selected, which model tier best fits?",
            criteria=_OPENCODE_CRITERIA,
        ),
        "pi_model": Choice(
            instructions="Assuming Pi is selected, which model tier best fits?",
            criteria=_PI_CRITERIA,
        ),
        "effort": Choice(
            instructions="Which reasoning effort is appropriate for this task?",
            criteria=_EFFORT_CRITERIA,
        ),
    }
    state: JSONContent = {
        "task": task,
        "role": role,
        "constraints": constraints_snapshot,
        "capacity": {
            capacity.harness.value: {
                "state": capacity.state.value,
                "penalty": capacity.penalty,
                "age_hours": capacity.age_hours,
                **capacity.quota.to_dict(),
            }
            for capacity in eligible_capacities
        },
    }
    return JevRequest(state=state, questions=questions)


async def route_with_jev(
    *,
    task: str,
    role: str,
    constraints: Mapping[str, object],
    capacities: Sequence[ProviderCapacity],
    api_key: str | None = None,
    transport: AsyncBaseTransport | None = None,
    timeout: float = 20.0,
    deadline: float = 25.0,
) -> JevRoutingResult:
    """Return one six-answer Jev routing result without launching an agent."""

    if any(
        not isinstance(value, int | float)
        or isinstance(value, bool)
        or not isfinite(value)
        or value <= 0
        for value in (timeout, deadline)
    ):
        raise JevError("invalid_timeout", "Jev timeouts must be positive and finite")
    request = build_jev_request(
        task=task,
        role=role,
        constraints=constraints,
        capacities=capacities,
    )

    try:
        async with async_timeout(deadline):
            async with AsyncTypeSafeClient(
                api_key=api_key,
                base_url=_BASE_URL,
                model=_MODEL,
                retry=RetryPolicy(max_retries=0),
                timeout=timeout,
                transport=transport,
            ) as client:
                response = await client.system_one(
                    state=request.state, questions=request.questions
                )
    except TypeSafeAuthenticationError:
        raise JevError("authentication", "Jev authentication failed") from None
    except TypeSafeUnprocessableEntityError:
        raise JevError("invalid_request", "Jev rejected the routing request") from None
    except TypeSafeRateLimitError:
        raise JevError("rate_limit", "Jev rate limit reached") from None
    except TypeSafeInternalServerError:
        raise JevError("service_unavailable", "Jev service unavailable") from None
    except TypeSafeAPIResponseValidationError:
        raise JevError("invalid_response", "Jev returned an invalid response") from None
    except TypeSafeAPITimeoutError:
        raise JevError("timeout", "Jev request timed out") from None
    except TypeSafeAPIConnectionError:
        raise JevError("connection", "Jev connection failed") from None
    except TypeSafeAPIError:
        raise JevError("api_error", "Jev request failed") from None
    except TypeSafeError:
        raise JevError("sdk_error", "Jev SDK failed") from None
    except TimeoutError:
        raise JevError("timeout", "Jev request timed out") from None

    if set(response.answers) != set(request.questions) or any(
        answer.type != "choice" for answer in response.answers.values()
    ):
        raise JevError("invalid_response", "Jev returned invalid routing answers")

    expected_choices = {
        "harness": frozenset(request.questions["harness"].criteria),
        "claude_model": frozenset(_CLAUDE_CRITERIA),
        "codex_model": frozenset(_CODEX_CRITERIA),
        "opencode_model": frozenset(_OPENCODE_CRITERIA),
        "pi_model": frozenset(_PI_CRITERIA),
        "effort": frozenset(_EFFORT_CRITERIA),
    }
    for answer_id, expected in expected_choices.items():
        answer = response.choices[answer_id]
        probabilities = answer.probabilities
        if (
            answer.choice not in expected
            or set(probabilities) != expected
            or any(
                not isfinite(probability) or not 0.0 <= probability <= 1.0
                for probability in probabilities.values()
            )
            or not isclose(
                fsum(probabilities.values()),
                1.0,
                abs_tol=probability_sum_tolerance(len(expected)),
            )
            or probabilities[answer.choice] != max(probabilities.values())
            or not isfinite(answer.confidence)
            or not 0.0 <= answer.confidence <= 1.0
        ):
            raise JevError("invalid_response", "Jev returned invalid routing answers")

    return JevRoutingResult(
        model=response.model,
        answers={
            answer_id: {"type": "choice", "value": answer.choice}
            for answer_id, answer in response.choices.items()
        },
        probabilities={
            answer_id: dict(answer.probabilities)
            for answer_id, answer in response.choices.items()
        },
        confidences={
            answer_id: answer.confidence
            for answer_id, answer in response.choices.items()
        },
        usage={
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
        },
    )
