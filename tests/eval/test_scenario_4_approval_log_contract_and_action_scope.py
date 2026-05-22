# tests/eval/test_scenario_4_external_side_effect_requires_approval.py
"""Scenario 4 — External side effect requires recorded approval.

The harness must enforce that any external side effect (e.g. "send a
message") is preceded by an explicit ``record_approval`` call logged in
the task state.

This scenario uses ``record_approval`` from ``helm_state_model`` as the
authoritative approval log, and a thin guard helper to check pre-condition.
The behavioral assertions are:

1. Attempting to "send" without prior approval raises ``PermissionError``
   (guard-denied / pre-condition not met).
2. After ``record_approval(action="send", target="<addressee>",
   approved_by="kevin")`` is recorded, the same call proceeds and the
   approval is present in the task state's approval list.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from helm_state_model import new_task_state, record_approval


# ---------------------------------------------------------------------------
# Minimal inline guard helper (no external deps, no subprocess)
# ---------------------------------------------------------------------------

class ExternalSideEffectGuardError(PermissionError):
    """Raised when an external side effect is attempted without recorded approval."""


def _find_approval(state: dict, action: str, target: str) -> dict | None:
    """Return the first matching approval entry or None."""
    for entry in state.get("external_side_effect_approvals") or []:
        if isinstance(entry, dict):
            if entry.get("action") == action and entry.get("target") == target:
                return entry
    return None


def perform_send(state: dict, target: str, message: str) -> str:
    """Simulate sending a message — requires a pre-recorded approval.

    Raises :class:`ExternalSideEffectGuardError` when no approval for
    (action="send", target=target) is found in the state.

    Returns a confirmation string when the approval is present.
    """
    approval = _find_approval(state, "send", target)
    if approval is None:
        raise ExternalSideEffectGuardError(
            f"No approval recorded for action='send', target={target!r}. "
            "Call record_approval() before performing external side effects."
        )
    # Side effect "performed" (in-memory only; no real network call).
    return f"sent:{target}:{message}"


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------


def test_scenario_4_external_side_effect_requires_approval() -> None:
    """send without prior approval raises; send after record_approval proceeds."""
    state = new_task_state()
    addressee = "kevin@kailoslab.com"

    # --- Phase 1: attempt send WITHOUT approval → must raise ---
    with pytest.raises(ExternalSideEffectGuardError) as exc_info:
        perform_send(state, addressee, "weekly report attached")

    assert "No approval recorded" in str(exc_info.value), (
        "Guard error message must describe the missing approval; "
        f"got: {exc_info.value!r}"
    )
    # No spurious approval should have been injected into state.
    assert state["external_side_effect_approvals"] == [], (
        "State was mutated during the failing send attempt."
    )

    # --- Phase 2: record approval → send must succeed ---
    record_approval(state, action="send", target=addressee, approved_by="kevin")

    # Verify the approval is present.
    approvals = state["external_side_effect_approvals"]
    assert len(approvals) == 1
    assert approvals[0]["action"] == "send"
    assert approvals[0]["target"] == addressee
    assert approvals[0]["approved_by"] == "kevin"
    assert "approved_at" in approvals[0]  # ISO8601 timestamp recorded

    # Now the send must proceed without raising.
    result = perform_send(state, addressee, "weekly report attached")
    assert result.startswith("sent:"), (
        f"perform_send should return a confirmation string, got {result!r}"
    )
    assert addressee in result
