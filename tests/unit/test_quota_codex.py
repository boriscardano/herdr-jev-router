import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from herdr_jev_router.models import CapacityState
from herdr_jev_router.quota import classify_capacity, read_cache
from herdr_jev_router.quota_codex import (
    CODEX_CACHE_NAME,
    collect_codex,
    parse_app_server_jsonl,
    parse_rate_limits,
)

FIXTURES = Path(__file__).parents[1] / "fixtures" / "quota" / "codex"


def test_app_server_jsonl_normalizes_the_documented_subscription_view() -> None:
    result = parse_app_server_jsonl(
        (FIXTURES / "app-server-success.jsonl").read_text(),
        observed_at=1_800_000_000,
        captured_at=1_800_000_001,
    )

    assert result is not None
    assert result.provider == "codex"
    assert [
        (item.name, item.remaining_percent, item.window_seconds)
        for item in result.windows
    ] == [
        ("primary", 69, 604_800),
        ("secondary", 87.5, 18_000),
    ]
    assert classify_capacity(result, now=1_800_000_002) is CapacityState.SURPLUS
    assert "opaque-model-bucket" not in json.dumps(result.to_dict())


def test_api_key_and_malformed_app_server_output_are_unknown() -> None:
    api_key = parse_app_server_jsonl(
        (FIXTURES / "app-server-api-key.json").read_text(),
        observed_at=1_800_000_000,
        captured_at=1_800_000_001,
    )

    assert api_key is None
    assert (
        parse_app_server_jsonl(
            (FIXTURES / "app-server-error.jsonl").read_text(),
            observed_at=1,
            captured_at=2,
        )
        is None
    )


def test_codex_parser_excludes_auth_and_path_sentinels() -> None:
    document = (
        (FIXTURES / "app-server-success.jsonl")
        .read_text()
        .replace(
            '"type":"chatgpt"',
            '"type":"chatgpt","accessToken":"SENTINEL_CODEX_TOKEN"',
        )
    )
    document = document.replace(
        '"limitId":"codex"',
        '"limitId":"codex","transcriptPath":"/SENTINEL_CODEX_PATH"',
    )

    result = parse_app_server_jsonl(document, observed_at=1_800_000_000, captured_at=1)

    assert result is not None
    assert "SENTINEL_CODEX" not in json.dumps(result.to_dict())


@pytest.mark.parametrize("value", [True, False, [], ["primary"], {}, {"primary": True}])
def test_structured_rate_limit_reached_type_is_malformed(value: object) -> None:
    result = parse_rate_limits(
        {
            "rateLimits": {
                "rateLimitReachedType": value,
                "primary": {
                    "usedPercent": 25,
                    "windowDurationMins": 300,
                    "resetsAt": 1_800_010_000,
                },
            }
        },
        observed_at=1_800_000_000,
        captured_at=1_800_000_001,
    )

    assert result is None


def test_app_server_collector_uses_bounded_json_rpc_and_reaps_timeout(tmp_path) -> None:
    pid_path = tmp_path / "app-server.pid"
    program = """
import json
import os
import sys
import time

pid_path = sys.argv[1]
with open(pid_path, "w") as output:
    output.write(str(os.getpid()))
for line in sys.stdin:
    request = json.loads(line)
    if request.get("id") == 1:
        assert request.get("params") == {
            "clientInfo": {"name": "herdr-jev-router", "version": "0.1.0"},
            "capabilities": {},
        }
        print(json.dumps({"id": 1, "result": {}}), flush=True)
    elif request.get("id") == 2:
        print(json.dumps({"id": 2, "result": {"account": {"type": "chatgpt"}}}), flush=True)
    elif request.get("id") == 3:
        print(json.dumps({"id": 3, "result": {"rateLimits": {"primary": {"usedPercent": 25, "windowDurationMins": 300, "resetsAt": 1800010000}}}}), flush=True)
        break
time.sleep(30)
"""

    result = asyncio.run(
        collect_codex(
            tmp_path,
            now=1_800_000_000,
            command=(sys.executable, "-u", "-c", program, str(pid_path)),
            timeout=1,
        )
    )

    assert result is not None
    assert result.windows[0].remaining_percent == 75
    with pytest.raises(ProcessLookupError):
        os.kill(int(pid_path.read_text()), 0)


_COLLECT_ONE_ROUND = """
import asyncio
import sys
from pathlib import Path

from herdr_jev_router.quota_codex import collect_codex

state_dir = Path(sys.argv[1])
observed_at = float(sys.argv[2])
command = tuple(sys.argv[3:])
snapshot = asyncio.run(
    collect_codex(state_dir, now=observed_at, command=command, timeout=5)
)
raise SystemExit(0 if snapshot is not None else 3)
"""

