#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path


SECRET_KEY_RE = re.compile(r"(?:secret|token|password|passwd|api[_-]?key|credential)", re.I)
SECRET_VALUE_RE = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+\-/]+=*|\b(?:sk|ghp|github_pat|xox[baprs])-[-A-Za-z0-9_]{8,}\b")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_task_id(value: object) -> str:
    task_id = str(value or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", task_id):
        raise ValueError("task state requires a safe task_id")
    return task_id


def _redact(value: object) -> object:
    if isinstance(value, dict):
        return {
            str(key): "[REDACTED]" if SECRET_KEY_RE.search(str(key)) else _redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, str):
        return SECRET_VALUE_RE.sub("[REDACTED]", value)
    return value


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def _relative(path: Path, workspace: Path) -> str:
    try:
        return str(path.resolve().relative_to(workspace.resolve()))
    except ValueError:
        return str(path)


def _phase(task: dict) -> str:
    status = str(task.get("status") or "queued")
    return {
        "queued": "plan",
        "running": "execute",
        "completed": "complete",
        "failed": "blocked",
        "timeout": "blocked",
        "blocked": "blocked",
        "cancelled": "cancelled",
        "handoff_required": "handoff",
    }.get(status, status)


def _blockers(task: dict) -> list[str]:
    values = []
    for key in ("blocked_reason", "failure_reason"):
        if task.get(key):
            values.append(str(task[key]))
    finalization = task.get("finalization_gate") or {}
    for claim in finalization.get("claims") or []:
        if isinstance(claim, dict) and not claim.get("ok"):
            values.append(f"unverified claim: {claim.get('claim')} ({claim.get('reason')})")
    scope_gate = task.get("scope_gate") or {}
    for path in scope_gate.get("violations") or []:
        values.append(f"scope violation: {path}")
    return list(dict.fromkeys(values))


def _external_surfaces(task: dict) -> list[str]:
    surfaces = []
    if task.get("runtime_target"):
        surfaces.append(str(task["runtime_target"]))
    if task.get("profile") == "service_ops":
        surfaces.append("external service")
    for row in ((task.get("evidence_gathering") or {}).get("service_results") or []):
        if isinstance(row, dict) and (row.get("source") or row.get("reference")):
            surfaces.append(str(row.get("source") or row.get("reference")))
    return list(dict.fromkeys(surfaces))


def build_evidence_payload(task: dict) -> dict:
    return _redact(
        {
            "schema_version": 1,
            "task_id": task.get("task_id"),
            "updated_at": utc_now_iso(),
            "operational_status": task.get("operational_status"),
            "evidence_refs": task.get("evidence_refs") or [],
            "completion_claims": task.get("completion_claims") or [],
            "finalization_gate": task.get("finalization_gate") or {},
            "evidence_gathering": task.get("evidence_gathering") or {},
            "scope_gate": task.get("scope_gate") or {},
        }
    )


def render_state(task: dict, touched_paths: list[str]) -> str:
    blockers = _blockers(task)
    next_action = task.get("next_action")
    if not next_action:
        next_action = "No further action required." if task.get("operational_status") == "verified" else "Resolve blockers and rerun verification."
    lines = [
        "# Task state",
        "",
        f"- Task ID: `{task.get('task_id')}`",
        "- Ledger: `task-ledger.jsonl` under the configured state root",
        f"- Phase: `{_phase(task)}`",
        f"- Status: `{task.get('status') or 'unknown'}`",
        f"- Operational status: `{task.get('operational_status') or 'pending'}`",
        f"- Current task: {task.get('task_name') or task.get('command_preview') or '-'}",
        f"- Next action: {next_action}",
        "",
        "## Touched paths",
        "",
        *([f"- `{path}`" for path in touched_paths] or ["- none"]),
        "",
        "## External surfaces",
        "",
        *([f"- {surface}" for surface in _external_surfaces(task)] or ["- none"]),
        "",
        "## Known blockers",
        "",
        *([f"- {blocker}" for blocker in blockers] or ["- none"]),
        "",
        "## Resume context",
        "",
        "- Read `plan.md` for approved scope and rollback.",
        "- Read `evidence.json` for claim-level evidence.",
        "- Read `blockers.md` before retrying a blocked task.",
    ]
    return "\n".join(lines).rstrip() + "\n"


def render_fallback_plan(task: dict) -> str:
    return "\n".join(
        [
            "# Task plan",
            "",
            f"- Task ID: `{task.get('task_id')}`",
            f"- Objective: {task.get('task_name') or task.get('command_preview') or '-'}",
            f"- Profile: `{task.get('profile') or '-'}`",
            "- Consensus gate: not required for this risk profile",
            "- Verification: process exit and applicable readback evidence",
            "- Rollback: inspect the task checkpoint or scoped diff before reverting",
            "",
        ]
    )


def render_blockers(task: dict) -> str:
    blockers = _blockers(task)
    lines = ["# Task blockers", "", f"- Task ID: `{task.get('task_id')}`", f"- Status: `{task.get('status') or 'unknown'}`", ""]
    if blockers:
        lines.extend(["## Active", "", *[f"- {item}" for item in blockers], ""])
    else:
        lines.extend(["No active blockers.", ""])
    return "\n".join(lines)


def write_task_state_bundle(
    task: dict,
    *,
    touched_paths: list[str],
    workspace: Path,
    state_root: Path,
) -> dict:
    task_id = _safe_task_id(task.get("task_id"))
    root = state_root / "task-bundles" / task_id
    state_path = root / "state.md"
    plan_path = root / "plan.md"
    evidence_path = root / "evidence.json"
    blockers_path = root / "blockers.md"
    _atomic_write(state_path, str(_redact(render_state(task, touched_paths))))
    if not plan_path.exists():
        _atomic_write(plan_path, str(_redact(render_fallback_plan(task))))
    _atomic_write(evidence_path, json.dumps(build_evidence_payload(task), indent=2, ensure_ascii=False) + "\n")
    _atomic_write(blockers_path, str(_redact(render_blockers(task))))
    return {
        "path": _relative(root, workspace),
        "state": _relative(state_path, workspace),
        "plan": _relative(plan_path, workspace),
        "evidence": _relative(evidence_path, workspace),
        "blockers": _relative(blockers_path, workspace),
    }
