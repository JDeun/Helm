"""Skill promotion state ledger for Wave 4.

Tracks which trace scaffold candidates have been notified (sent as a Telegram
digest), and whether each was subsequently approved or rejected.

State file
----------
Default path: ``~/.openclaw/workspace/.openclaw/skill-promotion-state.json``
Override via env var ``OPENCLAW_SKILL_PROMOTION_STATE``.

candidate_id
------------
An 8-character lowercase hexadecimal hash derived from
``hashlib.sha256(f"{skill}\\x00{task_name}".encode()).hexdigest()[:8]``.
The NUL separator prevents ``("ab", "cde")`` from colliding with
``("abc", "de")``.  The hash is stable across runs because it depends only on
the skill name and task name — the two fields that identify a
(skill, task_name) scaffold candidate.

Entry shape
-----------
::

    {
      "candidate_id": "a1b2c3d4",
      "fingerprint": {<original candidate dict from skill_scaffold_candidates>},
      "notified_at": "2026-05-22T08:00:00.000000+00:00",
      "status": "notified" | "approved" | "rejected",
      "approved_by": "kevin",          # only when status == "approved"
      "approved_at": "<iso8601>",       # only when status == "approved"
      "rejected_at": "<iso8601>",       # only when status == "rejected"
      "reason": "<free text>"           # only when status == "rejected"
    }

The top-level state dict has a single key ``"entries"`` whose value is a
list of the above entry objects.
"""

from __future__ import annotations

import hashlib
import os
import pathlib
from typing import Any

from scripts.io_utils import atomic_write_json
from scripts.time_helpers import utc_now_iso

__all__ = [
    "candidate_id_for",
    "default_state_path",
    "load_state",
    "save_state",
    "record_notified",
    "mark_approved",
    "mark_rejected",
    "pending_approvals",
    "is_processed",
]

_DEFAULT_STATE_REL = pathlib.Path(".openclaw") / "skill-promotion-state.json"
_DEFAULT_WORKSPACE = pathlib.Path.home() / ".openclaw" / "workspace"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def candidate_id_for(skill: str | None, task_name: str) -> str:
    """Return the stable 8-hex candidate_id for *(skill, task_name)*.

    Uses SHA-256 over ``"<skill>\\x00<task_name>"`` (NUL separator) so that
    ``("ab", "cde")`` and ``("abc", "de")`` produce different IDs.
    *skill* is normalised to the empty string when ``None``.
    """
    skill_str = skill if skill is not None else ""
    raw = f"{skill_str}\x00{task_name}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:8]


def default_state_path() -> pathlib.Path:
    """Return the state file path, respecting ``OPENCLAW_SKILL_PROMOTION_STATE``."""
    env = os.environ.get("OPENCLAW_SKILL_PROMOTION_STATE")
    if env:
        return pathlib.Path(env).expanduser()
    return _DEFAULT_WORKSPACE / _DEFAULT_STATE_REL


def _empty_state() -> dict:
    return {"entries": []}


def _entry_index(state: dict) -> dict[str, int]:
    """Build a ``{candidate_id: list_index}`` lookup for *state['entries']*."""
    return {e["candidate_id"]: i for i, e in enumerate(state.get("entries", []))}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_state(state_path: pathlib.Path | None = None) -> dict:
    """Load the promotion state from *state_path* (or the default path).

    Returns an empty state dict if the file does not exist or is unreadable.
    """
    import json

    path = state_path if state_path is not None else default_state_path()
    path = pathlib.Path(path)
    if not path.exists():
        return _empty_state()
    try:
        text = path.read_text(encoding="utf-8")
        data = json.loads(text)
        if not isinstance(data, dict) or "entries" not in data:
            return _empty_state()
        return data
    except (OSError, json.JSONDecodeError):
        return _empty_state()


def save_state(state: dict, state_path: pathlib.Path | None = None) -> None:
    """Atomically write *state* to *state_path* (or the default path)."""
    path = state_path if state_path is not None else default_state_path()
    atomic_write_json(pathlib.Path(path), state)


def record_notified(
    state: dict,
    candidate_id: str,
    fingerprint: dict,
) -> None:
    """Add a ``"notified"`` entry for *candidate_id* if not already present.

    If *candidate_id* already exists in *state* this is a no-op (idempotent).
    The caller is responsible for persisting via :func:`save_state`.
    """
    idx = _entry_index(state)
    if candidate_id in idx:
        return  # already recorded; idempotent
    state.setdefault("entries", []).append(
        {
            "candidate_id": candidate_id,
            "fingerprint": fingerprint,
            "notified_at": utc_now_iso(),
            "status": "notified",
        }
    )


def mark_approved(
    state: dict,
    candidate_id: str,
    approved_by: str = "kevin",
) -> None:
    """Transition *candidate_id* to ``"approved"`` status.

    Raises ``KeyError`` if *candidate_id* is not in *state*.
    The caller is responsible for persisting via :func:`save_state`.
    """
    idx = _entry_index(state)
    if candidate_id not in idx:
        raise KeyError(f"candidate_id not found: {candidate_id!r}")
    entry = state["entries"][idx[candidate_id]]
    entry["status"] = "approved"
    entry["approved_by"] = approved_by
    entry["approved_at"] = utc_now_iso()


def mark_rejected(
    state: dict,
    candidate_id: str,
    reason: str | None = None,
) -> None:
    """Transition *candidate_id* to ``"rejected"`` status.

    Raises ``KeyError`` if *candidate_id* is not in *state*.
    The caller is responsible for persisting via :func:`save_state`.
    """
    idx = _entry_index(state)
    if candidate_id not in idx:
        raise KeyError(f"candidate_id not found: {candidate_id!r}")
    entry = state["entries"][idx[candidate_id]]
    entry["status"] = "rejected"
    entry["rejected_at"] = utc_now_iso()
    if reason is not None:
        entry["reason"] = reason


def pending_approvals(state: dict) -> list[dict]:
    """Return entries that are notified but not yet approved or rejected."""
    return [e for e in state.get("entries", []) if e.get("status") == "notified"]


def is_processed(state: dict, candidate_id: str) -> bool:
    """Return ``True`` iff *candidate_id* has been approved or rejected."""
    idx = _entry_index(state)
    if candidate_id not in idx:
        return False
    status = state["entries"][idx[candidate_id]].get("status")
    return status in {"approved", "rejected"}