_COUNTING_APP_SERVER = """
import json
import os
import sys
import time

counter_path = sys.argv[1]
with open(counter_path, "a", encoding="utf-8") as output:
    output.write(f"{os.getpid()}\\n")
# Hold the provider lock long enough for a second, freshly started process to
# reach it.  On platforms with slower process startup (for example macOS) a
# shorter pause lets the second call read the freshly written cache without
# ever contending for the lock, so the lock-contention path would go untested.
time.sleep(0.5)
for line in sys.stdin:
    request = json.loads(line)
    if request.get("id") == 1:
        print(json.dumps({"id": 1, "result": {}}), flush=True)
    elif request.get("id") == 2:
        print(json.dumps({"id": 2, "result": {"account": {"type": "chatgpt"}}}), flush=True)
    elif request.get("id") == 3:
        print(json.dumps({"id": 3, "result": {"rateLimits": {"primary": {"usedPercent": 25, "windowDurationMins": 300, "resetsAt": 1800010000}}}}), flush=True)
        break
time.sleep(30)
"""

_NEVER_ANSWERING_APP_SERVER = """
import os
import sys
import time

pid_path = sys.argv[1]
with open(pid_path, "w", encoding="utf-8") as output:
    output.write(str(os.getpid()))
for _line in sys.stdin:
    pass
time.sleep(30)
"""

_CONCURRENT_REFRESH_ROUNDS = 3


def test_concurrent_codex_refreshes_do_not_duplicate_the_provider_request(
    tmp_path,
) -> None:
    server = tmp_path / "counting-app-server.py"
    server.write_text(_COUNTING_APP_SERVER, encoding="utf-8")

    for attempt in range(_CONCURRENT_REFRESH_ROUNDS):
        state_dir = tmp_path / f"state-{attempt}"
        state_dir.mkdir(mode=0o700)
        counter = tmp_path / f"app-server-starts-{attempt}.txt"
        command = (sys.executable, "-u", str(server), str(counter))
        children = [
            subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    _COLLECT_ONE_ROUND,
                    str(state_dir),
                    "1800000000",
                    *command,
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
            for _ in range(2)
        ]
        statuses = []
        try:
            for child in children:
                _, stderr = child.communicate(timeout=30)
                statuses.append((child.returncode, stderr))
        finally:
            for child in children:
                if child.poll() is None:
                    child.kill()
                    child.wait()

        assert all(status in (0, 3) for status, _ in statuses), statuses
        assert any(status == 0 for status, _ in statuses), statuses

        starts = counter.read_text(encoding="utf-8").splitlines()
        assert len(starts) == 1, starts

        cache_path = state_dir / CODEX_CACHE_NAME
        snapshot = read_cache(cache_path, now=1_800_000_000)
        assert snapshot is not None
        assert snapshot.provider == "codex"
        assert snapshot.freshness == "fresh"
        assert snapshot.windows
        assert json.loads(cache_path.read_text(encoding="utf-8")) == snapshot.to_dict()


def test_codex_collector_passes_arguments_without_a_shell(tmp_path) -> None:
    marker = tmp_path / "shell-metacharacter-ran"
    argv_path = tmp_path / "app-server-argv.json"
    payload = f"payload;touch {marker}"
    program = (
        "import json\n"
        "import sys\n"
        "import time\n"
        f"with open({str(argv_path)!r}, 'w', encoding='utf-8') as output:\n"
        "    json.dump(sys.argv[1:], output)\n"
        "for _line in sys.stdin:\n"
        "    pass\n"
        "time.sleep(30)\n"
    )

    result = asyncio.run(
        collect_codex(
            tmp_path,
            command=(sys.executable, "-u", "-c", program, payload),
            timeout=0.5,
        )
    )

    assert result is None
    assert json.loads(argv_path.read_text(encoding="utf-8")) == [payload]
    assert not marker.exists()


def test_codex_collector_reaps_an_app_server_that_never_answers(tmp_path) -> None:
    pid_path = tmp_path / "unresponsive-app-server.pid"
    server = tmp_path / "unresponsive-app-server.py"
    server.write_text(_NEVER_ANSWERING_APP_SERVER, encoding="utf-8")

    result = asyncio.run(
        collect_codex(
            tmp_path,
            now=1_800_000_000,
            command=(sys.executable, "-u", str(server), str(pid_path)),
            timeout=0.5,
        )
    )

    assert result is None
    pid = int(pid_path.read_text(encoding="utf-8"))
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_codex_collector_treats_an_overlong_app_server_line_as_failure(
    tmp_path,
) -> None:
    program = """
import sys

sys.stdout.write("x" * 200000 + "\\n")
sys.stdout.flush()
for _line in sys.stdin:
    pass
"""

    result = asyncio.run(
        collect_codex(
            tmp_path,
            now=1_800_000_000,
            command=(sys.executable, "-u", "-c", program),
            timeout=1,
        )
    )

    assert result is None
