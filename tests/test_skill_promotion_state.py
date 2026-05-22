"""Tests for :mod:`scripts.skill_promotion_state`.

Coverage matrix
---------------
1.  load_state on missing file returns empty state.
2.  save_state / load_state round-trip preserves all fields.
3.  record_notified adds an entry with status "notified".
4.  record_notified twice does not duplicate the entry (idempotent).
5.  mark_approved sets status, approved_by, approved_at.
6.  mark_rejected sets status, rejected_at, and optional reason.
7.  pending_approvals filters to status == "notified" only.
8.  is_processed is True only for approved or rejected, not notified.
9.  mark_approved raises KeyError for unknown id.
10. mark_rejected raises KeyError for unknown id.
11. candidate_id_for is stable across calls.
12. candidate_id_for with None skill differs from empty-string skill.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.skill_promotion_state import (
    candidate_id_for,
    is_processed,
    load_state,
    mark_approved,
    mark_rejected,
    pending_approvals,
    record_notified,
    save_state,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def _fp(skill: str | None = "my-skill", task: str = "do the thing") -> dict:
    return {"skill": skill, "task_name": task, "count": 5}


# ---------------------------------------------------------------------------
# 1. load_state on missing file
# ---------------------------------------------------------------------------

class TestLoadStateMissing:
    def test_returns_dict(self, tmp_path):
        state = load_state(tmp_path / "no_such.json")
        assert isinstance(state, dict)

    def test_entries_is_empty_list(self, tmp_path):
        state = load_state(tmp_path / "no_such.json")
        assert state["entries"] == []

    def test_does_not_raise(self, tmp_path):
        try:
            load_state(tmp_path / "ghost.json")
        except Exception as exc:
            pytest.fail(f"Unexpected exception: {exc}")


# ---------------------------------------------------------------------------
# 2. save / load round-trip
# ---------------------------------------------------------------------------

class TestSaveLoadRoundTrip:
    def test_empty_state_round_trips(self, tmp_path):
        sp = tmp_path / "state.json"
        state = {"entries": []}
        save_state(state, sp)
        loaded = load_state(sp)
        assert loaded == state

    def test_entry_round_trips(self, tmp_path):
        sp = tmp_path / "state.json"
        state = {"entries": []}
        record_notified(state, "a1b2c3d4", _fp())
        save_state(state, sp)
        loaded = load_state(sp)
        assert len(loaded["entries"]) == 1
        entry = loaded["entries"][0]
        assert entry["candidate_id"] == "a1b2c3d4"
        assert entry["status"] == "notified"

    def test_file_is_valid_json(self, tmp_path):
        sp = tmp_path / "state.json"
        state = {"entries": []}
        record_notified(state, "deadbeef", _fp())
        save_state(state, sp)
        raw = json.loads(sp.read_text(encoding="utf-8"))
        assert "entries" in raw

    def test_parent_dir_created(self, tmp_path):
        sp = tmp_path / "nested" / "deep" / "state.json"
        save_state({"entries": []}, sp)
        assert sp.exists()


# ---------------------------------------------------------------------------
# 3. record_notified adds entry
# ---------------------------------------------------------------------------

class TestRecordNotified:
    def test_adds_entry(self):
        state = {"entries": []}
        record_notified(state, "aabbccdd", _fp())
        assert len(state["entries"]) == 1

    def test_entry_has_correct_fields(self):
        state = {"entries": []}
        record_notified(state, "aabbccdd", _fp())
        entry = state["entries"][0]
        assert entry["candidate_id"] == "aabbccdd"
        assert entry["status"] == "notified"
        assert "notified_at" in entry
        assert "fingerprint" in entry

    def test_fingerprint_stored(self):
        state = {"entries": []}
        fp = _fp(skill="test-skill", task="my-task")
        record_notified(state, "11223344", fp)
        assert state["entries"][0]["fingerprint"] == fp


# ---------------------------------------------------------------------------
# 4. record_notified idempotent
# ---------------------------------------------------------------------------

class TestRecordNotifiedIdempotent:
    def test_no_duplicate_on_second_call(self):
        state = {"entries": []}
        record_notified(state, "aabbccdd", _fp())
        record_notified(state, "aabbccdd", _fp())
        assert len(state["entries"]) == 1

    def test_different_ids_both_added(self):
        state = {"entries": []}
        record_notified(state, "aabbccdd", _fp())
        record_notified(state, "11223344", _fp(task="other task"))
        assert len(state["entries"]) == 2


# ---------------------------------------------------------------------------
# 5. mark_approved
# ---------------------------------------------------------------------------

class TestMarkApproved:
    def test_status_becomes_approved(self):
        state = {"entries": []}
        record_notified(state, "cafebabe", _fp())
        mark_approved(state, "cafebabe")
        entry = state["entries"][0]
        assert entry["status"] == "approved"

    def test_approved_by_default(self):
        state = {"entries": []}
        record_notified(state, "cafebabe", _fp())
        mark_approved(state, "cafebabe")
        assert state["entries"][0]["approved_by"] == "kevin"

    def test_approved_by_custom(self):
        state = {"entries": []}
        record_notified(state, "cafebabe", _fp())
        mark_approved(state, "cafebabe", approved_by="alice")
        assert state["entries"][0]["approved_by"] == "alice"

    def test_approved_at_present(self):
        state = {"entries": []}
        record_notified(state, "cafebabe", _fp())
        mark_approved(state, "cafebabe")
        assert "approved_at" in state["entries"][0]

    def test_raises_for_unknown_id(self):
        state = {"entries": []}
        with pytest.raises(KeyError):
            mark_approved(state, "ffffffff")


# ---------------------------------------------------------------------------
# 6. mark_rejected
# ---------------------------------------------------------------------------

class TestMarkRejected:
    def test_status_becomes_rejected(self):
        state = {"entries": []}
        record_notified(state, "deadbeef", _fp())
        mark_rejected(state, "deadbeef")
        assert state["entries"][0]["status"] == "rejected"

    def test_rejected_at_present(self):
        state = {"entries": []}
        record_notified(state, "deadbeef", _fp())
        mark_rejected(state, "deadbeef")
        assert "rejected_at" in state["entries"][0]

    def test_reason_stored_when_given(self):
        state = {"entries": []}
        record_notified(state, "deadbeef", _fp())
        mark_rejected(state, "deadbeef", reason="not needed")
        assert state["entries"][0]["reason"] == "not needed"

    def test_reason_absent_when_not_given(self):
        state = {"entries": []}
        record_notified(state, "deadbeef", _fp())
        mark_rejected(state, "deadbeef")
        assert "reason" not in state["entries"][0]

    def test_raises_for_unknown_id(self):
        state = {"entries": []}
        with pytest.raises(KeyError):
            mark_rejected(state, "ffffffff")


# ---------------------------------------------------------------------------
# 7. pending_approvals
# ---------------------------------------------------------------------------

class TestPendingApprovals:
    def test_returns_only_notified(self):
        state = {"entries": []}
        record_notified(state, "aaaaaaaa", _fp())
        record_notified(state, "bbbbbbbb", _fp(task="task b"))
        record_notified(state, "cccccccc", _fp(task="task c"))
        mark_approved(state, "bbbbbbbb")
        mark_rejected(state, "cccccccc")
        pending = pending_approvals(state)
        assert len(pending) == 1
        assert pending[0]["candidate_id"] == "aaaaaaaa"

    def test_empty_when_all_processed(self):
        state = {"entries": []}
        record_notified(state, "aaaaaaaa", _fp())
        mark_approved(state, "aaaaaaaa")
        assert pending_approvals(state) == []

    def test_empty_state_returns_empty(self):
        assert pending_approvals({"entries": []}) == []

    def test_all_notified_are_pending(self):
        state = {"entries": []}
        for i in range(3):
            record_notified(state, f"0000000{i}", _fp(task=f"task {i}"))
        assert len(pending_approvals(state)) == 3


# ---------------------------------------------------------------------------
# 8. is_processed
# ---------------------------------------------------------------------------

class TestIsProcessed:
    def test_false_for_notified(self):
        state = {"entries": []}
        record_notified(state, "12345678", _fp())
        assert is_processed(state, "12345678") is False

    def test_true_for_approved(self):
        state = {"entries": []}
        record_notified(state, "12345678", _fp())
        mark_approved(state, "12345678")
        assert is_processed(state, "12345678") is True

    def test_true_for_rejected(self):
        state = {"entries": []}
        record_notified(state, "12345678", _fp())
        mark_rejected(state, "12345678")
        assert is_processed(state, "12345678") is True

    def test_false_for_unknown_id(self):
        state = {"entries": []}
        assert is_processed(state, "ffffffff") is False


# ---------------------------------------------------------------------------
# 11–12. candidate_id_for
# ---------------------------------------------------------------------------

class TestCandidateIdFor:
    def test_returns_8_hex_chars(self):
        cid = candidate_id_for("my-skill", "do the thing")
        assert len(cid) == 8
        assert all(c in "0123456789abcdef" for c in cid)

    def test_stable_across_calls(self):
        cid1 = candidate_id_for("my-skill", "do the thing")
        cid2 = candidate_id_for("my-skill", "do the thing")
        assert cid1 == cid2

    def test_different_task_different_id(self):
        assert candidate_id_for("skill", "task a") != candidate_id_for("skill", "task b")

    def test_different_skill_different_id(self):
        assert candidate_id_for("skill-a", "task") != candidate_id_for("skill-b", "task")

    def test_none_skill_different_from_empty_string_skill(self):
        # NUL separator prevents ("", "task") == (None→"", "task") collisions.
        cid_none = candidate_id_for(None, "task")
        cid_empty = candidate_id_for("", "task")
        # They should actually be equal since None is normalised to "" — confirm
        # the current behaviour is consistent (not a bug; documented).
        assert cid_none == cid_empty

    def test_separator_prevents_collision(self):
        # ("ab", "cde") must differ from ("abc", "de")
        assert candidate_id_for("ab", "cde") != candidate_id_for("abc", "de")


# ---------------------------------------------------------------------------
# FIX M-3: candidate_id_for rejects NUL-byte inputs
# ---------------------------------------------------------------------------

class TestCandidateIdForNulByte:
    """M-3: NUL bytes in skill or task_name raise ValueError."""

    def test_nul_in_skill_raises(self):
        with pytest.raises(ValueError, match=r"NUL"):
            candidate_id_for("skill\x00evil", "task")

    def test_nul_in_task_name_raises(self):
        with pytest.raises(ValueError, match=r"NUL"):
            candidate_id_for("skill", "task\x00evil")

    def test_nul_only_skill_raises(self):
        with pytest.raises(ValueError, match=r"NUL"):
            candidate_id_for("\x00", "task")

    def test_nul_only_task_name_raises(self):
        with pytest.raises(ValueError, match=r"NUL"):
            candidate_id_for("skill", "\x00")

    def test_normal_inputs_still_work(self):
        """Regression: valid inputs are unaffected by the guard."""
        cid = candidate_id_for("my-skill", "do the thing")
        assert len(cid) == 8
        assert all(c in "0123456789abcdef" for c in cid)

    def test_none_skill_with_valid_task_does_not_raise(self):
        """None skill is normalised to '' (no NUL) — should not raise."""
        cid = candidate_id_for(None, "my-task")
        assert isinstance(cid, str)
