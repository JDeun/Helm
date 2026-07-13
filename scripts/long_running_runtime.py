"""Long-running agent runtime primitives for Helm.

This module keeps recoverable task state outside the model transcript:
task runs, phase checkpoints, approval pauses, idempotency keys, and a
specialist agent registry. It is intentionally small and file-backed so the
runner, Telegram bridge, cron jobs, and future coordinators can share one
state contract without importing a larger harness.
"""

from __future__ import annotations

import copy
import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from helm_workspace import get_workspace_layout
from scripts.io_utils import atomic_write_json
from scripts.model_health_lib import resolve_runtime_model
from scripts.role_catalog import expand_role_markers, load_role_catalog, resolve_role
from scripts.time_helpers import utc_now_iso

RUNTIME_STATE_SCHEMA_VERSION = 1

TASK_STATUSES = frozenset(
    {"pending", "running", "paused", "completed", "failed", "cancelled"}
)
APPROVAL_RESPONSES = frozenset({"approved", "cancelled"})
EXTERNAL_ACTION_RISK_CLASSES = frozenset(
    {"external_send", "delete", "financial", "security", "high_risk_mutation"}
)


def default_state_path() -> Path:
    return get_workspace_layout().state_root / "long-running-runtime.json"


def empty_runtime_state() -> dict[str, Any]:
    return {
        "runtime_state_schema_version": RUNTIME_STATE_SCHEMA_VERSION,
        "task_runs": {},
        "approval_gates": {},
        "agent_registry": {},
    }


def load_runtime_state(path: Path | None = None) -> dict[str, Any]:
    target = path or default_state_path()
    if not target.exists():
        return empty_runtime_state()
    payload = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("runtime state must be a JSON object")
    state = empty_runtime_state()
    state.update(payload)
    for key in ("task_runs", "approval_gates", "agent_registry"):
        if not isinstance(state.get(key), dict):
            raise ValueError(f"runtime state field {key!r} must be an object")
    return state


def save_runtime_state(state: dict[str, Any], path: Path | None = None) -> dict[str, Any]:
    target = path or default_state_path()
    saved = copy.deepcopy(state)
    atomic_write_json(target, saved, indent=2)
    return saved


def _now() -> str:
    return utc_now_iso()


def _stable_hash(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _is_expired(expires_at: str | None) -> bool:
    if not expires_at:
        return False
    try:
        expires = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
    except ValueError:
        return True
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) > expires.astimezone(timezone.utc)


