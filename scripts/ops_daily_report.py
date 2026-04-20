#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from helm_workspace import get_workspace_layout


WORKSPACE = get_workspace_layout().root
STATE_ROOT = get_workspace_layout().state_root
DRAFTS_ROOT = WORKSPACE / "skill_drafts"


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


def latest_tasks() -> list[dict]:
    entries = read_jsonl(STATE_ROOT / "task-ledger.jsonl")
    by_task: dict[str, dict] = {}
    for entry in entries:
        task_id = entry.get("task_id")
        if task_id:
            by_task[task_id] = entry
    return sorted(by_task.values(), key=lambda item: item.get("started_at", ""))


def load_checkpoints() -> list[dict]:
    index = STATE_ROOT / "checkpoints" / "index.json"
    if not index.exists():
        return []
    try:
        payload = json.loads(index.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, list):
        return []
    return [item for item in payload if isinstance(item, dict)]


def load_draft_assessments() -> list[dict]:
    reports: list[dict] = []
    if not DRAFTS_ROOT.exists():
        return reports
    for draft in sorted(DRAFTS_ROOT.iterdir()):
        assessment = draft / "meta" / "assessment.json"
        if assessment.exists():
            try:
                payload = json.loads(assessment.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                reports.append(payload)
    return reports


def build_report(limit: int) -> dict:
    tasks = latest_tasks()
    commands = read_jsonl(STATE_ROOT / "command-log.jsonl")
    checkpoints = load_checkpoints()
    assessments = load_draft_assessments()

    recent_tasks = tasks[-limit:]
    failed_tasks = [task for task in recent_tasks if task.get("status") == "failed"]
    handoffs = [task for task in recent_tasks if task.get("status") == "handoff_required"]
    failed_commands = [cmd for cmd in commands[-100:] if cmd.get("exit_code") not in (0, None)]
    status_counts = Counter(task.get("status", "unknown") for task in recent_tasks)
    finalization_counts = Counter(
        (task.get("memory_capture") or {}).get("finalization_status", "unknown")
        for task in recent_tasks
    )

    return {
        "recent_task_status_counts": dict(status_counts),
        "recent_finalization_counts": dict(finalization_counts),
        "recent_failed_tasks": failed_tasks[-5:],
        "recent_handoff_tasks": handoffs[-5:],
        "recent_failed_commands": failed_commands[-5:],
        "recent_checkpoints": checkpoints[-5:],
        "draft_assessments": assessments[-10:],
    }


def print_text(report: dict) -> None:
    print("Recent task status counts:")
    for key, value in sorted(report["recent_task_status_counts"].items()):
        print(f"  {key}: {value}")
    print("Recent finalization counts:")
    for key, value in sorted(report["recent_finalization_counts"].items()):
        print(f"  {key}: {value}")
    print("Recent failed tasks:")
    for task in report["recent_failed_tasks"]:
        print(f"  {task.get('task_id')} {task.get('task_name')} [{task.get('profile')}]")
    print("Recent handoff tasks:")
    for task in report["recent_handoff_tasks"]:
        print(f"  {task.get('task_id')} {task.get('task_name')} -> {task.get('runtime_target')}")
    print("Recent checkpoints:")
    for checkpoint in report["recent_checkpoints"]:
        print(f"  {checkpoint.get('checkpoint_id')} {checkpoint.get('label')}")
    print("Draft assessments:")
    for assessment in report["draft_assessments"]:
        print(f"  {assessment.get('draft')} passed={assessment.get('passed')}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarize tasks, commands, checkpoints, and draft assessments in one report.")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    report = build_report(args.limit)
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print_text(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
