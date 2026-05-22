"""Tests for :mod:`scripts.trace_recorder`.

Covers:
1. start_trace returns object with all required keys; toolSequence empty; outcome unset.
2. record_tool_call appends; duration_ms persists.
3. record_changed_file deduplicates.
4. record_validation_gate persists each entry.
5. save_trace + load_trace round-trips (tmp_path).
6. save_trace is atomic — writing twice doesn't leave a partial file.
7. Schema serializable: json.dumps(trace) succeeds for a populated trace.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.trace_recorder import (
    default_traces_dir,
    load_trace,
    record_changed_file,
    record_tool_call,
    record_validation_gate,
    save_trace,
    set_failure_signature,
    set_outcome,
    start_trace,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_REQUIRED_KEYS = {
    "taskId",
    "startedAt",
    "profile",
    "skill",
    "inputSummary",
    "toolSequence",
    "changedFiles",
    "validationGates",
    "failureSignature",
    "outcome",
    "replayHint",
    "skillCandidate",
}


def _make_trace() -> dict:
    return start_trace(
        task_id="test-task-001",
        profile="service_ops",
        skill="my-skill",
        input_summary="Run the widget pipeline for 2026-05-22",
    )


# ---------------------------------------------------------------------------
# Test 1 — start_trace structure
# ---------------------------------------------------------------------------

class TestStartTrace:
    def test_all_required_keys_present(self):
        trace = _make_trace()
        assert _REQUIRED_KEYS == set(trace.keys())

    def test_tool_sequence_initially_empty(self):
        trace = _make_trace()
        assert trace["toolSequence"] == []

    def test_changed_files_initially_empty(self):
        trace = _make_trace()
        assert trace["changedFiles"] == []

    def test_validation_gates_initially_empty(self):
        trace = _make_trace()
        assert trace["validationGates"] == []

    def test_outcome_initially_none(self):
        trace = _make_trace()
        assert trace["outcome"] is None

    def test_replay_hint_initially_none(self):
        trace = _make_trace()
        assert trace["replayHint"] is None

    def test_skill_candidate_initially_false(self):
        trace = _make_trace()
        assert trace["skillCandidate"] is False

    def test_failure_signature_initially_none(self):
        trace = _make_trace()
        assert trace["failureSignature"] is None

    def test_task_id_stored(self):
        trace = start_trace("my-id", "profile_x", None, "summary")
        assert trace["taskId"] == "my-id"

    def test_profile_stored(self):
        trace = start_trace("id", "service_ops", None, "summary")
        assert trace["profile"] == "service_ops"

    def test_skill_none_allowed(self):
        trace = start_trace("id", "service_ops", None, "summary")
        assert trace["skill"] is None

    def test_skill_string_stored(self):
        trace = start_trace("id", "service_ops", "my-skill", "summary")
        assert trace["skill"] == "my-skill"

    def test_input_summary_stored(self):
        trace = start_trace("id", "service_ops", None, "Hello world")
        assert trace["inputSummary"] == "Hello world"

    def test_started_at_is_iso_string(self):
        trace = _make_trace()
        # Should be a non-empty ISO-8601 string
        assert isinstance(trace["startedAt"], str)
        assert "T" in trace["startedAt"]


# ---------------------------------------------------------------------------
# Test 2 — record_tool_call
# ---------------------------------------------------------------------------

class TestRecordToolCall:
    def test_appends_entry(self):
        trace = _make_trace()
        record_tool_call(trace, "Bash", "run tests", {"cmd": "pytest"}, 1200, "success")
        assert len(trace["toolSequence"]) == 1

    def test_duration_ms_persists(self):
        trace = _make_trace()
        record_tool_call(trace, "Bash", "run tests", {}, 4242, "success")
        assert trace["toolSequence"][0]["durationMs"] == 4242

    def test_status_persists(self):
        trace = _make_trace()
        record_tool_call(trace, "Read", "read file", {}, 10, "failure")
        assert trace["toolSequence"][0]["status"] == "failure"

    def test_name_persists(self):
        trace = _make_trace()
        record_tool_call(trace, "Edit", "edit source", {}, 50, "success")
        assert trace["toolSequence"][0]["name"] == "Edit"

    def test_purpose_persists(self):
        trace = _make_trace()
        record_tool_call(trace, "Bash", "run linter", {}, 100, "success")
        assert trace["toolSequence"][0]["purpose"] == "run linter"

    def test_args_persists(self):
        trace = _make_trace()
        args = {"file": "/tmp/foo.py", "flags": ["-v"]}
        record_tool_call(trace, "Bash", "run", args, 100, "success")
        assert trace["toolSequence"][0]["args"] == args

    def test_result_summary_default_none(self):
        trace = _make_trace()
        record_tool_call(trace, "Bash", "run", {}, 100, "success")
        assert trace["toolSequence"][0]["resultSummary"] is None

    def test_result_summary_stored(self):
        trace = _make_trace()
        record_tool_call(trace, "Bash", "run", {}, 100, "success", result_summary="OK")
        assert trace["toolSequence"][0]["resultSummary"] == "OK"

    def test_multiple_calls_ordered(self):
        trace = _make_trace()
        record_tool_call(trace, "Bash", "first", {}, 1, "success")
        record_tool_call(trace, "Read", "second", {}, 2, "success")
        assert trace["toolSequence"][0]["name"] == "Bash"
        assert trace["toolSequence"][1]["name"] == "Read"


# ---------------------------------------------------------------------------
# Test 3 — record_changed_file deduplication
# ---------------------------------------------------------------------------

class TestRecordChangedFile:
    def test_appends_path(self):
        trace = _make_trace()
        record_changed_file(trace, "/tmp/foo.py")
        assert "/tmp/foo.py" in trace["changedFiles"]

    def test_deduplicates_same_path(self):
        trace = _make_trace()
        record_changed_file(trace, "/tmp/foo.py")
        record_changed_file(trace, "/tmp/foo.py")
        assert trace["changedFiles"].count("/tmp/foo.py") == 1

    def test_different_paths_both_kept(self):
        trace = _make_trace()
        record_changed_file(trace, "/tmp/a.py")
        record_changed_file(trace, "/tmp/b.py")
        assert len(trace["changedFiles"]) == 2

    def test_order_preserved(self):
        trace = _make_trace()
        record_changed_file(trace, "/tmp/a.py")
        record_changed_file(trace, "/tmp/b.py")
        assert trace["changedFiles"][0] == "/tmp/a.py"
        assert trace["changedFiles"][1] == "/tmp/b.py"

    def test_triple_add_stays_single(self):
        trace = _make_trace()
        for _ in range(3):
            record_changed_file(trace, "/same/path.py")
        assert len(trace["changedFiles"]) == 1


# ---------------------------------------------------------------------------
# Test 4 — record_validation_gate
# ---------------------------------------------------------------------------

class TestRecordValidationGate:
    def test_persists_single_gate(self):
        trace = _make_trace()
        record_validation_gate(trace, "pytest", "passed")
        assert trace["validationGates"] == [{"name": "pytest", "status": "passed"}]

    def test_persists_multiple_gates(self):
        trace = _make_trace()
        record_validation_gate(trace, "pytest", "passed")
        record_validation_gate(trace, "mypy", "failed")
        assert len(trace["validationGates"]) == 2
        assert trace["validationGates"][0]["name"] == "pytest"
        assert trace["validationGates"][1]["name"] == "mypy"

    def test_status_value_preserved(self):
        trace = _make_trace()
        record_validation_gate(trace, "lint", "skipped")
        assert trace["validationGates"][0]["status"] == "skipped"

    def test_duplicate_gates_allowed(self):
        # Gates are not deduplicated — the same check can run twice.
        trace = _make_trace()
        record_validation_gate(trace, "pytest", "passed")
        record_validation_gate(trace, "pytest", "passed")
        assert len(trace["validationGates"]) == 2


# ---------------------------------------------------------------------------
# Test 5 — save_trace + load_trace round-trip
# ---------------------------------------------------------------------------

class TestSaveLoadRoundTrip:
    def test_round_trip_task_id(self, tmp_path):
        trace = _make_trace()
        save_trace(trace, tmp_path)
        loaded = load_trace(tmp_path, "test-task-001")
        assert loaded["taskId"] == "test-task-001"

    def test_round_trip_full_equality(self, tmp_path):
        trace = _make_trace()
        record_tool_call(trace, "Bash", "run", {"cmd": "ls"}, 50, "success", "ok")
        record_changed_file(trace, "/tmp/foo.py")
        record_validation_gate(trace, "pytest", "passed")
        set_outcome(trace, "completed", replay_hint="re-run with --verbose", skill_candidate=True)
        save_trace(trace, tmp_path)
        loaded = load_trace(tmp_path, "test-task-001")
        assert loaded == trace

    def test_file_written_at_expected_path(self, tmp_path):
        trace = _make_trace()
        path = save_trace(trace, tmp_path)
        assert path == tmp_path / "test-task-001.json"
        assert path.exists()

    def test_returns_path_object(self, tmp_path):
        trace = _make_trace()
        result = save_trace(trace, tmp_path)
        assert isinstance(result, Path)

    def test_load_raises_for_missing_task(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_trace(tmp_path, "nonexistent-task")

    def test_creates_traces_dir_if_absent(self, tmp_path):
        nested = tmp_path / "a" / "b" / "c"
        trace = _make_trace()
        save_trace(trace, nested)
        assert (nested / "test-task-001.json").exists()


# ---------------------------------------------------------------------------
# Test 6 — atomic write
# ---------------------------------------------------------------------------

class TestAtomicWrite:
    def test_writing_twice_produces_single_valid_file(self, tmp_path):
        trace1 = start_trace("atomic-task", "service_ops", None, "first write")
        save_trace(trace1, tmp_path)
        trace2 = start_trace("atomic-task", "service_ops", None, "second write")
        save_trace(trace2, tmp_path)
        loaded = load_trace(tmp_path, "atomic-task")
        assert loaded["inputSummary"] == "second write"

    def test_no_tmp_files_left_behind(self, tmp_path):
        trace = _make_trace()
        save_trace(trace, tmp_path)
        tmp_files = list(tmp_path.glob("*.tmp"))
        assert tmp_files == []

    def test_dest_file_is_valid_json_after_two_writes(self, tmp_path):
        for summary in ("first", "second"):
            t = start_trace("atomic-task-2", "p", None, summary)
            save_trace(t, tmp_path)
        data = json.loads((tmp_path / "atomic-task-2.json").read_text())
        assert data["inputSummary"] == "second"


# ---------------------------------------------------------------------------
# Test 7 — JSON serialisability
# ---------------------------------------------------------------------------

class TestJsonSerializable:
    def test_empty_trace_serializable(self):
        trace = _make_trace()
        dumped = json.dumps(trace)
        assert isinstance(dumped, str)

    def test_populated_trace_serializable(self):
        trace = _make_trace()
        record_tool_call(trace, "Bash", "run tests", {"cmd": "pytest -q"}, 3000, "success", "ok")
        record_changed_file(trace, "scripts/foo.py")
        record_validation_gate(trace, "pytest", "passed")
        set_failure_signature(trace, {"component": "skill", "fingerprint": "abcd1234"})
        set_outcome(trace, "failed", replay_hint="retry with debug", skill_candidate=False)
        dumped = json.dumps(trace)
        assert isinstance(dumped, str)
        reloaded = json.loads(dumped)
        assert reloaded["outcome"] == "failed"

    def test_null_skill_serializable(self):
        trace = start_trace("id", "profile", None, "summary")
        dumped = json.dumps(trace)
        parsed = json.loads(dumped)
        assert parsed["skill"] is None


# ---------------------------------------------------------------------------
# OPENCLAW_TRACES_DIR env var expansion
# ---------------------------------------------------------------------------

class TestTracesDirEnvExpansion:
    def test_OPENCLAW_TRACES_DIR_expands_tilde(self, monkeypatch):
        """``OPENCLAW_TRACES_DIR=~/x`` must resolve to ``$HOME/x``."""
        monkeypatch.setenv("OPENCLAW_TRACES_DIR", "~/some-name")
        resolved = default_traces_dir()
        assert resolved == Path.home() / "some-name"
        # And it must NOT be a literal `~` path.
        assert "~" not in str(resolved)
