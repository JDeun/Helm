from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from commands import (
    SCRIPT_ROOT,
    discover_workspace,
    read_json,
    state_root_for,
    target_root,
)
from commands.context import (
    build_recent_state_payload,
    build_state_snapshot_payload,
    latest_tasks,
    read_jsonl,
    task_finalization_status,
)


def run_script(script_name: str, script_args: list[str], workspace: Path | None = None) -> int:
    script_path = SCRIPT_ROOT / script_name
    env = os.environ.copy()
    if workspace is not None:
        env["HELM_WORKSPACE"] = str(workspace)
    result = subprocess.run([sys.executable, str(script_path), *script_args], env=env)
    return result.returncode


def recommend_checkpoint(root: Path, task_id: str | None = None) -> dict:
    state_root = state_root_for(root)
    tasks = latest_tasks(read_jsonl(state_root / "task-ledger.jsonl"))
    checkpoints = read_json(state_root / "checkpoints" / "index.json", [])
    target = None
    if task_id:
        target = next((item for item in tasks if item.get("task_id") == task_id), None)
    else:
        risky = [item for item in tasks if item.get("profile") == "risky_edit"]
        if risky:
            target = risky[-1]
    if target is None:
        return {"task": None, "checkpoint": None}

    explicit = target.get("checkpoint_id")
    checkpoint = None
    if explicit:
        checkpoint = next((item for item in checkpoints if item.get("checkpoint_id") == explicit), None)
    if checkpoint is None and checkpoints:
        started_at = target.get("started_at", "")
        older = [item for item in checkpoints if item.get("created_at", "") <= started_at.replace("-", "").replace(":", "").replace("+00:00", "Z")]
        checkpoint = older[-1] if older else checkpoints[-1]
    return {"task": target, "checkpoint": checkpoint}


def build_finalize_payload(root: Path, task_id: str | None) -> dict:
    recommendation = recommend_checkpoint(root, task_id)
    task = recommendation.get("task")
    checkpoint = recommendation.get("checkpoint")
    memory_capture = (task or {}).get("memory_capture") or {}
    return {
        "workspace": str(root),
        "task": task,
        "checkpoint": checkpoint,
        "finalization": {
            "status": memory_capture.get("finalization_status", "unknown"),
            "relevant": memory_capture.get("relevant", False),
            "recommended_layers": memory_capture.get("recommended_layers", []),
            "event_types": memory_capture.get("event_types", []),
            "claim_state": memory_capture.get("claim_state", {}),
            "retention": memory_capture.get("retention", {}),
            "review_flags": memory_capture.get("review_flags", []),
            "supersession": memory_capture.get("supersession", {}),
            "crystallization": memory_capture.get("crystallization", {}),
            "reasons": memory_capture.get("reasons", []),
            "summary": memory_capture.get("summary"),
        },
    }


def build_capture_state_payload(root: Path, limit: int) -> dict:
    state_root = state_root_for(root)
    tasks = latest_tasks(read_jsonl(state_root / "task-ledger.jsonl"))
    recent_tasks = tasks[-limit:]
    from collections import Counter
    finalization_counts = Counter(task_finalization_status(task) for task in recent_tasks)
    pending_tasks = [
        {
            "task_id": task.get("task_id"),
            "task_name": task.get("task_name"),
            "profile": task.get("profile"),
            "status": task.get("status"),
            "finalization_status": task_finalization_status(task),
            "recommended_layers": (task.get("memory_capture") or {}).get("recommended_layers", []),
            "review_flags": (task.get("memory_capture") or {}).get("review_flags", []),
            "confidence_hint": ((task.get("memory_capture") or {}).get("claim_state") or {}).get("confidence_hint"),
        }
        for task in recent_tasks
        if task_finalization_status(task) in {"capture_planned", "capture_partial"}
    ]
    return {
        "workspace": str(root),
        "window": len(recent_tasks),
        "finalization_counts": dict(finalization_counts),
        "pending_tasks": pending_tasks,
    }


def cmd_checkpoint_recommend(args: argparse.Namespace) -> int:
    root = target_root(args.path)
    payload = recommend_checkpoint(root, args.task_id)
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0
    task = payload["task"]
    checkpoint = payload["checkpoint"]
    if task is None:
        print("No risky task found.")
        return 0
    print(f"task_id={task.get('task_id')}")
    print(f"task_name={task.get('task_name')}")
    print(f"profile={task.get('profile')}")
    print(f"status={task.get('status')}")
    if checkpoint is None:
        print("checkpoint_id=-")
        return 0
    print(f"checkpoint_id={checkpoint.get('checkpoint_id')}")
    print(f"checkpoint_label={checkpoint.get('label')}")
    print("checkpoint_paths=" + ", ".join(checkpoint.get("paths", [])))
    print(f"restore_hint=helm checkpoint --path {root} restore {checkpoint.get('checkpoint_id')}")
    return 0


