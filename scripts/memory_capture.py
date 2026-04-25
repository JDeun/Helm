from __future__ import annotations

import json
import sys
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path

from helm_workspace import get_workspace_layout


PROFILE_SIGNAL_MAP = {
    "service_ops": ("operational_state", "service operation may change live systems or external records"),
    "risky_edit": ("project_state", "risky edit usually changes reusable workflow or workspace behavior"),
    "remote_handoff": ("operational_state", "remote handoff changes where execution responsibility lives"),
}

KEYWORD_RULES = (
    (
        "project_state",
        (
            "readme",
            "docs",
            "skill",
            "reference",
            "router",
            "workflow",
            "policy",
            "release",
            "tag",
            "commit",
            "push",
        ),
        "project docs, workflow rules, or release state changed",
    ),
    (
        "operational_state",
        (
            "cron",
            "automation",
            "calendar",
            "sheet",
            "service",
            "integration",
            "deploy",
            "publish",
            "delivery",
        ),
        "operational workflow or external integration changed",
    ),
    (
        "knowledge_state",
        (
            "memory",
            "ontology",
            "obsidian",
            "note",
            "notes",
            "entity",
            "relation",
        ),
        "durable knowledge source or note structure changed",
    ),
)


def _norm(value: object) -> str:
    return str(value or "").casefold()


def _iter_strings(values: Iterable[object]) -> Iterable[str]:
    for value in values:
        if value is None:
            continue
        yield str(value)


def _add_reason(reasons: list[str], reason: str) -> None:
    if reason not in reasons:
        reasons.append(reason)


def _add_event(event_types: list[str], event_type: str) -> None:
    if event_type not in event_types:
        event_types.append(event_type)


def _recommended_layers(event_types: list[str], command_blob: str) -> list[str]:
    layers = ["daily_memory"]
    if "project_state" in event_types or "operational_state" in event_types:
        layers.append("long_term_memory")
    if "knowledge_state" in event_types:
        layers.append("notes")
    if "ontology" in command_blob or "entity" in command_blob or "relation" in command_blob:
        layers.append("ontology")
    return layers


def _task_timestamp(task: dict) -> str:
    return str(task.get("finished_at") or task.get("started_at") or task.get("created_at") or datetime.now(timezone.utc).isoformat())


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


def _normalized_task_name(task: dict) -> str:
    return " ".join(str(task.get("task_name") or "").casefold().split())


def _recent_final_tasks(task: dict, state_root: Path | None = None) -> list[dict]:
    if state_root is None:
        state_root = get_workspace_layout().state_root
    ledger = _read_jsonl(state_root / "task-ledger.jsonl")
    by_task: dict[str, dict] = {}
    for entry in ledger:
        task_id = entry.get("task_id")
        if task_id and entry.get("status") in {"completed", "handoff_required", "failed"}:
            by_task[task_id] = entry
    current_id = task.get("task_id")
    rows = [entry for entry in by_task.values() if entry.get("task_id") != current_id]
    rows.sort(key=_task_timestamp)
    return rows[-80:]


def _retention_profile(event_types: list[str]) -> dict:
    if not event_types:
        return {"tier": "ephemeral", "decay_hint": "drop_if_unreferenced"}
    if "knowledge_state" in event_types:
        return {"tier": "durable_knowledge", "decay_hint": "keep_until_superseded"}
    if "project_state" in event_types or "operational_state" in event_types:
        return {"tier": "durable_operational", "decay_hint": "keep_until_workflow_changes"}
    return {"tier": "working_memory", "decay_hint": "downrank_if_stale"}


def _claim_state(task: dict, event_types: list[str], harness_meta: dict) -> dict:
    source_count = 0
    if task.get("checkpoint_paths"):
        source_count += 1
    if harness_meta.get("browser_evidence"):
        source_count += 1
    if harness_meta.get("retrieval_evidence"):
        source_count += 1
    confidence_hint = "low"
    if task.get("status") == "completed" and source_count >= 2:
        confidence_hint = "high"
    elif task.get("status") in {"completed", "handoff_required"} and event_types:
        confidence_hint = "medium"
    return {
        "last_confirmed_at": _task_timestamp(task),
        "source_count": source_count,
        "confidence_hint": confidence_hint,
        "recency_hint": "fresh" if event_types else "unscoped",
    }


