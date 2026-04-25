#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from helm_workspace import get_workspace_layout
from scripts.memory_capture import build_memory_capture_plan
from scripts.state_io import append_jsonl_atomic

WORKSPACE = get_workspace_layout().root
TASK_LEDGER = get_workspace_layout().state_root / "task-ledger.jsonl"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_task(args: argparse.Namespace) -> dict:
    command_preview = args.command_preview or f"chat-summary:{args.task_name}"
    return {
        "task_id": args.task_id or str(uuid.uuid4()),
        "task_name": args.task_name,
        "skill": args.skill,
        "profile": args.profile,
        "backend": "conversation",
        "runtime_backend": "conversation",
        "runtime_target": args.runtime_target,
        "runtime_note": args.runtime_note,
        "command_preview": command_preview,
    }


def build_ledger_entries(task: dict, final_status: str) -> list[dict]:
    created_at = utc_now_iso()
    started_at = utc_now_iso()
    finished_at = utc_now_iso()

    queued = dict(task)
    queued.update({"status": "queued", "created_at": created_at})

    running = dict(task)
    running.update(
        {
            "status": "running",
            "created_at": created_at,
            "started_at": started_at,
            "started_execution_at": started_at,
        }
    )

    final = dict(task)
    final.update(
        {
            "status": final_status,
            "created_at": created_at,
            "started_at": started_at,
            "started_execution_at": started_at,
            "finished_at": finished_at,
        }
    )
    return [queued, running, final]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Write durable memory capture for chat-driven work without a profiled shell run."
    )
    parser.add_argument("--task-name", required=True, help="Human-readable task name.")
    parser.add_argument(
        "--profile",
        default="risky_edit",
        choices=["risky_edit", "service_ops", "remote_handoff", "inspect_local", "workspace_edit"],
        help="Execution profile semantics to reuse for durable capture planning.",
    )
    parser.add_argument(
        "--status",
        default="completed",
        choices=["completed", "handoff_required", "failed"],
        help="Outcome status used for finalization planning.",
    )
    parser.add_argument("--skill", help="Owning skill slug, if any.")
    parser.add_argument("--runtime-target", help="Optional named runtime target.")
    parser.add_argument("--runtime-note", help="Optional runtime note.")
    parser.add_argument("--command-preview", help="Synthetic command preview for keyword-based planning.")
    parser.add_argument("--task-id", help="Explicit task id override.")
    parser.add_argument(
        "--path",
        action="append",
        default=[],
        help="Workspace-relative touched path. Repeat for multiple paths.",
    )
    parser.add_argument("--json", action="store_true", help="Print the full capture payload as JSON.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    task = build_task(args)
    task["status"] = args.status
    payload = build_memory_capture_plan(task, touched_paths=args.path)
    final_task = dict(task)
    final_task["memory_capture"] = payload
    for entry in build_ledger_entries(final_task, args.status):
        append_jsonl_atomic(TASK_LEDGER, entry)

    output = {
        "workspace": str(WORKSPACE),
        "task_id": task["task_id"],
        "task_name": task["task_name"],
        "profile": task["profile"],
        "status": args.status,
        "memory_capture": payload,
    }
    if args.json:
        print(json.dumps(output, indent=2, ensure_ascii=False))
        return 0
    print(f"task_id={task['task_id']}")
    print(f"task_name={task['task_name']}")
    print(f"profile={task['profile']}")
    print(f"status={args.status}")
    print(f"finalization_status={payload.get('finalization_status')}")
    print("recommended_layers=" + ", ".join(payload.get("recommended_layers", [])))
    for reason in payload.get("reasons", []):
        print(f"reason={reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