def cmd_checkpoint_list(args: argparse.Namespace) -> int:
    root = target_root(args.path)
    checkpoints = read_json(state_root_for(root) / "checkpoints" / "index.json", [])
    if args.json:
        print(json.dumps(checkpoints, indent=2, ensure_ascii=False))
        return 0
    if not checkpoints:
        print("No checkpoints found.")
        return 0
    for checkpoint in checkpoints[-args.limit:]:
        print(
            f"{checkpoint.get('checkpoint_id')} "
            f"label={checkpoint.get('label')} "
            f"paths={', '.join(checkpoint.get('paths', []))}"
        )
    return 0


def cmd_checkpoint_show(args: argparse.Namespace) -> int:
    root = target_root(args.path)
    checkpoints = read_json(state_root_for(root) / "checkpoints" / "index.json", [])
    checkpoint = next((item for item in checkpoints if item.get("checkpoint_id") == args.checkpoint_id), None)
    if checkpoint is None:
        print(f"checkpoint not found: {args.checkpoint_id}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(checkpoint, indent=2, ensure_ascii=False))
        return 0
    print(f"checkpoint_id={checkpoint.get('checkpoint_id')}")
    print(f"label={checkpoint.get('label')}")
    print(f"created_at={checkpoint.get('created_at')}")
    print("paths=" + ", ".join(checkpoint.get("paths", [])))
    print(f"archive={checkpoint.get('archive')}")
    print(f"preview_hint=helm checkpoint --path {root} preview {checkpoint.get('checkpoint_id')}")
    print(f"restore_hint=helm checkpoint --path {root} restore {checkpoint.get('checkpoint_id')}")
    return 0


def cmd_checkpoint_preview(args: argparse.Namespace) -> int:
    root = target_root(args.path)
    script_args = ["preview", args.checkpoint_id]
    return run_script("workspace_checkpoint.py", script_args, root)


def cmd_checkpoint_restore(args: argparse.Namespace) -> int:
    root = target_root(args.path)
    print(f"Restoring checkpoint {args.checkpoint_id} into {root}")
    print(f"Preview first with: helm checkpoint --path {root} preview {args.checkpoint_id}")
    script_args = ["restore", args.checkpoint_id]
    return run_script("workspace_checkpoint.py", script_args, root)


def cmd_checkpoint_create(args: argparse.Namespace) -> int:
    root = target_root(args.path)
    script_args = ["create", "--label", args.label]
    for item in args.include:
        script_args.extend(["--path", item])
    return run_script("workspace_checkpoint.py", script_args, root)


def cmd_checkpoint_finalize(args: argparse.Namespace) -> int:
    root = target_root(args.path)
    payload = build_finalize_payload(root, args.task_id)
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0
    task = payload["task"]
    if task is None:
        print("No matching task found.")
        return 0
    finalization = payload["finalization"]
    checkpoint = payload["checkpoint"]
    print(f"task_id={task.get('task_id')}")
    print(f"task_name={task.get('task_name')}")
    print(f"status={task.get('status')}")
    print(f"finalization_status={finalization['status']}")
    print("recommended_layers=" + ", ".join(finalization["recommended_layers"]))
    print("event_types=" + ", ".join(finalization["event_types"]))
    for reason in finalization["reasons"]:
        print(f"reason={reason}")
    if checkpoint:
        print(f"checkpoint_id={checkpoint.get('checkpoint_id')}")
        print(f"checkpoint_label={checkpoint.get('label')}")
    return 0


def cmd_checkpoint(args: argparse.Namespace) -> int:
    root = target_root(args.path) if args.path else discover_workspace().root
    if args.args:
        subcommand, *remainder = args.args
        if subcommand == "finalize":
            parser = argparse.ArgumentParser(prog="helm checkpoint finalize")
            parser.add_argument("--task-id")
            parser.add_argument("--json", action="store_true")
            parsed = parser.parse_args(remainder)
            return cmd_checkpoint_finalize(argparse.Namespace(path=str(root), task_id=parsed.task_id, json=parsed.json))
    return run_script("workspace_checkpoint.py", args.args, root)