def _supersession(task: dict) -> dict:
    status = str(task.get("status") or "")
    if status not in {"completed", "handoff_required"}:
        return {"state": "not_applicable", "supersedes_task_ids": []}
    current_name = _normalized_task_name(task)
    current_skill = str(task.get("skill") or "")
    current_runtime = str(task.get("runtime_target") or "")
    matches: list[dict] = []
    for entry in reversed(_recent_final_tasks(task)):
        score = 0
        if current_name and current_name == _normalized_task_name(entry):
            score += 4
        if current_skill and current_skill == entry.get("skill"):
            score += 2
        if current_runtime and current_runtime == entry.get("runtime_target"):
            score += 1
        if score < 4:
            continue
        matches.append(
            {
                "task_id": entry.get("task_id"),
                "task_name": entry.get("task_name"),
                "status": entry.get("status"),
                "score": score,
            }
        )
        if len(matches) >= 3:
            break
    if not matches:
        return {"state": "none", "supersedes_task_ids": [], "superseded_by": None, "replacement_reason": None}
    failed_matches = [item for item in matches if item.get("status") == "failed"]
    return {
        "state": "retries_or_replaces_prior_work" if failed_matches else "refreshes_prior_state",
        "supersedes_task_ids": [item["task_id"] for item in matches],
        "superseded_by": None,
        "replacement_reason": "newer finalized task overlaps prior work with the same task or runtime identity",
    }


def _crystallization(task: dict, event_types: list[str], reasons: list[str]) -> dict:
    affected = []
    if task.get("skill"):
        affected.append(f"skill:{task['skill']}")
    if task.get("runtime_target"):
        affected.append(f"runtime:{task['runtime_target']}")
    affected.extend(f"event:{item}" for item in event_types)
    return {
        "question": task.get("task_name") or "What changed in this run?",
        "action": task.get("command_preview") or task.get("task_name") or "unknown action",
        "result": f"{task.get('status')} task finalization assessed",
        "lesson": reasons[0] if reasons else "No durable lesson inferred.",
        "affected_entities": affected[:8],
    }


def _review_flags(task: dict, claim_state: dict) -> list[dict]:
    flags: list[dict] = []
    name_blob = _norm(task.get("task_name"))
    supersession = _supersession(task)
    if supersession.get("supersedes_task_ids"):
        flags.append({"type": "supersession_review", "severity": "medium"})
    if claim_state.get("confidence_hint") == "low":
        flags.append({"type": "low_confidence_review", "severity": "low"})
    if any(token in name_blob for token in ("replace", "rewrite", "rename", "correct", "refresh", "update", "fix", "supersede")):
        flags.append({"type": "contradiction_review", "severity": "medium"})
    return flags


def _suggested_entries(task: dict, event_types: list[str], layers: list[str]) -> dict[str, list[str]]:
    task_name = task.get("task_name") or "unnamed task"
    profile = task.get("profile") or "unknown"
    command_preview = task.get("command_preview") or ""
    suggestions: dict[str, list[str]] = {layer: [] for layer in layers}

    if "daily_memory" in suggestions:
        suggestions["daily_memory"].append(
            f"Record that `{task_name}` finished under `{profile}` with command `{command_preview}`."
        )
        if "project_state" in event_types:
            suggestions["daily_memory"].append(
                "Capture which reusable project files, docs, or release-facing artifacts changed."
            )
        if "operational_state" in event_types:
            suggestions["daily_memory"].append(
                "Capture the operational side effect so later work starts from the updated system state."
            )

    if "long_term_memory" in suggestions:
        suggestions["long_term_memory"].append(
            "Promote only durable rules, workflow changes, or positioning decisions that should affect future tasks."
        )

    if "notes" in suggestions:
        suggestions["notes"].append(
            "If the change needs human-readable explanation, keep a short note or Obsidian entry instead of only raw logs."
        )

    if "ontology" in suggestions:
        suggestions["ontology"].append(
            "Update entities or relations only when the task changed stable people, assets, organizations, workflows, or named systems."
        )

    return suggestions


