from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from commands import read_jsonl, state_root_for, target_root
from scripts.state_io import append_jsonl_atomic


TERMINAL_STATUSES = {"completed", "failed", "blocked", "timeout", "handoff_required", "archived", "stale"}
ACTIVE_STATUSES = {"queued", "running", "ready", "triage"}
TASK_STATE_SCHEMA_VERSION = 1


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _state_root(root: Path) -> Path:
    return state_root_for(root)


def _ledger_path(root: Path) -> Path:
    return _state_root(root) / "task-ledger.jsonl"


def _latest_tasks(root: Path) -> list[dict]:
    by_task: dict[str, dict] = {}
    for entry in read_jsonl(_ledger_path(root)):
        task_id = entry.get("task_id")
        if task_id:
            by_task[str(task_id)] = entry
    return sorted(by_task.values(), key=lambda item: item.get("started_at") or item.get("updated_at") or "")


def _find_task(root: Path, task_id: str) -> dict | None:
    for entry in reversed(read_jsonl(_ledger_path(root))):
        if entry.get("task_id") == task_id:
            return entry
    return None


def _append_state(root: Path, entry: dict) -> dict:
    payload = dict(entry)
    payload["updated_at"] = utc_now_iso()
    payload.setdefault("task_state_schema_version", TASK_STATE_SCHEMA_VERSION)
    append_jsonl_atomic(_ledger_path(root), payload)
    return payload


def _status(entry: dict) -> str:
    return str(entry.get("status") or "unknown")


def _task_time(entry: dict) -> str:
    return str(entry.get("updated_at") or entry.get("finished_at") or entry.get("started_execution_at") or entry.get("started_at") or "")


def _evidence_items(entry: dict) -> list[str]:
    items: list[str] = []
    raw = entry.get("completion_evidence")
    if isinstance(raw, list):
        items.extend(str(item) for item in raw if str(item))
    elif isinstance(raw, str) and raw.strip():
        items.append(raw.strip())

    if entry.get("exit_code") is not None:
        items.append(f"exit_code:{entry.get('exit_code')}")
    if entry.get("checkpoint_id"):
        items.append(f"checkpoint:{entry.get('checkpoint_id')}")

    memory_capture = entry.get("memory_capture") or {}
    if isinstance(memory_capture, dict):
        finalization = memory_capture.get("finalization_status")
        if finalization:
            items.append(f"finalization:{finalization}")
        write_validation = memory_capture.get("write_validation") or {}
        if isinstance(write_validation, dict) and write_validation.get("ok") is not None:
            items.append(f"write_validation:{'ok' if write_validation.get('ok') else 'issues'}")

    harness = ((entry.get("meta") or {}).get("harness") or {})
    if isinstance(harness, dict):
        for key, label in (
            ("browser_evidence", "browser"),
            ("retrieval_evidence", "retrieval"),
            ("file_intake_evidence", "file_intake"),
        ):
            if isinstance(harness.get(key), dict):
                items.append(f"{label}:present")

    return items


def _doctor_findings(root: Path, entries: list[dict], stale_minutes: int) -> list[dict]:
    now = datetime.now(timezone.utc)
    findings: list[dict] = []
    for entry in entries:
        status = _status(entry)
        task_id = entry.get("task_id")
        if not task_id:
            continue

        if status in {"running", "queued"}:
            heartbeat = _parse_ts(entry.get("heartbeat_at"))
            started = _parse_ts(entry.get("started_execution_at") or entry.get("started_at"))
            liveness_at = heartbeat or started
            if liveness_at is not None:
                age_minutes = (now - liveness_at).total_seconds() / 60
                if age_minutes >= stale_minutes:
                    findings.append(
                        {
                            "severity": "warning",
                            "kind": "stale_active_task",
                            "task_id": task_id,
                            "task_name": entry.get("task_name"),
                            "status": status,
                            "age_minutes": round(age_minutes, 1),
                            "suggested_action": f"helm task mark-stale {task_id} --reason stale-active-task --path {root}",
                        }
                    )

        if status in ACTIVE_STATUSES:
            pid = entry.get("pid") or entry.get("process_id")
            if pid is not None:
                try:
                    os.kill(int(pid), 0)
                except (OSError, ValueError):
                    findings.append(
                        {
                            "severity": "warning",
                            "kind": "active_process_not_alive",
                            "task_id": task_id,
                            "task_name": entry.get("task_name"),
                            "status": status,
                            "pid": pid,
                            "suggested_action": f"helm task mark-stale {task_id} --reason process-not-alive --path {root}",
                        }
                    )

        if status == "completed" and not _evidence_items(entry):
            findings.append(
                {
                    "severity": "info",
                    "kind": "completed_without_evidence",
                    "task_id": task_id,
                    "task_name": entry.get("task_name"),
                    "status": status,
                    "suggested_action": f"helm task show {task_id} --path {root}",
                }
            )

        if status in ACTIVE_STATUSES and entry.get("finished_at"):
            findings.append(
                {
                    "severity": "warning",
                    "kind": "active_task_has_finished_at",
                    "task_id": task_id,
                    "task_name": entry.get("task_name"),
                    "status": status,
                    "suggested_action": f"helm task block {task_id} --reason inconsistent-finished-at --path {root}",
                }
            )

        retry_count = entry.get("retry_count")
        max_retries = entry.get("max_retries")
        if status in {"failed", "timeout", "blocked"} and retry_count is not None and max_retries is not None:
            try:
                retry_count_int = int(retry_count)
                max_retries_int = int(max_retries)
            except (TypeError, ValueError):
                continue
            if max_retries_int >= 0 and retry_count_int >= max_retries_int:
                findings.append(
                    {
                        "severity": "info",
                        "kind": "retry_limit_reached",
                        "task_id": task_id,
                        "task_name": entry.get("task_name"),
                        "status": status,
                        "retry_count": retry_count_int,
                        "max_retries": max_retries_int,
                        "suggested_action": f"helm task block {task_id} --reason retry-limit-reached --path {root}",
                    }
                )
    return findings


