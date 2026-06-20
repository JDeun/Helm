from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.conversational_memory_capture import build_ledger_entries
from scripts.experience_attribution import attach_experience_attribution


def test_service_ops_without_evidence_records_review_flag() -> None:
    task = {
        "task_id": "task-1",
        "task_name": "deploy service",
        "profile": "service_ops",
        "status": "completed",
        "command": ["python3", "scripts/deploy.py"],
        "tool_grant": {"granted": ["read_file"], "requires_approval": ["external_network"], "denied": []},
        "memory_capture": {"review_flags": [{"type": "existing_review"}]},
        "meta": {},
    }

    attach_experience_attribution(task)

    attribution = task["experience_attribution"]
    assert attribution["tool_selected"] == ["deploy.py"]
    assert attribution["tool_candidates"] == ["read_file", "external_network"]
    assert attribution["outcome_signal"]["evidence_state"] == "missing_required"
    assert {"type": "missing_service_ops_evidence", "source": "experience_attribution"} in attribution["review_flags"]


def test_python_module_commands_record_module_as_selected_tool() -> None:
    task = {"task_id": "task-2", "profile": "workspace_edit", "status": "completed", "command": ["python3", "-m", "pytest"]}

    attach_experience_attribution(task)

    assert task["experience_attribution"]["tool_selected"] == ["pytest"]


def test_chat_capture_final_only_fields_stay_off_queued_and_running_rows() -> None:
    task = {
        "task_id": "task-chat",
        "task_name": "record chat memory",
        "profile": "workspace_edit",
        "command_preview": "chat-summary:record chat memory",
        "memory_capture": {"finalization_status": "capture_planned"},
        "experience_attribution": {"tool_selected": ["conversation"]},
    }

    queued, running, final = build_ledger_entries(task, "completed")

    assert "memory_capture" not in queued
    assert "experience_attribution" not in queued
    assert "memory_capture" not in running
    assert "experience_attribution" not in running
    assert final["memory_capture"]["finalization_status"] == "capture_planned"
    assert final["experience_attribution"]["tool_selected"] == ["conversation"]
