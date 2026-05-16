from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from commands import (
    read_json,
    read_jsonl,
    run_script,
    state_root_for,
    target_root,
)
from commands.context import (
    build_recent_state_payload,
    build_state_snapshot_payload,
    latest_tasks,
    task_finalization_status,
)


def _parse_timestamp(ts: str) -> datetime | None:
    """Parse an ISO-8601 or compact (20260413T090959Z) timestamp into a UTC-aware datetime."""
    if not ts:
        return None
    # Compact format: YYYYMMDDTHHMMSSz
    if len(ts) == 16 and ts[8] == "T" and ts.endswith("Z") and ts[:8].isdigit():
        try:
            return datetime.strptime(ts, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


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
        task_start_dt = _parse_timestamp(started_at)
        if task_start_dt is not None:
            older = []
            for item in checkpoints:
                cp_dt = _parse_timestamp(item.get("created_at", ""))
                if cp_dt is not None and cp_dt <= task_start_dt:
                    older.append(item)
        else:
            older = []
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


def _checkpoint_archive_size(root: Path, item: dict) -> int:
    archive_path = _checkpoint_archive_path(root, item)
    if archive_path is None:
        return 0
    try:
        return archive_path.stat().st_size
    except OSError:
        return 0


def _checkpoint_archive_path(root: Path, item: dict) -> Path | None:
    archive = item.get("archive")
    if not archive:
        return None
    archive_path = Path(str(archive))
    if archive_path.is_absolute():
        return archive_path

    state_root = state_root_for(root)
    candidates = (
        root / archive_path,
        state_root / "checkpoints" / archive_path,
    )
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def build_checkpoint_prune_plan(root: Path, *, keep_recent: int, keep_days: int, max_total_mb: int | None = None) -> dict:
    state_root = state_root_for(root)
    checkpoints = read_json(state_root / "checkpoints" / "index.json", [])
    tasks = latest_tasks(read_jsonl(state_root / "task-ledger.jsonl"))
    referenced = {str(task.get("checkpoint_id")) for task in tasks if task.get("checkpoint_id")}
    pinned = {
        str(item.get("checkpoint_id"))
        for item in checkpoints
        if item.get("pinned") or str(item.get("retention") or "").casefold() == "pinned"
    }
    now = datetime.now(timezone.utc)
    protected_recent = set()
    if keep_recent > 0:
        protected_recent = {
            str(item.get("checkpoint_id"))
            for item in checkpoints[-keep_recent:]
            if item.get("checkpoint_id")
        }
    keep: list[dict] = []
    prune: list[dict] = []
    total_bytes = 0
    for item in checkpoints:
        checkpoint_id = str(item.get("checkpoint_id") or "")
        archive_size = _checkpoint_archive_size(root, item)
        total_bytes += archive_size
        created = _parse_timestamp(str(item.get("created_at") or ""))
        age_days = None
        if created is not None:
            age_days = round((now - created).total_seconds() / 86400.0, 1)
        reasons: list[str] = []
        if checkpoint_id in referenced:
            reasons.append("referenced_by_task")
        if checkpoint_id in pinned:
            reasons.append("pinned")
        if checkpoint_id in protected_recent:
            reasons.append("recent_keep_window")
        if age_days is not None and age_days < keep_days:
            reasons.append("within_keep_days")
        row = dict(item)
        row["age_days"] = age_days
        row["archive_size_bytes"] = archive_size
        if reasons:
            row["retention_reasons"] = reasons
            keep.append(row)
        else:
            row["prune_reason"] = f"older_than_{keep_days}_days_unreferenced"
            prune.append(row)
    if max_total_mb is not None and total_bytes > max_total_mb * 1024 * 1024:
        protected_ids = {item.get("checkpoint_id") for item in keep}
        candidates = [item for item in checkpoints if item.get("checkpoint_id") not in protected_ids]
        current_bytes = total_bytes
        for item in candidates:
            if current_bytes <= max_total_mb * 1024 * 1024:
                break
            if any(existing.get("checkpoint_id") == item.get("checkpoint_id") for existing in prune):
                current_bytes -= _checkpoint_archive_size(root, item)
                continue
            row = dict(item)
            row["archive_size_bytes"] = _checkpoint_archive_size(root, item)
            row["prune_reason"] = f"exceeds_max_total_mb_{max_total_mb}"
            prune.append(row)
            current_bytes -= row["archive_size_bytes"]
    return {
        "workspace": str(root),
        "keep_recent": keep_recent,
        "keep_days": keep_days,
        "max_total_mb": max_total_mb,
        "total_bytes": total_bytes,
        "keep": keep,
        "prune": prune,
    }


def _apply_checkpoint_prune(root: Path, plan: dict) -> None:
    state_root = state_root_for(root)
    index_path = state_root / "checkpoints" / "index.json"
    prune_ids = {item.get("checkpoint_id") for item in plan["prune"]}
    remaining = [item for item in read_json(index_path, []) if item.get("checkpoint_id") not in prune_ids]
    for item in plan["prune"]:
        archive = item.get("archive")
        if not archive:
            continue
        archive_path = _checkpoint_archive_path(root, item)
        if archive_path is None:
            continue
        try:
            archive_path.unlink()
        except FileNotFoundError:
            pass
    tmp = index_path.with_suffix(index_path.suffix + ".tmp")
    tmp.write_text(json.dumps(remaining, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, index_path)


def cmd_checkpoint_prune(args: argparse.Namespace) -> int:
    root = target_root(args.path)
    policy = checkpoint_policy(root)["policy"]
    keep_recent = args.keep_recent if args.keep_recent is not None else int(policy.get("keep_recent", 5) or 0)
    keep_days = args.keep_days if args.keep_days is not None else int(policy.get("keep_days", 30) or 0)
    max_total_mb = args.max_total_mb if args.max_total_mb is not None else policy.get("max_total_mb")
    if max_total_mb is not None:
        max_total_mb = int(max_total_mb)
    plan = build_checkpoint_prune_plan(root, keep_recent=keep_recent, keep_days=keep_days, max_total_mb=max_total_mb)
    if args.apply:
        _apply_checkpoint_prune(root, plan)
        plan["applied"] = True
    else:
        plan["applied"] = False
    if args.json:
        print(json.dumps(plan, indent=2, ensure_ascii=False))
        return 0
    print(f"checkpoint_prune_candidates={len(plan['prune'])}")
    print(f"checkpoint_keep={len(plan['keep'])}")
    for item in plan["prune"]:
        print(f"prune={item.get('checkpoint_id')} reason={item.get('prune_reason')} archive={item.get('archive') or '-'}")
    if not args.apply:
        print("apply_hint=helm checkpoint prune --apply")
    return 0


def cmd_checkpoint_protect(args: argparse.Namespace) -> int:
    root = target_root(args.path)
    state_root = state_root_for(root)
    index_path = state_root / "checkpoints" / "index.json"
    checkpoints = read_json(index_path, [])
    changed = False
    for item in checkpoints:
        if item.get("checkpoint_id") == args.checkpoint_id:
            item["pinned"] = not args.unprotect
            changed = True
            break
    if not changed:
        print(f"checkpoint not found: {args.checkpoint_id}", file=sys.stderr)
        return 1
    tmp = index_path.with_suffix(index_path.suffix + ".tmp")
    tmp.write_text(json.dumps(checkpoints, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, index_path)
    print(("unprotected" if args.unprotect else "protected") + f": {args.checkpoint_id}")
    return 0


def checkpoint_policy(root: Path) -> dict:
    default = {
        "keep_recent": 5,
        "keep_days": 30,
        "max_total_mb": None,
        "protect_referenced": True,
        "protect_pinned": True,
    }
    policy_path = root / "references" / "checkpoint_policy.json"
    if not policy_path.exists():
        return {"path": str(policy_path), "source": "default", "policy": default}
    loaded = read_json(policy_path, {})
    policy = dict(default)
    if isinstance(loaded, dict):
        policy.update(loaded)
    return {"path": str(policy_path), "source": "file", "policy": policy}


def cmd_checkpoint_policy(args: argparse.Namespace) -> int:
    root = target_root(args.path)
    payload = checkpoint_policy(root)
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0
    print(f"source={payload['source']}")
    print(f"path={payload['path']}")
    for key, value in payload["policy"].items():
        print(f"{key}={value}")
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