def build_memory_capture_plan(task: dict) -> dict:
    harness_meta = ((task.get("meta") or {}).get("harness") or {})
    command_blob = " ".join(
        _iter_strings(
            [
                task.get("task_name"),
                task.get("command_preview"),
                task.get("skill"),
                task.get("runtime_target"),
                task.get("runtime_note"),
                *task.get("checkpoint_paths", []),
                json.dumps(harness_meta.get("browser_evidence") or {}, ensure_ascii=False),
                json.dumps(harness_meta.get("retrieval_evidence") or {}, ensure_ascii=False),
            ]
        )
    ).casefold()

    event_types: list[str] = []
    reasons: list[str] = []

    profile = str(task.get("profile") or "")
    signal = PROFILE_SIGNAL_MAP.get(profile)
    if signal:
        event_type, reason = signal
        _add_event(event_types, event_type)
        _add_reason(reasons, reason)

    for event_type, keywords, reason in KEYWORD_RULES:
        if any(keyword in command_blob for keyword in keywords):
            _add_event(event_types, event_type)
            _add_reason(reasons, reason)

    browser_evidence = harness_meta.get("browser_evidence")
    if isinstance(browser_evidence, dict) and browser_evidence:
        _add_event(event_types, "operational_state")
        _add_reason(reasons, "browser evidence was captured for a task that depended on runtime inspection")
        if browser_evidence.get("api_reusable") is True:
            _add_event(event_types, "project_state")
            _add_reason(reasons, "browser work identified an API-reusable path worth keeping as reusable workflow state")

    retrieval_evidence = harness_meta.get("retrieval_evidence")
    if isinstance(retrieval_evidence, dict) and retrieval_evidence:
        _add_event(event_types, "operational_state")
        _add_reason(reasons, "retrieval escalation evidence was captured for blocked or degraded access handling")
        exit_classification = _norm(retrieval_evidence.get("exit_classification"))
        if exit_classification in {"auth_required", "unsafe", "human_approval_needed"}:
            _add_event(event_types, "knowledge_state")
            _add_reason(reasons, "retrieval exit classification should stay visible as durable operator context")

    status = task.get("status")
    exit_code = task.get("exit_code")
    relevant = status in {"completed", "handoff_required"} and bool(event_types)
    if not relevant and status == "failed" and profile in {"service_ops", "risky_edit", "remote_handoff"}:
        relevant = True
        _add_reason(reasons, "failure on a high-impact profile is itself durable operational context")

    layers = _recommended_layers(event_types, command_blob) if relevant else []
    suggestions = _suggested_entries(task, event_types, layers) if relevant else {}
    priority = "required" if relevant else "none"
    claim_state = _claim_state(task, event_types, harness_meta)
    retention = _retention_profile(event_types)
    supersession = _supersession(task)
    crystallization = _crystallization(task, event_types, reasons)
    review_flags = _review_flags(task, claim_state)
    summary = (
        "Durable capture should be planned before the task is treated as operationally complete."
        if relevant
        else "No durable capture plan is recommended for this task."
    )

    return {
        "relevant": relevant,
        "priority": priority,
        "event_types": event_types,
        "recommended_layers": layers,
        "reasons": reasons,
        "suggested_entries": suggestions,
        "claim_state": claim_state,
        "retention": retention,
        "supersession": supersession,
        "crystallization": crystallization,
        "review_flags": review_flags,
        "finalization_status": "capture_planned" if relevant else "no_capture_needed",
        "summary": summary,
        "task_exit_code": exit_code,
    }
