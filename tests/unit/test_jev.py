import asyncio
import json
import logging
import traceback
from collections.abc import Iterator, Mapping

import pytest
from httpx2 import ConnectError, MockTransport, ReadTimeout, Request, Response

from herdr_jev_router.jev import JevError, route_with_jev
from herdr_jev_router.models import (
    CapacityState,
    CodexModel,
    Harness,
    ProviderCapacity,
    QuotaDetail,
)
from herdr_jev_router.policy import (
    CRITICAL_CAPACITY_PENALTY,
    apply_critical_fallback,
    validate_decision,
)


def valid_response() -> dict[str, object]:
    return {
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
                "probabilities": {"luna": 0.2, "terra": 0.7, "sol": 0.1},
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


def all_capacities() -> tuple[ProviderCapacity, ...]:
    return (
        ProviderCapacity(Harness.CLAUDE, CapacityState.ON_PACE),
        ProviderCapacity(Harness.CODEX, CapacityState.SURPLUS),
        ProviderCapacity(Harness.OPENCODE, CapacityState.ON_PACE),
        ProviderCapacity(Harness.PI, CapacityState.ON_PACE),
    )


def response(body: dict[str, object]) -> Response:
    return Response(
        200,
        headers={"content-type": "application/json"},
        content=json.dumps(body).encode(),
    )


def call_with_transport(transport: MockTransport, **changes: object):
    kwargs = {
        "task": "Review a bounded change.",
        "role": "reviewer",
        "constraints": {
            "read_only": True,
            "worktree": False,
            "network_required": False,
        },
        "capacities": all_capacities(),
        "api_key": "dummy-test-key",
        "transport": transport,
    }
    kwargs.update(changes)
    return asyncio.run(
        route_with_jev(**kwargs)  # type: ignore[arg-type]
    )


def test_one_system_one_call_contains_six_typed_questions() -> None:
    requests: list[Request] = []

    def handler(request: Request) -> Response:
        requests.append(request)
        return Response(
            200,
            headers={"x-typesafe-request-id": "test-request"},
            json=valid_response(),
        )

    result = call_with_transport(MockTransport(handler))

    assert len(requests) == 1
    request = requests[0]
    assert request.method == "POST"
    assert request.url.path == "/v1/systemone"
    assert set(request.extensions["timeout"].values()) == {20.0}
    payload = json.loads(request.content)
    assert set(payload["questions"]) == {
        "harness",
        "claude_model",
        "codex_model",
        "opencode_model",
        "pi_model",
        "effort",
    }
    assert all(
        question["type"] == "choice" for question in payload["questions"].values()
    )
    assert payload["questions"]["harness"]["criteria"] == {
        "claude": "Claude Code",
        "codex": "Codex",
        "opencode": "OpenCode",
        "pi": "Pi",
    }
    assert payload["state"] == {
        "task": "Review a bounded change.",
        "role": "reviewer",
        "constraints": {
            "read_only": True,
            "worktree": False,
            "network_required": False,
        },
        "capacity": {
            "claude": {
                "state": "on_pace",
                "penalty": 0,
                "age_hours": None,
                "five_hour_remaining_percent": None,
                "five_hour_resets_in_hours": None,
                "weekly_remaining_percent": None,
                "weekly_resets_in_hours": None,
            },
            "codex": {
                "state": "surplus",
                "penalty": 0,
                "age_hours": None,
                "five_hour_remaining_percent": None,
                "five_hour_resets_in_hours": None,
                "weekly_remaining_percent": None,
                "weekly_resets_in_hours": None,
            },
            "opencode": {
                "state": "on_pace",
                "penalty": 0,
                "age_hours": None,
                "five_hour_remaining_percent": None,
                "five_hour_resets_in_hours": None,
                "weekly_remaining_percent": None,
                "weekly_resets_in_hours": None,
            },
            "pi": {
                "state": "on_pace",
                "penalty": 0,
                "age_hours": None,
                "five_hour_remaining_percent": None,
                "five_hour_resets_in_hours": None,
                "weekly_remaining_percent": None,
                "weekly_resets_in_hours": None,
            },
        },
    }
    assert payload["questions"]["claude_model"]["instructions"].startswith(
        "Assuming Claude Code is selected"
    )
    assert payload["questions"]["codex_model"]["instructions"].startswith(
        "Assuming Codex is selected"
    )
    assert payload["questions"]["opencode_model"]["instructions"].startswith(
        "Assuming OpenCode is selected"
    )
    assert payload["questions"]["pi_model"]["instructions"].startswith(
        "Assuming Pi is selected"
    )
    assert result.model == "jev-test-1"
    assert result.answers == {
        "harness": {"type": "choice", "value": "codex"},
        "claude_model": {"type": "choice", "value": "sonnet"},
        "codex_model": {"type": "choice", "value": "terra"},
        "opencode_model": {"type": "choice", "value": "deepseek"},
        "pi_model": {"type": "choice", "value": "deepseek"},
        "effort": {"type": "choice", "value": "high"},
    }
    assert set(result.probabilities) == {
        "harness",
        "claude_model",
        "codex_model",
        "opencode_model",
        "pi_model",
        "effort",
    }
    assert validate_decision(result.answers, all_capacities()).model is CodexModel.TERRA
    assert result.usage == {"input_tokens": 123, "output_tokens": 45}
    assert b"dummy-test-key" not in request.content


def test_client_pins_the_https_endpoint_against_environment_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TYPESAFE_BASE_URL", "http://attacker.invalid")
    requests: list[Request] = []

    def handler(request: Request) -> Response:
        requests.append(request)
        return response(valid_response())

    call_with_transport(MockTransport(handler))

    assert len(requests) == 1
    assert requests[0].url.scheme == "https"
    assert requests[0].url.host == "api.typesafe.ai"


def test_missing_answer_fails_closed() -> None:
    body = valid_response()
    del body["answers"]["effort"]  # type: ignore[index]

    with pytest.raises(JevError) as error:
        call_with_transport(MockTransport(lambda request: response(body)))

    assert error.value.code == "invalid_response"


def test_harness_question_only_offers_eligible_providers() -> None:
    body = valid_response()
    answers = body["answers"]
    assert isinstance(answers, dict)
    harness = answers["harness"]
    assert isinstance(harness, dict)
    harness["probabilities"] = {"codex": 1.0}
    capacities = (
        ProviderCapacity(Harness.CLAUDE, CapacityState.EXHAUSTED),
        ProviderCapacity(Harness.CODEX, CapacityState.ON_PACE),
    )
    requests: list[Request] = []

    def handler(request: Request) -> Response:
        requests.append(request)
        return response(body)

    result = call_with_transport(
        MockTransport(handler),
        capacities=capacities,
    )

    payload = json.loads(requests[0].content)
    assert payload["questions"]["harness"]["criteria"] == {"codex": "Codex"}
    assert set(payload["state"]["capacity"]) == {"codex"}
    assert validate_decision(result.answers, capacities).harness is Harness.CODEX


@pytest.mark.parametrize(
    "capacities, expected_code",
    [
        ((), "no_eligible_provider"),
        (
            (
                ProviderCapacity(Harness.CLAUDE, CapacityState.EXHAUSTED),
                ProviderCapacity(Harness.CODEX, CapacityState.EXHAUSTED),
            ),
            "no_eligible_provider",
        ),
        (
            (
                ProviderCapacity(Harness.CODEX, CapacityState.ON_PACE),
                ProviderCapacity(Harness.CODEX, CapacityState.SURPLUS),
            ),
            "invalid_capacity",
        ),
    ],
)
def test_invalid_capacity_fails_before_the_request(
    capacities: tuple[ProviderCapacity, ...], expected_code: str
) -> None:
    requests = 0

    def handler(request: Request) -> Response:
        nonlocal requests
        requests += 1
        return response(valid_response())

    with pytest.raises(JevError) as error:
        call_with_transport(MockTransport(handler), capacities=capacities)

    assert error.value.code == expected_code
    assert requests == 0


@pytest.mark.parametrize(
    "status_code, expected_code",
    [
        (401, "authentication"),
        (422, "invalid_request"),
        (429, "rate_limit"),
        (500, "service_unavailable"),
        (529, "service_unavailable"),
        (400, "api_error"),
    ],
)
def test_http_failures_map_to_stable_secret_safe_categories(
    status_code: int, expected_code: str
) -> None:
    requests = 0

    def handler(request: Request) -> Response:
        nonlocal requests
        requests += 1
        return Response(status_code, json={"detail": "server-secret-body"})

    with pytest.raises(JevError) as error:
        call_with_transport(MockTransport(handler))

    assert error.value.code == expected_code
    assert requests == 1
    assert "server-secret-body" not in str(error.value)
    assert "dummy-test-key" not in str(error.value)
    rendered = "".join(traceback.format_exception(error.value))
    assert "server-secret-body" not in rendered


@pytest.mark.parametrize(
    "transport_error, expected_code",
    [
        (ConnectError("connection-secret"), "connection"),
        (ReadTimeout("timeout-secret"), "timeout"),
    ],
)
def test_transport_failures_map_to_stable_secret_safe_categories(
    transport_error: Exception, expected_code: str
) -> None:
    def handler(request: Request) -> Response:
        raise transport_error

    with pytest.raises(JevError) as error:
        call_with_transport(MockTransport(handler))

    assert error.value.code == expected_code
    assert "secret" not in str(error.value)


def test_redirect_responses_are_not_followed() -> None:
    requests: list[Request] = []

    def handler(request: Request) -> Response:
        requests.append(request)
        return Response(302, headers={"location": "https://evil.example/collect"})

    with pytest.raises(JevError):
        call_with_transport(MockTransport(handler))

    assert len(requests) == 1
    assert requests[0].url.path == "/v1/systemone"


def test_outer_deadline_fails_closed() -> None:
    async def handler(request: Request) -> Response:
        await asyncio.sleep(0.05)
        return response(valid_response())

    with pytest.raises(JevError) as error:
        call_with_transport(
            MockTransport(handler),
            timeout=10.0,
            deadline=0.001,
        )

    assert error.value.code == "timeout"


@pytest.mark.parametrize("field", ["timeout", "deadline"])
@pytest.mark.parametrize("value", [float("inf"), float("nan"), 0.0, -1.0])
def test_network_timeouts_must_be_positive_and_finite(field: str, value: float) -> None:
    with pytest.raises(JevError) as error:
        call_with_transport(
            MockTransport(lambda request: response(valid_response())),
            **{field: value},
        )

    assert error.value.code == "invalid_timeout"


@pytest.mark.parametrize(
    "constraints",
    [
        {"read_only": True, "worktree": False},
        {
            "read_only": True,
            "worktree": False,
            "network_required": False,
            "api_token": "must-not-send",
        },
        {"read_only": True, "worktree": False, "network_required": "no"},
    ],
)
def test_unnormalized_constraints_fail_before_the_request(
    constraints: dict[str, object],
) -> None:
    requests = 0

    def handler(request: Request) -> Response:
        nonlocal requests
        requests += 1
        return response(valid_response())

    with pytest.raises(JevError) as error:
        call_with_transport(MockTransport(handler), constraints=constraints)

    assert error.value.code == "invalid_state"
    assert requests == 0


def test_constraints_are_snapshotted_before_validation() -> None:
    class ChangingConstraints(Mapping[str, object]):
        expected = {
            "read_only": True,
            "worktree": False,
            "network_required": False,
        }

        def __init__(self) -> None:
            self.reads: dict[str, int] = {}

        def __getitem__(self, key: str) -> object:
            self.reads[key] = self.reads.get(key, 0) + 1
            if self.reads[key] > 1:
                return "must-not-send"
            return self.expected[key]

        def __iter__(self) -> Iterator[str]:
            return iter(self.expected)

        def __len__(self) -> int:
            return len(self.expected)

    requests: list[Request] = []

    def handler(request: Request) -> Response:
        requests.append(request)
        return response(valid_response())

    call_with_transport(
        MockTransport(handler),
        constraints=ChangingConstraints(),
    )

    payload = json.loads(requests[0].content)
    assert payload["state"]["constraints"] == ChangingConstraints.expected


def test_sdk_debug_logs_redact_request_and_response_bodies(
    caplog: pytest.LogCaptureFixture,
) -> None:
    body = valid_response()
    body["model"] = "response-secret-sentinel"

    with caplog.at_level(logging.DEBUG, logger="typesafe_sdk"):
        call_with_transport(
            MockTransport(lambda request: response(body)),
            task="request-secret-sentinel",
        )

    assert "request-secret-sentinel" not in caplog.text
    assert "response-secret-sentinel" not in caplog.text


def test_sdk_debug_logs_redact_authorization_headers(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.DEBUG, logger="typesafe_sdk"):
        call_with_transport(
            MockTransport(lambda request: response(valid_response())),
            api_key="SENTINEL_AUTHORIZATION_KEY",
        )

    assert "authorization" in caplog.text
    assert "SENTINEL_AUTHORIZATION_KEY" not in caplog.text


def test_router_log_filter_redacts_credential_headers() -> None:
    from herdr_jev_router.jev import _RedactSdkBodies

    record = logging.LogRecord(
        name="typesafe_sdk",
        level=logging.DEBUG,
        pathname=__file__,
        lineno=1,
        msg="%(method)s %(headers)s",
        args={
            "method": "POST",
            "headers": {
                "authorization": "Bearer secret",
                "x-api-key": "secret",
                "accept": "application/json",
            },
        },
        exc_info=None,
    )

    assert _RedactSdkBodies().filter(record)
    assert record.args == {
        "method": "POST",
        "headers": {
            "authorization": "***",
            "x-api-key": "***",
            "accept": "application/json",
        },
    }


@pytest.mark.parametrize(
    "answer_id, label",
    [
        ("harness", "claude"),
        ("claude_model", "haiku"),
        ("codex_model", "luna"),
        ("opencode_model", "deepseek"),
        ("pi_model", "deepseek"),
        ("effort", "low"),
    ],
)
def test_each_probability_map_is_validated(answer_id: str, label: str) -> None:
    body = valid_response()
    answers = body["answers"]
    assert isinstance(answers, dict)
    answer = answers[answer_id]
    assert isinstance(answer, dict)
    probabilities = answer["probabilities"]
    assert isinstance(probabilities, dict)
    probabilities[label] = 0.0

    with pytest.raises(JevError) as error:
        call_with_transport(MockTransport(lambda request: response(body)))

    assert error.value.code == "invalid_response"


@pytest.mark.parametrize(
    "probabilities, choice",
    [
        # Jev rounds each probability to two decimals, so a valid integer-ish
        # split can sum to 0.99 or 1.01 instead of exactly 1.0.
        ({"luna": 0.33, "terra": 0.33, "sol": 0.33}, "terra"),
        ({"luna": 0.34, "terra": 0.33, "sol": 0.34}, "luna"),
    ],
)
def test_two_decimal_rounded_probability_sums_are_accepted(
    probabilities: dict[str, float], choice: str
) -> None:
    body = valid_response()
    answers = body["answers"]
    assert isinstance(answers, dict)
    codex_model = answers["codex_model"]
    assert isinstance(codex_model, dict)
    codex_model["choice"] = choice
    codex_model["probabilities"] = probabilities

    result = call_with_transport(MockTransport(lambda request: response(body)))

    assert result.probabilities["codex_model"] == probabilities


@pytest.mark.parametrize(
    "probabilities, choice",
    [
        # 0.8 is far below one; nothing that rounds to two decimals can explain it.
        ({"luna": 0.5, "terra": 0.2, "sol": 0.1}, "luna"),
        # 1.2 is far above one for the same reason.
        ({"luna": 0.5, "terra": 0.4, "sol": 0.3}, "luna"),
        # Five options tolerate at most 5 * 0.005; 0.97 is outside that bound.
        ({"low": 0.2, "medium": 0.2, "high": 0.2, "xhigh": 0.2, "max": 0.17}, "low"),
    ],
)
def test_probability_sums_far_from_one_are_rejected(
    probabilities: dict[str, float], choice: str
) -> None:
    body = valid_response()
    answers = body["answers"]
    assert isinstance(answers, dict)
    answer_id = "effort" if "low" in probabilities else "codex_model"
    answer = answers[answer_id]
    assert isinstance(answer, dict)
    answer["choice"] = choice
    answer["probabilities"] = probabilities

    with pytest.raises(JevError) as error:
        call_with_transport(MockTransport(lambda request: response(body)))

    assert error.value.code == "invalid_response"


def test_malformed_success_body_maps_to_invalid_response() -> None:
    with pytest.raises(JevError) as error:
        call_with_transport(
            MockTransport(lambda request: Response(200, json={"not": "a response"}))
        )

    assert error.value.code == "invalid_response"


@pytest.mark.parametrize(
    "case",
    [
        "wrong_type",
        "unknown_choice",
        "missing_probability",
        "extra_probability",
        "non_finite_probability",
        "out_of_range_probability",
        "invalid_probability_sum",
        "choice_not_highest_probability",
        "invalid_confidence",
    ],
)
def test_malformed_typed_answer_or_probability_map_fails_closed(case: str) -> None:
    body = valid_response()
    answers = body["answers"]
    assert isinstance(answers, dict)
    harness = answers["harness"]
    assert isinstance(harness, dict)

    if case == "wrong_type":
        answers["harness"] = {"type": "noul", "noul": 0.8}
    elif case == "unknown_choice":
        harness["choice"] = "other"
    elif case == "missing_probability":
        del harness["probabilities"]["claude"]
    elif case == "extra_probability":
        harness["probabilities"]["other"] = 0.0
    elif case == "non_finite_probability":
        harness["probabilities"]["claude"] = float("nan")
    elif case == "out_of_range_probability":
        harness["probabilities"]["claude"] = -0.1
    elif case == "invalid_probability_sum":
        harness["probabilities"]["pi"] = 0.2
    elif case == "choice_not_highest_probability":
        harness["probabilities"].update({"claude": 0.5, "codex": 0.2})
    elif case == "invalid_confidence":
        harness["confidence"] = float("inf")

    with pytest.raises(JevError) as error:
        call_with_transport(MockTransport(lambda request: response(body)))

    assert error.value.code == "invalid_response"


def test_jev_state_carries_window_numbers_and_a_quota_preference() -> None:
    capacities = (
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
    requests: list[Request] = []

    def handler(request: Request) -> Response:
        requests.append(request)
        return response(valid_response())

    call_with_transport(MockTransport(handler), capacities=capacities)

    payload = json.loads(requests[0].content)
    assert payload["state"]["capacity"]["claude"] == {
        "state": "on_pace",
        "penalty": 0,
        "age_hours": None,
        "five_hour_remaining_percent": 85,
        "five_hour_resets_in_hours": 2,
        "weekly_remaining_percent": 35,
        "weekly_resets_in_hours": 100,
    }
    assert payload["state"]["capacity"]["codex"] == {
        "state": "surplus",
        "penalty": 0,
        "age_hours": None,
        "five_hour_remaining_percent": None,
        "five_hour_resets_in_hours": None,
        "weekly_remaining_percent": None,
        "weekly_resets_in_hours": None,
    }
    assert "remaining quota" in payload["questions"]["harness"]["instructions"]


def test_jev_removes_a_critical_provider_when_an_alternative_remains() -> None:
    body = valid_response()
    answers = body["answers"]
    assert isinstance(answers, dict)
    harness = answers["harness"]
    assert isinstance(harness, dict)
    harness["choice"] = "claude"
    harness["probabilities"] = {"claude": 1.0}
    capacities = (
        ProviderCapacity(Harness.CLAUDE, CapacityState.ON_PACE),
        ProviderCapacity(
            Harness.CODEX,
            CapacityState.CRITICAL,
            0,
            QuotaDetail(None, None, 6, 38),
            "codex weekly 6% left, resets in 38h",
        ),
    )
    requests: list[Request] = []

    def handler(request: Request) -> Response:
        requests.append(request)
        return response(body)

    call_with_transport(MockTransport(handler), capacities=capacities)

    payload = json.loads(requests[0].content)
    assert payload["questions"]["harness"]["criteria"] == {"claude": "Claude Code"}
    assert set(payload["state"]["capacity"]) == {"claude"}


def test_jev_offers_the_only_critical_provider_with_a_penalty() -> None:
    body = valid_response()
    answers = body["answers"]
    assert isinstance(answers, dict)
    harness = answers["harness"]
    assert isinstance(harness, dict)
    harness["probabilities"] = {"codex": 1.0}
    capacities = apply_critical_fallback(
        (
            ProviderCapacity(
                Harness.CODEX,
                CapacityState.CRITICAL,
                0,
                QuotaDetail(None, None, 6, 38),
                "codex weekly 6% left, resets in 38h",
            ),
            ProviderCapacity(Harness.CLAUDE, CapacityState.EXHAUSTED),
        )
    )
    requests: list[Request] = []

    def handler(request: Request) -> Response:
        requests.append(request)
        return response(body)

    call_with_transport(MockTransport(handler), capacities=capacities)

    payload = json.loads(requests[0].content)
    assert payload["questions"]["harness"]["criteria"] == {"codex": "Codex"}
    assert payload["state"]["capacity"]["codex"] == {
        "state": "critical",
        "penalty": CRITICAL_CAPACITY_PENALTY,
        "age_hours": None,
        "five_hour_remaining_percent": None,
        "five_hour_resets_in_hours": None,
        "weekly_remaining_percent": 6,
        "weekly_resets_in_hours": 38,
    }
