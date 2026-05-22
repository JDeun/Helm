"""Tests for scripts/shadow_mode_recommendation.py (Wave 6).

Coverage matrix
---------------
1.  Empty report (no features) → all keys absent from result (empty dict).
2.  browser_verifier: high block rate → caution.
3.  browser_verifier: 5 shadow_count → needs_more_data.
4.  browser_verifier: 50 shadow_count with low block rate → ready_to_enforce.
5.  pause_gate: 0 blocks → no_signal.
6.  pause_gate: 3 blocks → ready_to_enforce.
7.  model_repair: 4 events → needs_more_data.
8.  model_repair: 60% abort rate → caution.
9.  synthetic_respond: 0 terminal events → no_signal.
10. cleanup_evidence_gate: 40% missing → caution.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.shadow_mode_recommendation import recommend


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_report(features: dict) -> dict:
    """Minimal report dict with just the features needed."""
    return {
        "generated_at": "2026-05-22T00:00:00+00:00",
        "window": {"days": 14, "since": "2026-05-08T00:00:00+00:00", "until": "2026-05-22T00:00:00+00:00"},
        "data_freshness": {},
        "features": features,
        "raw_filter_applied": None,
    }


def _browser_feature(
    shadow_count: int,
    block_mutation_true: int = 0,
    enforced_block: int = 0,
    enforced_approval: int = 0,
) -> dict:
    return {
        "browser_verifier": {
            "shadow_count": shadow_count,
            "enforced_block_count": enforced_block,
            "enforced_approval_count": enforced_approval,
            "decision_breakdown": {"block_mutation_true": block_mutation_true},
            "samples": [],
        }
    }


def _pause_feature(blocked_count: int) -> dict:
    return {
        "pause_gate": {
            "blocked_count": blocked_count,
            "samples": [],
        }
    }


def _repair_feature(
    event_count: int,
    ok: int = 0,
    nudge: int = 0,
    abort: int = 0,
    give_up: int = 0,
) -> dict:
    return {
        "model_repair": {
            "event_count": event_count,
            "verdict_breakdown": {"ok": ok, "nudge_and_retry": nudge, "abort": abort, "give_up": give_up},
            "shadow_event_count": 0,
            "top_issues": [],
            "samples": [],
        }
    }


def _synthetic_feature(terminal: int, would_have_helped: int) -> dict:
    return {
        "synthetic_respond_inferred": {
            "terminal_without_tool_events": terminal,
            "would_have_helped_estimate": would_have_helped,
        }
    }


def _cleanup_feature(required: int, missing: int) -> dict:
    return {
        "cleanup_evidence_gate": {
            "required_count": required,
            "missing_cleanup_count": missing,
            "exit_28_count": 0,
        }
    }


# ---------------------------------------------------------------------------
# Test 1: Empty report → empty result
# ---------------------------------------------------------------------------

class TestEmptyReport:
    def test_empty_features_empty_result(self):
        report = _make_report({})
        result = recommend(report)
        assert result == {}

    def test_missing_features_key(self):
        result = recommend({"generated_at": "x", "window": {}, "data_freshness": {}})
        assert result == {}


# ---------------------------------------------------------------------------
# Test 2: browser_verifier caution
# ---------------------------------------------------------------------------

class TestBrowserVerifierCaution:
    def test_high_block_rate_caution(self):
        # 10 shadow, 6 block_mutation → 60% rate → caution
        report = _make_report(_browser_feature(shadow_count=10, block_mutation_true=6))
        result = recommend(report)
        assert result["browser_verifier"]["verdict"] == "caution"

    def test_caution_reason_not_empty(self):
        report = _make_report(_browser_feature(shadow_count=10, block_mutation_true=8))
        result = recommend(report)
        assert result["browser_verifier"]["reason"]
        assert result["browser_verifier"]["next_step"]


# ---------------------------------------------------------------------------
# Test 3: browser_verifier needs_more_data
# ---------------------------------------------------------------------------

class TestBrowserVerifierNeedsMoreData:
    def test_five_shadow_needs_more_data(self):
        report = _make_report(_browser_feature(shadow_count=5))
        result = recommend(report)
        assert result["browser_verifier"]["verdict"] == "needs_more_data"

    def test_reason_mentions_count(self):
        report = _make_report(_browser_feature(shadow_count=5))
        result = recommend(report)
        assert "5" in result["browser_verifier"]["reason"]


# ---------------------------------------------------------------------------
# Test 4: browser_verifier ready_to_enforce
# ---------------------------------------------------------------------------

class TestBrowserVerifierReady:
    def test_fifty_shadow_low_block_rate(self):
        # 50 shadow, 4 block_mutation → 8% → ready_to_enforce
        report = _make_report(_browser_feature(shadow_count=50, block_mutation_true=4))
        result = recommend(report)
        assert result["browser_verifier"]["verdict"] == "ready_to_enforce"

    def test_exactly_at_threshold(self):
        # 10 shadow, 4 block_mutation → 40% < 50% → ready_to_enforce
        report = _make_report(_browser_feature(shadow_count=10, block_mutation_true=4))
        result = recommend(report)
        assert result["browser_verifier"]["verdict"] == "ready_to_enforce"


# ---------------------------------------------------------------------------
# Test 5: pause_gate no_signal
# ---------------------------------------------------------------------------

class TestPauseGateNoSignal:
    def test_zero_blocks_no_signal(self):
        report = _make_report(_pause_feature(blocked_count=0))
        result = recommend(report)
        assert result["pause_gate"]["verdict"] == "no_signal"


# ---------------------------------------------------------------------------
# Test 6: pause_gate ready_to_enforce
# ---------------------------------------------------------------------------

class TestPauseGateReady:
    def test_three_blocks_ready(self):
        report = _make_report(_pause_feature(blocked_count=3))
        result = recommend(report)
        assert result["pause_gate"]["verdict"] == "ready_to_enforce"

    def test_one_block_ready(self):
        report = _make_report(_pause_feature(blocked_count=1))
        result = recommend(report)
        assert result["pause_gate"]["verdict"] == "ready_to_enforce"


# ---------------------------------------------------------------------------
# Test 7: model_repair needs_more_data
# ---------------------------------------------------------------------------

class TestModelRepairNeedsMoreData:
    def test_four_events_needs_more_data(self):
        report = _make_report(_repair_feature(event_count=4, ok=4))
        result = recommend(report)
        assert result["model_repair"]["verdict"] == "needs_more_data"

    def test_zero_events_needs_more_data(self):
        report = _make_report(_repair_feature(event_count=0))
        result = recommend(report)
        assert result["model_repair"]["verdict"] == "needs_more_data"


# ---------------------------------------------------------------------------
# Test 8: model_repair caution (60% abort rate)
# ---------------------------------------------------------------------------

class TestModelRepairCaution:
    def test_sixty_percent_abort_caution(self):
        # 10 events: 3 ok, 1 nudge, 4 abort, 2 give_up → abort+give_up=6/10=60%
        report = _make_report(_repair_feature(
            event_count=10, ok=3, nudge=1, abort=4, give_up=2
        ))
        result = recommend(report)
        assert result["model_repair"]["verdict"] == "caution"

    def test_exactly_at_threshold_caution(self):
        # 10 events: 5 ok, 5 abort → 50% → caution (>= threshold)
        report = _make_report(_repair_feature(event_count=10, ok=5, abort=5))
        result = recommend(report)
        assert result["model_repair"]["verdict"] == "caution"

    def test_below_threshold_ready(self):
        # 10 events: 6 ok, 2 nudge, 2 abort → 20% < 50% → ready_to_enforce
        report = _make_report(_repair_feature(event_count=10, ok=6, nudge=2, abort=2))
        result = recommend(report)
        assert result["model_repair"]["verdict"] == "ready_to_enforce"


# ---------------------------------------------------------------------------
# Test 9: synthetic_respond no_signal
# ---------------------------------------------------------------------------

class TestSyntheticRespondNoSignal:
    def test_zero_terminal_events_no_signal(self):
        report = _make_report(_synthetic_feature(terminal=0, would_have_helped=0))
        result = recommend(report)
        assert result["synthetic_respond_inferred"]["verdict"] == "no_signal"


# ---------------------------------------------------------------------------
# Test 10: cleanup_evidence_gate caution (40% missing)
# ---------------------------------------------------------------------------

class TestCleanupEvidenceCaution:
    def test_forty_percent_missing_caution(self):
        # 10 required, 4 missing → 40% >= 30% threshold → caution
        report = _make_report(_cleanup_feature(required=10, missing=4))
        result = recommend(report)
        assert result["cleanup_evidence_gate"]["verdict"] == "caution"

    def test_low_missing_ready(self):
        # 10 required, 2 missing → 20% < 30% → ready_to_enforce
        report = _make_report(_cleanup_feature(required=10, missing=2))
        result = recommend(report)
        assert result["cleanup_evidence_gate"]["verdict"] == "ready_to_enforce"

    def test_zero_required_no_signal(self):
        report = _make_report(_cleanup_feature(required=0, missing=0))
        result = recommend(report)
        assert result["cleanup_evidence_gate"]["verdict"] == "no_signal"
