"""Regression tests for defects found in the 0.13.0 adversarial review.

Each test would have FAILED (crash or wrong output) before its fix.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# --- skill_router: one malformed on-disk manifest must not crash the router ---
def test_route_skill_tolerates_malformed_manifest():
    from scripts.skill_router import route_skill

    manifests = {
        "ok-skill": {"route_decision": {"task_type": "generic"}},
        "bad": {"route_decision": ["not", "a", "dict"]},  # would raise in scorer
    }
    result = route_skill("do something", manifests)  # must not raise
    assert result["decision"] in {"direct", "candidates", "none"}


# --- request_intake: a non-dict (untrusted) payload must ACK, not crash ---
def test_accept_request_tolerates_non_dict_payload():
    from scripts.request_intake import accept_request

    state: dict = {}
    result = accept_request(state, "d1", ["not", "a", "dict"])  # must not raise
    assert result["ack"] is True
    assert result["status"] == "accepted"
    # duplicate of the same delivery still collapses
    dup = accept_request(state, "d1", ["not", "a", "dict"])
    assert dup["status"] == "duplicate"
    assert dup["task_id"] == result["task_id"]


# --- grounding: template substitution is single-pass (no bleed, order-independent) ---
def test_template_substitution_no_bleed_and_order_independent(tmp_path):
    from scripts.grounding import render_deterministic_template

    skill = tmp_path / "s"
    (skill / "templates").mkdir(parents=True)
    (skill / "templates" / "fallback.md").write_text("X: __X__\nY: __Y__\n", encoding="utf-8")

    out1 = render_deterministic_template(skill, {"x": "__Y__", "y": "42"})
    out2 = render_deterministic_template(skill, {"y": "42", "x": "__Y__"})
    assert out1 == out2  # independent of dict order
    assert out1 == "X: __Y__\nY: 42\n"  # value "__Y__" is NOT re-substituted


# --- grounding: None repair budget means "unlimited", not a TypeError ---
def test_should_use_deterministic_fallback_handles_none_budget():
    from scripts.grounding import should_use_deterministic_fallback

    assert should_use_deterministic_fallback("frontier", None) is False
    assert should_use_deterministic_fallback("deterministic_only", None) is True
    assert should_use_deterministic_fallback("frontier", 0) is True
    assert should_use_deterministic_fallback("frontier", 3) is False


# --- tool_adapter: a raising guard must be contained, not propagated ---
def test_invoke_tool_contains_raising_guard():
    from scripts.tool_adapter import invoke_tool

    class _Adapter:
        def describe(self):
            return {"name": "a"}

        def invoke(self, op, args):
            return {"ok": True}

    def _bad_guard(name, op, args):
        raise KeyError("unclassified tool")

    result = invoke_tool({"a": _Adapter()}, "a", "op", {}, guard=_bad_guard)
    assert result["ok"] is False
    assert result["error"] == "guard_error"


# --- tool_adapter: a non-dict adapter return is normalized, not leaked ---
def test_invoke_tool_normalizes_non_dict_return():
    from scripts.tool_adapter import invoke_tool

    class _Adapter:
        def describe(self):
            return {"name": "a"}

        def invoke(self, op, args):
            return None  # violates the -> dict contract

    result = invoke_tool({"a": _Adapter()}, "a", "op", {})
    assert result["ok"] is False
    assert result["error"] == "adapter_bad_return"
