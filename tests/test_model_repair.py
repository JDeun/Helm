"""Tests for scripts/model_repair.py — Wave 2 N-C orchestrator.

Coverage (~15 cases):
 1-2.  repair_enabled truthy/falsy detection (parametrize)
 3.    evaluate_response with valid payload → verdict=ok, shadow_mode present when flag off
 4.    evaluate_response with malformed tool call → verdict=nudge_and_retry, nudge non-empty
 5.    evaluate_response shadow_mode=True when HELM_MODEL_REPAIR is unset
 6.    evaluate_response shadow_mode=False when HELM_MODEL_REPAIR=1
 7.    evaluate_response logs to traces_dir when provided
 8.    evaluate_response abort issue → verdict=abort
 9.    evaluate_response at max retries → verdict=give_up
10.    repair_loop terminates on verdict=ok
11.    repair_loop terminates at max_attempts
12.    repair_loop with repair disabled invokes ONCE, no retry
13.    repair_loop with abort issue terminates immediately
14.    shadow_mode flag present when flag is off
15.    evaluate_response loads policy from file when policy=None (integration)
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_POLICY: dict = {
    "max_retries": 2,
    "nudge_on": [
        "malformed_tool_call",
        "non_json_when_tool_required",
        "invalid_json_in_arguments",
        "empty_response",
    ],
    "abort_on": ["terminal_without_tool"],
}

_VALID_PAYLOAD: dict = {
    "content": "",
    "tool_calls": [{"name": "search", "arguments": '{"query": "hello"}'}],
}

_MALFORMED_PAYLOAD: dict = {
    "content": "",
    "tool_calls": [{"name": "search"}],  # missing arguments
}

_ABORT_PAYLOAD: dict = {
    "content": "",
    "tool_calls": None,
    "tool_required": True,
}


# ---------------------------------------------------------------------------
# 1-2. repair_enabled truthy/falsy detection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "env_val, expected",
    [
        ("1", True),
        ("true", True),
        ("TRUE", True),
        ("True", True),
        ("yes", True),
        ("YES", True),
        ("  1  ", True),   # leading/trailing whitespace
        ("  yes  ", True),
        ("0", False),
        ("false", False),
        ("no", False),
        ("", False),
        (None, False),     # unset
    ],
)
def test_repair_enabled_truthy_falsy(env_val, expected, monkeypatch):
    from scripts import model_repair
    if env_val is None:
        monkeypatch.delenv("HELM_MODEL_REPAIR", raising=False)
    else:
        monkeypatch.setenv("HELM_MODEL_REPAIR", env_val)
    assert model_repair.repair_enabled() == expected


# ---------------------------------------------------------------------------
# 3. evaluate_response — valid payload → verdict=ok
# ---------------------------------------------------------------------------


def test_evaluate_response_valid_payload_verdict_ok(monkeypatch):
    monkeypatch.delenv("HELM_MODEL_REPAIR", raising=False)
    from scripts import model_repair

    result = model_repair.evaluate_response(
        _VALID_PAYLOAD,
        model="ollama/mistral:7b",
        tool_required=False,
        attempt=0,
        policy=_POLICY,
    )
    assert result["verdict"] == "ok"
    assert result["issues"] == []
    assert result["nudge"] is None


# ---------------------------------------------------------------------------
# 4. evaluate_response — malformed tool call → nudge_and_retry, nudge non-empty
# ---------------------------------------------------------------------------


def test_evaluate_response_malformed_tool_call_nudge_and_retry(monkeypatch):
    monkeypatch.setenv("HELM_MODEL_REPAIR", "1")
    from scripts import model_repair

    result = model_repair.evaluate_response(
        _MALFORMED_PAYLOAD,
        model="ollama/mistral:7b",
        tool_required=False,
        attempt=0,
        policy=_POLICY,
    )
    assert result["verdict"] == "nudge_and_retry"
    assert "malformed_tool_call" in result["issues"]
    assert isinstance(result["nudge"], str) and len(result["nudge"]) > 0


# ---------------------------------------------------------------------------
# 5. shadow_mode=True when HELM_MODEL_REPAIR is unset
# ---------------------------------------------------------------------------


def test_evaluate_response_shadow_mode_when_flag_off(monkeypatch):
    monkeypatch.delenv("HELM_MODEL_REPAIR", raising=False)
    from scripts import model_repair

    result = model_repair.evaluate_response(
        _MALFORMED_PAYLOAD,
        model="ollama/mistral:7b",
        tool_required=False,
        attempt=0,
        policy=_POLICY,
    )
    assert result["shadow_mode"] is True


# ---------------------------------------------------------------------------
# 6. shadow_mode=False when HELM_MODEL_REPAIR=1
# ---------------------------------------------------------------------------


def test_evaluate_response_shadow_mode_false_when_flag_on(monkeypatch):
    monkeypatch.setenv("HELM_MODEL_REPAIR", "1")
    from scripts import model_repair

    result = model_repair.evaluate_response(
        _MALFORMED_PAYLOAD,
        model="ollama/mistral:7b",
        tool_required=False,
        attempt=0,
        policy=_POLICY,
    )
    assert result["shadow_mode"] is False


# ---------------------------------------------------------------------------
# 7. evaluate_response logs to traces_dir when provided
# ---------------------------------------------------------------------------


def test_evaluate_response_logs_to_traces_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("HELM_MODEL_REPAIR", "1")
    from scripts import model_repair

    traces_dir = tmp_path / "traces"
    model_repair.evaluate_response(
        _VALID_PAYLOAD,
        model="test-model",
        tool_required=False,
        attempt=0,
        policy=_POLICY,
        traces_dir=traces_dir,
    )
    event_file = traces_dir / "proxy-events.jsonl"
    assert event_file.exists()
    lines = [l for l in event_file.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(lines) == 1
    event = json.loads(lines[0])
    assert event["model"] == "test-model"
    assert event["action"] == "ok"


# ---------------------------------------------------------------------------
# 8. evaluate_response — abort issue → verdict=abort
# ---------------------------------------------------------------------------


def test_evaluate_response_abort_issue_verdict_abort(monkeypatch):
    monkeypatch.setenv("HELM_MODEL_REPAIR", "1")
    from scripts import model_repair

    result = model_repair.evaluate_response(
        _ABORT_PAYLOAD,
        model="ollama/mistral:7b",
        tool_required=True,
        attempt=0,
        policy=_POLICY,
    )
    assert result["verdict"] == "abort"
    assert result["nudge"] is None


# ---------------------------------------------------------------------------
# 9. evaluate_response — at max retries → verdict=give_up
# ---------------------------------------------------------------------------


def test_evaluate_response_at_max_retries_give_up(monkeypatch):
    monkeypatch.setenv("HELM_MODEL_REPAIR", "1")
    from scripts import model_repair

    result = model_repair.evaluate_response(
        _MALFORMED_PAYLOAD,
        model="ollama/mistral:7b",
        tool_required=False,
        attempt=2,          # max_retries=2 in _POLICY → exhausted
        policy=_POLICY,
    )
    assert result["verdict"] == "give_up"
    assert result["nudge"] is None


# ---------------------------------------------------------------------------
# 10. repair_loop terminates on verdict=ok
# ---------------------------------------------------------------------------


def test_repair_loop_terminates_on_ok(monkeypatch):
    monkeypatch.setenv("HELM_MODEL_REPAIR", "1")
    from scripts import model_repair

    call_count = 0

    def invoke(tools, nudge):
        nonlocal call_count
        call_count += 1
        return _VALID_PAYLOAD

    result = model_repair.repair_loop(
        invoke_model_fn=invoke,
        tools=[],
        model="ollama/mistral:7b",
        tool_required=False,
        policy=_POLICY,
        max_attempts=3,
    )
    assert call_count == 1
    assert result["issues"] == []
    assert result["attempts"] == 1


# ---------------------------------------------------------------------------
# 11. repair_loop terminates at max_attempts
# ---------------------------------------------------------------------------


def test_repair_loop_terminates_at_max_attempts(monkeypatch):
    monkeypatch.setenv("HELM_MODEL_REPAIR", "1")
    from scripts import model_repair

    call_count = 0

    def invoke(tools, nudge):
        nonlocal call_count
        call_count += 1
        return _MALFORMED_PAYLOAD

    result = model_repair.repair_loop(
        invoke_model_fn=invoke,
        tools=[],
        model="ollama/mistral:7b",
        tool_required=False,
        policy=_POLICY,
        max_attempts=3,
    )
    assert call_count == 3
    assert result["attempts"] == 3


# ---------------------------------------------------------------------------
# 12. repair_loop with repair disabled invokes ONCE, no retry
# ---------------------------------------------------------------------------


def test_repair_loop_disabled_invokes_once(monkeypatch):
    monkeypatch.delenv("HELM_MODEL_REPAIR", raising=False)
    from scripts import model_repair

    call_count = 0

    def invoke(tools, nudge):
        nonlocal call_count
        call_count += 1
        return _MALFORMED_PAYLOAD

    result = model_repair.repair_loop(
        invoke_model_fn=invoke,
        tools=[],
        model="ollama/mistral:7b",
        tool_required=False,
        policy=_POLICY,
        max_attempts=3,
    )
    # Even though payload has issues, shadow mode → no retry, invoke once only
    assert call_count == 1
    assert result["attempts"] == 1


# ---------------------------------------------------------------------------
# 13. repair_loop with abort issue terminates immediately after one call
# ---------------------------------------------------------------------------


def test_repair_loop_abort_issue_terminates_immediately(monkeypatch):
    monkeypatch.setenv("HELM_MODEL_REPAIR", "1")
    from scripts import model_repair

    call_count = 0

    def invoke(tools, nudge):
        nonlocal call_count
        call_count += 1
        return _ABORT_PAYLOAD

    result = model_repair.repair_loop(
        invoke_model_fn=invoke,
        tools=[],
        model="ollama/mistral:7b",
        tool_required=True,
        policy=_POLICY,
        max_attempts=3,
    )
    assert call_count == 1
    assert result["attempts"] == 1


# ---------------------------------------------------------------------------
# 14. shadow_mode flag present when flag is off (already in test 5, confirm key exists)
# ---------------------------------------------------------------------------


def test_shadow_mode_key_always_present(monkeypatch):
    monkeypatch.delenv("HELM_MODEL_REPAIR", raising=False)
    from scripts import model_repair

    result = model_repair.evaluate_response(
        _VALID_PAYLOAD,
        model="test-model",
        tool_required=False,
        attempt=0,
        policy=_POLICY,
    )
    assert "shadow_mode" in result
    assert result["shadow_mode"] is True


# ---------------------------------------------------------------------------
# 15. evaluate_response loads policy from file when policy=None
# ---------------------------------------------------------------------------


def test_evaluate_response_loads_policy_from_file_when_none(monkeypatch):
    """When policy=None, the function loads references/local_model_proxy_policy.json."""
    monkeypatch.setenv("HELM_MODEL_REPAIR", "1")
    from scripts import model_repair

    # Valid payload → should return ok regardless of which policy loads
    result = model_repair.evaluate_response(
        _VALID_PAYLOAD,
        model="ollama/test",
        tool_required=False,
        attempt=0,
        policy=None,  # force file-based load
    )
    assert "verdict" in result
    assert result["verdict"] == "ok"


# ---------------------------------------------------------------------------
# next_attempt field consistency
# ---------------------------------------------------------------------------


def test_evaluate_response_next_attempt_increments_on_retry(monkeypatch):
    monkeypatch.setenv("HELM_MODEL_REPAIR", "1")
    from scripts import model_repair

    result = model_repair.evaluate_response(
        _MALFORMED_PAYLOAD,
        model="ollama/mistral:7b",
        tool_required=False,
        attempt=0,
        policy=_POLICY,
    )
    assert result["next_attempt"] == 1


def test_evaluate_response_next_attempt_unchanged_on_ok(monkeypatch):
    monkeypatch.setenv("HELM_MODEL_REPAIR", "1")
    from scripts import model_repair

    result = model_repair.evaluate_response(
        _VALID_PAYLOAD,
        model="ollama/mistral:7b",
        tool_required=False,
        attempt=0,
        policy=_POLICY,
    )
    assert result["next_attempt"] == 0