def create_task_run(
    *,
    requester: str,
    source_surface: str,
    user_message: str,
    normalized_intent: str,
    task_id: str | None = None,
    risk_class: str = "low",
    status: str = "pending",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if status not in TASK_STATUSES:
        raise ValueError(f"unknown task status: {status!r}")
    timestamp = _now()
    return {
        "task_id": task_id or _new_id("task"),
        "requester": requester,
        "source_surface": source_surface,
        "user_message": user_message,
        "normalized_intent": normalized_intent,
        "status": status,
        "risk_class": risk_class,
        "created_at": timestamp,
        "updated_at": timestamp,
        "checkpoint_ids": [],
        "checkpoints": [],
        "artifact_paths": [],
        "evidence_refs": [],
        "idempotency_keys": [],
        "pending_approval_id": None,
        "metadata": dict(metadata or {}),
    }


def upsert_task_run(state: dict[str, Any], task_run: dict[str, Any]) -> dict[str, Any]:
    task_id = task_run.get("task_id")
    if not task_id:
        raise ValueError("task_run requires task_id")
    state.setdefault("task_runs", {})[task_id] = copy.deepcopy(task_run)
    return copy.deepcopy(task_run)


def get_task_run(state: dict[str, Any], task_id: str) -> dict[str, Any]:
    try:
        return copy.deepcopy(state["task_runs"][task_id])
    except KeyError as exc:
        raise ValueError(f"task_run not found: {task_id}") from exc


def append_checkpoint(
    state: dict[str, Any],
    *,
    task_id: str,
    phase: str,
    input_payload: Any,
    processed_items: list[Any] | None = None,
    pending_items: list[Any] | None = None,
    state_blob: dict[str, Any] | None = None,
    output_artifacts: list[str] | None = None,
    tool_evidence: list[dict[str, Any]] | None = None,
    checkpoint_id: str | None = None,
) -> dict[str, Any]:
    task = state.setdefault("task_runs", {}).get(task_id)
    if not isinstance(task, dict):
        raise ValueError(f"task_run not found: {task_id}")
    processed = list(processed_items or [])
    pending = list(pending_items or [])
    input_hash = _stable_hash(input_payload)
    idempotency_key = _stable_hash(
        {
            "task_id": task_id,
            "phase": phase,
            "input_hash": input_hash,
            "processed_items": processed,
            "output_artifacts": list(output_artifacts or []),
        }
    )
    if idempotency_key in task.setdefault("idempotency_keys", []):
        for existing in task.get("checkpoints", []):
            if existing.get("idempotency_key") == idempotency_key:
                return copy.deepcopy(existing)
    checkpoint = {
        "checkpoint_id": checkpoint_id or _new_id("cp"),
        "task_id": task_id,
        "phase": phase,
        "input_hash": input_hash,
        "processed_items": processed,
        "pending_items": pending,
        "state_blob": copy.deepcopy(state_blob or {}),
        "output_artifacts": list(output_artifacts or []),
        "tool_evidence": copy.deepcopy(tool_evidence or []),
        "idempotency_key": idempotency_key,
        "created_at": _now(),
    }
    task.setdefault("checkpoint_ids", []).append(checkpoint["checkpoint_id"])
    task.setdefault("checkpoints", []).append(checkpoint)
    task["idempotency_keys"].append(idempotency_key)
    task["updated_at"] = checkpoint["created_at"]
    return copy.deepcopy(checkpoint)


def resume_from_last_checkpoint(state: dict[str, Any], task_id: str) -> dict[str, Any]:
    task = state.setdefault("task_runs", {}).get(task_id)
    if not isinstance(task, dict):
        raise ValueError(f"task_run not found: {task_id}")
    checkpoints = task.get("checkpoints") or []
    if not checkpoints:
        raise ValueError(f"task_run has no checkpoints: {task_id}")
    checkpoint = copy.deepcopy(checkpoints[-1])
    task["status"] = "running"
    task["updated_at"] = _now()
    return {
        "task_id": task_id,
        "resume_checkpoint_id": checkpoint["checkpoint_id"],
        "phase": checkpoint["phase"],
        "processed_items": copy.deepcopy(checkpoint["processed_items"]),
        "pending_items": copy.deepcopy(checkpoint["pending_items"]),
        "state_blob": copy.deepcopy(checkpoint["state_blob"]),
        "idempotency_keys": list(task.get("idempotency_keys", [])),
    }


def pause_for_approval(
    state: dict[str, Any],
    *,
    task_id: str,
    pending_action: str,
    resource: str,
    risk_reason: str,
    risk_class: str,
    options: list[str] | None = None,
    expires_at: str | None = None,
    resume_command: str | None = None,
    approval_id: str | None = None,
) -> dict[str, Any]:
    task = state.setdefault("task_runs", {}).get(task_id)
    if not isinstance(task, dict):
        raise ValueError(f"task_run not found: {task_id}")
    existing_id = task.get("pending_approval_id")
    existing = state.setdefault("approval_gates", {}).get(existing_id)
    if (
        isinstance(existing, dict)
        and not existing.get("resolved_at")
        and existing.get("pending_action") == pending_action
        and existing.get("resource") == resource
        and existing.get("risk_class") == risk_class
    ):
        return copy.deepcopy(existing)
    created_at = _now()
    if expires_at is None:
        expires_at = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()
    gate = {
        "approval_id": approval_id or _new_id("approval"),
        "task_id": task_id,
        "pending_action": pending_action,
        "resource": resource,
        "risk_reason": risk_reason,
        "risk_class": risk_class,
        "options": list(options or ["approve", "cancel"]),
        "expires_at": expires_at,
        "resume_command": resume_command,
        "response": None,
        "resolved_at": None,
        "created_at": created_at,
    }
    state.setdefault("approval_gates", {})[gate["approval_id"]] = gate
    task["status"] = "paused"
    task["pending_approval_id"] = gate["approval_id"]
    task["updated_at"] = created_at
    return copy.deepcopy(gate)


def resolve_approval(
    state: dict[str, Any],
    *,
    approval_id: str,
    response: str,
    responder: str,
) -> dict[str, Any]:
    if response not in APPROVAL_RESPONSES:
        raise ValueError(f"unknown approval response: {response!r}")
    gate = state.setdefault("approval_gates", {}).get(approval_id)
    if not isinstance(gate, dict):
        raise ValueError(f"approval gate not found: {approval_id}")
    if gate.get("resolved_at"):
        raise ValueError(f"approval gate already resolved: {approval_id}")
    if _is_expired(gate.get("expires_at")):
        raise ValueError(f"approval gate expired: {approval_id}")
    task = state.setdefault("task_runs", {}).get(gate.get("task_id"))
    if not isinstance(task, dict):
        raise ValueError(f"task_run not found: {gate.get('task_id')}")
    resolved_at = _now()
    gate["response"] = response
    gate["responder"] = responder
    gate["resolved_at"] = resolved_at
    task["pending_approval_id"] = None
    task["status"] = "running" if response == "approved" else "cancelled"
    task["updated_at"] = resolved_at
    return copy.deepcopy(gate)


def can_execute_external_action(
    state: dict[str, Any],
    *,
    task_id: str,
    pending_action: str,
    resource: str,
    risk_class: str,
) -> bool:
    if risk_class not in EXTERNAL_ACTION_RISK_CLASSES:
        return True
    task = state.setdefault("task_runs", {}).get(task_id)
    if not isinstance(task, dict):
        raise ValueError(f"task_run not found: {task_id}")
    if task.get("status") != "running":
        return False
    for gate in state.get("approval_gates", {}).values():
        if (
            isinstance(gate, dict)
            and gate.get("task_id") == task_id
            and gate.get("pending_action") == pending_action
            and gate.get("resource") == resource
            and gate.get("risk_class") == risk_class
            and gate.get("response") == "approved"
            and not _is_expired(gate.get("expires_at"))
        ):
            return True
    return False


def register_agent(
    state: dict[str, Any],
    *,
    agent_id: str,
    role: str,
    allowed_tools: list[str],
    memory_scope: str,
    model_policy: dict[str, Any],
    skill_profile: str,
    timeout: int,
    owner: str,
    version: str,
    output_contract: dict[str, Any],
) -> dict[str, Any]:
    if not agent_id:
        raise ValueError("agent_id is required")
    if timeout <= 0:
        raise ValueError("timeout must be positive")
    role_contract = None
    role_warning = None
    if role.startswith("[role:"):
        expanded = expand_role_markers(role)
        role_contract = expanded["role"]
    elif role in load_role_catalog():
        role_contract = resolve_role(role)
    else:
        role_warning = "legacy free-form role; migrate to [role:<role-id>]"
    resolved_model_policy = copy.deepcopy(model_policy)
    requested_models = [
        str(resolved_model_policy.get(key) or "").casefold()
        for key in ("model", "ref", "selected_model")
    ]
    requested_omfm = any(value.startswith("omfm/") for value in requested_models) or str(
        resolved_model_policy.get("provider") or ""
    ).casefold() == "omfm"
    if resolved_model_policy.get("runtime_recovery") is True or requested_omfm:
        choice = resolve_runtime_model(profile=skill_profile, model_policy=resolved_model_policy)
        resolved_model_policy["selected_model"] = choice.model
        resolved_model_policy["selection_reason"] = choice.reason
        resolved_model_policy["selection_source"] = choice.source
    entry = {
        "agent_id": agent_id,
        "role": role,
        "role_id": (role_contract or {}).get("role_id"),
        "role_marker": f"[role:{role_contract['role_id']}]" if role_contract else None,
        "role_prompt": (role_contract or {}).get("prompt"),
        "role_warning": role_warning,
        "allowed_tools": sorted(dict.fromkeys(allowed_tools)),
        "memory_scope": memory_scope,
        "model_policy": resolved_model_policy,
        "skill_profile": skill_profile,
        "timeout": timeout,
        "owner": owner,
        "version": version,
        "output_contract": copy.deepcopy(output_contract),
        "updated_at": _now(),
    }
    state.setdefault("agent_registry", {})[agent_id] = entry
    return copy.deepcopy(entry)


def get_agent(state: dict[str, Any], agent_id: str) -> dict[str, Any]:
    try:
        return copy.deepcopy(state["agent_registry"][agent_id])
    except KeyError as exc:
        raise ValueError(f"agent not found: {agent_id}") from exc


def record_agent_event(
    state: dict[str, Any],
    *,
    task_id: str,
    agent_id: str,
    event: str,
    recoverable: bool,
    detail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    task = state.setdefault("task_runs", {}).get(task_id)
    if not isinstance(task, dict):
        raise ValueError(f"task_run not found: {task_id}")
    if agent_id not in state.setdefault("agent_registry", {}):
        raise ValueError(f"agent not found: {agent_id}")
    payload = {
        "agent_id": agent_id,
        "event": event,
        "recoverable": recoverable,
        "detail": copy.deepcopy(detail or {}),
        "recorded_at": _now(),
    }
    task.setdefault("agent_events", []).append(payload)
    task["updated_at"] = payload["recorded_at"]
    return copy.deepcopy(payload)
