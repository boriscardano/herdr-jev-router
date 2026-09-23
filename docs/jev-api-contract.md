# Jev API contract

This is the contract the router implements against the TypeSafe Jev API. It
was checked against the published TypeSafe documentation and the
`typesafe-sdk==0.7.0` release. The router sends six `Choice` questions across
four harnesses. See [multi-harness-routing.md](multi-harness-routing.md) and
`src/herdr_jev_router/jev.py`.

## Request shape

The router makes one asynchronous `system_one` call containing a shared state
and a mapping of named typed questions:

1. `harness`, with the installed and enabled harnesses as options.
2. `claude_model`, whose instructions explicitly assume Claude Code was selected.
3. `codex_model`, whose instructions explicitly assume Codex was selected.
4. `opencode_model`, whose instructions explicitly assume OpenCode was selected.
5. `pi_model`, whose instructions explicitly assume Pi was selected.
6. `effort`, with the supported effort labels.

All six questions see the same state and cannot see one another's answers. The
four model questions are therefore speculative branch questions. Router code
uses only the model answer for the selected harness, while retaining all six
answers for validation and audit.

The documented async entry point is:

```python
async with AsyncTypeSafeClient(
    model="jev-latest",
    base_url="https://api.typesafe.ai",
    timeout=20.0,
    retry=RetryPolicy(max_retries=0),
) as client:
    response = await client.system_one(
        state=state,
        questions=questions,
    )
```

The router pins `base_url` to `https://api.typesafe.ai` so that
`TYPESAFE_BASE_URL` cannot redirect the key to a host the user did not choose.
Proxy support is kept, because the SDK builds a default client with `trust_env`
enabled.

`state` may be a string, JSON object, or array. For the router it is a small
JSON object containing the delegated task, role, normalized constraints, and
normalized provider capacity. Do not include credentials, raw quota timestamps,
or unrelated repository content.

Each question has a map key chosen by the caller, a typed question object,
`instructions`, and, for `Choice`, a required criteria map. Instructions and
criteria descriptions can be strings, objects, or arrays. A criteria value may
be `None` when a label needs no description.

The request shape is:

```python
questions = {
    "harness": Choice(
        instructions="Which harness is the better fit for this delegated task?",
        criteria={
            "claude": "Claude Code",
            "codex": "Codex",
            "opencode": "OpenCode",
            "pi": "Pi",
        },
    ),
    "claude_model": Choice(
        instructions="Assuming Claude Code is selected, which model tier fits?",
        criteria={
            "haiku": "small and fast",
            "sonnet": "balanced",
            "opus": "highest capability",
        },
    ),
    "codex_model": Choice(
        instructions="Assuming Codex is selected, which model tier fits?",
        criteria={
            "luna": "small and fast",
            "terra": "balanced",
            "sol": "highest capability",
        },
    ),
    "opencode_model": Choice(
        instructions="Assuming OpenCode is selected, which model tier fits?",
        criteria={
            "deepseek": "small and fast",
            "glm": "balanced",
            "kimi": "highest capability",
        },
    ),
    "pi_model": Choice(
        instructions="Assuming Pi is selected, which model tier fits?",
        criteria={
            "deepseek": "small and fast",
            "glm": "balanced",
            "kimi": "highest capability",
        },
    ),
    "effort": Choice(
        instructions="Which reasoning effort is appropriate for this task?",
        criteria={
            "low": None,
            "medium": None,
            "high": None,
            "xhigh": None,
            "max": None,
        },
    ),
}

response = await client.system_one(
    state={
        "task": delegated_task,
        "role": role,
        "constraints": normalized_constraints,
        "capacity": normalized_capacity,
    },
    questions=questions,
)
```

The question IDs are not sent as semantic content to the model. They are
response keys. Questions in one call are evaluated independently, so a
conditional question must state its premise in its own instructions. If a
question truly needs an earlier answer to build new state or options, make a
second call. The six routing questions do not need one.

## Response shape

The HTTP body is shaped like this:

```json
{
  "model": "jev-1.13.0",
  "answers": {
    "harness": {
      "type": "choice",
      "choice": "claude",
      "probabilities": {"claude": 0.78, "codex": 0.22},
      "confidence": 0.56
    }
  },
  "usage": {"input_tokens": 546, "output_tokens": 165}
}
```

The SDK returns a `SystemOneResponse` with `model`, `answers`, and `usage`.
Each `Choice` answer contains:

- `type`, which must be `choice`.
- `choice`, the highest-probability label.
- `probabilities`, a map covering every supplied criteria label. The values are
  floats that sum to 1.
- `confidence`, a value from 0 to 1 derived from the distribution.

