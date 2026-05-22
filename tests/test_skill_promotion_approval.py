"""Tests for :mod:`scripts.skill_promotion_approval`.

Coverage matrix
---------------
parse_reply
-----------
1.  "approve abc12345" → action="approve", candidate_id="abc12345"
2.  "reject abc12345"  → action="reject", candidate_id="abc12345", reason=None
3.  "reject abc12345 spam reason text" → reason="spam reason text"
4.  "details abc12345" → action="details", candidate_id="abc12345"
5.  Garbage strings → None
6.  Mixed-case action verb accepted.
7.  Leading/trailing whitespace stripped.
8.  candidate_id not exactly 8 hex → None.
9.  Non-hex candidate_id → None.

handle_reply
------------
10. Unknown id → outcome "unknown_id" without state mutation.
11. Already-processed → outcome "already_processed".
12. Successful approve → state updated, approve_callback called.
13. Successful reject → state updated, reject_callback called.
14. "details" → outcome "ok" without state mutation beyond status.
15. Not-an-approval → outcome "not_an_approval".
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.skill_promotion_approval import handle_reply, parse_reply
from scripts.skill_promotion_state import (
    is_processed,
    load_state,
    mark_approved,
    record_notified,
    save_state,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fp() -> dict:
    return {"skill": "my-skill", "task_name": "do the thing", "count": 3}


def _setup_state(tmp_path: Path, cid: str = "abc12345") -> Path:
    """Create a state file with *cid* as a notified candidate."""
    sp = tmp_path / "state.json"
    state = {"entries": []}
    record_notified(state, cid, _fp())
    save_state(state, sp)
    return sp


# ---------------------------------------------------------------------------
# 1–9. parse_reply
# ---------------------------------------------------------------------------

class TestParseReply:
    def test_approve_basic(self):
        result = parse_reply("approve abc12345")
        assert result == {"action": "approve", "candidate_id": "abc12345"}

    def test_reject_no_reason(self):
        result = parse_reply("reject abc12345")
        assert result is not None
        assert result["action"] == "reject"
        assert result["candidate_id"] == "abc12345"
        assert result["reason"] is None

    def test_reject_with_reason(self):
        result = parse_reply("reject abc12345 spam reason text")
        assert result is not None
        assert result["action"] == "reject"
        assert result["reason"] == "spam reason text"

    def test_details(self):
        result = parse_reply("details abc12345")
        assert result == {"action": "details", "candidate_id": "abc12345"}

    def test_garbage_returns_none(self):
        assert parse_reply("hello world") is None
        assert parse_reply("") is None
        assert parse_reply("approve") is None
        assert parse_reply("42") is None

    def test_mixed_case_action(self):
        assert parse_reply("APPROVE abc12345") is not None
        assert parse_reply("Approve abc12345") is not None
        assert parse_reply("REJECT abc12345") is not None
        assert parse_reply("Details abc12345") is not None

    def test_mixed_case_action_normalised(self):
        result = parse_reply("APPROVE abc12345")
        assert result["action"] == "approve"

    def test_candidate_id_lowercased(self):
        result = parse_reply("approve ABC12345")
        assert result is not None
        assert result["candidate_id"] == "abc12345"

    def test_leading_trailing_whitespace_stripped(self):
        result = parse_reply("  approve abc12345  ")
        assert result is not None
        assert result["candidate_id"] == "abc12345"

    def test_7_hex_chars_rejected(self):
        assert parse_reply("approve abc1234") is None

    def test_9_hex_chars_rejected(self):
        assert parse_reply("approve abc123456") is None

    def test_non_hex_id_rejected(self):
        assert parse_reply("approve xyz!1234") is None

    def test_approve_extra_text_is_ignored(self):
        # Extra text after candidate_id on approve: still parses
        result = parse_reply("approve abc12345 some extra")
        assert result is not None
        assert result["action"] == "approve"

    def test_details_returns_no_reason_key(self):
        result = parse_reply("details abc12345")
        assert "reason" not in result


# ---------------------------------------------------------------------------
# 10–15. handle_reply
# ---------------------------------------------------------------------------

class TestHandleReply:
    def test_unknown_id_outcome(self, tmp_path):
        sp = tmp_path / "state.json"
        save_state({"entries": []}, sp)
        result = handle_reply("approve deadbeef", state_path=sp)
        assert result["outcome"] == "unknown_id"
        assert result["candidate_id"] == "deadbeef"

    def test_unknown_id_no_state_mutation(self, tmp_path):
        sp = tmp_path / "state.json"
        save_state({"entries": []}, sp)
        handle_reply("approve deadbeef", state_path=sp)
        state = load_state(sp)
        assert state["entries"] == []

    def test_already_processed_outcome(self, tmp_path):
        sp = _setup_state(tmp_path)
        # Approve first via direct state manipulation.
        state = load_state(sp)
        mark_approved(state, "abc12345")
        save_state(state, sp)
        # Now try to approve again via handle_reply.
        result = handle_reply("approve abc12345", state_path=sp)
        assert result["outcome"] == "already_processed"

    def test_successful_approve_state_updated(self, tmp_path):
        sp = _setup_state(tmp_path)
        handle_reply("approve abc12345", state_path=sp)
        state = load_state(sp)
        assert is_processed(state, "abc12345")
        entry = [e for e in state["entries"] if e["candidate_id"] == "abc12345"][0]
        assert entry["status"] == "approved"

    def test_successful_approve_callback_called(self, tmp_path):
        sp = _setup_state(tmp_path)
        called_with: list = []

        def cb(cid, trace_id):
            called_with.append((cid, trace_id))

        handle_reply("approve abc12345", state_path=sp, approve_callback=cb)
        assert len(called_with) == 1
        assert called_with[0][0] == "abc12345"

    def test_successful_approve_callback_receives_candidate_id(self, tmp_path):
        sp = _setup_state(tmp_path)
        received_ids: list[str] = []

        handle_reply(
            "approve abc12345",
            state_path=sp,
            approve_callback=lambda cid, _tid: received_ids.append(cid),
        )
        assert received_ids == ["abc12345"]

    def test_successful_reject_state_updated(self, tmp_path):
        sp = _setup_state(tmp_path)
        handle_reply("reject abc12345 not needed", state_path=sp)
        state = load_state(sp)
        assert is_processed(state, "abc12345")
        entry = [e for e in state["entries"] if e["candidate_id"] == "abc12345"][0]
        assert entry["status"] == "rejected"
        assert entry.get("reason") == "not needed"

    def test_successful_reject_callback_called(self, tmp_path):
        sp = _setup_state(tmp_path)
        called: list = []

        handle_reply(
            "reject abc12345 spam",
            state_path=sp,
            reject_callback=lambda cid, reason: called.append((cid, reason)),
        )
        assert called == [("abc12345", "spam")]

    def test_details_outcome_ok_no_state_change(self, tmp_path):
        sp = _setup_state(tmp_path)
        state_before = load_state(sp)
        result = handle_reply("details abc12345", state_path=sp)
        assert result["outcome"] == "ok"
        state_after = load_state(sp)
        assert state_before == state_after

    def test_not_an_approval_outcome(self, tmp_path):
        sp = tmp_path / "state.json"
        save_state({"entries": []}, sp)
        result = handle_reply("hello world", state_path=sp)
        assert result["outcome"] == "not_an_approval"
        assert result["action"] == "not_an_approval"

    def test_null_callbacks_ok(self, tmp_path):
        sp = _setup_state(tmp_path)
        # Must not raise when callbacks are None (the default).
        result = handle_reply("approve abc12345", state_path=sp)
        assert result["outcome"] == "ok"
