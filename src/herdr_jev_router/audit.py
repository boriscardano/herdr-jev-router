"""Persist redacted routing audit events before the decision is returned."""

import json
import os
import stat
from collections.abc import Mapping
from pathlib import Path

from herdr_jev_router.quota import owner_only_directory

if os.name == "posix":
    import fcntl


class AuditError(RuntimeError):
    """Report a stable failure at the fail-closed audit boundary."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def append_audit_record(path: Path, record: Mapping[str, object]) -> None:
    """Append one fsynced JSON record to an owner-only audit file."""

    descriptor: int | None = None
    locked = False
    failed = False
    try:
        payload = (
            json.dumps(
                record,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
            + b"\n"
        )
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if not owner_only_directory(path.parent):
            raise OSError("audit directory is not owner-only")
        descriptor = os.open(
            path,
            os.O_APPEND | os.O_CREAT | os.O_WRONLY | os.O_NOFOLLOW,
            0o600,
        )
        os.fchmod(descriptor, 0o600)
        # O_NOFOLLOW rejects a symlinked path and fstat then proves the opened
        # inode is our own owner-only regular file. A hard link to another
        # user's file is caught by the owner check.
        details = os.fstat(descriptor)
        if (
            not stat.S_ISREG(details.st_mode)
            or stat.S_IMODE(details.st_mode) != 0o600
            or details.st_uid != os.geteuid()
        ):
            raise OSError("audit file is not an owner-only regular file")
        if os.name == "posix":
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            locked = True
        remaining = memoryview(payload)
        while remaining:
            written = os.write(descriptor, remaining)
            if written == 0:
                raise OSError("audit write made no progress")
            remaining = remaining[written:]
        os.fsync(descriptor)
    except (OSError, TypeError, ValueError):
        failed = True
    finally:
        if descriptor is not None:
            if locked:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                except OSError:
                    failed = True
            try:
                os.close(descriptor)
            except OSError:
                failed = True
    if failed:
        raise AuditError(
            "audit_write_failed", "routing audit could not be written"
        ) from None
