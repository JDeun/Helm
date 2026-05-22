# tests/test_task_state_control.py
"""Tests for the task-state control-flow container (Task #6).

These cover the structured control-state fields that must survive context
compaction — the "Control Flow Is Not Memory" principle from Forge applied
to Helm's task-state model. Transcript / message buffers are *memory* and
may be truncated; required_steps, completed_steps, recovered_messages, and
finalization_state are *control state* and must remain authoritative.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from helm_state_model import (
    TASK_STATE_SCHEMA_VERSION,
    is_finalized,
    load_task_state,
    mark_recovered_message,
    mark_step_completed,
    new_task_state,
    record_approval,
    record_recovered_message,
    save_task_state,
    unhandled_recovered_messages,
)


# ---------------------------------------------------------------------------
# Scenario 1: new state defaults
# ---------------------------------------------------------------------------


def test_new_state_defaults() -> None:
    state = new_task_state()
    assert state["task_state_schema_version"] == 1
    assert state["task_state_schema_version"] == TASK_STATE_SCHEMA_VERSION
    assert state["required_steps"] == []
    assert state["completed_steps"] == []
    assert state["blockers"] == []
    assert state["external_side_effect_approvals"] == []
    assert state["finalization_state"] == "pending"
    assert state["recovered_messages"] == []


# ---------------------------------------------------------------------------
# Scenario 2: loading an old state without these fields fills defaults
# ---------------------------------------------------------------------------


def test_load_old_state_fills_defaults() -> None:
    old = {"some_legacy_field": "value"}
    state = load_task_state(old)
    assert state["task_state_schema_version"] == 1
    assert state["required_steps"] == []
    assert state["completed_steps"] == []
    assert state["blockers"] == []
    assert state["external_side_effect_approvals"] == []
    assert state["finalization_state"] == "pending"
    assert state["recovered_messages"] == []
    # Legacy field must be preserved.
    assert state["some_legacy_field"] == "value"


def test_load_empty_dict_yields_defaults() -> None:
    state = load_task_state({})
    assert state["finalization_state"] == "pending"
    assert state["task_state_schema_version"] == 1


# ---------------------------------------------------------------------------
# Scenario 3: unknown extra keys are preserved on round-trip
# ---------------------------------------------------------------------------


def test_unknown_keys_preserved_on_round_trip() -> None:
    raw = {
        "some_future_field": {"nested": [1, 2, 3]},
        "another_extra": "keep me",
    }
    loaded = load_task_state(raw)
    saved = save_task_state(loaded)

    # Unknown keys must round-trip intact.
    assert saved["some_future_field"] == {"nested": [1, 2, 3]}
    assert saved["another_extra"] == "keep me"

    # Saved form must be JSON-serializable (no dataclasses leaking out).
    json.dumps(saved)

    # Re-loading the saved form must yield identical extra keys.
    reloaded = load_task_state(saved)
    assert reloaded["some_future_field"] == {"nested": [1, 2, 3]}
    assert reloaded["another_extra"] == "keep me"


# ---------------------------------------------------------------------------
# Scenario 4: mark_step_completed — add, idempotent, raise on unknown
# ---------------------------------------------------------------------------


def test_mark_step_completed_adds_step() -> None:
    state = new_task_state()
    state["required_steps"] = ["draft", "review", "send"]
    mark_step_completed(state, "draft")
    assert state["completed_steps"] == ["draft"]


def test_mark_step_completed_is_idempotent() -> None:
    state = new_task_state()
    state["required_steps"] = ["draft", "review", "send"]
    mark_step_completed(state, "draft")
    mark_step_completed(state, "draft")
    assert state["completed_steps"] == ["draft"]


def test_mark_step_completed_raises_on_unknown_step() -> None:
    state = new_task_state()
    state["required_steps"] = ["draft", "review", "send"]
    with pytest.raises(ValueError):
        mark_step_completed(state, "nonsense")
    assert state["completed_steps"] == []


# ---------------------------------------------------------------------------
# Scenario 5: is_finalized requires both conditions
# ---------------------------------------------------------------------------


def test_is_finalized_false_when_pending_even_if_all_steps_done() -> None:
    state = new_task_state()
    state["required_steps"] = ["draft", "send"]
    mark_step_completed(state, "draft")
    mark_step_completed(state, "send")
    # finalization_state is still "pending" → not finalized
    assert state["finalization_state"] == "pending"
    assert is_finalized(state) is False


def test_is_finalized_false_when_finalized_flag_but_steps_incomplete() -> None:
    state = new_task_state()
    state["required_steps"] = ["draft", "send"]
    mark_step_completed(state, "draft")
    state["finalization_state"] = "finalized"
    # missing "send"
    assert is_finalized(state) is False


def test_is_finalized_true_only_when_both_conditions_hold() -> None:
    state = new_task_state()
    state["required_steps"] = ["draft", "send"]
    mark_step_completed(state, "draft")
    mark_step_completed(state, "send")
    state["finalization_state"] = "finalized"
    assert is_finalized(state) is True


def test_is_finalized_false_for_other_finalization_states() -> None:
    state = new_task_state()
    state["required_steps"] = ["draft"]
    mark_step_completed(state, "draft")
    for fs in ("pending", "in_progress", "abandoned"):
        state["finalization_state"] = fs
        assert is_finalized(state) is False, fs


# ---------------------------------------------------------------------------
# Scenario 6: Telegram recovered-context regression scenario
#
# Recovered-context regression scenario: simulates the bug where a
# Telegram message containing an un-acted request was lost across context
# compaction. After compaction blanks the message buffer (transcript),
# the structured `recovered_messages` list must still report the
# active_unhandled entry — proving that control state lives outside the
# transcript and survives compaction.
# ---------------------------------------------------------------------------


def test_recovered_messages_survive_compaction() -> None:
    """recovered-context regression scenario."""
    state = new_task_state()
    # Simulate a transcript / messages buffer alongside control state.
    state["transcript"] = [
        {"role": "user", "content": "please send report"},
        {"role": "assistant", "content": "ack"},
    ]
    state["messages"] = list(state["transcript"])  # alias the model sometimes uses

    record_recovered_message(
        state,
        source="telegram",
        message_id="tg-1001",
        action_verb="send_report",
        topic_continuity_score=0.82,
    )
    record_recovered_message(
        state,
        source="telegram",
        message_id="tg-1002",
        action_verb="check_status",
        topic_continuity_score=0.40,
    )
    # Mark one as handled, leaving tg-1001 active_unhandled.
    mark_recovered_message(state, "tg-1002", "handled")

    # Sanity: both messages stored.
    assert len(state["recovered_messages"]) == 2

    # Compaction simulation: blank out the transcript / messages buffer.
    state["transcript"] = []
    state["messages"] = []

    # The recovered-message list must survive compaction.
    assert len(state["recovered_messages"]) == 2

    unhandled = unhandled_recovered_messages(state)
    assert len(unhandled) == 1
    assert unhandled[0]["message_id"] == "tg-1001"
    assert unhandled[0]["status"] == "active_unhandled"
    assert unhandled[0]["source"] == "telegram"
    assert unhandled[0]["action_verb"] == "send_report"


def test_unhandled_recovered_messages_filters_status() -> None:
    state = new_task_state()
    record_recovered_message(state, "telegram", "a", "do_thing", 0.5)
    record_recovered_message(state, "telegram", "b", "do_other", 0.6)
    record_recovered_message(state, "telegram", "c", None, None)
    mark_recovered_message(state, "a", "handled")
    mark_recovered_message(state, "b", "superseded")
    # c stays active_unhandled
    out = unhandled_recovered_messages(state)
    assert [m["message_id"] for m in out] == ["c"]


# ---------------------------------------------------------------------------
# Scenario 7: record_approval — iso8601 timestamp, append in order
# ---------------------------------------------------------------------------


_ISO8601_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
)


def test_record_approval_writes_iso8601_and_preserves_order() -> None:
    state = new_task_state()
    record_approval(state, "send_email", "user@example.com", "kevin")
    record_approval(state, "post_telegram", "chat-42", "kevin")
    record_approval(state, "write_sheet", "sheet-abc/range-A1", "operator")

    approvals = state["external_side_effect_approvals"]
    assert len(approvals) == 3
    assert [a["action"] for a in approvals] == [
        "send_email",
        "post_telegram",
        "write_sheet",
    ]
    assert [a["target"] for a in approvals] == [
        "user@example.com",
        "chat-42",
        "sheet-abc/range-A1",
    ]
    assert [a["approved_by"] for a in approvals] == ["kevin", "kevin", "operator"]

    for a in approvals:
        assert "approved_at" in a
        assert _ISO8601_RE.match(a["approved_at"]), a["approved_at"]
        # Must be parseable.
        parsed = datetime.fromisoformat(a["approved_at"].replace("Z", "+00:00"))
        assert parsed.tzinfo is not None


# ---------------------------------------------------------------------------
# Scenario 8: record_recovered_message rejects duplicate ids
# ---------------------------------------------------------------------------


def test_record_recovered_message_rejects_duplicate_ids() -> None:
    state = new_task_state()
    record_recovered_message(state, "telegram", "tg-1", "send", 0.7)
    with pytest.raises(ValueError):
        record_recovered_message(state, "telegram", "tg-1", "send", 0.7)
    # Original entry untouched.
    assert len(state["recovered_messages"]) == 1
    assert state["recovered_messages"][0]["status"] == "active_unhandled"


def test_record_recovered_message_writes_iso8601_since_on_status_change() -> None:
    state = new_task_state()
    record_recovered_message(state, "telegram", "tg-1", "send", 0.7)
    entry = state["recovered_messages"][0]
    assert entry["source"] == "telegram"
    assert entry["message_id"] == "tg-1"
    assert entry["action_verb"] == "send"
    assert entry["topic_continuity_score"] == 0.7
    assert entry["status"] == "active_unhandled"


def test_record_recovered_message_accepts_none_verb_and_score() -> None:
    state = new_task_state()
    record_recovered_message(state, "telegram", "tg-x", None, None)
    entry = state["recovered_messages"][0]
    assert entry["action_verb"] is None
    assert entry["topic_continuity_score"] is None


# ---------------------------------------------------------------------------
# Scenario 9: mark_recovered_message sets status / raises on unknown
# ---------------------------------------------------------------------------


def test_mark_recovered_message_sets_status() -> None:
    state = new_task_state()
    record_recovered_message(state, "telegram", "tg-1", "send", 0.7)
    mark_recovered_message(state, "tg-1", "handled")
    assert state["recovered_messages"][0]["status"] == "handled"
    mark_recovered_message(state, "tg-1", "superseded")
    assert state["recovered_messages"][0]["status"] == "superseded"


def test_mark_recovered_message_raises_on_unknown_id() -> None:
    state = new_task_state()
    record_recovered_message(state, "telegram", "tg-1", "send", 0.7)
    with pytest.raises(ValueError):
        mark_recovered_message(state, "tg-does-not-exist", "handled")


def test_mark_recovered_message_supports_all_documented_statuses() -> None:
    state = new_task_state()
    for mid in ("a", "b", "c", "d"):
        record_recovered_message(state, "telegram", mid, "do", 0.5)
    mark_recovered_message(state, "a", "handled")
    mark_recovered_message(state, "b", "superseded")
    mark_recovered_message(state, "c", "active_unhandled")
    mark_recovered_message(state, "d", "blocked_by_truncation")
    statuses = {m["message_id"]: m["status"] for m in state["recovered_messages"]}
    assert statuses == {
        "a": "handled",
        "b": "superseded",
        "c": "active_unhandled",
        "d": "blocked_by_truncation",
    }
