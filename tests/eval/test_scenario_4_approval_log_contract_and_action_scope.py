# tests/eval/test_scenario_4_approval_log_contract_and_action_scope.py
"""Scenario 4 — Approval-log contract + action-scope gate for external sends.

Scope choice (honest framing — Review-2, Option B)
---------------------------------------------------
The original spec called for a scenario that fails if the *production*
approval gate regresses. After auditing the codebase we confirmed that
**no production entrypoint currently consults
``state["external_side_effect_approvals"]`` before performing a send**:

* The only callers of ``record_approval`` today are this scenario file
  and the Task 6 unit tests (``tests/test_task_state_control.py``).
* ``scripts/action_scope.py`` is the closest analog to a real
  external-send gate — it enforces that the message's locked_scope must
  be ``EXTERNAL_SEND`` to act on ``telegram_outbound`` — but it does
  not consult the approval log.

Rather than pretend, this scenario is explicitly scoped as a
**contract test for two real surfaces**:

1. The ``record_approval`` / ``external_side_effect_approvals`` API
   contract from ``helm_state_model`` (Task 6) — the durable approval
   log shape and ordering.
2. The ``scripts.action_scope`` gate for ``telegram_outbound`` — the
   real production gate that *would* deny a send if the user's
   message did not carry an EXTERNAL_SEND-class verb.

When a production "send" entrypoint is added that consults the
approval log, this scenario should be expanded (or replaced) with a
direct call to that entrypoint. Until then, the inline
``perform_send_contract`` helper documents the contract the production
code is expected to honour.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from helm_state_model import new_task_state, record_approval
from scripts.action_scope import (
    ActionScopeKind,
    attempted_action_allowed,
    evaluate as scope_evaluate,
)


# ---------------------------------------------------------------------------
# Contract helper (documents the shape a production "send" entrypoint must take)
# ---------------------------------------------------------------------------

class ExternalSideEffectGuardError(PermissionError):
    """Raised when an external side effect is attempted without recorded approval."""


def _find_approval(state: dict, action: str, target: str) -> dict | None:
    for entry in state.get("external_side_effect_approvals") or []:
        if isinstance(entry, dict):
            if entry.get("action") == action and entry.get("target") == target:
                return entry
    return None


def perform_send_contract(state: dict, target: str, message: str) -> str:
    """Contract for a production "send" entrypoint.

    The two gates production code must combine:

      1. Action-scope gate (real production module): the user message's
         locked_scope must be EXTERNAL_SEND for the ``telegram_outbound``
         resource.
      2. Approval log gate (this contract): an entry in
         ``state['external_side_effect_approvals']`` with
         ``action='send'`` and ``target=target`` must exist.

    Raises :class:`ExternalSideEffectGuardError` when the approval gate
    fails. The action-scope gate is tested directly via the real
    ``attempted_action_allowed`` API in the scenario body.
    """
    approval = _find_approval(state, "send", target)
    if approval is None:
        raise ExternalSideEffectGuardError(
            f"No approval recorded for action='send', target={target!r}. "
            "Call record_approval() before performing external side effects."
        )
    return f"sent:{target}:{message}"


# ---------------------------------------------------------------------------
# Test (uses the in_memory_state fixture from conftest.py to exercise
# scaffold wiring and prove the fixture is not dead code)
# ---------------------------------------------------------------------------


def test_scenario_4_approval_log_contract_and_action_scope(
    in_memory_state: dict,
) -> None:
    """Approval log + action-scope gate jointly govern external sends."""
    state = in_memory_state
    addressee = "kevin@kailoslab.com"

    # ------------------------------------------------------------------
    # Part 1 — Real production action-scope gate refuses without verb
    # ------------------------------------------------------------------
    # A user message with NO external-send verb must not unlock
    # telegram_outbound.
    inspect_only_message = "오늘 일정 좀 보여줘"  # "show me today's schedule"
    decision = scope_evaluate(inspect_only_message)
    allowed_send, reason_send = attempted_action_allowed(
        decision,
        ActionScopeKind.EXTERNAL_SEND,
        resource="telegram_outbound",
    )
    assert allowed_send is False, (
        "Real action-scope gate should refuse EXTERNAL_SEND for an inspect-only message; "
        f"decision={decision.as_dict()}"
    )
    assert reason_send is not None and "forbids" in reason_send, (
        f"Expected a 'forbids' refusal reason from the action-scope gate, got: {reason_send!r}"
    )

    # ------------------------------------------------------------------
    # Part 2 — Approval-log contract: send without record_approval raises
    # ------------------------------------------------------------------
    with pytest.raises(ExternalSideEffectGuardError) as exc_info:
        perform_send_contract(state, addressee, "weekly report attached")
    assert "No approval recorded" in str(exc_info.value)
    assert state["external_side_effect_approvals"] == [], (
        "State was mutated during the failing send attempt."
    )

    # ------------------------------------------------------------------
    # Part 3 — A real EXTERNAL_SEND message unlocks the scope gate
    # ------------------------------------------------------------------
    send_message = '"weekly-report" 텔레그램으로 보내줘'  # explicit send verb + quoted target
    send_decision = scope_evaluate(send_message)
    allowed_send_2, reason_send_2 = attempted_action_allowed(
        send_decision,
        ActionScopeKind.EXTERNAL_SEND,
        resource="telegram_outbound",
    )
    assert allowed_send_2 is True, (
        f"Action-scope gate should allow EXTERNAL_SEND on telegram_outbound when "
        f"the message carries a send verb; reason={reason_send_2!r}; "
        f"decision={send_decision.as_dict()}"
    )

    # ------------------------------------------------------------------
    # Part 4 — record_approval unlocks the approval-log gate
    # ------------------------------------------------------------------
    record_approval(state, action="send", target=addressee, approved_by="kevin")

    approvals = state["external_side_effect_approvals"]
    assert len(approvals) == 1
    assert approvals[0]["action"] == "send"
    assert approvals[0]["target"] == addressee
    assert approvals[0]["approved_by"] == "kevin"
    assert "approved_at" in approvals[0]

    # With both gates satisfied, the send proceeds.
    result = perform_send_contract(state, addressee, "weekly report attached")
    assert result.startswith("sent:")
    assert addressee in result
