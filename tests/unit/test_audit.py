import json
import os
import stat
from importlib import import_module
from pathlib import Path

import pytest

from herdr_jev_router.audit import AuditError, append_audit_record


@pytest.mark.skipif(os.name != "posix", reason="POSIX advisory lock contract")
def test_audit_append_holds_an_exclusive_process_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fcntl = import_module("fcntl")
    calls: list[tuple[int, int]] = []

    def record_lock(descriptor: int, operation: int) -> None:
        calls.append((descriptor, operation))

    monkeypatch.setattr(fcntl, "flock", record_lock)

    append_audit_record(tmp_path / "routing.jsonl", {"attempt": 1})

    assert [operation for _, operation in calls] == [fcntl.LOCK_EX, fcntl.LOCK_UN]


@pytest.mark.skipif(os.name != "posix", reason="POSIX advisory lock contract")
@pytest.mark.parametrize("failed_operation", ["lock", "unlock"])
def test_audit_lock_failure_is_stable_and_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failed_operation: str,
) -> None:
    fcntl = import_module("fcntl")

    def fail_at_operation(descriptor: int, operation: int) -> None:
        if (failed_operation == "lock" and operation == fcntl.LOCK_EX) or (
            failed_operation == "unlock" and operation == fcntl.LOCK_UN
        ):
            raise OSError("private lock detail")

    monkeypatch.setattr(fcntl, "flock", fail_at_operation)

    with pytest.raises(AuditError) as error:
        append_audit_record(tmp_path / "routing.jsonl", {"attempt": 1})

    assert error.value.code == "audit_write_failed"
    assert "private lock detail" not in str(error.value)


def test_audit_records_are_owner_only_and_appended_as_json_lines(
    tmp_path: Path,
) -> None:
    path = tmp_path / "state" / "routing.jsonl"

    append_audit_record(path, {"attempt": 1})
    append_audit_record(path, {"attempt": 2})

    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert [json.loads(line) for line in path.read_text().splitlines()] == [
        {"attempt": 1},
        {"attempt": 2},
    ]


def test_audit_writer_refuses_a_symlink_without_touching_the_target(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target.jsonl"
    target.write_text("sentinel\n", encoding="utf-8")
    target.chmod(0o600)
    link = tmp_path / "routing.jsonl"
    link.symlink_to(target)

    with pytest.raises(AuditError) as error:
        append_audit_record(link, {"attempt": 1})

    assert error.value.code == "audit_write_failed"
    assert target.read_text(encoding="utf-8") == "sentinel\n"
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_audit_writer_refuses_a_file_not_owned_by_the_current_user(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "routing.jsonl"
    path.write_text("", encoding="utf-8")
    path.chmod(0o600)
    # Keep the directory check green so the file-owner branch is the one under
    # test. Faking geteuid alone would fail on the directory first.
    monkeypatch.setattr(
        "herdr_jev_router.audit.owner_only_directory", lambda directory: True
    )
    monkeypatch.setattr(os, "geteuid", lambda: os.getuid() + 1)

    with pytest.raises(AuditError) as error:
        append_audit_record(path, {"attempt": 1})

    assert error.value.code == "audit_write_failed"
    assert path.read_text(encoding="utf-8") == ""


def test_audit_writer_refuses_an_insecure_state_directory(tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir(mode=0o755)

    with pytest.raises(AuditError) as error:
        append_audit_record(state / "routing.jsonl", {"attempt": 1})

    assert error.value.code == "audit_write_failed"
    assert not (state / "routing.jsonl").exists()


@pytest.mark.parametrize("failed_call", ["write", "fsync", "close"])
def test_audit_io_failure_is_stable_and_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failed_call: str,
) -> None:
    def fail(*args: object) -> None:
        raise OSError("private operating-system detail")

    monkeypatch.setattr(os, failed_call, fail)

    with pytest.raises(AuditError) as error:
        append_audit_record(tmp_path / "routing.jsonl", {"attempt": 1})

    assert error.value.code == "audit_write_failed"
    assert "private operating-system detail" not in str(error.value)
