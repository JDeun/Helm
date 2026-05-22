# scripts/profile_pause_resume.py
"""Profile-level hard-stop for browser fan-out and long-running operations.

Public API
----------
pause_profile(profile, reason, state_path) -> dict
    Write a pause entry for *profile*.  Returns the new entry.

is_paused(profile, state_path) -> bool
    Return True if *profile* currently has a pause entry.

resume_profile(profile, resume_token, state_path) -> dict
    Remove the pause entry, but only if *resume_token* matches.
    Raises ValueError on token mismatch or if the profile is not paused.
    Returns the removed entry.

list_paused(state_path) -> list[dict]
    Return all paused profiles with their metadata, sorted by profile name.

pause_session_summary(profile, sessions, cleanup_status) -> dict
    Return a ledger-friendly dict for the ``pause_resume`` field.

check_can_start(profile, state_path) -> tuple[bool, str | None]
    Pre-flight check callers MUST honour before starting a new browser
    session.  Returns ``(False, reason)`` when the profile is paused;
    ``(True, None)`` otherwise.

    **Calling code is responsible for honouring this check.**  The module
    does not block or kill in-flight sessions automatically; it only records
    pause state and exposes the predicate.

State-file contract
-------------------
The state file is a plain JSON object written atomically via
``tempfile.mkstemp`` + ``os.replace`` (same-directory temp file so the
rename is within the same filesystem mount, which POSIX guarantees is
atomic for the rename(2) syscall).

Schema::

    {
        "<profile-name>": {
            "paused_at":    "<ISO-8601 UTC>",
            "reason":       "<human-readable string>",
            "resume_token": "<8-hex chars>"
        },
        ...
    }

Default path: ``~/.openclaw/workspace/.openclaw/profile-pause-state.json``
Override via environment variable ``OPENCLAW_PAUSE_STATE``.

Thread/process safety: concurrent *same-instant* writes (two processes that
both read the file, mutate, and write back in an interleaved fashion) are NOT
safe — the last writer wins.  File-level locking is deferred to a future
iteration.  Sequential writes within the same process are safe.
"""
from __future__ import annotations

import json
import os
import secrets
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Default state-file location
# ---------------------------------------------------------------------------

def _default_path() -> Path:
    """Return the effective default state-file path (re-evaluates env var each call)."""
    env = os.environ.get("OPENCLAW_PAUSE_STATE")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".openclaw" / "workspace" / ".openclaw" / "profile-pause-state.json"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _read_state(state_path: Path) -> dict[str, Any]:
    """Read the state file, returning an empty dict if it does not exist."""
    if not state_path.exists():
        return {}
    text = state_path.read_text(encoding="utf-8").strip()
    if not text:
        return {}
    return json.loads(text)


