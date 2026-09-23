from pathlib import Path

ROOT = Path(__file__).parents[2]


def test_cleanroom_builds_the_router_and_never_with_secret() -> None:
    dockerfile = (ROOT / "Dockerfile.cleanroom").read_text()

    assert "github.com/boriscardano/herdr" not in dockerfile
    assert "cargo build" not in dockerfile
    assert "patch" not in dockerfile.lower()
    assert "FROM rust@sha256:" in dockerfile
    assert "UV_VERSION=0.12.17" in dockerfile
    assert "ziglang.org/download/0.16.0" in dockerfile
    assert "TYPESAFE_API_KEY" not in dockerfile
    assert "uv sync --frozen" in dockerfile


def test_cleanroom_runs_the_router_gate_and_reports_machine_readable_success() -> None:
    entrypoint = (ROOT / "scripts" / "cleanroom-entrypoint.sh").read_text()

    assert "uv run pytest" in entrypoint
    assert "uv run ruff check ." in entrypoint
    assert "uv run ruff format --check ." in entrypoint
    assert "git diff --cached --check" in entrypoint
    assert "cargo test" not in entrypoint
    assert "github.com/boriscardano/herdr" not in entrypoint
    assert '{"claude", "codex", "opencode", "pi"}' in entrypoint
    assert "herdr-jev-router explain" in entrypoint
    assert '"jev_recommendation_ok"' in entrypoint
    assert '"event":"clean_room_ok"' in entrypoint


def test_host_runner_mounts_jev_key_as_read_only_secret_file() -> None:
    runner = (ROOT / "scripts" / "test-docker-cleanroom.sh").read_text()

    assert "--mount" in runner
    assert "target=/run/secrets/typesafe_api_key" in runner
    assert "readonly" in runner
    assert "--env TYPESAFE_API_KEY" not in runner
