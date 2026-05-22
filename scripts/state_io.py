# scripts/state_io.py
"""Atomic JSONL append with cross-platform file locking.

Also provides :func:`build_ledger_entry` — a thin schema helper that
formalises the optional task-ledger fields added in harness-engineering
Task 2.  The underlying writer (:func:`append_jsonl_atomic`) remains
unchanged; backward compatibility is guaranteed because new fields are
only included when the caller explicitly passes them.

New optional fields (Task 2):
  failure_signature  (dict)   — structured FS-001..FS-010 signature
  retry_count        (int)    — already present; left intact
  sessions           (list)   — session IDs associated with this task
  snapshot_evidence  (str)    — path to the snapshot used as evidence
  cleanup_status     (str)    — "ok" | "partial" | "failed" | "not_required"

Browser-specific stubs (accepted and persisted; values not generated here):
  browser_profile         (str)
  browser_mode            (str)
  source_urls             (list)
  screenshot_evidence     (str)
  console_network_signals (dict)
  site_note_update        (str)
"""
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


# ---------------------------------------------------------------------------
# Task-ledger entry schema helper (harness-engineering Task 2)
# ---------------------------------------------------------------------------

_CLEANUP_STATUS_VALUES = frozenset({"ok", "partial", "failed", "not_required"})


def build_ledger_entry(
    base: dict[str, Any],
    *,
    failure_signature: dict | None = None,
    sessions: list[str] | None = None,
    snapshot_evidence: str | None = None,
    cleanup_status: str | None = None,
    # Browser-specific stubs — accepted and persisted; values not generated here.
    browser_profile: str | None = None,
    browser_mode: str | None = None,
    source_urls: list[str] | None = None,
    screenshot_evidence: str | None = None,
    console_network_signals: dict | None = None,
    site_note_update: str | None = None,
) -> dict[str, Any]:
    """Return a copy of *base* with optional task-ledger fields merged in.

    Only fields that are explicitly passed (non-``None``) are included in
    the result — guarantees that old entries that never pass new fields will
    not acquire ``null`` fillers when round-tripped through this helper.

    The underlying writer (:func:`append_jsonl_atomic`) can be called with
    the returned dict directly and will persist only the fields present.

    Existing fields on *base* (including ``retry_count``) are preserved
    unchanged.

    Args:
        base: Existing task entry dict (must not be mutated by caller after
            passing; a shallow copy is made internally).
        failure_signature: Structured FS-001..FS-010 signature produced by
            ``scripts.failure_signature.signature()``.
        sessions: List of session IDs that contributed to this task.
        snapshot_evidence: Path to the snapshot file used as task evidence.
        cleanup_status: Post-task cleanup outcome.  Must be one of
            ``"ok"``, ``"partial"``, ``"failed"``, or ``"not_required"``.
        browser_profile: Browser profile name used during the task (stub).
        browser_mode: Browser mode (e.g. ``"headless"``, ``"visible"``) (stub).
        source_urls: URLs browsed during the task (stub).
        screenshot_evidence: Path to a screenshot taken as evidence (stub).
        console_network_signals: Structured network/console observations (stub).
        site_note_update: Note update produced by a site-interaction task (stub).

    Returns:
        New dict containing ``base`` fields plus any non-``None`` extras.

    Raises:
        ValueError: If ``cleanup_status`` is not one of the allowed values.
    """
    if cleanup_status is not None and cleanup_status not in _CLEANUP_STATUS_VALUES:
        raise ValueError(
            f"cleanup_status must be one of {sorted(_CLEANUP_STATUS_VALUES)!r}, "
            f"got {cleanup_status!r}"
        )

    entry: dict[str, Any] = dict(base)

    if failure_signature is not None:
        entry["failure_signature"] = failure_signature
    if sessions is not None:
        entry["sessions"] = sessions
    if snapshot_evidence is not None:
        entry["snapshot_evidence"] = snapshot_evidence
    if cleanup_status is not None:
        entry["cleanup_status"] = cleanup_status
    if browser_profile is not None:
        entry["browser_profile"] = browser_profile
    if browser_mode is not None:
        entry["browser_mode"] = browser_mode
    if source_urls is not None:
        entry["source_urls"] = source_urls
    if screenshot_evidence is not None:
        entry["screenshot_evidence"] = screenshot_evidence
    if console_network_signals is not None:
        entry["console_network_signals"] = console_network_signals
    if site_note_update is not None:
        entry["site_note_update"] = site_note_update

    return entry
