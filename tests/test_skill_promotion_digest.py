"""Tests for :mod:`scripts.skill_promotion_digest`.

Coverage matrix
---------------
1.  build_digest returns the expected top-level keys.
2.  candidates list is correct from synthetic traces.
3.  reminder vs new status classification.
4.  cap at max_candidates with "+N more" in summary_text.
5.  cadence field passes through verbatim.
6.  summary_text length ≤ 800 bytes.
7.  integration: build_digest persists new candidates to state (tmp_path).
8.  already-processed candidates are excluded.
9.  approval_reply_examples is a non-empty list of strings.
10. empty traces dir → empty candidates list.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.skill_promotion_digest import build_digest
from scripts.skill_promotion_state import (
    load_state,
    mark_approved,
    record_notified,
    save_state,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_trace(
    task_id: str,
    *,
    skill: str | None = "my-skill",
    input_summary: str = "do the thing",
    outcome: str = "completed",
    started_at: str = "2026-01-01T00:00:00Z",
) -> dict:
    return {
        "taskId": task_id,
        "startedAt": started_at,
        "profile": "service_ops",
        "skill": skill,
        "inputSummary": input_summary,
        "toolSequence": [],
        "changedFiles": [],
        "validationGates": [],
        "failureSignature": None,
        "outcome": outcome,
        "replayHint": None,
        "skillCandidate": False,
    }


def _write_trace(traces_dir: Path, task_id: str, data: dict) -> None:
    traces_dir.mkdir(parents=True, exist_ok=True)
    (traces_dir / f"{task_id}.json").write_text(json.dumps(data), encoding="utf-8")


def _populate_traces(traces_dir: Path, skill: str = "my-skill", task: str = "do the thing", n: int = 3) -> None:
    for i in range(n):
        _write_trace(
            traces_dir, f"{skill}-{task[:4].replace(' ', '_')}-{i}",
            _make_trace(f"{skill}-{i}", skill=skill, input_summary=task),
        )


# ---------------------------------------------------------------------------
# 1. Top-level keys
# ---------------------------------------------------------------------------

class TestBuildDigestKeys:
    def test_all_expected_keys_present(self, tmp_path):
        payload = build_digest(
            traces_dir=tmp_path / "traces",
            state_path=tmp_path / "state.json",
        )
        assert "generated_at" in payload
        assert "cadence" in payload
        assert "candidates" in payload
        assert "summary_text" in payload
        assert "approval_reply_examples" in payload

    def test_candidates_is_list(self, tmp_path):
        payload = build_digest(
            traces_dir=tmp_path / "traces",
            state_path=tmp_path / "state.json",
        )
        assert isinstance(payload["candidates"], list)

    def test_approval_reply_examples_is_list(self, tmp_path):
        payload = build_digest(
            traces_dir=tmp_path / "traces",
            state_path=tmp_path / "state.json",
        )
        assert isinstance(payload["approval_reply_examples"], list)
        assert len(payload["approval_reply_examples"]) > 0


# ---------------------------------------------------------------------------
# 2. Candidates from synthetic traces
# ---------------------------------------------------------------------------

class TestBuildDigestCandidates:
    def test_candidate_has_expected_keys(self, tmp_path):
        traces_dir = tmp_path / "traces"
        _populate_traces(traces_dir)
        payload = build_digest(traces_dir=traces_dir, state_path=tmp_path / "state.json")
        assert len(payload["candidates"]) >= 1
        c = payload["candidates"][0]
        assert "candidate_id" in c
        assert "skill" in c
        assert "task_name" in c
        assert "count" in c
        assert "sample_trace_ids" in c
        assert "status" in c

    def test_candidate_id_is_8_hex(self, tmp_path):
        traces_dir = tmp_path / "traces"
        _populate_traces(traces_dir)
        payload = build_digest(traces_dir=traces_dir, state_path=tmp_path / "state.json")
        for c in payload["candidates"]:
            assert len(c["candidate_id"]) == 8
            assert all(ch in "0123456789abcdef" for ch in c["candidate_id"])

    def test_count_reflects_trace_count(self, tmp_path):
        traces_dir = tmp_path / "traces"
        _populate_traces(traces_dir, n=5)
        payload = build_digest(traces_dir=traces_dir, state_path=tmp_path / "state.json", min_success=3)
        assert payload["candidates"][0]["count"] == 5

    def test_empty_traces_yields_empty_candidates(self, tmp_path):
        payload = build_digest(
            traces_dir=tmp_path / "no_traces",
            state_path=tmp_path / "state.json",
        )
        assert payload["candidates"] == []


# ---------------------------------------------------------------------------
# 3. Reminder vs new status
# ---------------------------------------------------------------------------

class TestReminderVsNew:
    def test_first_digest_marks_new(self, tmp_path):
        traces_dir = tmp_path / "traces"
        _populate_traces(traces_dir)
        payload = build_digest(traces_dir=traces_dir, state_path=tmp_path / "state.json")
        for c in payload["candidates"]:
            assert c["status"] == "new"

    def test_second_digest_marks_reminder(self, tmp_path):
        traces_dir = tmp_path / "traces"
        state_path = tmp_path / "state.json"
        _populate_traces(traces_dir)
        # First run: marks as new and persists.
        build_digest(traces_dir=traces_dir, state_path=state_path)
        # Second run: same candidate should now be reminder.
        payload2 = build_digest(traces_dir=traces_dir, state_path=state_path)
        for c in payload2["candidates"]:
            assert c["status"] == "reminder"


# ---------------------------------------------------------------------------
# 4. Cap at max_candidates with "+N more"
# ---------------------------------------------------------------------------

class TestMaxCandidatesCap:
    def _write_distinct_candidates(self, traces_dir: Path, n: int) -> None:
        """Write n distinct (skill, task) pairs, each with 3 successes."""
        for i in range(n):
            for j in range(3):
                _write_trace(
                    traces_dir,
                    f"cand{i}-trace{j}",
                    _make_trace(f"cand{i}-trace{j}", skill=f"skill-{i}", input_summary=f"task {i}"),
                )

    def test_candidates_capped(self, tmp_path):
        traces_dir = tmp_path / "traces"
        self._write_distinct_candidates(traces_dir, 8)
        payload = build_digest(
            traces_dir=traces_dir,
            state_path=tmp_path / "state.json",
            max_candidates=3,
        )
        assert len(payload["candidates"]) == 3

    def test_summary_text_contains_more_line(self, tmp_path):
        traces_dir = tmp_path / "traces"
        self._write_distinct_candidates(traces_dir, 8)
        payload = build_digest(
            traces_dir=traces_dir,
            state_path=tmp_path / "state.json",
            max_candidates=3,
        )
        assert "+ 5 more" in payload["summary_text"]

    def test_no_more_line_when_under_cap(self, tmp_path):
        traces_dir = tmp_path / "traces"
        self._write_distinct_candidates(traces_dir, 2)
        payload = build_digest(
            traces_dir=traces_dir,
            state_path=tmp_path / "state.json",
            max_candidates=5,
        )
        assert "more" not in payload["summary_text"]


# ---------------------------------------------------------------------------
# 5. Cadence field
# ---------------------------------------------------------------------------

class TestCadenceField:
    def test_daily_cadence(self, tmp_path):
        payload = build_digest(
            traces_dir=tmp_path / "t",
            state_path=tmp_path / "s.json",
            cadence="daily",
        )
        assert payload["cadence"] == "daily"

    def test_weekly_cadence(self, tmp_path):
        payload = build_digest(
            traces_dir=tmp_path / "t",
            state_path=tmp_path / "s.json",
            cadence="weekly",
        )
        assert payload["cadence"] == "weekly"

    def test_custom_cadence_passes_through(self, tmp_path):
        payload = build_digest(
            traces_dir=tmp_path / "t",
            state_path=tmp_path / "s.json",
            cadence="monthly",
        )
        assert payload["cadence"] == "monthly"


# ---------------------------------------------------------------------------
# 6. summary_text ≤ 800 bytes
# ---------------------------------------------------------------------------

class TestSummaryTextLength:
    def _write_many_candidates(self, traces_dir: Path, n: int) -> None:
        for i in range(n):
            for j in range(3):
                long_task = f"a very long task name that goes on and on for candidate {i} iteration {j}"
                _write_trace(
                    traces_dir,
                    f"long{i}-t{j}",
                    _make_trace(f"long{i}-t{j}", skill=f"skill-long-{i}", input_summary=long_task),
                )

    def test_summary_text_within_byte_budget(self, tmp_path):
        traces_dir = tmp_path / "traces"
        self._write_many_candidates(traces_dir, 20)
        payload = build_digest(
            traces_dir=traces_dir,
            state_path=tmp_path / "state.json",
            max_candidates=5,
        )
        assert len(payload["summary_text"].encode("utf-8")) <= 800

    def test_summary_text_nonempty(self, tmp_path):
        payload = build_digest(
            traces_dir=tmp_path / "t",
            state_path=tmp_path / "s.json",
        )
        assert isinstance(payload["summary_text"], str)
        assert len(payload["summary_text"]) >= 1


# ---------------------------------------------------------------------------
# 7. Integration: build_digest persists new candidates to state
# ---------------------------------------------------------------------------

class TestPersistsState:
    def test_new_candidates_recorded_in_state_file(self, tmp_path):
        traces_dir = tmp_path / "traces"
        state_path = tmp_path / "state.json"
        _populate_traces(traces_dir)
        payload = build_digest(traces_dir=traces_dir, state_path=state_path)
        # State file must exist.
        assert state_path.exists()
        state = load_state(state_path)
        assert len(state["entries"]) >= 1
        # IDs in state match those in payload.
        state_ids = {e["candidate_id"] for e in state["entries"]}
        payload_ids = {c["candidate_id"] for c in payload["candidates"]}
        assert payload_ids.issubset(state_ids)

    def test_reminders_not_duplicated_in_state(self, tmp_path):
        traces_dir = tmp_path / "traces"
        state_path = tmp_path / "state.json"
        _populate_traces(traces_dir)
        build_digest(traces_dir=traces_dir, state_path=state_path)
        build_digest(traces_dir=traces_dir, state_path=state_path)
        state = load_state(state_path)
        ids = [e["candidate_id"] for e in state["entries"]]
        # No duplicate ids.
        assert len(ids) == len(set(ids))


# ---------------------------------------------------------------------------
# 8. Already-processed candidates excluded
# ---------------------------------------------------------------------------

class TestProcessedExclusion:
    def test_approved_candidate_not_in_digest(self, tmp_path):
        traces_dir = tmp_path / "traces"
        state_path = tmp_path / "state.json"
        _populate_traces(traces_dir)
        # Run once to register the candidate.
        payload1 = build_digest(traces_dir=traces_dir, state_path=state_path)
        if not payload1["candidates"]:
            pytest.skip("No candidates generated from synthetic traces")
        cid = payload1["candidates"][0]["candidate_id"]
        # Approve it.
        state = load_state(state_path)
        mark_approved(state, cid)
        save_state(state, state_path)
        # Run again — approved candidate must not appear.
        payload2 = build_digest(traces_dir=traces_dir, state_path=state_path)
        ids2 = [c["candidate_id"] for c in payload2["candidates"]]
        assert cid not in ids2
