from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import warnings as _warnings
from collections import deque
from pathlib import Path
from typing import Any

_lock_warning_event = threading.Event()
_LOCK_WARNING_ISSUED = False


def _warn_lock_once(msg: str) -> None:
    global _LOCK_WARNING_ISSUED
    if not _lock_warning_event.is_set():
        _lock_warning_event.set()
        _LOCK_WARNING_ISSUED = True
        _warnings.warn(msg)


def iter_jsonl(path: Path):
    if not path.exists():
        return
    with path.open("r", encoding="utf-8", errors="ignore") as handle:
        for lineno, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                print(f"warning: ignoring malformed JSONL line {lineno} in {path}: {exc}", file=sys.stderr)
                continue
            if not isinstance(payload, dict):
                print(f"warning: ignoring non-object JSONL line {lineno} in {path}", file=sys.stderr)
                continue
            yield payload


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return list(iter_jsonl(path))


def tail_lines(path: Path, limit: int) -> list[str]:
    if limit <= 0 or not path.exists():
        return []
    window: deque[str] = deque(maxlen=limit)
    with path.open("r", encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            window.append(line.rstrip("\n"))
    return list(window)


def atomic_write_text(path: Path, text: str, *, fsync: bool = True) -> None:
    """Atomically write *text* to *path*: write a sibling tempfile, fsync, then
    ``os.replace`` (atomic rename on POSIX) so readers never see a partial file.
    Creates the parent directory. On failure the tempfile is removed and the
    error re-raised."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        # Encode to bytes ourselves so a lone surrogate (\ud800-\udfff — a broken
        # emoji half that slipped in from upstream truncation) can't crash the write.
        # Valid text encodes byte-identically; only corrupt content gets sanitized.
        try:
            data = text.encode("utf-8")
        except UnicodeEncodeError:
            data = text.encode("utf-8", "replace")
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            if fsync:
                os.fsync(handle.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def atomic_write_json(
    path: Path,
    payload: Any,
    *,
    indent: int | None = 2,
    sort_keys: bool = False,
    ensure_ascii: bool = False,
    trailing_newline: bool = True,
    fsync: bool = True,
) -> None:
    """Serialize *payload* to JSON and write it atomically (see
    ``atomic_write_text``). Defaults match the common workspace idiom
    (``indent=2, ensure_ascii=False`` + trailing newline)."""
    text = json.dumps(payload, indent=indent, ensure_ascii=ensure_ascii, sort_keys=sort_keys)
    if trailing_newline:
        text += "\n"
    atomic_write_text(path, text, fsync=fsync)


def append_jsonl_atomic(path: Path, entry: dict[str, Any]) -> None:
    global _LOCK_WARNING_ISSUED
    if not _LOCK_WARNING_ISSUED and _lock_warning_event.is_set():
        _lock_warning_event.clear()

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
            except (ImportError, OSError):  # noqa: BLE001 - fcntl optional on macOS variants
                _warn_lock_once("File locking unavailable; concurrent writes may corrupt data")
        else:
            try:
                import msvcrt

                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)
                locked = True
            except (ImportError, OSError):  # noqa: BLE001 - msvcrt optional in WSL
                _warn_lock_once("File locking unavailable; concurrent writes may corrupt data")

        try:
            fh.write(line_bytes)
            fh.flush()
            os.fsync(fh.fileno())
        finally:
            # Release the lock if we hold one. Must NOT `return` here: a bare
            # `return` inside `finally` would swallow a write/flush/fsync
            # exception raised in the `try` (e.g. ENOSPC on a platform where
            # locking was unavailable), silently losing the append. When we
            # never locked there is simply nothing to unlock.
            if locked:
                if sys.platform != "win32":
                    try:
                        import fcntl

                        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
                    except (ImportError, OSError):
                        # Unlock failure is harmless: file handle is about to close.
                        pass
                else:
                    try:
                        import msvcrt

                        fh.seek(0)
                        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
                    except (ImportError, OSError):
                        pass

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
    policy_transition: dict | None = None,
    browser_recon: dict | None = None,
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
        policy_transition: Structured transition record from
            ``scripts.policy_transition.transition_record()`` documenting an
            automatic policy action triggered by a repeated failure pattern.
        browser_recon: BrowserReconDecision dict returned by
            ``scripts.browser_work_verifier.verify()``.  Recorded for
            observability when the browser gate evaluates a task (Wave 3a).

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
    if policy_transition is not None:
        entry["policy_transition"] = policy_transition
    if browser_recon is not None:
        entry["browser_recon"] = browser_recon

    return entry
