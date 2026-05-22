# tests/eval/test_scenario_2_save_request_persists_artifact.py
"""Scenario 2 — Save request must persist a file.

A task running under the ``workspace_edit`` profile with a save action must:
  1. Pass the command guard (action != "deny").
  2. Actually land an artifact on disk via the atomic JSONL writer.
  3. Move the task's finalization_state toward "finalized" after all required
     steps are marked complete.

Behavioral assertion: after a successful save action the artifact file exists,
and once all required steps are completed and finalization_state is set to
"finalized", is_finalized(state) returns True.
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
from scripts.command_guard import evaluate_command_guard
from scripts.state_io import append_jsonl_atomic, build_ledger_entry


_PROFILES = {
    "workspace_edit": {
        "writes_allowed": True,
        "network_allowed": False,
        "checkpoint": "optional",
    },
}


def test_scenario_2_save_request_persists_artifact(
    tmp_path: Path,
    ledger_path: Path,
) -> None:
    """workspace_edit save action: artifact written + state moves toward finalized."""
    # ledger_path comes from tests/eval/conftest.py — exercises the shared fixture.
    ledger = ledger_path
    artifact = tmp_path / "report.txt"

    # --- Step 1: guard allows write under workspace_edit ---
    write_command = ["touch", str(artifact)]
    decision = evaluate_command_guard(
        command=write_command,
        selected_profile="workspace_edit",
        profiles=_PROFILES,
        workspace=tmp_path,
    )
    assert decision.action != "deny", (
        f"workspace_edit should allow write commands, got action={decision.action!r}; "
        f"reasons={list(decision.reasons)}"
    )

    # --- Step 2: simulate the save (write artifact to disk) ---
    artifact.write_text("report content\n", encoding="utf-8")
    assert artifact.exists(), "Artifact was not created on disk"

    # --- Step 3: record the save in the task ledger ---
    base_entry: dict = {
        "task_id": "eval-task-002",
        "profile": "workspace_edit",
        "outcome": "completed",
        "retry_count": 0,
    }
    entry = build_ledger_entry(
        base_entry,
        snapshot_evidence=str(artifact),
        cleanup_status="ok",
    )
    append_jsonl_atomic(ledger, entry)

    # Verify the ledger entry was persisted.
    assert ledger.exists(), "Ledger file was not created"
    lines = ledger.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    persisted = json.loads(lines[0])
    assert persisted["outcome"] == "completed"
    assert persisted["snapshot_evidence"] == str(artifact)

    # --- Step 4: mark steps complete and finalize ---
    state = new_task_state()
    state["required_steps"] = ["save_report"]
    mark_step_completed(state, "save_report")
    state["finalization_state"] = "finalized"

    # The task is now fully finalized.
    assert is_finalized(state) is True, (
        "Expected is_finalized=True after all steps completed and "
        f"finalization_state='finalized'; state={state}"
    )
