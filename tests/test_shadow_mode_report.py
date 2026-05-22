"""Tests for scripts/shadow_mode_report.py (Wave 6).

Coverage matrix
---------------
1.  Empty ledger and empty events → report has all features with zero counts.
2.  Synthetic ledger: 5 browser_recon_shadow + 2 browser_blocked + 1 browser_requires_approval.
3.  Decision breakdown — each flag in each shadow row is counted.
4.  Samples capped at 5.
5.  Pause gate: 3 blocked_by_pause rows → counted, samples populated.
6.  Model repair: synthetic proxy-events file with verdicts → breakdown correct.
7.  Top issues: 7 distinct issue codes → top 5 returned in count-descending order.
8.  Synthetic respond inferred: counts terminal_without_tool events.
9.  Skill promotion: state file with mix of statuses → counts match.
10. Max sessions: 2 max_sessions hits across 2 profiles → by_profile dict correct.
11. Cleanup evidence: pairs of require / missing detected.
12. Window filter: rows older than since_days excluded; freshness shows scanned count.
13. tail_lines respected — 10k lines, tail=5000, only last 5000 scanned.
14. feature_filter: only requested features in result.
15. to_markdown produces a string with headers per feature, no Python repr leakage.
16. to_markdown handles empty report (all-zero counts) gracefully.
17. Sample list never includes raw user-home paths (/Users/kevin/).
18. Timestamps in output are ISO8601 UTC.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.shadow_mode_report import generate_report, to_markdown


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _iso_now(offset_days: int = 0) -> str:
    dt = datetime.now(tz=timezone.utc) + timedelta(days=offset_days)
    return dt.strftime("%Y-%m-%dT%H:%M:%S+00:00")


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")


def _default_report(tmp_path: Path, **kwargs) -> dict:
    """Call generate_report with tmp_path file overrides and no live paths."""
    ledger = tmp_path / "ledger.jsonl"
    proxy = tmp_path / "proxy.jsonl"
    skill_state = tmp_path / "skill-state.json"
    if not ledger.exists():
        _write_jsonl(ledger, [])
    if not proxy.exists():
        _write_jsonl(proxy, [])
    return generate_report(
        ledger_path=ledger,
        proxy_events_path=proxy,
        skill_state_path=skill_state,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Test 1: Empty ledger and events → all features with zero counts
# ---------------------------------------------------------------------------

class TestEmptyInputs:
    def test_all_features_present(self, tmp_path):
        report = _default_report(tmp_path)
        features = report["features"]
        for key in (
            "browser_verifier", "pause_gate", "model_repair",
            "synthetic_respond_inferred", "skill_promotion",
            "max_sessions_hits", "cleanup_evidence_gate",
        ):
            assert key in features, f"Missing feature: {key}"

    def test_browser_verifier_zeros(self, tmp_path):
        report = _default_report(tmp_path)
        bv = report["features"]["browser_verifier"]
        assert bv["shadow_count"] == 0
        assert bv["enforced_block_count"] == 0
        assert bv["enforced_approval_count"] == 0
        assert bv["samples"] == []

    def test_pause_gate_zero(self, tmp_path):
        report = _default_report(tmp_path)
        assert report["features"]["pause_gate"]["blocked_count"] == 0

    def test_model_repair_zero(self, tmp_path):
        report = _default_report(tmp_path)
        mr = report["features"]["model_repair"]
        assert mr["event_count"] == 0
        assert mr["shadow_event_count"] == 0

    def test_skill_promotion_zero(self, tmp_path):
        report = _default_report(tmp_path)
        sp = report["features"]["skill_promotion"]
        assert sp["candidates_notified"] == 0
        assert sp["approved"] == 0

    def test_data_freshness_present(self, tmp_path):
        report = _default_report(tmp_path)
        df = report["data_freshness"]
        assert "ledger_lines_scanned" in df
        assert "proxy_events_lines_scanned" in df
        assert "skill_state_present" in df


# ---------------------------------------------------------------------------
# Test 2: Browser verifier counts
# ---------------------------------------------------------------------------

class TestBrowserVerifierCounts:
    def test_shadow_blocked_approval_counts(self, tmp_path):
        rows = (
            [{"status": "browser_recon_shadow", "task_id": f"t{i}", "updated_at": _iso_now()}
             for i in range(5)]
            + [{"status": "browser_blocked", "task_id": f"b{i}", "updated_at": _iso_now()}
               for i in range(2)]
            + [{"status": "browser_requires_approval", "task_id": "a0", "updated_at": _iso_now()}]
        )
        ledger = tmp_path / "ledger.jsonl"
        _write_jsonl(ledger, rows)
        proxy = tmp_path / "proxy.jsonl"
        _write_jsonl(proxy, [])
        report = generate_report(
            ledger_path=ledger,
            proxy_events_path=proxy,
            skill_state_path=tmp_path / "no-state.json",
        )
        bv = report["features"]["browser_verifier"]
        assert bv["shadow_count"] == 5
        assert bv["enforced_block_count"] == 2
        assert bv["enforced_approval_count"] == 1


# ---------------------------------------------------------------------------
# Test 3: Decision breakdown flags
# ---------------------------------------------------------------------------

class TestDecisionBreakdown:
    def test_each_flag_counted(self, tmp_path):
        rows = [
            {
                "status": "browser_recon_shadow",
                "task_id": "t1",
                "updated_at": _iso_now(),
                "block_mutation": True,
                "require_cleanup_evidence": True,
                "require_confirmation": True,
            },
            {
                "status": "browser_recon_shadow",
                "task_id": "t2",
                "updated_at": _iso_now(),
                "block_mutation": True,
                "allow_single_session": True,
            },
        ]
        ledger = tmp_path / "ledger.jsonl"
        _write_jsonl(ledger, rows)
        proxy = tmp_path / "proxy.jsonl"
        _write_jsonl(proxy, [])
        report = generate_report(
            ledger_path=ledger,
            proxy_events_path=proxy,
            skill_state_path=tmp_path / "no-state.json",
        )
        bd = report["features"]["browser_verifier"]["decision_breakdown"]
        assert bd.get("block_mutation_true", 0) == 2
        assert bd.get("require_cleanup_evidence_true", 0) == 1
        assert bd.get("require_confirmation_true", 0) == 1
        assert bd.get("allow_single_session_true", 0) == 1


# ---------------------------------------------------------------------------
# Test 4: Samples capped at 5
# ---------------------------------------------------------------------------

class TestSamplesCap:
    def test_samples_capped_at_five(self, tmp_path):
        rows = [
            {"status": "browser_recon_shadow", "task_id": f"task{i:03d}", "updated_at": _iso_now()}
            for i in range(10)
        ]
        ledger = tmp_path / "ledger.jsonl"
        _write_jsonl(ledger, rows)
        proxy = tmp_path / "proxy.jsonl"
        _write_jsonl(proxy, [])
        report = generate_report(
            ledger_path=ledger,
            proxy_events_path=proxy,
            skill_state_path=tmp_path / "no-state.json",
        )
        samples = report["features"]["browser_verifier"]["samples"]
        assert len(samples) <= 5


# ---------------------------------------------------------------------------
# Test 5: Pause gate
# ---------------------------------------------------------------------------

class TestPauseGate:
    def test_blocked_count_and_samples(self, tmp_path):
        rows = [
            {"status": "blocked_by_pause", "task_id": f"pause{i}", "updated_at": _iso_now()}
            for i in range(3)
        ]
        ledger = tmp_path / "ledger.jsonl"
        _write_jsonl(ledger, rows)
        proxy = tmp_path / "proxy.jsonl"
        _write_jsonl(proxy, [])
        report = generate_report(
            ledger_path=ledger,
            proxy_events_path=proxy,
            skill_state_path=tmp_path / "no-state.json",
        )
        pg = report["features"]["pause_gate"]
        assert pg["blocked_count"] == 3
        assert len(pg["samples"]) >= 1


# ---------------------------------------------------------------------------
# Test 6: Model repair verdict breakdown
# ---------------------------------------------------------------------------

class TestModelRepair:
    def test_verdict_breakdown(self, tmp_path):
        rows = [
            {"verdict": "ok", "timestamp": _iso_now()},
            {"verdict": "ok", "timestamp": _iso_now()},
            {"verdict": "nudge_and_retry", "timestamp": _iso_now()},
            {"verdict": "abort", "timestamp": _iso_now()},
            {"verdict": "give_up", "timestamp": _iso_now()},
        ]
        ledger = tmp_path / "ledger.jsonl"
        _write_jsonl(ledger, [])
        proxy = tmp_path / "proxy.jsonl"
        _write_jsonl(proxy, rows)
        report = generate_report(
            ledger_path=ledger,
            proxy_events_path=proxy,
            skill_state_path=tmp_path / "no-state.json",
        )
        vbd = report["features"]["model_repair"]["verdict_breakdown"]
        assert vbd["ok"] == 2
        assert vbd["nudge_and_retry"] == 1
        assert vbd["abort"] == 1
        assert vbd["give_up"] == 1
        assert report["features"]["model_repair"]["event_count"] == 5

    def test_shadow_event_count(self, tmp_path):
        rows = [
            {"verdict": "ok", "shadow_mode": True, "timestamp": _iso_now()},
            {"verdict": "ok", "shadow_mode": False, "timestamp": _iso_now()},
            {"verdict": "ok", "timestamp": _iso_now()},
        ]
        ledger = tmp_path / "ledger.jsonl"
        _write_jsonl(ledger, [])
        proxy = tmp_path / "proxy.jsonl"
        _write_jsonl(proxy, rows)
        report = generate_report(
            ledger_path=ledger,
            proxy_events_path=proxy,
            skill_state_path=tmp_path / "no-state.json",
        )
        assert report["features"]["model_repair"]["shadow_event_count"] == 1


# ---------------------------------------------------------------------------
# Test 7: Top issues — 7 distinct codes → top 5
# ---------------------------------------------------------------------------

class TestTopIssues:
    def test_top_five_in_count_order(self, tmp_path):
        # Create events with varying issue counts
        # issue_a: 7, issue_b: 6, issue_c: 5, issue_d: 4, issue_e: 3, issue_f: 2, issue_g: 1
        rows = []
        for code, count in [
            ("issue_a", 7), ("issue_b", 6), ("issue_c", 5),
            ("issue_d", 4), ("issue_e", 3), ("issue_f", 2), ("issue_g", 1),
        ]:
            for _ in range(count):
                rows.append({
                    "verdict": "nudge_and_retry",
                    "issues": [code],
                    "timestamp": _iso_now(),
                })
        ledger = tmp_path / "ledger.jsonl"
        _write_jsonl(ledger, [])
        proxy = tmp_path / "proxy.jsonl"
        _write_jsonl(proxy, rows)
        report = generate_report(
            ledger_path=ledger,
            proxy_events_path=proxy,
            skill_state_path=tmp_path / "no-state.json",
        )
        top = report["features"]["model_repair"]["top_issues"]
        assert len(top) == 5
        # Must be in descending order
        counts = [item["count"] for item in top]
        assert counts == sorted(counts, reverse=True)
        # Top issue must be issue_a
        assert top[0]["issue"] == "issue_a"
        # issue_f and issue_g must not appear
        issue_names = {item["issue"] for item in top}
        assert "issue_g" not in issue_names
        assert "issue_f" not in issue_names


# ---------------------------------------------------------------------------
# Test 8: Synthetic respond inferred
# ---------------------------------------------------------------------------

class TestSyntheticRespondInferred:
    def test_counts_terminal_without_tool(self, tmp_path):
        rows = [
            {"issues": ["terminal_without_tool"], "tool_required": True, "timestamp": _iso_now()},
            {"issues": ["terminal_without_tool"], "tool_required": False, "timestamp": _iso_now()},
            {"issues": ["some_other_issue"], "timestamp": _iso_now()},
        ]
        ledger = tmp_path / "ledger.jsonl"
        _write_jsonl(ledger, [])
        proxy = tmp_path / "proxy.jsonl"
        _write_jsonl(proxy, rows)
        report = generate_report(
            ledger_path=ledger,
            proxy_events_path=proxy,
            skill_state_path=tmp_path / "no-state.json",
        )
        sr = report["features"]["synthetic_respond_inferred"]
        assert sr["terminal_without_tool_events"] == 2
        assert sr["would_have_helped_estimate"] == 1


# ---------------------------------------------------------------------------
# Test 9: Skill promotion state
# ---------------------------------------------------------------------------

class TestSkillPromotion:
    def test_status_counts(self, tmp_path):
        state = {
            "entries": [
                {"candidate_id": "aaa", "status": "approved", "notified_at": _iso_now()},
                {"candidate_id": "bbb", "status": "rejected", "notified_at": _iso_now()},
                {"candidate_id": "ccc", "status": "notified", "notified_at": _iso_now()},
                {"candidate_id": "ddd", "status": "notified", "notified_at": _iso_now()},
            ]
        }
        skill_state = tmp_path / "skill-state.json"
        skill_state.write_text(json.dumps(state), encoding="utf-8")
        ledger = tmp_path / "ledger.jsonl"
        _write_jsonl(ledger, [])
        proxy = tmp_path / "proxy.jsonl"
        _write_jsonl(proxy, [])
        report = generate_report(
            ledger_path=ledger,
            proxy_events_path=proxy,
            skill_state_path=skill_state,
        )
        sp = report["features"]["skill_promotion"]
        assert sp["candidates_notified"] == 4
        assert sp["approved"] == 1
        assert sp["rejected"] == 1
        assert sp["pending"] == 2


# ---------------------------------------------------------------------------
# Test 10: Max sessions
# ---------------------------------------------------------------------------

class TestMaxSessions:
    def test_by_profile_dict(self, tmp_path):
        rows = [
            {
                "status": "browser_blocked",
                "reason": "max_sessions exceeded",
                "profile": "service_ops",
                "task_id": "ms1",
                "updated_at": _iso_now(),
            },
            {
                "status": "browser_blocked",
                "reason": "max_sessions exceeded",
                "profile": "code_review",
                "task_id": "ms2",
                "updated_at": _iso_now(),
            },
            {
                "status": "browser_blocked",
                "reason": "some other reason",
                "profile": "service_ops",
                "task_id": "ms3",
                "updated_at": _iso_now(),
            },
        ]
        ledger = tmp_path / "ledger.jsonl"
        _write_jsonl(ledger, rows)
        proxy = tmp_path / "proxy.jsonl"
        _write_jsonl(proxy, [])
        report = generate_report(
            ledger_path=ledger,
            proxy_events_path=proxy,
            skill_state_path=tmp_path / "no-state.json",
        )
        ms = report["features"]["max_sessions_hits"]
        assert ms["count"] == 2
        assert ms["by_profile"]["service_ops"] == 1
        assert ms["by_profile"]["code_review"] == 1


# ---------------------------------------------------------------------------
# Test 11: Cleanup evidence gate
# ---------------------------------------------------------------------------

class TestCleanupEvidenceGate:
    def test_required_and_missing(self, tmp_path):
        rows = [
            # requires cleanup, no subsequent cleanup → missing
            {
                "status": "browser_recon_shadow",
                "task_id": "t1",
                "require_cleanup_evidence": True,
                "updated_at": _iso_now(),
            },
            # requires cleanup, task has cleanup status → NOT missing
            {
                "status": "browser_recon_shadow",
                "task_id": "t2",
                "require_cleanup_evidence": True,
                "updated_at": _iso_now(),
            },
            # cleanup status for t2 — should prevent t2 from being missing
            {
                "task_id": "t2",
                "cleanup_status": "done",
                "updated_at": _iso_now(),
            },
            # does not require cleanup — not counted
            {
                "status": "browser_recon_shadow",
                "task_id": "t3",
                "require_cleanup_evidence": False,
                "updated_at": _iso_now(),
            },
        ]
        ledger = tmp_path / "ledger.jsonl"
        _write_jsonl(ledger, rows)
        proxy = tmp_path / "proxy.jsonl"
        _write_jsonl(proxy, [])
        report = generate_report(
            ledger_path=ledger,
            proxy_events_path=proxy,
            skill_state_path=tmp_path / "no-state.json",
        )
        ceg = report["features"]["cleanup_evidence_gate"]
        assert ceg["required_count"] == 2
        assert ceg["missing_cleanup_count"] == 1


# ---------------------------------------------------------------------------
# Test 12: Window filter
# ---------------------------------------------------------------------------

class TestWindowFilter:
    def test_old_rows_excluded(self, tmp_path):
        old_ts = (datetime.now(tz=timezone.utc) - timedelta(days=20)).strftime(
            "%Y-%m-%dT%H:%M:%S+00:00"
        )
        new_ts = _iso_now()
        rows = [
            {"status": "browser_recon_shadow", "task_id": "old1", "updated_at": old_ts},
            {"status": "browser_recon_shadow", "task_id": "new1", "updated_at": new_ts},
        ]
        ledger = tmp_path / "ledger.jsonl"
        _write_jsonl(ledger, rows)
        proxy = tmp_path / "proxy.jsonl"
        _write_jsonl(proxy, [])
        report = generate_report(
            ledger_path=ledger,
            proxy_events_path=proxy,
            skill_state_path=tmp_path / "no-state.json",
            since_days=14,
        )
        assert report["features"]["browser_verifier"]["shadow_count"] == 1

    def test_freshness_shows_scanned_count(self, tmp_path):
        old_ts = (datetime.now(tz=timezone.utc) - timedelta(days=20)).strftime(
            "%Y-%m-%dT%H:%M:%S+00:00"
        )
        rows = [{"status": "x", "updated_at": old_ts} for _ in range(5)]
        ledger = tmp_path / "ledger.jsonl"
        _write_jsonl(ledger, rows)
        proxy = tmp_path / "proxy.jsonl"
        _write_jsonl(proxy, [])
        report = generate_report(
            ledger_path=ledger,
            proxy_events_path=proxy,
            skill_state_path=tmp_path / "no-state.json",
            since_days=14,
        )
        # scanned = 5 (tail read), but all excluded from features due to window
        assert report["data_freshness"]["ledger_lines_scanned"] == 5


# ---------------------------------------------------------------------------
# Test 13: tail_lines respected
# ---------------------------------------------------------------------------

class TestTailLines:
    def test_only_last_n_lines_scanned(self, tmp_path):
        # Write 200 rows, read tail=100 → only 100 scanned
        rows = [
            {"status": "browser_recon_shadow", "task_id": f"t{i}", "updated_at": _iso_now()}
            for i in range(200)
        ]
        ledger = tmp_path / "ledger.jsonl"
        _write_jsonl(ledger, rows)
        proxy = tmp_path / "proxy.jsonl"
        _write_jsonl(proxy, [])
        report = generate_report(
            ledger_path=ledger,
            proxy_events_path=proxy,
            skill_state_path=tmp_path / "no-state.json",
            tail_lines=100,
        )
        assert report["data_freshness"]["ledger_lines_scanned"] == 100
        assert report["features"]["browser_verifier"]["shadow_count"] == 100


# ---------------------------------------------------------------------------
# Test 14: feature_filter
# ---------------------------------------------------------------------------

class TestFeatureFilter:
    def test_only_requested_features_in_result(self, tmp_path):
        report = _default_report(tmp_path, feature_filter=["browser_verifier"])
        features = report["features"]
        assert "browser_verifier" in features
        assert "pause_gate" not in features
        assert "model_repair" not in features

    def test_filter_echoed_in_report(self, tmp_path):
        report = _default_report(tmp_path, feature_filter=["pause_gate"])
        assert report["raw_filter_applied"] == ["pause_gate"]


# ---------------------------------------------------------------------------
# Test 15: to_markdown
# ---------------------------------------------------------------------------

class TestToMarkdown:
    def test_has_headers_per_feature(self, tmp_path):
        report = _default_report(tmp_path)
        md = to_markdown(report)
        assert "## Feature: browser_verifier" in md
        assert "## Feature: pause_gate" in md
        assert "## Feature: model_repair" in md

    def test_no_python_repr_leakage(self, tmp_path):
        report = _default_report(tmp_path)
        md = to_markdown(report)
        # Python repr leakage would include things like "defaultdict" or raw class names
        assert "defaultdict" not in md
        assert "<class" not in md
        assert "object at 0x" not in md


# ---------------------------------------------------------------------------
# Test 16: to_markdown with empty report
# ---------------------------------------------------------------------------

class TestToMarkdownEmpty:
    def test_graceful_empty(self, tmp_path):
        report = _default_report(tmp_path)
        md = to_markdown(report)
        assert isinstance(md, str)
        assert len(md) > 0
        assert "# Shadow-Mode Report" in md

    def test_zero_counts_rendered(self, tmp_path):
        report = _default_report(tmp_path)
        md = to_markdown(report)
        assert "Shadow events: 0" in md or "shadow_count" not in md


# ---------------------------------------------------------------------------
# Test 17: Samples never include raw user-home paths
# ---------------------------------------------------------------------------

class TestNoPathLeakage:
    def test_samples_no_home_path(self, tmp_path):
        # Use a task_id that looks like a path
        rows = [
            {
                "status": "browser_recon_shadow",
                "task_id": "/Users/kevin/some/absolute/path/task123",
                "updated_at": _iso_now(),
            }
        ]
        ledger = tmp_path / "ledger.jsonl"
        _write_jsonl(ledger, rows)
        proxy = tmp_path / "proxy.jsonl"
        _write_jsonl(proxy, [])
        report = generate_report(
            ledger_path=ledger,
            proxy_events_path=proxy,
            skill_state_path=tmp_path / "no-state.json",
        )
        samples = report["features"]["browser_verifier"]["samples"]
        for s in samples:
            assert "/Users/kevin/" not in s, f"Path leaked into sample: {s}"


# ---------------------------------------------------------------------------
# Test 18: Timestamps in output are ISO8601 UTC
# ---------------------------------------------------------------------------

class TestTimestamps:
    def test_generated_at_is_iso8601_utc(self, tmp_path):
        report = _default_report(tmp_path)
        gen_at = report["generated_at"]
        # Should parse without error
        try:
            dt = datetime.fromisoformat(gen_at.replace("+00:00", "+00:00"))
        except ValueError:
            # Try with Z suffix
            dt = datetime.strptime(gen_at, "%Y-%m-%dT%H:%M:%S+00:00")
        assert dt.tzinfo is not None or "+00:00" in gen_at

    def test_window_since_until_are_iso8601(self, tmp_path):
        report = _default_report(tmp_path)
        for key in ("since", "until"):
            val = report["window"][key]
            assert "T" in val
            assert val.endswith("+00:00") or val.endswith("Z"), f"{key}={val!r}"