def cmd_task_list(args: argparse.Namespace) -> int:
    root = target_root(args.path)
    entries = _latest_tasks(root)
    if args.status:
        entries = [entry for entry in entries if _status(entry) == args.status]
    if args.profile:
        entries = [entry for entry in entries if entry.get("profile") == args.profile]
    if args.skill:
        entries = [entry for entry in entries if entry.get("skill") == args.skill]
    entries = entries[-args.limit :]
    if args.json:
        print(json.dumps(entries, indent=2, ensure_ascii=False))
        return 0
    if not entries:
        print("No tasks found.")
        return 0
    for entry in entries:
        evidence_count = len(_evidence_items(entry))
        print(
            f"{entry.get('task_id')} "
            f"status={_status(entry)} "
            f"profile={entry.get('profile') or '-'} "
            f"skill={entry.get('skill') or '-'} "
            f"evidence={evidence_count} "
            f"updated={_task_time(entry) or '-'} "
            f"name={entry.get('task_name') or '-'}"
        )
    return 0


def cmd_task_show(args: argparse.Namespace) -> int:
    root = target_root(args.path)
    entry = _find_task(root, args.task_id)
    if entry is None:
        print(f"task not found: {args.task_id}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(entry, indent=2, ensure_ascii=False))
        return 0
    print(f"task_id={entry.get('task_id')}")
    print(f"task_name={entry.get('task_name') or '-'}")
    print(f"status={_status(entry)}")
    print(f"profile={entry.get('profile') or '-'}")
    print(f"skill={entry.get('skill') or '-'}")
    print(f"started_at={entry.get('started_at') or '-'}")
    print(f"updated_at={entry.get('updated_at') or '-'}")
    print(f"finished_at={entry.get('finished_at') or '-'}")
    print(f"heartbeat_at={entry.get('heartbeat_at') or '-'}")
    print(f"parent_task_id={entry.get('parent_task_id') or '-'}")
    print(f"retry_count={entry.get('retry_count') if entry.get('retry_count') is not None else '-'}")
    print(f"max_retries={entry.get('max_retries') if entry.get('max_retries') is not None else '-'}")
    print(f"idempotency_key={entry.get('idempotency_key') or '-'}")
    print(f"owner_session_id={entry.get('owner_session_id') or '-'}")
    print(f"blocked_reason={entry.get('blocked_reason') or entry.get('failure_reason') or '-'}")
    evidence = _evidence_items(entry)
    print("completion_evidence=" + (", ".join(evidence) if evidence else "-"))
    next_action = entry.get("next_action")
    if next_action:
        print(f"next_action={next_action}")
    return 0


def cmd_task_block(args: argparse.Namespace) -> int:
    root = target_root(args.path)
    entry = _find_task(root, args.task_id)
    if entry is None:
        print(f"task not found: {args.task_id}", file=sys.stderr)
        return 1
    payload = dict(entry)
    payload["status"] = "blocked"
    payload["task_state_schema_version"] = TASK_STATE_SCHEMA_VERSION
    payload["blocked_reason"] = args.reason
    payload["failure_reason"] = args.reason
    payload["failure_stage"] = args.stage
    payload["finished_at"] = utc_now_iso()
    payload["heartbeat_at"] = payload["finished_at"]
    if args.next_action:
        payload["next_action"] = args.next_action
    _append_state(root, payload)
    print(f"blocked task {args.task_id}: {args.reason}")
    return 0


def cmd_task_complete(args: argparse.Namespace) -> int:
    root = target_root(args.path)
    entry = _find_task(root, args.task_id)
    if entry is None:
        print(f"task not found: {args.task_id}", file=sys.stderr)
        return 1
    evidence = list(args.evidence or [])
    if not evidence:
        print("completion requires at least one --evidence value", file=sys.stderr)
        return 2
    payload = dict(entry)
    existing = payload.get("completion_evidence")
    merged: list[str] = []
    if isinstance(existing, list):
        merged.extend(str(item) for item in existing if str(item))
    elif isinstance(existing, str) and existing.strip():
        merged.append(existing.strip())
    for item in evidence:
        if item not in merged:
            merged.append(item)
    payload["status"] = "completed"
    payload["task_state_schema_version"] = TASK_STATE_SCHEMA_VERSION
    payload["completion_evidence"] = merged
    payload["finished_at"] = utc_now_iso()
    payload["heartbeat_at"] = payload["finished_at"]
    if args.next_action:
        payload["next_action"] = args.next_action
    _append_state(root, payload)
    print(f"completed task {args.task_id} with {len(evidence)} evidence item(s)")
    return 0


def cmd_task_retry(args: argparse.Namespace) -> int:
    root = target_root(args.path)
    entry = _find_task(root, args.task_id)
    if entry is None:
        print(f"task not found: {args.task_id}", file=sys.stderr)
        return 1
    retry_count = int(entry.get("retry_count") or 0) + 1
    max_retries = entry.get("max_retries")
    if max_retries is not None:
        try:
            if int(max_retries) >= 0 and retry_count > int(max_retries):
                print(f"retry limit reached for {args.task_id}: retry_count={retry_count} max_retries={max_retries}", file=sys.stderr)
                return 2
        except (TypeError, ValueError):
            pass
    payload = dict(entry)
    payload["task_id"] = args.new_task_id or str(uuid.uuid4())
    payload["parent_task_id"] = entry.get("task_id")
    payload["status"] = "ready"
    payload["task_state_schema_version"] = TASK_STATE_SCHEMA_VERSION
    payload["retry_count"] = retry_count
    payload["started_at"] = utc_now_iso()
    payload["heartbeat_at"] = payload["started_at"]
    payload.pop("finished_at", None)
    payload.pop("started_execution_at", None)
    payload.pop("exit_code", None)
    payload.pop("failure_reason", None)
    payload.pop("failure_stage", None)
    payload.pop("blocked_reason", None)
    if args.reason:
        payload["retry_reason"] = args.reason
    _append_state(root, payload)
    print(f"created retry task {payload['task_id']} parent={entry.get('task_id')}")
    return 0


def cmd_task_mark_stale(args: argparse.Namespace) -> int:
    root = target_root(args.path)
    entry = _find_task(root, args.task_id)
    if entry is None:
        print(f"task not found: {args.task_id}", file=sys.stderr)
        return 1
    payload = dict(entry)
    now = utc_now_iso()
    payload["status"] = "stale"
    payload["task_state_schema_version"] = TASK_STATE_SCHEMA_VERSION
    payload["stale_reason"] = args.reason
    payload["blocked_reason"] = args.reason
    payload["failure_stage"] = args.stage
    payload["finished_at"] = now
    payload["heartbeat_at"] = now
    if args.next_action:
        payload["next_action"] = args.next_action
    else:
        payload["next_action"] = "reclaim or retry this task before continuing dependent work"
    _append_state(root, payload)
    print(f"marked stale task {args.task_id}: {args.reason}")
    return 0


def cmd_task_reclaim(args: argparse.Namespace) -> int:
    root = target_root(args.path)
    entry = _find_task(root, args.task_id)
    if entry is None:
        print(f"task not found: {args.task_id}", file=sys.stderr)
        return 1
    payload = dict(entry)
    now = utc_now_iso()
    payload["status"] = "ready"
    payload["task_state_schema_version"] = TASK_STATE_SCHEMA_VERSION
    payload["reclaimed_at"] = now
    payload["heartbeat_at"] = now
    payload["reclaim_reason"] = args.reason
    payload["owner_session_id"] = args.owner_session_id
    payload["next_action"] = args.next_action or "resume this reclaimed task"
    payload.pop("finished_at", None)
    payload.pop("started_execution_at", None)
    payload.pop("exit_code", None)
    payload.pop("failure_reason", None)
    payload.pop("failure_stage", None)
    payload.pop("blocked_reason", None)
    payload.pop("stale_reason", None)
    _append_state(root, payload)
    print(f"reclaimed task {args.task_id}")
    return 0


def cmd_task_doctor(args: argparse.Namespace) -> int:
    root = target_root(args.path)
    entries = _latest_tasks(root)
    findings = _doctor_findings(root, entries, args.stale_minutes)
    if args.json:
        print(json.dumps({"workspace": str(root), "findings": findings, "ok": not findings}, indent=2, ensure_ascii=False))
        return 0 if not findings else 1
    if not findings:
        print("No task state issues found.")
        return 0
    for item in findings:
        print(
            f"{item['severity']} {item['kind']} "
            f"task_id={item['task_id']} "
            f"status={item.get('status', '-')} "
            f"name={item.get('task_name') or '-'}"
        )
        print(f"  next={item['suggested_action']}")
    return 1
