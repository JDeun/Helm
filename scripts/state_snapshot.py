#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def compact_timestamp(value: str | None = None) -> str:
    if value:
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            dt = datetime.now(timezone.utc)
    else:
        dt = datetime.now(timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def safe_slug(value: object, *, fallback: str = "task") -> str:
    text = str(value or fallback).strip().lower()
    text = re.sub(r"[^a-z0-9._-]+", "-", text)
    text = text.strip("-._")
    return text[:80] or fallback


def _json_inline(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _workspace_rel(path: Path, workspace: Path) -> str:
    try:
        return str(path.resolve().relative_to(workspace.resolve()))
    except ValueError:
        return str(path)


def build_state_snapshot(task: dict, *, workspace: Path) -> dict:
    status = str(task.get("status") or "unknown")
    profile = task.get("profile") or "-"
    task_name = task.get("task_name") or task.get("command_preview") or task.get("task_id") or "unnamed task"
    command_preview = task.get("command_preview") or _json_inline(task.get("command") or [])
    checkpoint_id = task.get("checkpoint_id")
    memory_capture = task.get("memory_capture") or {}
    harness_meta = ((task.get("meta") or {}).get("harness") or {})
    failure_reason = task.get("failure_reason")
    exit_code = task.get("exit_code")

    blockers: list[str] = []
    if failure_reason:
        blockers.append(str(failure_reason))
    elif status == "failed":
        blockers.append(f"command exited with code {exit_code}")
    elif status == "handoff_required":
        blockers.append("manual remote handoff required before execution can continue")

    if status == "completed":
        recommended_next_step = "Review memory_capture recommendations and promote durable state if needed."
    elif status == "failed":
        recommended_next_step = "Inspect the failure reason, restore from the checkpoint if needed, then retry from this task context."
    elif status == "handoff_required":
        recommended_next_step = "Run the command on the declared runtime target and record the outcome in the task ledger."
    else:
        recommended_next_step = "Inspect the task ledger and continue from the latest recorded state."

    rollback_hint = "No checkpoint is attached to this task."
    if checkpoint_id:
        rollback_hint = f"Restore with: helm checkpoint --path {workspace} restore {checkpoint_id}"

    confirmed_facts = [
        f"status={status}",
        f"profile={profile}",
        f"runtime_backend={task.get('runtime_backend') or task.get('backend') or '-'}",
    ]
    if task.get("skill"):
        confirmed_facts.append(f"skill={task.get('skill')}")
    if checkpoint_id:
        confirmed_facts.append(f"checkpoint_id={checkpoint_id}")
    if memory_capture:
        confirmed_facts.append(
            "memory_capture="
            + _json_inline(
                {
                    "finalization_status": memory_capture.get("finalization_status"),
                    "recommended_layers": memory_capture.get("recommended_layers", []),
                    "event_types": memory_capture.get("event_types", []),
                    "touched_paths": memory_capture.get("touched_paths", []),
                }
            )
        )
    if harness_meta:
        confirmed_facts.append(
            "harness="
            + _json_inline(
                {
                    "interaction_workflow": harness_meta.get("interaction_workflow"),
                    "skill_relevance": harness_meta.get("skill_relevance"),
                    "route_decision": harness_meta.get("route_decision"),
                    "file_intake_evidence": harness_meta.get("file_intake_evidence"),
                    "browser_evidence_present": isinstance(harness_meta.get("browser_evidence"), dict),
                    "retrieval_evidence_present": isinstance(harness_meta.get("retrieval_evidence"), dict),
                }
            )
        )

    return {
        "task_id": task.get("task_id"),
        "objective": str(task_name),
        "current_state": f"Task is {status}.",
        "confirmed_facts": confirmed_facts,
        "attempted_actions": [command_preview],
        "blockers": blockers,
        "recommended_next_step": recommended_next_step,
        "rollback_or_resume_hint": rollback_hint,
    }


def render_state_snapshot(snapshot: dict) -> str:
    lines = ["[STATE_SNAPSHOT]"]
    ordered_keys = (
        "task_id",
        "objective",
        "current_state",
        "confirmed_facts",
        "attempted_actions",
        "blockers",
        "recommended_next_step",
        "rollback_or_resume_hint",
    )
    for key in ordered_keys:
        value = snapshot.get(key)
        if isinstance(value, list):
            lines.append(f"- {key}:")
            if value:
                lines.extend(f"  - {item}" for item in value)
            else:
                lines.append("  - none")
        else:
            lines.append(f"- {key}: {value or '-'}")
    lines.append("")
    return "\n".join(lines)


def write_state_snapshot(task: dict, *, workspace: Path, state_root: Path) -> dict:
    snapshots_root = state_root / "state-snapshots"
    snapshots_root.mkdir(parents=True, exist_ok=True)
    snapshot = build_state_snapshot(task, workspace=workspace)
    timestamp = compact_timestamp(task.get("finished_at") or task.get("started_at"))
    task_slug = safe_slug(task.get("task_id"))
    name_slug = safe_slug(task.get("task_name") or task.get("status"))
    path = snapshots_root / f"{timestamp}-{task_slug}-{name_slug}.md"
    path.write_text(render_state_snapshot(snapshot), encoding="utf-8")
    return {
        "created_at": utc_now_iso(),
        "path": _workspace_rel(path, workspace),
        "format": "markdown",
        "summary": snapshot.get("current_state"),
    }


def latest_snapshot_path(state_root: Path) -> Path | None:
    snapshots_root = state_root / "state-snapshots"
    if not snapshots_root.exists():
        return None
    matches = sorted(path for path in snapshots_root.glob("*.md") if path.is_file())
    return matches[-1] if matches else None


def load_latest_tasks(task_ledger: Path) -> list[dict]:
    if not task_ledger.exists():
        return []
    by_task: dict[str, dict] = {}
    anonymous: list[dict] = []
    for line in task_ledger.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        task_id = payload.get("task_id")
        if task_id:
            by_task[str(task_id)] = payload
        else:
            anonymous.append(payload)
    return [*anonymous, *by_task.values()]


def snapshot_payload(
    task_id: str | None = None,
    *,
    workspace: Path,
    state_root: Path,
) -> dict:
    tasks = load_latest_tasks(state_root / "task-ledger.jsonl")
    target = None
    if task_id:
        target = next((task for task in tasks if task.get("task_id") == task_id), None)
    elif tasks:
        target = next((task for task in reversed(tasks) if task.get("state_snapshot")), None)
    meta = (target or {}).get("state_snapshot") or {}
    path = workspace / meta["path"] if meta.get("path") else latest_snapshot_path(state_root)
    content = path.read_text(encoding="utf-8") if path and path.exists() else None
    return {
        "workspace": str(workspace),
        "task": target,
        "snapshot": meta or None,
        "snapshot_path": str(path) if path else None,
        "content": content,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect Helm task state snapshots.")
    parser.add_argument("--workspace", "--path", default=".", help="Workspace root. Defaults to current directory.")
    parser.add_argument("--state-root", help="State root. Defaults to <workspace>/.helm.")
    parser.add_argument("--task-id")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    workspace = Path(args.workspace).expanduser().resolve()
    state_root = Path(args.state_root).expanduser().resolve() if args.state_root else workspace / ".helm"
    payload = snapshot_payload(args.task_id, workspace=workspace, state_root=state_root)
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0
    if not payload["content"]:
        print("No state snapshot found.")
        return 0
    print(payload["content"], end="" if payload["content"].endswith("\n") else "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
