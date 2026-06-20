from __future__ import annotations

from pathlib import Path


def evidence_state(task: dict) -> str:
    harness = (task.get("meta") or {}).get("harness") or {}
    if any(
        isinstance(harness.get(key), dict)
        for key in ("browser_evidence", "retrieval_evidence", "file_intake_evidence")
    ):
        return "present"
    if task.get("profile") == "service_ops":
        return "missing_required"
    return "missing_optional"


def selected_tool_name(task: dict) -> str | None:
    command = task.get("command") or []
    if len(command) >= 3 and Path(command[0]).name.startswith("python") and command[1] == "-m":
        return command[2]
    if len(command) >= 2 and Path(command[0]).name.startswith("python"):
        return Path(command[1]).name
    if command:
        return Path(command[0]).name
    preview = str(task.get("command_preview") or "")
    if preview.startswith("chat-summary:"):
        return "conversation"
    return preview.split()[0] if preview else None


def attach_experience_attribution(task: dict) -> None:
    if "experience_attribution" in task:
        return
    skill = task.get("skill")
    selected_tool = selected_tool_name(task)
    tool_grant = task.get("tool_grant") or {}
    memory_capture = task.get("memory_capture") or {}
    review_flags = list(memory_capture.get("review_flags") or [])
    if task.get("profile") == "service_ops" and evidence_state(task) == "missing_required":
        review_flags.append({"type": "missing_service_ops_evidence", "source": "experience_attribution"})
    task["experience_attribution"] = {
        "memory_candidates": [],
        "memory_selected": [],
        "skill_candidates": [],
        "skill_selected": [skill] if skill else [],
        "tool_candidates": list(tool_grant.get("granted") or [])
        + list(tool_grant.get("requires_approval") or [])
        + list(tool_grant.get("denied") or []),
        "tool_selected": [selected_tool] if selected_tool else [],
        "selection_reason": "runner recorded declared skill, tool grants, and executed command",
        "outcome_signal": {
            "status": task.get("status") or "unknown",
            "evidence_state": evidence_state(task),
            "user_correction": bool((task.get("meta") or {}).get("user_correction")),
        },
        "retrospective_score": None,
        "review_flags": review_flags,
    }
