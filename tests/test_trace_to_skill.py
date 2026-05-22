"""Tests for :mod:`scripts.trace_to_skill` and :mod:`scripts.skill_capture_ext`.

Covers:
1.  load_recent_traces returns [] on missing dir.
2.  load_recent_traces orders by startedAt descending.
3.  skill_scaffold_candidates filters by min_success threshold (default 3).
4.  skill_scaffold_candidates groups same skill+task even if other fields differ.
5.  skill_repair_candidates filters by min_failures threshold (default 2).
6.  compound_runner_candidates finds sequence repetition; rejects sequences of length 1.
7.  CLI integration test: all three sections appear in stdout.
8.  skill_capture_ext draft-from-task produces a non-empty markdown file
    containing the trace's task name.
9.  skill_capture_ext assess-draft reports missing required sections as numbered
    issues; passes a fully-populated draft.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.trace_to_skill import (
    compound_runner_candidates,
    load_recent_traces,
    skill_repair_candidates,
    skill_scaffold_candidates,
)
from scripts.skill_capture_ext import (
    assess_draft_path,
    draft_from_trace,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TEMPLATE_PATH = ROOT / "references" / "skill-capture-template.md"


def _write_trace(traces_dir: Path, task_id: str, data: dict) -> Path:
    """Write *data* as ``<task_id>.json`` in *traces_dir*."""
    traces_dir.mkdir(parents=True, exist_ok=True)
    path = traces_dir / f"{task_id}.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _make_trace(
    task_id: str,
    *,
    started_at: str = "2026-01-01T00:00:00Z",
    skill: str | None = "my-skill",
    input_summary: str = "do the thing",
    outcome: str = "completed",
    failure_fingerprint: str | None = None,
    tool_names: list[str] | None = None,
) -> dict:
    trace: dict = {
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
    if tool_names is not None:
        trace["toolSequence"] = [
            {"name": n, "purpose": "", "args": {}, "durationMs": 0, "status": "success"}
            for n in tool_names
        ]
    if failure_fingerprint is not None:
        trace["failureSignature"] = {"fingerprint": failure_fingerprint, "component": "test"}
    return trace


# ---------------------------------------------------------------------------
# Test 1 — load_recent_traces returns [] on missing dir
# ---------------------------------------------------------------------------

class TestLoadRecentTracesMissingDir:
    def test_returns_empty_list_when_dir_absent(self, tmp_path):
        missing = tmp_path / "no_such_dir"
        result = load_recent_traces(missing)
        assert result == []

    def test_does_not_raise_on_missing_dir(self, tmp_path):
        missing = tmp_path / "nonexistent"
        try:
            load_recent_traces(missing)
        except Exception as exc:
            pytest.fail(f"Unexpected exception: {exc}")


# ---------------------------------------------------------------------------
# Test 2 — load_recent_traces orders by startedAt descending
# ---------------------------------------------------------------------------

class TestLoadRecentTracesOrdering:
    def test_newer_trace_appears_first(self, tmp_path):
        _write_trace(tmp_path, "old", _make_trace("old", started_at="2026-01-01T00:00:00Z"))
        _write_trace(tmp_path, "new", _make_trace("new", started_at="2026-06-01T00:00:00Z"))
        traces = load_recent_traces(tmp_path)
        assert traces[0]["taskId"] == "new"
        assert traces[1]["taskId"] == "old"

    def test_three_traces_correct_order(self, tmp_path):
        for tid, ts in [("a", "2026-01-01T00:00:00Z"), ("b", "2026-03-01T00:00:00Z"), ("c", "2026-02-01T00:00:00Z")]:
            _write_trace(tmp_path, tid, _make_trace(tid, started_at=ts))
        traces = load_recent_traces(tmp_path)
        ids = [t["taskId"] for t in traces]
        assert ids == ["b", "c", "a"]

    def test_limit_respected(self, tmp_path):
        for i in range(10):
            _write_trace(tmp_path, f"t{i:03}", _make_trace(f"t{i:03}", started_at=f"2026-01-{i+1:02}T00:00:00Z"))
        traces = load_recent_traces(tmp_path, limit=5)
        assert len(traces) == 5


# ---------------------------------------------------------------------------
# Test 3 — skill_scaffold_candidates filters by min_success
# ---------------------------------------------------------------------------

class TestSkillScaffoldCandidatesThreshold:
    def test_excludes_groups_below_threshold(self):
        traces = [_make_trace(f"t{i}", outcome="completed") for i in range(2)]
        result = skill_scaffold_candidates(traces, min_success=3)
        assert result == []

    def test_includes_groups_at_threshold(self):
        traces = [_make_trace(f"t{i}", outcome="completed") for i in range(3)]
        result = skill_scaffold_candidates(traces, min_success=3)
        assert len(result) == 1
        assert result[0]["count"] == 3

    def test_only_completed_outcomes_count(self):
        traces = [
            _make_trace("s1", outcome="completed"),
            _make_trace("s2", outcome="failed"),
            _make_trace("s3", outcome="completed"),
        ]
        result = skill_scaffold_candidates(traces, min_success=2)
        assert len(result) == 1
        assert result[0]["count"] == 2

    def test_default_min_success_is_three(self):
        traces = [_make_trace(f"t{i}", outcome="completed") for i in range(2)]
        assert skill_scaffold_candidates(traces) == []

    def test_sample_trace_ids_capped_at_three(self):
        traces = [_make_trace(f"t{i}", outcome="completed") for i in range(5)]
        result = skill_scaffold_candidates(traces, min_success=3)
        assert len(result[0]["sample_trace_ids"]) <= 3


# ---------------------------------------------------------------------------
# Test 4 — skill_scaffold_candidates groups same skill+task
# ---------------------------------------------------------------------------

class TestSkillScaffoldCandidatesGrouping:
    def test_same_skill_and_task_grouped(self):
        traces = [
            _make_trace("a", skill="skill-x", input_summary="run the pipeline", outcome="completed"),
            _make_trace("b", skill="skill-x", input_summary="run the pipeline", outcome="completed"),
            _make_trace("c", skill="skill-x", input_summary="run the pipeline", outcome="completed"),
        ]
        result = skill_scaffold_candidates(traces, min_success=3)
        assert len(result) == 1
        assert result[0]["count"] == 3

    def test_different_tasks_not_grouped(self):
        traces = [
            _make_trace("a", skill="skill-x", input_summary="do alpha", outcome="completed"),
            _make_trace("b", skill="skill-x", input_summary="do alpha", outcome="completed"),
            _make_trace("c", skill="skill-x", input_summary="do alpha", outcome="completed"),
            _make_trace("d", skill="skill-x", input_summary="do beta", outcome="completed"),
            _make_trace("e", skill="skill-x", input_summary="do beta", outcome="completed"),
            _make_trace("f", skill="skill-x", input_summary="do beta", outcome="completed"),
        ]
        result = skill_scaffold_candidates(traces, min_success=3)
        assert len(result) == 2

    def test_case_insensitive_grouping(self):
        traces = [
            _make_trace("a", input_summary="Run The Pipeline", outcome="completed"),
            _make_trace("b", input_summary="run the pipeline", outcome="completed"),
            _make_trace("c", input_summary="RUN THE PIPELINE", outcome="completed"),
        ]
        result = skill_scaffold_candidates(traces, min_success=3)
        assert len(result) == 1

    def test_other_fields_ignored_for_grouping(self):
        # Same skill+task_name but different profile and outcome date — still grouped.
        traces = [
            {**_make_trace("a", outcome="completed"), "profile": "inspect_local"},
            {**_make_trace("b", outcome="completed"), "profile": "workspace_edit"},
            {**_make_trace("c", outcome="completed"), "profile": "service_ops"},
        ]
        result = skill_scaffold_candidates(traces, min_success=3)
        assert len(result) == 1

    def test_different_skills_not_grouped(self):
        traces = [
            _make_trace("a", skill="skill-a", input_summary="do it", outcome="completed"),
            _make_trace("b", skill="skill-a", input_summary="do it", outcome="completed"),
            _make_trace("c", skill="skill-a", input_summary="do it", outcome="completed"),
            _make_trace("d", skill="skill-b", input_summary="do it", outcome="completed"),
            _make_trace("e", skill="skill-b", input_summary="do it", outcome="completed"),
            _make_trace("f", skill="skill-b", input_summary="do it", outcome="completed"),
        ]
        result = skill_scaffold_candidates(traces, min_success=3)
        assert len(result) == 2


# ---------------------------------------------------------------------------
# Test 5 — skill_repair_candidates filters by min_failures
# ---------------------------------------------------------------------------

class TestSkillRepairCandidatesThreshold:
    def test_excludes_groups_below_threshold(self):
        traces = [_make_trace("t1", failure_fingerprint="fp-abc", outcome="failed")]
        result = skill_repair_candidates(traces, min_failures=2)
        assert result == []

    def test_includes_groups_at_threshold(self):
        traces = [
            _make_trace("t1", failure_fingerprint="fp-abc", outcome="failed"),
            _make_trace("t2", failure_fingerprint="fp-abc", outcome="failed"),
        ]
        result = skill_repair_candidates(traces, min_failures=2)
        assert len(result) == 1
        assert result[0]["fingerprint"] == "fp-abc"
        assert result[0]["count"] == 2

    def test_different_fingerprints_not_grouped(self):
        traces = [
            _make_trace("t1", failure_fingerprint="fp-a", outcome="failed"),
            _make_trace("t2", failure_fingerprint="fp-b", outcome="failed"),
        ]
        result = skill_repair_candidates(traces, min_failures=2)
        assert result == []

    def test_missing_failure_signature_skipped(self):
        traces = [
            _make_trace("t1", outcome="failed"),  # no fingerprint
            _make_trace("t2", failure_fingerprint="fp-abc", outcome="failed"),
            _make_trace("t3", failure_fingerprint="fp-abc", outcome="failed"),
        ]
        result = skill_repair_candidates(traces, min_failures=2)
        assert len(result) == 1

    def test_default_min_failures_is_two(self):
        traces = [_make_trace("t1", failure_fingerprint="fp-abc", outcome="failed")]
        assert skill_repair_candidates(traces) == []

    def test_example_skill_from_matching_trace(self):
        traces = [
            _make_trace("t1", skill="repair-me", failure_fingerprint="fp-xyz", outcome="failed"),
            _make_trace("t2", skill="repair-me", failure_fingerprint="fp-xyz", outcome="failed"),
        ]
        result = skill_repair_candidates(traces, min_failures=2)
        assert result[0]["example_skill"] == "repair-me"


# ---------------------------------------------------------------------------
# Test 6 — compound_runner_candidates finds sequence repetition; rejects len 1
# ---------------------------------------------------------------------------

class TestCompoundRunnerCandidates:
    def test_single_tool_sequence_excluded(self):
        traces = [
            _make_trace(f"t{i}", tool_names=["Bash"])
            for i in range(5)
        ]
        result = compound_runner_candidates(traces, min_repetitions=3)
        assert result == []

    def test_empty_sequence_excluded(self):
        traces = [_make_trace(f"t{i}", tool_names=[]) for i in range(5)]
        result = compound_runner_candidates(traces, min_repetitions=3)
        assert result == []

    def test_repeated_sequence_found(self):
        traces = [
            _make_trace(f"t{i}", tool_names=["Bash", "Read", "Edit"])
            for i in range(4)
        ]
        result = compound_runner_candidates(traces, min_repetitions=3)
        assert len(result) == 1
        assert result[0]["count"] == 4

    def test_sequence_below_threshold_excluded(self):
        traces = [
            _make_trace(f"t{i}", tool_names=["Bash", "Read"])
            for i in range(2)
        ]
        result = compound_runner_candidates(traces, min_repetitions=3)
        assert result == []

    def test_different_sequences_not_grouped(self):
        traces = (
            [_make_trace(f"a{i}", tool_names=["Bash", "Read"]) for i in range(3)]
            + [_make_trace(f"b{i}", tool_names=["Edit", "Bash"]) for i in range(3)]
        )
        result = compound_runner_candidates(traces, min_repetitions=3)
        assert len(result) == 2

    def test_sequence_order_matters(self):
        traces = (
            [_make_trace(f"a{i}", tool_names=["Bash", "Read"]) for i in range(3)]
            + [_make_trace(f"b{i}", tool_names=["Read", "Bash"]) for i in range(3)]
        )
        result = compound_runner_candidates(traces, min_repetitions=3)
        # Different orders → different groups
        assert len(result) == 2

    def test_sample_trace_ids_present(self):
        traces = [_make_trace(f"t{i}", tool_names=["Bash", "Read"]) for i in range(3)]
        result = compound_runner_candidates(traces, min_repetitions=3)
        assert len(result[0]["sample_trace_ids"]) > 0


# ---------------------------------------------------------------------------
# Test 7 — CLI integration: all three sections appear in stdout
# ---------------------------------------------------------------------------

class TestCLIIntegration:
    def _run_cli(self, traces_dir: Path, extra_args: list[str] | None = None) -> str:
        cmd = [
            sys.executable,
            str(ROOT / "scripts" / "trace_to_skill.py"),
            "--traces-dir", str(traces_dir),
        ] + (extra_args or [])
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return result.stdout

    def test_scaffold_section_present(self, tmp_path):
        output = self._run_cli(tmp_path)
        assert "Skill Scaffold Candidates" in output

    def test_repair_section_present(self, tmp_path):
        output = self._run_cli(tmp_path)
        assert "Skill Repair Candidates" in output

    def test_compound_section_present(self, tmp_path):
        output = self._run_cli(tmp_path)
        assert "Compound Runner Candidates" in output

    def test_all_three_sections_from_synthetic_traces(self, tmp_path):
        traces_dir = tmp_path / "traces"
        # Write scaffold traces
        for i in range(3):
            _write_trace(traces_dir, f"scaffold-{i}", _make_trace(f"scaffold-{i}", outcome="completed"))
        # Write repair traces
        for i in range(2):
            _write_trace(
                traces_dir, f"repair-{i}",
                _make_trace(f"repair-{i}", failure_fingerprint="fp-boom", outcome="failed"),
            )
        # Write compound traces
        for i in range(3):
            _write_trace(
                traces_dir, f"compound-{i}",
                _make_trace(f"compound-{i}", tool_names=["Bash", "Read", "Edit"]),
            )
        output = self._run_cli(traces_dir)
        assert "Skill Scaffold Candidates" in output
        assert "Skill Repair Candidates" in output
        assert "Compound Runner Candidates" in output

    def test_exits_zero_on_empty_dir(self, tmp_path):
        cmd = [
            sys.executable,
            str(ROOT / "scripts" / "trace_to_skill.py"),
            "--traces-dir", str(tmp_path / "empty"),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        assert result.returncode == 0


# ---------------------------------------------------------------------------
# Test 8 — draft-from-task produces markdown with task name
# ---------------------------------------------------------------------------

class TestDraftFromTask:
    def test_produces_non_empty_file(self, tmp_path):
        traces_dir = tmp_path / "traces"
        _write_trace(
            traces_dir,
            "task-abc",
            _make_trace("task-abc", input_summary="Deploy the staging service"),
        )
        out_path = draft_from_trace(
            "task-abc",
            traces_dir=traces_dir,
            drafts_dir=tmp_path / "drafts",
        )
        assert out_path.exists()
        assert out_path.stat().st_size > 0

    def test_file_contains_task_name(self, tmp_path):
        traces_dir = tmp_path / "traces"
        _write_trace(
            traces_dir,
            "task-xyz",
            _make_trace("task-xyz", input_summary="Ingest daily feed"),
        )
        out_path = draft_from_trace(
            "task-xyz",
            traces_dir=traces_dir,
            drafts_dir=tmp_path / "drafts",
        )
        text = out_path.read_text(encoding="utf-8")
        assert "Ingest daily feed" in text

    def test_output_is_markdown(self, tmp_path):
        traces_dir = tmp_path / "traces"
        _write_trace(
            traces_dir,
            "task-md",
            _make_trace("task-md", input_summary="Check health metrics"),
        )
        out_path = draft_from_trace(
            "task-md",
            traces_dir=traces_dir,
            drafts_dir=tmp_path / "drafts",
        )
        assert out_path.suffix == ".md"

    def test_raises_on_missing_trace(self, tmp_path):
        with pytest.raises(SystemExit):
            draft_from_trace(
                "no-such-task",
                traces_dir=tmp_path / "empty",
                drafts_dir=tmp_path / "drafts",
            )

    def test_file_contains_task_id(self, tmp_path):
        traces_dir = tmp_path / "traces"
        _write_trace(
            traces_dir,
            "task-provenance",
            _make_trace("task-provenance", input_summary="Run the audit"),
        )
        out_path = draft_from_trace(
            "task-provenance",
            traces_dir=traces_dir,
            drafts_dir=tmp_path / "drafts",
        )
        text = out_path.read_text(encoding="utf-8")
        assert "task-provenance" in text

    def test_via_cli(self, tmp_path):
        """CLI draft-from-task produces a path to an existing file."""
        traces_dir = tmp_path / "traces"
        _write_trace(
            traces_dir,
            "cli-task",
            _make_trace("cli-task", input_summary="CLI generated draft"),
        )
        drafts_dir = tmp_path / "drafts"
        cmd = [
            sys.executable,
            str(ROOT / "scripts" / "skill_capture_ext.py"),
            "draft-from-task",
            "--task-id", "cli-task",
            "--traces-dir", str(traces_dir),
            "--drafts-dir", str(drafts_dir),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        assert result.returncode == 0
        out_path = Path(result.stdout.strip())
        assert out_path.exists()
        assert "CLI generated draft" in out_path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Test 9 — assess-draft: reports missing sections; passes populated draft
# ---------------------------------------------------------------------------

class TestAssessDraftPath:
    def _full_draft(self, tmp_path: Path) -> Path:
        """Write a draft that contains all required sections."""
        draft = tmp_path / "full-draft.md"
        sections = [
            "## Core rule",
            "## Input contract",
            "## Decision contract",
            "## Execution contract",
            "## Output contract",
            "## Post-write validation contract",
            "## Failure contract",
            "## Do",
            "## Do not",
        ]
        content = "---\nname: full-draft\n---\n\n# Full Draft\n\n"
        for sec in sections:
            content += f"{sec}\n\n- item\n\n"
        draft.write_text(content, encoding="utf-8")
        return draft

    def test_passes_fully_populated_draft(self, tmp_path):
        draft = self._full_draft(tmp_path)
        ok, issues = assess_draft_path(draft, template_path=_TEMPLATE_PATH)
        assert ok is True
        assert issues == []

    def test_reports_missing_section_as_numbered_issue(self, tmp_path):
        # Write a draft missing "## Failure contract"
        draft = tmp_path / "partial.md"
        content = (
            "---\nname: partial\n---\n\n# Partial\n\n"
            "## Core rule\n\n- item\n\n"
            "## Input contract\n\n- item\n\n"
            "## Decision contract\n\n- item\n\n"
            "## Execution contract\n\n- item\n\n"
            "## Output contract\n\n- item\n\n"
            "## Post-write validation contract\n\n- item\n\n"
            # "## Failure contract" deliberately missing
            "## Do\n\n- item\n\n"
            "## Do not\n\n- item\n\n"
        )
        draft.write_text(content, encoding="utf-8")
        ok, issues = assess_draft_path(draft, template_path=_TEMPLATE_PATH)
        assert ok is False
        assert len(issues) >= 1
        assert any("Failure contract" in iss for iss in issues)

    def test_multiple_missing_sections_all_reported(self, tmp_path):
        draft = tmp_path / "empty.md"
        draft.write_text("# Empty\n\nNo sections here.\n", encoding="utf-8")
        ok, issues = assess_draft_path(draft, template_path=_TEMPLATE_PATH)
        assert ok is False
        assert len(issues) >= 5  # many sections missing

    def test_issues_are_strings(self, tmp_path):
        draft = tmp_path / "empty2.md"
        draft.write_text("# Nothing\n", encoding="utf-8")
        _ok, issues = assess_draft_path(draft, template_path=_TEMPLATE_PATH)
        for issue in issues:
            assert isinstance(issue, str)

    def test_missing_file_returns_false(self, tmp_path):
        ok, issues = assess_draft_path(tmp_path / "ghost.md", template_path=_TEMPLATE_PATH)
        assert ok is False
        assert len(issues) >= 1

    def test_template_not_found_raises(self, tmp_path):
        draft = tmp_path / "d.md"
        draft.write_text("# X\n", encoding="utf-8")
        with pytest.raises(SystemExit, match="not found"):
            assess_draft_path(draft, template_path=tmp_path / "no-template.md")

    def test_directory_with_skill_md(self, tmp_path):
        """assess_draft_path should resolve SKILL.md when given a directory."""
        skill_dir = tmp_path / "my-skill"
        skill_dir.mkdir()
        skill_md = skill_dir / "SKILL.md"
        # Write a partial SKILL.md
        skill_md.write_text(
            "## Core rule\n\n- item\n",
            encoding="utf-8",
        )
        ok, issues = assess_draft_path(skill_dir, template_path=_TEMPLATE_PATH)
        # Should find missing sections
        assert ok is False

    def test_via_cli_fail(self, tmp_path):
        draft = tmp_path / "incomplete.md"
        draft.write_text("# Incomplete\n", encoding="utf-8")
        cmd = [
            sys.executable,
            str(ROOT / "scripts" / "skill_capture_ext.py"),
            "assess-draft",
            "--draft-path", str(draft),
            "--template", str(_TEMPLATE_PATH),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        assert result.returncode != 0
        # Output should have numbered issues
        assert "1." in result.stdout

    def test_via_cli_pass(self, tmp_path):
        draft = self._full_draft(tmp_path)
        cmd = [
            sys.executable,
            str(ROOT / "scripts" / "skill_capture_ext.py"),
            "assess-draft",
            "--draft-path", str(draft),
            "--template", str(_TEMPLATE_PATH),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        assert result.returncode == 0
        assert "OK" in result.stdout