def _write_state(state: dict[str, Any], state_path: Path) -> None:
    """Atomically write *state* to *state_path*.

    Uses ``tempfile.mkstemp`` in the same directory so ``os.replace`` stays
    on the same filesystem mount, making the rename POSIX-atomic.
    """
    state_path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(state, indent=2, sort_keys=True, ensure_ascii=False)
    dir_ = str(state_path.parent)
    fd, tmp_path = tempfile.mkstemp(dir=dir_, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
        os.replace(tmp_path, state_path)
    except Exception:
        # Clean up the temp file if the rename failed.
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def pause_profile(
    profile: str,
    reason: str,
    state_path: Path | None = None,
) -> dict[str, Any]:
    """Record a pause entry for *profile*.

    If *profile* already has a pause entry it is overwritten (new reason,
    new token, new timestamp).  Only one entry per profile is kept.

    Args:
        profile:    Browser profile name (must be a non-empty string).
        reason:     Human-readable explanation for the pause.
        state_path: Path to the JSON state file.  Defaults to the value of
                    ``OPENCLAW_PAUSE_STATE`` or the standard Helm path.

    Returns:
        The newly written entry dict (``paused_at``, ``reason``,
        ``resume_token``).
    """
    if not profile:
        raise ValueError("profile must be a non-empty string")
    if state_path is None:
        state_path = _default_path()

    state = _read_state(state_path)
    entry: dict[str, Any] = {
        "paused_at": datetime.now(tz=timezone.utc).isoformat(),
        "reason": reason,
        "resume_token": secrets.token_hex(4),
    }
    state[profile] = entry
    _write_state(state, state_path)
    return dict(entry)


def is_paused(profile: str, state_path: Path | None = None) -> bool:
    """Return ``True`` if *profile* currently has a pause entry.

    Args:
        profile:    Browser profile name.
        state_path: Path to the JSON state file.

    Returns:
        ``True`` if paused, ``False`` otherwise.
    """
    if state_path is None:
        state_path = _default_path()
    state = _read_state(state_path)
    return profile in state


def resume_profile(
    profile: str,
    resume_token: str,
    state_path: Path | None = None,
) -> dict[str, Any]:
    """Remove the pause entry for *profile* if the token matches.

    Args:
        profile:      Browser profile name.
        resume_token: The 8-hex token returned (or displayed) at pause time.
        state_path:   Path to the JSON state file.

    Returns:
        The removed entry dict (``paused_at``, ``reason``, ``resume_token``).

    Raises:
        ValueError: If *profile* is not currently paused.
        ValueError: If *resume_token* does not match the stored token.
    """
    if state_path is None:
        state_path = _default_path()
    state = _read_state(state_path)

    if profile not in state:
        raise ValueError(f"Profile {profile!r} is not paused; nothing to resume.")

    stored_token = state[profile].get("resume_token", "")
    if resume_token != stored_token:
        raise ValueError(
            f"Token mismatch for profile {profile!r}: "
            f"expected {stored_token!r}, got {resume_token!r}."
        )

    removed = dict(state.pop(profile))
    _write_state(state, state_path)
    return removed


def list_paused(state_path: Path | None = None) -> list[dict[str, Any]]:
    """Return all paused profiles with their metadata.

    The result is sorted deterministically by profile name.

    Args:
        state_path: Path to the JSON state file.

    Returns:
        List of dicts, each containing ``profile``, ``paused_at``,
        ``reason``, and ``resume_token`` keys.
    """
    if state_path is None:
        state_path = _default_path()
    state = _read_state(state_path)
    return [
        {"profile": name, **entry}
        for name, entry in sorted(state.items())
    ]


def pause_session_summary(
    profile: str,
    sessions: list[str],
    cleanup_status: str,
) -> dict[str, Any]:
    """Return a ledger-friendly dict for the ``pause_resume`` task-ledger field.

    This helper does **not** interact with the state file; it only assembles
    a JSON-serializable structure that callers can embed in a task ledger
    entry under the ``pause_resume`` key.

    Args:
        profile:        Browser profile name.
        sessions:       Session IDs that were active at pause time.
        cleanup_status: One of ``"ok"``, ``"partial"``, ``"failed"``,
                        ``"not_required"``.

    Returns:
        Dict with keys ``profile``, ``paused_sessions``, ``cleanup_status``,
        and ``stop_reason``.
    """
    return {
        "profile": profile,
        "paused_sessions": list(sessions),
        "cleanup_status": cleanup_status,
        "stop_reason": "hard_stop",
    }


def check_can_start(
    profile: str,
    state_path: Path | None = None,
) -> tuple[bool, str | None]:
    """Pre-flight check before starting a new browser session for *profile*.

    **Callers are responsible for honouring the result of this check.**
    This module does not block or kill in-flight sessions; it only exposes
    the current pause state.

    Args:
        profile:    Browser profile name.
        state_path: Path to the JSON state file.

    Returns:
        ``(True, None)`` if the profile is not paused and a session may
        start.  ``(False, reason)`` where *reason* is the human-readable
        string recorded at pause time if the profile is paused.
    """
    if state_path is None:
        state_path = _default_path()
    state = _read_state(state_path)
    if profile in state:
        return (False, state[profile].get("reason"))
    return (True, None)
