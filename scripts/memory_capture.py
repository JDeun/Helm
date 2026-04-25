from __future__ import annotations

from pathlib import Path

from commands import read_jsonl
from helm_workspace import get_workspace_layout
from scripts.task_capture_core import (
    infer_crystallization,
    infer_retention_profile,
    infer_review_flags,
    infer_supersession,
    build_memory_capture_plan as _build_memory_capture_plan,
)


def _recent_final_tasks(task: dict, state_root: Path | None = None) -> list[dict]:
    if state_root is None:
        state_root = get_workspace_layout().state_root
    ledger = read_jsonl(state_root / "task-ledger.jsonl")
    by_task: dict[str, dict] = {}
    for entry in ledger:
        task_id = entry.get("task_id")
        if task_id and entry.get("status") in {"completed", "handoff_required", "failed"}:
            by_task[task_id] = entry
    current_id = task.get("task_id")
    rows = [entry for entry in by_task.values() if entry.get("task_id") != current_id]
    rows.sort(key=lambda item: str(item.get("finished_at") or item.get("started_at") or item.get("created_at") or ""))
    return rows[-80:]


def _retention_profile(event_types: list[str]) -> dict:
    return infer_retention_profile(event_types)


def _supersession(task: dict) -> dict:
    touched_paths = list(task.get("touched_paths") or [])
    return infer_supersession(
        task,
        touched_paths,
        load_recent_final_tasks=lambda: _recent_final_tasks(task),
    )


def _crystallization(task: dict, event_types: list[str], reasons: list[str]) -> dict:
    return infer_crystallization(task, event_types, reasons, list(task.get("touched_paths") or []))


def _review_flags(task: dict, claim_state: dict) -> list[dict]:
    return infer_review_flags(task, claim_state, _supersession(task), list(task.get("touched_paths") or []))


def build_memory_capture_plan(
    task: dict,
    *,
    touched_paths: list[str] | None = None,
    state_root: Path | None = None,
) -> dict:
    return _build_memory_capture_plan(
        task,
        touched_paths=touched_paths,
        load_recent_final_tasks=lambda: _recent_final_tasks(task, state_root=state_root),
    )
