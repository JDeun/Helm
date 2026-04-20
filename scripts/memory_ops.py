from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from helm_workspace import get_workspace_layout


def _state_root() -> Path:
    return get_workspace_layout().state_root


def _jsonl_path(name: str) -> Path:
    return _state_root() / name


def _warn_parse_failure(path: Path, detail: str) -> None:
    print(f"warning: ignoring malformed state file {path}: {detail}", file=sys.stderr)


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows: list[dict] = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            _warn_parse_failure(path, f"line {lineno}: {exc}")
            continue
        if not isinstance(payload, dict):
            _warn_parse_failure(path, f"line {lineno}: expected JSON object")
            continue
        rows.append(payload)
    return rows


def _append_jsonl(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _latest_tasks_for_state(state_root: Path) -> list[dict]:
    ledger = _read_jsonl(state_root / "task-ledger.jsonl")
    by_task: dict[str, dict] = {}
    for entry in ledger:
        task_id = entry.get("task_id")
        if task_id:
            by_task[task_id] = entry
    return sorted(by_task.values(), key=lambda item: item.get("finished_at") or item.get("started_at") or "")


def _latest_tasks() -> list[dict]:
    return _latest_tasks_for_state(_state_root())


def _select_task(task_id: str) -> dict:
    for task in reversed(_latest_tasks()):
        if task.get("task_id") == task_id:
            return task
    raise SystemExit(f"Task not found: {task_id}")


def _crystallized_task_ids_for_state(state_root: Path) -> set[str]:
    return {
        str(row.get("task_id"))
        for row in _read_jsonl(state_root / "crystallized-sessions.jsonl")
        if row.get("task_id")
    }


def _operations_by_task_for_state(state_root: Path) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    for row in _read_jsonl(state_root / "memory-operations.jsonl"):
        task_id = row.get("task_id")
        if not task_id:
            continue
        grouped.setdefault(str(task_id), []).append(row)
    return grouped


def _resolved_task_ids_for_state(state_root: Path) -> set[str]:
    resolved: set[str] = set()
    for row in _read_jsonl(state_root / "memory-operations.jsonl"):
        operation = str(row.get("operation") or "")
        if operation == "supersede":
            resolved.update(str(item) for item in (row.get("supersedes") or []) if item)
        if operation in {"archive", "rollback"} and row.get("task_id"):
            resolved.add(str(row["task_id"]))
    return resolved


def review_queue_items(state_root: Path, limit: int | None = None) -> list[dict]:
    crystallized = _crystallized_task_ids_for_state(state_root)
    operations_by_task = _operations_by_task_for_state(state_root)
    resolved_task_ids = _resolved_task_ids_for_state(state_root)
    queue: list[dict] = []
    for task in reversed(_latest_tasks_for_state(state_root)):
        memory_capture = task.get("memory_capture") or {}
        finalization = str(memory_capture.get("finalization_status") or "unknown")
        review_flags = list(memory_capture.get("review_flags") or [])
        supersession = memory_capture.get("supersession") or {}
        claim_state = memory_capture.get("claim_state") or {}
        task_id = str(task.get("task_id") or "")
        if task_id and task_id in resolved_task_ids:
            continue
        task_ops = operations_by_task.get(task_id, [])

        blockers: list[str] = []
        actions: list[str] = []
        if finalization in {"capture_planned", "capture_partial"}:
            blockers.append(f"finalization={finalization}")
            actions.append("complete durable capture")
        if review_flags:
            blockers.extend(f"review_flag={flag.get('type')}" for flag in review_flags if flag.get("type"))
            actions.append("resolve review flags")
        if (
            memory_capture.get("relevant")
            and finalization == "capture_written"
            and task_id
            and task_id not in crystallized
        ):
            blockers.append("missing_crystallization")
            actions.append("crystallize durable outcome")
        if supersession.get("supersedes_task_ids") and not any(
            row.get("operation") == "supersede" for row in task_ops
        ):
            blockers.append("missing_supersede_op")
            actions.append("record supersede operation")
        if claim_state.get("confidence_hint") == "low":
            blockers.append("low_confidence")
            actions.append("reconfirm evidence before promotion")

        if not blockers:
            continue
        queue.append(
            {
                "task_id": task.get("task_id"),
                "task_name": task.get("task_name"),
                "status": task.get("status"),
                "profile": task.get("profile"),
                "finalization_status": finalization,
                "blockers": blockers,
                "actions": list(dict.fromkeys(actions)),
                "review_flags": review_flags,
                "supersession": supersession,
                "claim_state": claim_state,
            }
        )
        if limit is not None and len(queue) >= limit:
            break
    return queue


def _review_queue(limit: int) -> list[dict]:
    return review_queue_items(_state_root(), limit)


def _record_operation(kind: str, args: argparse.Namespace) -> dict:
    record = {
        "id": f"memop-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}",
        "timestamp": _now(),
        "operation": kind,
        "subject": args.subject,
        "scope": args.scope,
        "reason": args.reason,
        "evidence": args.evidence,
        "task_id": args.task_id,
        "affected_entities": args.affected_entity or [],
        "supersedes": args.supersedes or [],
        "status": "recorded",
    }
    _append_jsonl(_jsonl_path("memory-operations.jsonl"), record)
    return record


def cmd_operation(args: argparse.Namespace) -> int:
    payload = _record_operation(args.operation, args)
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


def cmd_history(args: argparse.Namespace) -> int:
    rows = _read_jsonl(_jsonl_path("memory-operations.jsonl"))
    rows = rows[-args.limit:]
    if args.json:
        print(json.dumps({"count": len(rows), "items": rows}, indent=2, ensure_ascii=False))
        return 0
    if not rows:
        print("No memory operations recorded.")
        return 0
    for row in rows:
        print(
            f"{row['timestamp']} op={row['operation']} scope={row['scope']} "
            f"task={row.get('task_id') or '-'} subject={row['subject']}"
        )
    return 0


def cmd_crystallize(args: argparse.Namespace) -> int:
    task = _select_task(args.task_id)
    memory_capture = task.get("memory_capture") or {}
    crystallization = memory_capture.get("crystallization") or {}
    if not crystallization:
        raise SystemExit(f"Task {args.task_id} has no crystallization payload")
    record = {
        "id": f"crystal-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}",
        "timestamp": _now(),
        "task_id": args.task_id,
        "task_name": task.get("task_name"),
        "profile": task.get("profile"),
        "status": task.get("status"),
        "claim_state": memory_capture.get("claim_state", {}),
        "supersession": memory_capture.get("supersession", {}),
        "review_flags": memory_capture.get("review_flags", []),
        "crystallization": crystallization,
    }
    _append_jsonl(_jsonl_path("crystallized-sessions.jsonl"), record)
    print(json.dumps(record, indent=2, ensure_ascii=False))
    return 0


def cmd_review_queue(args: argparse.Namespace) -> int:
    items = _review_queue(args.limit)
    payload = {"count": len(items), "items": items}
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0
    if not items:
        print("No memory review items queued.")
        return 0
    for item in items:
        print(
            f"{item['task_id']} finalization={item['finalization_status']} "
            f"blockers={','.join(item['blockers'])} actions={','.join(item['actions'])} "
            f"name={item['task_name']}"
        )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Typed Helm memory operations and crystallized session artifacts.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    history = subparsers.add_parser("history", help="Show recent typed memory operations.")
    history.add_argument("--limit", type=int, default=20)
    history.add_argument("--json", action="store_true")
    history.set_defaults(func=cmd_history)

    crystallize = subparsers.add_parser("crystallize", help="Persist a crystallized session artifact for a task.")
    crystallize.add_argument("--task-id", required=True)
    crystallize.set_defaults(func=cmd_crystallize)

    review = subparsers.add_parser("review-queue", help="Show durable memory items that still need operator follow-up.")
    review.add_argument("--limit", type=int, default=20)
    review.add_argument("--json", action="store_true")
    review.set_defaults(func=cmd_review_queue)

    operation = subparsers.add_parser("op", help="Record a typed memory operation.")
    operation.add_argument("operation", choices=["write", "promote", "supersede", "archive", "rollback"])
    operation.add_argument("--subject", required=True)
    operation.add_argument("--scope", default="private", choices=["private", "shared", "team", "public"])
    operation.add_argument("--reason", required=True)
    operation.add_argument("--evidence", required=True)
    operation.add_argument("--task-id")
    operation.add_argument("--affected-entity", action="append")
    operation.add_argument("--supersedes", action="append")
    operation.set_defaults(func=cmd_operation)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
