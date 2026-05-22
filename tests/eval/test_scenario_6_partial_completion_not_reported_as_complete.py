# tests/eval/test_scenario_6_partial_completion_not_reported_as_complete.py
"""Scenario 6 — Partial completion is not reported as completion.

A task runner executes 3 sub-steps.  The third sub-step raises an exception,
simulating a mid-task failure.  After the exception:

  - The ledger entry must record outcome != "completed".
  - completed_steps must have length 2 (the two steps that finished).
  - is_finalized(state) must return False.

This scenario verifies that incomplete execution cannot be mis-reported as a
successful completion in either the task state or the task ledger.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from helm_state_model import (
    is_finalized,
    mark_step_completed,
    new_task_state,
)
from scripts.state_io import append_jsonl_atomic, build_ledger_entry


# ---------------------------------------------------------------------------
# Inline task runner (no subprocess; simulates step execution in-process)
# ---------------------------------------------------------------------------

class _StepThreeFailure(RuntimeError):
    """Sentinel: the third sub-step always fails in this scenario."""


def _run_task(state: dict, ledger_path: Path) -> None:
    """Execute three sub-steps, the third of which always raises.

    On exception the runner writes an 'interrupted' ledger entry and re-raises.
    """
    outcome = "completed"
    try:
        # Step 1: fetch data
        mark_step_completed(state, "fetch_data")

        # Step 2: process data
        mark_step_completed(state, "process_data")

        # Step 3: write output — always fails in this scenario
        raise _StepThreeFailure("write_output failed: disk full")

    except _StepThreeFailure:
        outcome = "interrupted"
        raise

    finally:
        entry = build_ledger_entry(
            {
                "task_id": "eval-task-006",
                "profile": "workspace_edit",
                "outcome": outcome,
                "retry_count": 0,
                "completed_steps": list(state.get("completed_steps") or []),
            }
        )
        append_jsonl_atomic(ledger_path, entry)


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------


def test_scenario_6_partial_completion_not_reported_as_complete(
    tmp_path: Path,
) -> None:
    """Partial task (2/3 steps done) must not be reported as completed."""
    state = new_task_state()
    state["required_steps"] = ["fetch_data", "process_data", "write_output"]

    ledger_path = tmp_path / "ledger.jsonl"

    # The runner raises on step 3.
    with pytest.raises(_StepThreeFailure):
        _run_task(state, ledger_path)

    # ---- Assert task state ----
    assert len(state["completed_steps"]) == 2, (
        f"Expected 2 completed steps, got {len(state['completed_steps'])}: "
        f"{state['completed_steps']}"
    )
    assert "fetch_data" in state["completed_steps"]
    assert "process_data" in state["completed_steps"]
    assert "write_output" not in state["completed_steps"], (
        "write_output must NOT appear in completed_steps because it raised."
    )

    # finalization_state was never set to "finalized".
    assert is_finalized(state) is False, (
        f"is_finalized must be False after a partial run; "
        f"state={state}"
    )

    # ---- Assert ledger entry ----
    assert ledger_path.exists(), "Ledger file was not written"
    lines = ledger_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1, f"Expected 1 ledger entry, got {len(lines)}"
    record = json.loads(lines[0])

    assert record["outcome"] != "completed", (
        f"Ledger outcome must not be 'completed' for a partial run; "
        f"got outcome={record['outcome']!r}"
    )
    assert record["completed_steps"] == ["fetch_data", "process_data"], (
        f"Ledger completed_steps must reflect the 2 finished steps; "
        f"got {record['completed_steps']!r}"
    )
