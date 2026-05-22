# scripts/io_utils.py
"""Low-level I/O utilities shared across Helm harness scripts.

Public API
----------
* :func:`atomic_write_json` — write a JSON-serializable value to a file
  atomically via ``tempfile.mkstemp`` + ``os.replace``.

Design notes
------------
Same-directory tempfile placement ensures that ``os.replace`` stays on
the same filesystem mount, which POSIX guarantees is atomic for the
``rename(2)`` syscall.  On Windows, ``os.replace`` is close-to-atomic
(replace is atomic only within the same volume there too).

Workspace note
--------------
This module lives in Helm (``~/Helm/.worktrees/harness-eng/scripts/``).
Workspace scripts (``~/.openclaw/workspace/.worktrees/harness-eng/``)
cannot import from Helm, so workspace sites with a single atomic JSON
write keep their inline implementation.  If two or more workspace sites
need the helper in the future, mirror this file into the workspace tree.
"""
from __future__ import annotations

import json
import os
import pathlib
import tempfile
from typing import Any

__all__ = ["atomic_write_json"]


def atomic_write_json(
    path: pathlib.Path,
    data: Any,
    *,
    indent: int | None = 2,
) -> None:
    """Write *data* to *path* atomically via tempfile + ``os.replace``.

    The tempfile is created in the same directory as *path* so that
    ``os.replace`` remains atomic on POSIX (same filesystem mount).
    The parent directory is created if it does not exist.  The tempfile
    is cleaned up on write failure so no partial files are left behind.

    Parameters
    ----------
    path:
        Destination file path.  Must be a :class:`pathlib.Path`.
    data:
        Any JSON-serializable value (dict, list, str, …).
    indent:
        JSON indentation level passed to :func:`json.dumps`.  Defaults
        to ``2`` for human-readable output.  Pass ``None`` for compact
        single-line output.

    Raises
    ------
    TypeError
        If *data* is not JSON-serializable.
    OSError
        If the directory cannot be created or the write/rename fails.
    """
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, indent=indent, ensure_ascii=False)
    dir_ = str(path.parent)
    fd, tmp_path = tempfile.mkstemp(dir=dir_, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
        os.replace(tmp_path, path)
    except Exception:
        # Clean up the temp file on write or rename failure so no
        # partial files are left behind.
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
