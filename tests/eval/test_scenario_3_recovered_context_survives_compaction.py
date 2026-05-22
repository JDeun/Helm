# tests/eval/test_scenario_3_recovered_context_survives_compaction.py
"""Scenario 3 — Recovered Telegram context: active_unhandled request preserved across compaction.

Regression scenario for the Forge "Control Flow Is Not Memory" principle.

A Telegram message containing an un-acted request was recorded with
``record_recovered_message(..., status="active_unhandled")``.  After context
compaction (simulated by blanking out transcript/messages buffers),
``unhandled_recovered_messages(state)`` must still return the active_unhandled
entry.  The recovered-message list lives outside the transcript and is
authoritative regardless of what happened to the message history.

This test duplicates the regression spirit of Task 6's
``test_recovered_messages_survive_compaction`` but lives at the eval/scenario
level as a named reliability scenario for the Forge후보 D suite.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
import sys
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from helm_state_model import (
    mark_recovered_message,
    new_task_state,
    record_recovered_message,
    unhandled_recovered_messages,
)


def test_scenario_3_recovered_context_survives_compaction() -> None:
    """active_unhandled recovered message must survive context compaction."""
    state = new_task_state()

    # Populate a transcript buffer (simulating pre-compaction in-memory history).
    state["transcript"] = [
        {"role": "user", "content": "send the weekly report"},
        {"role": "assistant", "content": "acknowledged"},
    ]
    state["messages"] = list(state["transcript"])

    # Record two recovered messages from the Telegram intake bridge.
    record_recovered_message(
        state,
        source="telegram",
        message_id="tg-eval-001",
        action_verb="send_report",
        topic_continuity_score=0.85,
    )
    record_recovered_message(
        state,
        source="telegram",
        message_id="tg-eval-002",
        action_verb="check_balance",
        topic_continuity_score=0.30,
    )

    # Mark the second message as handled (it was acted upon before compaction).
    mark_recovered_message(state, "tg-eval-002", "handled")

    # Sanity: both entries exist in the recovered_messages list.
    assert len(state["recovered_messages"]) == 2

    # ---- Simulate compaction: blank the transcript / messages buffer ----
    state["transcript"] = []
    state["messages"] = []

    # After compaction, the recovered_messages list must be unchanged.
    assert len(state["recovered_messages"]) == 2, (
        "recovered_messages count changed after compaction simulation; "
        "control state must live outside the transcript."
    )

    # The active_unhandled entry must still be visible via the public helper.
    unhandled = unhandled_recovered_messages(state)
    assert len(unhandled) == 1, (
        f"Expected 1 active_unhandled entry after compaction, got {len(unhandled)}; "
        f"unhandled={unhandled}"
    )
    entry = unhandled[0]
    assert entry["message_id"] == "tg-eval-001", (
        f"Wrong message_id survived: expected 'tg-eval-001', got {entry['message_id']!r}"
    )
    assert entry["status"] == "active_unhandled"
    assert entry["source"] == "telegram"
    assert entry["action_verb"] == "send_report"
    assert entry["topic_continuity_score"] == 0.85

    # The returned list must be a copy — mutating it cannot corrupt state.
    unhandled[0]["status"] = "tampered"
    assert state["recovered_messages"][0]["status"] == "active_unhandled", (
        "unhandled_recovered_messages() returned a live reference instead of a copy; "
        "mutations to the return value should not affect state."
    )
