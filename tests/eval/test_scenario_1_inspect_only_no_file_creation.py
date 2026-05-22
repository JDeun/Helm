# tests/eval/test_scenario_1_inspect_only_no_file_creation.py
"""Scenario 1 — Inspect-only request must not create files.

A task running under the ``inspect_local`` profile (writes_allowed=False,
network_allowed=False) must be refused by the command guard whenever a
write-class command is attempted.  No file may land on disk as a side effect.

Behavioral assertion: the guard returns action="deny" for a write attempt and
the filesystem remains unchanged (no artifact created under tmp_path).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.command_guard import evaluate_command_guard


_PROFILES = {
    "inspect_local": {
        "writes_allowed": False,
        "network_allowed": False,
        "checkpoint": "never",
    },
    "workspace_edit": {
        "writes_allowed": True,
        "network_allowed": False,
        "checkpoint": "optional",
    },
}


def test_scenario_1_inspect_only_no_file_creation(tmp_path: Path) -> None:
    """Guard must deny write commands under inspect_local; no file created."""
    # The artifact the writer would create if the guard were bypassed.
    artifact = tmp_path / "output.txt"

    # Simulate a "write to disk" command that would create the artifact.
    write_command = ["touch", str(artifact)]

    decision = evaluate_command_guard(
        command=write_command,
        selected_profile="inspect_local",
        profiles=_PROFILES,
        workspace=tmp_path,
    )

    # The guard must deny the write attempt under inspect_local.
    assert decision.action == "deny", (
        f"Expected guard action='deny' for write under inspect_local, "
        f"got action={decision.action!r}; reasons={list(decision.reasons)}"
    )

    # The artifact must NOT have been created — the guard must have stopped the action
    # before any write reached the filesystem.
    assert not artifact.exists(), (
        f"Artifact was created at {artifact} despite inspect_local guard denial. "
        "The guard's deny decision must prevent filesystem writes."
    )

    # Confirm the classification understood this was a write operation.
    assert decision.classification.writes_detected, (
        "Guard classified the command as non-write; classification may be broken."
    )
