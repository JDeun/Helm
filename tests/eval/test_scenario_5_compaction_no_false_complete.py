# tests/eval/test_scenario_5_compaction_no_false_complete.py
"""Scenario 5 — Compaction does not falsely mark task completed.

The "Control Flow Is Not Memory" principle requires that ``is_finalized``
remains authoritative after context compaction.  A task that has completed
only 2 of 3 required steps must NOT be reported as finalized, even after a
compaction event.

Behavioral assertions:
- State has 3 required_steps; only 2 are in completed_steps.
- Simulating compaction (blanking the transcript) does not alter required_steps
  or completed_steps.
- ``is_finalized(state)`` returns False after the compaction simulation.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from helm_state_model import (
    is_finalized,
    mark_step_completed,
    new_task_state,
)


def test_scenario_5_compaction_no_false_complete() -> None:
    """is_finalized must stay False after compaction when a required step is missing."""
    state = new_task_state()

    # Three required steps.
    state["required_steps"] = ["fetch_data", "process_data", "write_output"]

    # Complete only 2 of 3.
    mark_step_completed(state, "fetch_data")
    mark_step_completed(state, "process_data")
    # "write_output" intentionally left incomplete.

    # finalization_state is still "pending" (as set by new_task_state).
    assert state["finalization_state"] == "pending"
    assert is_finalized(state) is False

    # ---- Simulate compaction: blank the transcript buffer ----
    state["transcript"] = []
    state["messages"] = []

    # After compaction, control state must be unchanged.
    assert state["required_steps"] == ["fetch_data", "process_data", "write_output"], (
        "required_steps was modified during compaction simulation."
    )
    assert state["completed_steps"] == ["fetch_data", "process_data"], (
        "completed_steps was modified during compaction simulation."
    )

    # Even if the runner now sets finalization_state="finalized" prematurely,
    # is_finalized must return False because "write_output" is still missing.
    state["finalization_state"] = "finalized"
    assert is_finalized(state) is False, (
        "is_finalized returned True with only 2/3 required steps completed; "
        "compaction must not cause a false-completion signal. "
        f"required_steps={state['required_steps']}, "
        f"completed_steps={state['completed_steps']}, "
        f"finalization_state={state['finalization_state']!r}"
    )
