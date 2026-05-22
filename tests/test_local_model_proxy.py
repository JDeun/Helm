"""Tests for :mod:`scripts.local_model_proxy`.

Covers:
1. validate_response on a well-formed tool_calls payload → valid=True, issues=[].
2. validate_response flags malformed_tool_call when tool_calls missing arguments.
3. validate_response flags invalid_json_in_arguments when arguments string isn't JSON.
4. validate_response flags empty_response for content="" and no tool_calls.
5. validate_response flags terminal_without_tool when content is text but spec required tool.
6. build_nudge returns a non-empty string for any issue list; orders by priority.
7. should_retry(attempt=0, issues=["malformed_tool_call"], policy) → True;
   at max_retries → False; abort issue overrides retry.
8. record_proxy_event appends a JSONL line; atomic write verified by writing twice
   and asserting valid line-by-line JSON.
9. Empty issues list → should_retry returns False (no point retrying).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.local_model_proxy import (
    build_nudge,
    record_proxy_event,
    should_retry,
    validate_response,
)

# ---------------------------------------------------------------------------
# Shared policy fixture
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


# ---------------------------------------------------------------------------
# 1. Well-formed tool_calls payload → valid=True, issues=[]
# ---------------------------------------------------------------------------


def test_validate_response_well_formed_tool_call():
    payload = {
        "content": "",
        "tool_calls": [
            {"name": "search", "arguments": '{"query": "hello"}'},
        ],
    }
    result = validate_response(payload)
    assert result["valid"] is True
    assert result["issues"] == []
    assert result["repair_hint"] is None


def test_validate_response_well_formed_with_dict_arguments():
    """arguments can be a dict (pre-parsed); should not raise invalid_json_in_arguments."""
    payload = {
        "content": "",
        "tool_calls": [
            {"name": "write_file", "arguments": {"path": "/tmp/x", "content": "y"}},
        ],
    }
    result = validate_response(payload)
    assert result["valid"] is True
    assert result["issues"] == []


# ---------------------------------------------------------------------------
# 2. malformed_tool_call — tool_calls missing arguments key
# ---------------------------------------------------------------------------


def test_validate_response_malformed_tool_call_missing_arguments():
    payload = {
        "content": "",
        "tool_calls": [
            {"name": "search"},  # no 'arguments' key
        ],
    }
    result = validate_response(payload)
    assert result["valid"] is False
    assert "malformed_tool_call" in result["issues"]


def test_validate_response_malformed_tool_call_missing_name():
    payload = {
        "content": "",
        "tool_calls": [
            {"arguments": '{"q": "x"}'},  # no 'name' key
        ],
    }
    result = validate_response(payload)
    assert result["valid"] is False
    assert "malformed_tool_call" in result["issues"]


def test_validate_response_malformed_tool_call_not_a_dict():
    payload = {
        "content": "",
        "tool_calls": ["not-a-dict"],
    }
    result = validate_response(payload)
    assert result["valid"] is False
    assert "malformed_tool_call" in result["issues"]


# ---------------------------------------------------------------------------
# 3. invalid_json_in_arguments — arguments string isn't JSON
# ---------------------------------------------------------------------------


def test_validate_response_invalid_json_in_arguments():
    payload = {
        "content": "",
        "tool_calls": [
            {"name": "run", "arguments": "not { valid json"},
        ],
    }
    result = validate_response(payload)
    assert result["valid"] is False
    assert "invalid_json_in_arguments" in result["issues"]
    # malformed_tool_call should NOT also be raised — name and arguments key exist
    assert "malformed_tool_call" not in result["issues"]


# ---------------------------------------------------------------------------
# 4. empty_response — content="" and no tool_calls
# ---------------------------------------------------------------------------


def test_validate_response_empty_response_no_content_no_tools():
    payload = {"content": "", "tool_calls": None}
    result = validate_response(payload)
    assert result["valid"] is False
    assert "empty_response" in result["issues"]


def test_validate_response_empty_response_absent_fields():
    """Missing content and tool_calls entirely → empty_response."""
    payload = {}
    result = validate_response(payload)
    assert result["valid"] is False
    assert "empty_response" in result["issues"]


def test_validate_response_whitespace_content_counts_as_empty():
    payload = {"content": "   \n\t  "}
    result = validate_response(payload)
    assert result["valid"] is False
    assert "empty_response" in result["issues"]


# ---------------------------------------------------------------------------
# 5. terminal_without_tool — content is text but spec required tool
# ---------------------------------------------------------------------------


def test_validate_response_terminal_without_tool():
    payload = {
        "content": "Here is my final answer.",
        "tool_calls": None,
        "tool_required": True,
    }
    result = validate_response(payload)
    assert result["valid"] is False
    assert "terminal_without_tool" in result["issues"]
    assert "non_json_when_tool_required" in result["issues"]


def test_validate_response_tool_required_but_tool_provided_is_valid():
    payload = {
        "content": "",
        "tool_calls": [{"name": "respond", "arguments": '{"message": "done"}'}],
        "tool_required": True,
    }
    result = validate_response(payload)
    assert result["valid"] is True
    assert result["issues"] == []


def test_validate_response_no_tool_required_flag_plain_text_is_valid():
    """Without tool_required=True, plain-text responses are not flagged."""
    payload = {"content": "Here is my answer.", "tool_calls": None}
    result = validate_response(payload)
    assert result["valid"] is True
    assert result["issues"] == []


# ---------------------------------------------------------------------------
# 6. build_nudge — non-empty string; priority ordering
# ---------------------------------------------------------------------------


def test_build_nudge_returns_nonempty_for_malformed_tool_call():
    nudge = build_nudge(["malformed_tool_call"])
    assert isinstance(nudge, str)
    assert len(nudge) > 0


def test_build_nudge_returns_nonempty_for_all_codes():
    all_codes = [
        "malformed_tool_call",
        "non_json_when_tool_required",
        "invalid_json_in_arguments",
        "terminal_without_tool",
        "empty_response",
    ]
    for code in all_codes:
        nudge = build_nudge([code])
        assert len(nudge) > 0, f"nudge was empty for code={code!r}"


def test_build_nudge_priority_ordering():
    """malformed_tool_call nudge appears before invalid_json nudge."""
    nudge = build_nudge(["invalid_json_in_arguments", "malformed_tool_call"])
    malformed_pos = nudge.find("missing required fields")
    invalid_json_pos = nudge.find("not valid JSON")
    assert malformed_pos != -1, "malformed_tool_call nudge text not found"
    assert invalid_json_pos != -1, "invalid_json_in_arguments nudge text not found"
    assert malformed_pos < invalid_json_pos, (
        "malformed_tool_call should appear before invalid_json_in_arguments"
    )


def test_build_nudge_empty_list_returns_generic():
    """Edge case: empty issues list returns a non-empty fallback string."""
    nudge = build_nudge([])
    assert isinstance(nudge, str)
    assert len(nudge) > 0


def test_build_nudge_unknown_code_included():
    """Unrecognised codes are included as generic reminders."""
    nudge = build_nudge(["some_future_issue"])
    assert "some_future_issue" in nudge


# ---------------------------------------------------------------------------
# 7. should_retry — core policy decisions
# ---------------------------------------------------------------------------


def test_should_retry_at_attempt_zero_nudgeable_issue():
    result = should_retry(0, ["malformed_tool_call"], _POLICY)
    assert result is True


def test_should_retry_at_max_retries_returns_false():
    # max_retries=2, so attempt=2 should return False
    result = should_retry(2, ["malformed_tool_call"], _POLICY)
    assert result is False


def test_should_retry_at_max_retries_minus_one_returns_true():
    # attempt=1 < max_retries=2 → still retryable
    result = should_retry(1, ["malformed_tool_call"], _POLICY)
    assert result is True


def test_should_retry_abort_issue_overrides_retry():
    """abort_on issue overrides a nudge_on issue at any attempt count."""
    result = should_retry(0, ["terminal_without_tool"], _POLICY)
    assert result is False


def test_should_retry_abort_wins_even_with_nudgeable_issue():
    """Mixed list: abort issue present → False regardless of nudge_on."""
    result = should_retry(0, ["malformed_tool_call", "terminal_without_tool"], _POLICY)
    assert result is False


# ---------------------------------------------------------------------------
# 8. should_retry — empty issues → False (test 9 from spec)
# ---------------------------------------------------------------------------


def test_should_retry_empty_issues_returns_false():
    result = should_retry(0, [], _POLICY)
    assert result is False


# ---------------------------------------------------------------------------
# 9. record_proxy_event — JSONL append; two writes produce two valid lines
# ---------------------------------------------------------------------------


def test_record_proxy_event_creates_file_and_valid_json(tmp_path):
    traces_dir = tmp_path / "traces"
    record_proxy_event(
        traces_dir,
        model="ollama/mistral:7b",
        issues=["malformed_tool_call"],
        action="retry",
        attempt=0,
    )
    event_file = traces_dir / "proxy-events.jsonl"
    assert event_file.exists()
    lines = [l for l in event_file.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(lines) == 1
    event = json.loads(lines[0])
    assert event["model"] == "ollama/mistral:7b"
    assert event["issues"] == ["malformed_tool_call"]
    assert event["action"] == "retry"
    assert event["attempt"] == 0
    assert "timestamp" in event


def test_record_proxy_event_two_writes_produce_two_valid_lines(tmp_path):
    """Atomic append: writing twice produces exactly two independently parseable lines."""
    traces_dir = tmp_path / "traces"
    record_proxy_event(
        traces_dir,
        model="ollama/llama3:8b",
        issues=[],
        action="pass",
        attempt=0,
    )
    record_proxy_event(
        traces_dir,
        model="ollama/llama3:8b",
        issues=["empty_response"],
        action="abort",
        attempt=1,
    )
    event_file = traces_dir / "proxy-events.jsonl"
    lines = [l for l in event_file.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(lines) == 2, f"Expected 2 lines, got {len(lines)}"
    # Each line must be independently valid JSON.
    events = [json.loads(line) for line in lines]
    assert events[0]["action"] == "pass"
    assert events[1]["action"] == "abort"
    assert events[1]["issues"] == ["empty_response"]


def test_record_proxy_event_creates_parent_dir_if_missing(tmp_path):
    """traces_dir is created automatically if it does not exist."""
    traces_dir = tmp_path / "deep" / "nested" / "traces"
    assert not traces_dir.exists()
    record_proxy_event(traces_dir, "test-model", [], "pass", 0)
    assert (traces_dir / "proxy-events.jsonl").exists()


def test_record_proxy_event_empty_issues_recorded(tmp_path):
    """A pass event with no issues is still a valid JSONL line."""
    traces_dir = tmp_path / "traces"
    record_proxy_event(traces_dir, "model-x", [], "pass", 0)
    line = (traces_dir / "proxy-events.jsonl").read_text(encoding="utf-8").strip()
    event = json.loads(line)
    assert event["issues"] == []
    assert event["action"] == "pass"


# ---------------------------------------------------------------------------
# Integration: validate → should_retry → record pipeline
# ---------------------------------------------------------------------------


def test_full_pipeline_malformed_retries_then_aborts(tmp_path):
    """Simulate three calls: two malformed → retry, third pass."""
    traces_dir = tmp_path / "traces"
    model = "ollama/phi3:mini"

    # Call 1: malformed → retry
    result1 = validate_response({"content": "", "tool_calls": [{"name": "fn"}]})
    retry1 = should_retry(0, result1["issues"], _POLICY)
    record_proxy_event(traces_dir, model, result1["issues"], "retry" if retry1 else "abort", 0)
    assert retry1 is True

    # Call 2: malformed again → still within budget (attempt=1 < max_retries=2)
    result2 = validate_response({"content": "", "tool_calls": [{"name": "fn"}]})
    retry2 = should_retry(1, result2["issues"], _POLICY)
    record_proxy_event(traces_dir, model, result2["issues"], "retry" if retry2 else "abort", 1)
    assert retry2 is True

    # Call 3: budget exhausted (attempt=2 == max_retries=2)
    result3 = validate_response({"content": "", "tool_calls": [{"name": "fn"}]})
    retry3 = should_retry(2, result3["issues"], _POLICY)
    record_proxy_event(traces_dir, model, result3["issues"], "retry" if retry3 else "abort", 2)
    assert retry3 is False

    # All three events recorded
    lines = [
        l
        for l in (traces_dir / "proxy-events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if l.strip()
    ]
    assert len(lines) == 3
    assert all(json.loads(l)["model"] == model for l in lines)
