# scripts/state_io.py
"""Atomic JSONL append with cross-platform file locking."""
from __future__ import annotations

import json
import os
import sys
import warnings as _warnings
from pathlib import Path
from typing import Any

_LOCK_WARNING_ISSUED = False


def append_jsonl_atomic(path: Path, entry: dict[str, Any]) -> None:
    """Append one JSON object to a JSONL file with best-effort locking."""
    global _LOCK_WARNING_ISSUED

    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n"
    line_bytes = line.encode("utf-8")

    with path.open("ab") as fh:
        locked = False

        if sys.platform != "win32":
            try:
                import fcntl
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
                locked = True
            except Exception:
                locked = False
                if not _LOCK_WARNING_ISSUED:
                    _warnings.warn("File locking unavailable; concurrent writes may corrupt data")
                    _LOCK_WARNING_ISSUED = True
        else:
            try:
                import msvcrt
                lock_size = max(len(line_bytes), 1)
                msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, lock_size)
                locked = True
            except Exception:
                locked = False
                if not _LOCK_WARNING_ISSUED:
                    _warnings.warn("File locking unavailable; concurrent writes may corrupt data")
                    _LOCK_WARNING_ISSUED = True

        try:
            fh.write(line_bytes)
            fh.flush()
            os.fsync(fh.fileno())
        finally:
            if locked:
                if sys.platform != "win32":
                    try:
                        import fcntl
                        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
                    except Exception:
                        pass
                else:
                    try:
                        import msvcrt
                        lock_size = max(len(line_bytes), 1)
                        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, lock_size)
                    except Exception:
                        pass
