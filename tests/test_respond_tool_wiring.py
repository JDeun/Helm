"""Tests for scripts/respond_tool_wiring.py — Wave 2 N-D tier-aware injection.

Coverage (~8 cases):
 1. synthetic_respond_enabled truthy/falsy detection
 2. prepare_tools with flag off → input list returned unchanged (contents match)
 3. prepare_tools with flag on and model_tier="L3_local_model" → respond tool appended
 4. prepare_tools with flag on but model_tier="L4_cloud_provider" → input unchanged
 5. prepare_tools never mutates input list
 6. finalize_response strips respond call → content set, tool_calls reduced
 7. finalize_response with no respond call and no tool_calls and tool_required=True → _finalize_warning
 8. finalize_response with no respond call and tool_required=False → no warning
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_SAMPLE_TOOL: dict = {"type": "function", "function": {"name": "search", "description": "search"}}


# ---------------------------------------------------------------------------
# 1. synthetic_respond_enabled truthy/falsy detection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "env_val, expected",
    [
        ("1", True),
        ("true", True),
        ("TRUE", True),
        ("yes", True),
        ("  1  ", True),
        ("0", False),
        ("false", False),
        ("no", False),
        ("", False),
        (None, False),
    ],
)
def test_synthetic_respond_enabled(env_val, expected, monkeypatch):
    from scripts import respond_tool_wiring
    if env_val is None:
        monkeypatch.delenv("HELM_SYNTHETIC_RESPOND", raising=False)
    else:
        monkeypatch.setenv("HELM_SYNTHETIC_RESPOND", env_val)
    assert respond_tool_wiring.synthetic_respond_enabled() == expected


# ---------------------------------------------------------------------------
# 2. prepare_tools with flag off → input list returned unchanged (contents match)
# ---------------------------------------------------------------------------


def test_prepare_tools_flag_off_returns_unchanged(monkeypatch):
    monkeypatch.delenv("HELM_SYNTHETIC_RESPOND", raising=False)
    from scripts import respond_tool_wiring

    tools = [_SAMPLE_TOOL]
    result = respond_tool_wiring.prepare_tools(tools, model_tier="L3_local_model")
    assert result == tools


# ---------------------------------------------------------------------------
# 3. prepare_tools with flag on and model_tier="L3_local_model" → respond tool appended
# ---------------------------------------------------------------------------


def test_prepare_tools_flag_on_l3_appends_respond_tool(monkeypatch):
    monkeypatch.setenv("HELM_SYNTHETIC_RESPOND", "1")
    from scripts import respond_tool_wiring

    tools = [_SAMPLE_TOOL]
    result = respond_tool_wiring.prepare_tools(tools, model_tier="L3_local_model")
    assert len(result) == 2
    # Last tool should be the respond tool
    names = []
    for t in result:
        if "function" in t:
            names.append(t["function"].get("name"))
        else:
            names.append(t.get("name"))
    assert "respond" in names


# ---------------------------------------------------------------------------
# 4. prepare_tools with flag on but model_tier="L4_cloud_provider" → input unchanged
# ---------------------------------------------------------------------------


def test_prepare_tools_flag_on_l4_returns_unchanged(monkeypatch):
    monkeypatch.setenv("HELM_SYNTHETIC_RESPOND", "1")
    from scripts import respond_tool_wiring

    tools = [_SAMPLE_TOOL]
    result = respond_tool_wiring.prepare_tools(tools, model_tier="L4_cloud_provider")
    assert result == tools


# ---------------------------------------------------------------------------
# 5. prepare_tools never mutates input list
# ---------------------------------------------------------------------------


def test_prepare_tools_never_mutates_input(monkeypatch):
    monkeypatch.setenv("HELM_SYNTHETIC_RESPOND", "1")
    from scripts import respond_tool_wiring

    tools = [_SAMPLE_TOOL]
    original_len = len(tools)
    respond_tool_wiring.prepare_tools(tools, model_tier="L3_local_model")
    assert len(tools) == original_len  # input is unchanged


# ---------------------------------------------------------------------------
# 6. finalize_response strips respond call → content set, tool_calls reduced
# ---------------------------------------------------------------------------


def test_finalize_response_strips_respond_call(monkeypatch):
    from scripts import respond_tool_wiring

    response = {
        "content": "",
        "tool_calls": [
            {"name": "respond", "arguments": '{"message": "final answer"}'},
            {"name": "search", "arguments": '{"query": "x"}'},
        ],
    }
    result = respond_tool_wiring.finalize_response(response, tool_required=True)
    assert result["content"] == "final answer"
    # Only non-respond tool_calls should remain
    remaining = result.get("tool_calls", [])
    assert len(remaining) == 1
    assert remaining[0]["name"] == "search"
    assert "_finalize_warning" not in result


# ---------------------------------------------------------------------------
# 7. finalize_response with no respond call, no tool_calls, tool_required=True → _finalize_warning
# ---------------------------------------------------------------------------


def test_finalize_response_no_respond_no_tools_tool_required_warning(monkeypatch):
    from scripts import respond_tool_wiring

    response = {
        "content": "some text",
        "tool_calls": [],
    }
    result = respond_tool_wiring.finalize_response(response, tool_required=True)
    assert "_finalize_warning" in result
    assert result["_finalize_warning"]["valid"] is False
    assert result["_finalize_warning"]["issue"] == "terminal_without_respond"


# ---------------------------------------------------------------------------
# 8. finalize_response with no respond call and tool_required=False → no warning
# ---------------------------------------------------------------------------


def test_finalize_response_no_respond_tool_required_false_no_warning(monkeypatch):
    from scripts import respond_tool_wiring

    response = {
        "content": "some text",
        "tool_calls": [],
    }
    result = respond_tool_wiring.finalize_response(response, tool_required=False)
    assert "_finalize_warning" not in result
