# scripts/state_io.py
"""Atomic JSONL append with cross-platform file locking."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any


def append_jsonl_atomic(path: Path, entry: dict[str, Any]) -> None:
    """Append one JSON object to a JSONL file.

    - Creates parent directory if missing.
    - Serializes with ensure_ascii=False and sort_keys=True.
    - Writes exactly one JSON object per line.
    - Uses fcntl.flock on POSIX when available.
    - Uses msvcrt.locking on Windows when available.
    - Flushes and fsyncs after write.
    - Falls back to normal append when locking is unavailable.
    - Does not silently drop entries.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n"

    with path.open("a", encoding="utf-8") as fh:
        locked = False

        if sys.platform != "win32":
            try:
                import fcntl
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
                locked = True
            except Exception:
                locked = False
        else:
            try:
                import msvcrt
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                locked = True
            except Exception:
                locked = False

        try:
            fh.write(line)
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
                        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
                    except Exception:
                        pass
