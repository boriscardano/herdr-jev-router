# Contributing

Herdr Jev Router is experimental. Keep changes small, explicit, and limited to
advisory spawning on stock Herdr.

## Setup

Install Python 3.11 or newer and `uv`, then run:

```console
uv sync --locked
```

CI runs the quality job on Python 3.11, 3.12 and 3.13.

## Development workflow

All production behavior follows test-driven development:

1. Add one focused test for an observable behavior.
2. Run the focused test and confirm that it fails for the expected reason.
3. Add the smallest implementation that makes it pass.
4. Simplify while the focused test and full suite stay green.
5. Run the complete local gate before committing.

Bug fixes begin with a regression test. Security controls and fail-closed
behavior require negative tests. Do not weaken assertions to make a test pass.

Use a feature branch and open a pull request. Never push directly to `main`.
Keep commits focused and use Conventional Commit subjects.

## Local gate

The CI `quality` job runs exactly:

```console
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run ty check src
```

The CI `secrets` job scans the full git history:

```console
gitleaks git --no-banner --redact --exit-code 1 .
```

Document any check that cannot be run and explain why in the pull request.

## Clean-room verification

The Docker clean room builds a disposable Linux image, runs the local gate,
installs the built wheel into a fresh virtual environment, checks every console
script with `--help`, and makes one real Jev `explain` recommendation. It needs
Docker and a TypeSafe key.

```console
TYPESAFE_API_KEY=... scripts/test-docker-cleanroom.sh
```

The script passes the key to the container through a mode 0600 file at
`/run/secrets/typesafe_api_key` and removes it on exit. It puts a stub harness
on `PATH` so the live Jev `explain` check can run without installing a real
agent. The clean room never starts a Herdr server or a child agent.

## Pull requests

Describe the behavior, representative red and green test runs, security or
privacy implications, and documentation changes. Do not include credentials,
private task data, personal paths, or account identifiers.
