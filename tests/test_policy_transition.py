"""Tests for scripts.policy_transition (harness-engineering Task 4).

Covers:
  1. evaluate([]) → None
  2. One non-credential failure → None
  3. Two events same fingerprint → stop_retry_and_diagnose (beats patch_failed)
  4. Two patch_failed different fingerprints → reload_file_and_decompose
  5. Three events same tool (different fingerprints, different error_classes) → create_skill_repair_candidate
  6. One credential_invalid_grant → auth_recovery_profile
  7. Rule precedence: mixed history → first rule in order wins
  8. transition_record schema is JSON-serializable; required keys present
  9. Integration: synthetic failure stream → ledger entry has policy_transition
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.policy_transition import evaluate, transition_record


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_sig(
    fingerprint: str,
    error_class: str = "exit_nonzero",
    tool: str = "my_tool",
    component: str = "skill",
) -> dict:
    return {
        "component": component,
        "tool": tool,
        "profile": None,
        "error_class": error_class,
        "target": None,
        "fingerprint": fingerprint,
    }


def _make_event(
    fingerprint: str,
    error_class: str = "exit_nonzero",
    tool: str = "my_tool",
    task_name: str = "task-1",
    skill: str = "some-skill",
) -> dict:
    return {
        "signature": _make_sig(fingerprint, error_class=error_class, tool=tool),
        "task_name": task_name,
        "skill": skill,
        "occurred_at": "2026-05-22T00:00:00Z",
    }


# ---------------------------------------------------------------------------
# Test 1: empty history → None
# ---------------------------------------------------------------------------

def test_evaluate_empty_history_returns_none() -> None:
    assert evaluate([]) is None


# ---------------------------------------------------------------------------
# Test 2: one non-credential failure → None
# ---------------------------------------------------------------------------

def test_evaluate_single_nonmatching_event_returns_none() -> None:
    event = _make_event("aabbccdd", error_class="exit_nonzero")
    assert evaluate([event]) is None


# ---------------------------------------------------------------------------
# Test 3: two events with same fingerprint → stop_retry_and_diagnose
# (takes precedence over patch_failed if both apply)
# ---------------------------------------------------------------------------

def test_evaluate_repeated_fingerprint_gives_stop_retry() -> None:
    # Both events have same fingerprint AND are patch_failed — rule 1 wins
    events = [
        _make_event("fp123456", error_class="patch_failed"),
        _make_event("fp123456", error_class="patch_failed"),
    ]
    result = evaluate(events)
    assert result is not None
    assert result["action"] == "stop_retry_and_diagnose"
    assert "fp123456" in result["reason"]
    assert result["signature"]["fingerprint"] == "fp123456"


# ---------------------------------------------------------------------------
# Test 4: two patch_failed events with different fingerprints
# ---------------------------------------------------------------------------

def test_evaluate_two_patch_failed_different_fingerprints() -> None:
    events = [
        _make_event("fp111111", error_class="patch_failed"),
        _make_event("fp222222", error_class="patch_failed"),
    ]
    result = evaluate(events)
    assert result is not None
    assert result["action"] == "reload_file_and_decompose"
    assert "patch_failed" in result["reason"]


# ---------------------------------------------------------------------------
# Test 5: three events from same tool (different fingerprints, different error_classes)
# ---------------------------------------------------------------------------

def test_evaluate_three_same_tool_different_fingerprints() -> None:
    events = [
        _make_event("fp000001", error_class="exit_nonzero", tool="flaky_tool"),
        _make_event("fp000002", error_class="timeout", tool="flaky_tool"),
        _make_event("fp000003", error_class="google_sheets_api", tool="flaky_tool"),
    ]
    result = evaluate(events)
    assert result is not None
    assert result["action"] == "create_skill_repair_candidate"
    assert "flaky_tool" in result["reason"]


# ---------------------------------------------------------------------------
# Test 6: one credential_invalid_grant → auth_recovery_profile
# ---------------------------------------------------------------------------

def test_evaluate_credential_invalid_grant_triggers_auth_recovery() -> None:
    events = [
        _make_event("fp999aaa", error_class="credential_invalid_grant", tool="gws"),
    ]
    result = evaluate(events)
    assert result is not None
    assert result["action"] == "auth_recovery_profile"
    assert "credential_invalid_grant" in result["reason"]


# ---------------------------------------------------------------------------
# Test 7: rule precedence — first rule wins
# ---------------------------------------------------------------------------

def test_evaluate_rule_precedence_rule1_beats_all() -> None:
    """History matches rule 1 (same fingerprint) AND rule 4 (credential error)
    — rule 1 should fire."""
    events = [
        _make_event("fp_same", error_class="credential_invalid_grant", tool="gws"),
        _make_event("fp_same", error_class="credential_invalid_grant", tool="gws"),
    ]
    result = evaluate(events)
    assert result is not None
    assert result["action"] == "stop_retry_and_diagnose"


def test_evaluate_rule_precedence_rule2_beats_rule3_and_4() -> None:
    """History matches rule 2 (patch_failed x2) AND rule 3 (same tool x3)
    AND rule 4 (credential error) — rule 2 should fire."""
    events = [
        _make_event("fp_p1", error_class="patch_failed", tool="patcher"),
        _make_event("fp_p2", error_class="patch_failed", tool="patcher"),
        _make_event("fp_p3", error_class="credential_invalid_grant", tool="patcher"),
    ]
    # rule 2: 2 patch_failed events → reload_file_and_decompose
    result = evaluate(events)
    assert result is not None
    assert result["action"] == "reload_file_and_decompose"


def test_evaluate_rule_precedence_rule3_beats_rule4() -> None:
    """3 same-tool events (none patch_failed, no credential) — rule 3 fires."""
    events = [
        _make_event("fp_t1", error_class="exit_nonzero", tool="mytool"),
        _make_event("fp_t2", error_class="timeout", tool="mytool"),
        _make_event("fp_t3", error_class="exit_nonzero", tool="mytool"),
    ]
    result = evaluate(events)
    assert result is not None
    assert result["action"] == "create_skill_repair_candidate"


# ---------------------------------------------------------------------------
# Test 8: transition_record schema is JSON-serializable; required keys present
# ---------------------------------------------------------------------------

def test_transition_record_is_json_serializable() -> None:
    sig = _make_sig("deadbeef", error_class="timeout", tool="bash")
    record = transition_record("stop_retry_and_diagnose", "reason text", sig)

    # Must be JSON-serializable
    serialized = json.dumps(record)
    parsed = json.loads(serialized)

    # Required keys
    assert "action" in parsed
    assert "reason" in parsed
    assert "signature" in parsed
    assert parsed["action"] == "stop_retry_and_diagnose"
    assert parsed["reason"] == "reason text"
    assert parsed["signature"]["fingerprint"] == "deadbeef"


def test_transition_record_returns_fresh_dict_each_call() -> None:
    sig = _make_sig("aabbccdd")
    r1 = transition_record("auth_recovery_profile", "r1", sig)
    r2 = transition_record("auth_recovery_profile", "r2", sig)
    assert r1 is not r2
    r1["extra"] = "mutated"
    assert "extra" not in r2


# ---------------------------------------------------------------------------
# Test 9: integration — synthetic failure stream → ledger entry has policy_transition
# (subprocess is mocked so test runs offline)
# ---------------------------------------------------------------------------

def test_integration_ledger_entry_has_policy_transition() -> None:
    """Feed the harness a synthetic failure history via record_failure_with_policy_check
    and verify the written ledger entry contains the expected policy_transition."""
    from scripts import adaptive_harness_lib

    task_id = "task-integ-policy-test"
    # Build a synthetic ledger with two identical failure events (same fingerprint)
    # so that rule 1 fires when the third event is evaluated.
    existing_entry = {
        "task_id": task_id,
        "status": "failed",
        "task_name": "integ-task",
        "skill": "my-skill",
        "exit_code": 1,
        "command": ["python3", "my_script.py"],
        "profile": "inspect_local",
    }

    with tempfile.TemporaryDirectory() as tmpdir:
        ledger = Path(tmpdir) / "task-ledger.jsonl"
        # Pre-populate with one prior failure from run_with_profile
        # (simulated: same fingerprint as what failure_sig will produce below)
        # We'll write the ledger with events that failure_sig would generate.
        # To guarantee same fingerprint, we write the same event structure twice.
        ledger.write_text(
            json.dumps(existing_entry) + "\n"
            + json.dumps(existing_entry) + "\n",
            encoding="utf-8",
        )

        written: list[dict] = []

        def fake_append(path: Path, row: dict) -> None:
            written.append(row)

        with (
            patch.object(adaptive_harness_lib, "TASK_LEDGER", ledger),
            patch.object(adaptive_harness_lib, "append_jsonl_atomic", fake_append),
        ):
            # The failure event to process: same structure = same fingerprint
            failure_event = {
                "task_id": task_id,
                "task_name": "integ-task",
                "skill": "my-skill",
                "status": "failed",
                "exit_code": 1,
                "command": ["python3", "my_script.py"],
                "profile": "inspect_local",
            }
            pt = adaptive_harness_lib.record_failure_with_policy_check(task_id, failure_event)

    # A policy transition should have been triggered (rule 1: repeated fingerprint)
    assert pt is not None, "Expected a policy_transition but got None"
    assert pt["action"] == "stop_retry_and_diagnose"

    # The appended ledger row must carry the policy_transition
    assert len(written) == 1
    written_entry = written[0]
    assert written_entry["task_id"] == task_id
    assert "policy_transition" in written_entry
    assert written_entry["policy_transition"]["action"] == "stop_retry_and_diagnose"

    # Serializable
    json.dumps(written_entry)