The canonical access path is `response.answers[question_id]`. The SDK also
exposes grouped convenience properties such as `response.choices[question_id]`.
The response exposes `request_id` from the `x-typesafe-request-id` header and
raw HTTP metadata. Do not persist raw response bodies or headers. Record the
resolved `response.model` in the audit event, not only the requested alias.

## Dependency and imports

The package name is `typesafe-sdk`, and the Python import package is
`typesafe_sdk`. The dependency is pinned explicitly:

```toml
dependencies = [
  "typesafe-sdk==0.7.0",
]
```

The SDK's published requirements are `httpx2>=2.0.0`, `pydantic>=2.12.0`,
`pydantic-core>=2.41.1`, `tenacity>=9.0.0`, and `typing-extensions>=4.13.0`.
They are transitive requirements of `typesafe-sdk`, not separate router
choices.

The production boundary needs these imports:

```python
from typesafe_sdk import (
    AsyncTypeSafeClient,
    Choice,
    RetryPolicy,
    TypeSafeAPIConnectionError,
    TypeSafeAPIError,
    TypeSafeAPIResponseValidationError,
    TypeSafeAPITimeoutError,
    TypeSafeAuthenticationError,
    TypeSafeInternalServerError,
    TypeSafeRateLimitError,
    TypeSafeUnprocessableEntityError,
)
```

Use `TYPESAFE_API_KEY` from the environment, or the owner-only key file at
`$XDG_CONFIG_HOME/herdr-jev-router/key` when the variable is unset. Keep it in
the router process only.
Never include it in logs, test fixtures, exception output, or audit records.

## Errors, retries, and timeouts

The SDK performs local validation before sending. Empty questions or an empty
Score criteria list raise `TypeSafeError`. For a sent request:

- `TypeSafeAPIError` means an unsuccessful HTTP response after retries. It
  carries status, server body, headers, a credential-free endpoint, and request
  ID when available. Do not log its body or headers without redaction.
- `TypeSafeAuthenticationError` represents HTTP 401.
- `TypeSafeUnprocessableEntityError` represents HTTP 422 validation failure.
- `TypeSafeRateLimitError` represents HTTP 429 and may expose `retry_after_ms`.
- `TypeSafeInternalServerError` represents HTTP 5xx, including the documented
  529 overload response.
- `TypeSafeAPIConnectionError` means no HTTP response was received.
- `TypeSafeAPITimeoutError` is a connection error caused by the configured
  operation timeout and exposes the timeout value.
- `TypeSafeAPIResponseValidationError` means a successful response body is
  missing required structure. Its `field_path` identifies the invalid field.

The SDK default `RetryPolicy` is two retries, exponential backoff starting at
0.5 seconds and capped at 5 seconds, 0.25 jitter, retry statuses 408, 429, and
500 through 599, and retry enabled for connection and timeout errors. It honors
`Retry-After` and `retry-after-ms`. Its default total retry budget is 30 seconds
per SDK call. Passing `RetryPolicy(max_retries=0)` disables retries. The router
sets an explicit per-operation timeout and one overall routing deadline, then
fails closed on any exception or malformed answer. It adds no application
retries on top of the SDK retry budget.

## Test seam

`AsyncTypeSafeClient` accepts either an `httpx2.AsyncBaseTransport` through
`transport` or an `httpx2.AsyncClient` through `http_client`, but not both. The
client closes the supplied resource. Unit tests use `httpx2.MockTransport` with
a fixture handler and a dummy API key. The handler asserts one
`POST /v1/systemone`, captures the JSON body, and returns a fixture response. It
must never contact TypeSafe or use a real key.

The seam sends one request with body keys `state`, `model`, and `questions`,
then parses a fixture Choice answer through `response.answers["harness"]`.
Contract tests also cover malformed success bodies, 401, 422, 429, 529,
connection failure, and timeout without logging response bodies or headers.

## Sources

- [Documentation index](https://docs.typesafe.ai/llms.txt)
- [HTTP API reference](https://docs.typesafe.ai/api.md)
- [TypeSafe Python SDK](https://docs.typesafe.ai/sdk/python.md)
- [Async Python client](https://docs.typesafe.ai/sdk/python/api/clients/async.md)
- [Python questions](https://docs.typesafe.ai/sdk/python/api/types/questions.md)
- [Python answers and responses](https://docs.typesafe.ai/sdk/python/api/types/responses.md)
- [Python exceptions](https://docs.typesafe.ai/sdk/python/api/exceptions.md)
- [Python retry policy](https://docs.typesafe.ai/sdk/python/api/retries.md)
- [Primitives and multiple questions](https://docs.typesafe.ai/primitives.md)
- [Speculative fan-out](https://docs.typesafe.ai/patterns/fan-out.md)
- [State](https://docs.typesafe.ai/concepts/state.md)
- [Confidence](https://docs.typesafe.ai/confidence.md)
- [Published package metadata](https://pypi.org/project/typesafe-sdk/)
