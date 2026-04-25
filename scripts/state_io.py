# scripts/state_io.py
"""Atomic JSONL append with cross-platform file locking."""
from __future__ import annotations

import json
import os
import sys
import threading
import warnings as _warnings
from pathlib import Path
from typing import Any

_lock_warning_event = threading.Event()

# Keep the legacy name so tests that reset it directly still work.
# Tests do `state_io_mod._LOCK_WARNING_ISSUED = False`; we intercept that via
# a module-level property shim by keeping both in sync in the functions below.
_LOCK_WARNING_ISSUED = False


def _warn_lock_once(msg: str) -> None:
    """Emit a lock-unavailability warning exactly once, thread-safely."""
    global _LOCK_WARNING_ISSUED
    if not _lock_warning_event.is_set():
        _lock_warning_event.set()
        _LOCK_WARNING_ISSUED = True
        _warnings.warn(msg)


def append_jsonl_atomic(path: Path, entry: dict[str, Any]) -> None:
    """Append one JSON object to a JSONL file with best-effort locking."""
    # Allow tests to reset the event via the legacy boolean flag.
    # If _LOCK_WARNING_ISSUED has been reset to False externally, clear the event too.
    global _LOCK_WARNING_ISSUED
    if not _LOCK_WARNING_ISSUED and _lock_warning_event.is_set():
        _lock_warning_event.clear()

    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n"
    line_bytes = line.encode("utf-8")

    # "ab" (binary append) mode: writes always go to end-of-file regardless
    # of seek position, so the sentinel-region seek(0) for locking does not
    # affect where data is written.
    with path.open("ab") as fh:
        locked = False

        if sys.platform != "win32":
            try:
                import fcntl
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
                locked = True
            except Exception:
                locked = False
                _warn_lock_once("File locking unavailable; concurrent writes may corrupt data")
        else:
            try:
                import msvcrt
                # Use bytes 0–1 as a fixed sentinel mutex region.
                # This ensures lock and unlock always operate on the same
                # byte region regardless of file position changes during write.
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)
                locked = True
            except Exception:
                locked = False
                _warn_lock_once("File locking unavailable; concurrent writes may corrupt data")

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
                        # Unlock the same sentinel region locked above.
                        fh.seek(0)
                        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
                    except Exception:
                        pass
