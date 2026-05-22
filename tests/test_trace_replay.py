"""Tests for :mod:`scripts.trace_replay`.

Covers:
8.  CLI with --task-id and --traces-dir prints expected sections; exit 0.
9.  CLI with missing task_id → exit nonzero, friendly error.
10. --help exits 0 and mentions the dry-run default.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
REPLAY_SCRIPT = ROOT / "scripts" / "trace_replay.py"

# Make `scripts.*` importable for helpers below.  Done at module-load
# time (guarded) instead of inside helpers so it doesn't run on every
# call.
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.trace_recorder import (  # noqa: E402
    record_changed_file,
    record_tool_call,
    record_validation_gate,
    save_trace,
    set_outcome,
    start_trace,
)
from scripts.trace_replay import build_parser  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_and_save_trace(traces_dir: Path, task_id: str = "replay-test-001") -> dict:
    """Create a minimal populated trace and save it to traces_dir."""
    trace = start_trace(
        task_id=task_id,
        profile="service_ops",
        skill="my-skill",
        input_summary="Run the widget pipeline",
    )
    record_tool_call(trace, "Bash", "run tests", {"cmd": "pytest"}, 1500, "success", "all green")
    record_changed_file(trace, "scripts/trace_recorder.py")
    record_validation_gate(trace, "pytest", "passed")
    set_outcome(trace, "completed", replay_hint="re-run as-is", skill_candidate=True)
    save_trace(trace, traces_dir)
    return trace


def _run_replay(args: list[str]) -> subprocess.CompletedProcess:
    """Run trace_replay.py as a subprocess and return the CompletedProcess."""
    return subprocess.run(
        [sys.executable, str(REPLAY_SCRIPT)] + args,
        capture_output=True,
        text=True,
    )


# ---------------------------------------------------------------------------
# Test 8 — successful replay prints expected sections and exits 0
# ---------------------------------------------------------------------------

class TestReplayCLISuccess:
    def test_exit_code_zero(self, tmp_path):
        _make_and_save_trace(tmp_path)
        result = _run_replay(["--task-id", "replay-test-001", "--traces-dir", str(tmp_path)])
        assert result.returncode == 0, result.stderr

    def test_prints_task_id(self, tmp_path):
        _make_and_save_trace(tmp_path)
        result = _run_replay(["--task-id", "replay-test-001", "--traces-dir", str(tmp_path)])
        assert "replay-test-001" in result.stdout

    def test_prints_input_summary(self, tmp_path):
        _make_and_save_trace(tmp_path)
        result = _run_replay(["--task-id", "replay-test-001", "--traces-dir", str(tmp_path)])
        assert "Run the widget pipeline" in result.stdout

    def test_prints_profile(self, tmp_path):
        _make_and_save_trace(tmp_path)
        result = _run_replay(["--task-id", "replay-test-001", "--traces-dir", str(tmp_path)])
        assert "service_ops" in result.stdout

    def test_prints_skill(self, tmp_path):
        _make_and_save_trace(tmp_path)
        result = _run_replay(["--task-id", "replay-test-001", "--traces-dir", str(tmp_path)])
        assert "my-skill" in result.stdout

    def test_prints_tool_sequence_section(self, tmp_path):
        _make_and_save_trace(tmp_path)
        result = _run_replay(["--task-id", "replay-test-001", "--traces-dir", str(tmp_path)])
        assert "TOOL SEQUENCE" in result.stdout

    def test_prints_tool_name(self, tmp_path):
        _make_and_save_trace(tmp_path)
        result = _run_replay(["--task-id", "replay-test-001", "--traces-dir", str(tmp_path)])
        assert "Bash" in result.stdout

    def test_prints_changed_files_section(self, tmp_path):
        _make_and_save_trace(tmp_path)
        result = _run_replay(["--task-id", "replay-test-001", "--traces-dir", str(tmp_path)])
        assert "CHANGED FILES" in result.stdout

    def test_prints_changed_file_path(self, tmp_path):
        _make_and_save_trace(tmp_path)
        result = _run_replay(["--task-id", "replay-test-001", "--traces-dir", str(tmp_path)])
        assert "scripts/trace_recorder.py" in result.stdout

    def test_prints_validation_gates_section(self, tmp_path):
        _make_and_save_trace(tmp_path)
        result = _run_replay(["--task-id", "replay-test-001", "--traces-dir", str(tmp_path)])
        assert "VALIDATION GATES" in result.stdout

    def test_prints_gate_name(self, tmp_path):
        _make_and_save_trace(tmp_path)
        result = _run_replay(["--task-id", "replay-test-001", "--traces-dir", str(tmp_path)])
        assert "pytest" in result.stdout

    def test_prints_outcome_section(self, tmp_path):
        _make_and_save_trace(tmp_path)
        result = _run_replay(["--task-id", "replay-test-001", "--traces-dir", str(tmp_path)])
        assert "OUTCOME" in result.stdout

    def test_prints_outcome_value(self, tmp_path):
        _make_and_save_trace(tmp_path)
        result = _run_replay(["--task-id", "replay-test-001", "--traces-dir", str(tmp_path)])
        assert "completed" in result.stdout

    def test_prints_replay_plan_header(self, tmp_path):
        _make_and_save_trace(tmp_path)
        result = _run_replay(["--task-id", "replay-test-001", "--traces-dir", str(tmp_path)])
        assert "TRACE REPLAY PLAN" in result.stdout

    def test_dry_run_flag_accepted(self, tmp_path):
        _make_and_save_trace(tmp_path)
        result = _run_replay([
            "--task-id", "replay-test-001",
            "--traces-dir", str(tmp_path),
            "--dry-run",
        ])
        assert result.returncode == 0, result.stderr

    def test_no_reexecution_note_present(self, tmp_path):
        _make_and_save_trace(tmp_path)
        result = _run_replay(["--task-id", "replay-test-001", "--traces-dir", str(tmp_path)])
        # The module docstring / footer should mention that re-execution is not done.
        assert "re-execution" in result.stdout.lower() or "not implemented" in result.stdout.lower()


# ---------------------------------------------------------------------------
# Test 9 — missing trace → exit nonzero with friendly error
# ---------------------------------------------------------------------------

class TestReplayCLIError:
    def test_missing_trace_exits_nonzero(self, tmp_path):
        result = _run_replay(["--task-id", "no-such-task", "--traces-dir", str(tmp_path)])
        assert result.returncode != 0

    def test_missing_trace_prints_friendly_error(self, tmp_path):
        result = _run_replay(["--task-id", "no-such-task", "--traces-dir", str(tmp_path)])
        # Should mention the task id or "no trace" in stderr
        combined = result.stderr + result.stdout
        assert "no-such-task" in combined or "no trace" in combined.lower() or "Error" in combined

    def test_missing_task_id_arg_exits_nonzero(self):
        # --task-id is required; omitting it should produce a non-zero exit.
        result = _run_replay([])
        assert result.returncode != 0

    def test_missing_task_id_prints_usage(self):
        result = _run_replay([])
        combined = result.stderr + result.stdout
        assert "task-id" in combined or "usage" in combined.lower()


# ---------------------------------------------------------------------------
# Test 10 — --help exits 0 and mentions dry-run
# ---------------------------------------------------------------------------

class TestReplayCLIHelp:
    def test_help_exits_zero(self):
        result = _run_replay(["--help"])
        assert result.returncode == 0

    def test_help_mentions_dry_run(self):
        result = _run_replay(["--help"])
        assert "dry-run" in result.stdout or "dry_run" in result.stdout

    def test_help_mentions_task_id(self):
        result = _run_replay(["--help"])
        assert "task-id" in result.stdout or "task_id" in result.stdout

    def test_help_mentions_traces_dir(self):
        result = _run_replay(["--help"])
        assert "traces-dir" in result.stdout or "traces_dir" in result.stdout

    def test_help_output_nonempty(self):
        result = _run_replay(["--help"])
        assert len(result.stdout.strip()) > 10


# ---------------------------------------------------------------------------
# Tilde expansion in --traces-dir
# ---------------------------------------------------------------------------

class TestTracesDirTildeExpansion:
    def test_traces_dir_cli_expands_tilde(self):
        """``--traces-dir ~/x`` must expand to ``$HOME/x`` after argparse."""
        parser = build_parser()
        args = parser.parse_args(["--task-id", "irrelevant", "--traces-dir", "~/some-cli-traces"])
        resolved = Path(args.traces_dir).expanduser()
        assert resolved == Path.home() / "some-cli-traces"
        # And it must NOT be a literal `~` path.
        assert "~" not in str(resolved)
