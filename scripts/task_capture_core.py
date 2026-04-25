from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable


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

PATH_RULES = (
    ("project_state", ("docs/", "references/", "README", "CHANGELOG", "skills/"), "project docs or reusable workflow files changed"),
    ("knowledge_state", ("memory/", "MEMORY.md"), "memory or knowledge files changed"),
    ("operational_state", (".helm/", "scripts/"), "operational scripts or workspace automation changed"),
)


@dataclass(frozen=True)
class CaptureContext:
    task: dict
    touched_paths: list[str]
    command_blob: str
    harness_meta: dict


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def task_timestamp(task: dict) -> str:
    return str(task.get("finished_at") or task.get("started_at") or task.get("created_at") or utc_now_iso())


def add_unique(items: list[str], value: str) -> None:
    if value not in items:
        items.append(value)


def normalized_task_name(task_name: str) -> str:
    return " ".join(task_name.casefold().split())


def overlapping_paths(left: list[str], right: list[str]) -> list[str]:
    return sorted(set(left).intersection(right))


def summarize_paths(paths: list[str]) -> str:
    if not paths:
        return "no workspace file changes detected"
    preview = paths[:4]
    suffix = "" if len(paths) <= 4 else f" and {len(paths) - 4} more"
    return ", ".join(preview) + suffix


def build_context(task: dict, touched_paths: list[str]) -> CaptureContext:
    harness_meta = ((task.get("meta") or {}).get("harness") or {})
    parts = [
        task.get("task_name"),
        task.get("command_preview"),
        task.get("skill"),
        task.get("runtime_target"),
        task.get("runtime_note"),
        *task.get("checkpoint_paths", []),
        *touched_paths,
        json.dumps(harness_meta.get("browser_evidence") or {}, ensure_ascii=False),
        json.dumps(harness_meta.get("retrieval_evidence") or {}, ensure_ascii=False),
    ]
    command_blob = " ".join(str(part or "") for part in parts).casefold()
    return CaptureContext(task=task, touched_paths=touched_paths, command_blob=command_blob, harness_meta=harness_meta)


def infer_event_types(context: CaptureContext) -> tuple[list[str], list[str]]:
    event_types: list[str] = []
    reasons: list[str] = []

    profile = str(context.task.get("profile") or "")
    signal = PROFILE_SIGNAL_MAP.get(profile)
    if signal:
        event_type, reason = signal
        add_unique(event_types, event_type)
        add_unique(reasons, reason)

    for event_type, path_prefixes, reason in PATH_RULES:
        if any(path.startswith(path_prefixes) or path in path_prefixes for path in context.touched_paths):
            add_unique(event_types, event_type)
            add_unique(reasons, reason)

    for event_type, keywords, reason in KEYWORD_RULES:
        if any(keyword in context.command_blob for keyword in keywords):
            add_unique(event_types, event_type)
            add_unique(reasons, reason)

    browser_evidence = context.harness_meta.get("browser_evidence")
    if isinstance(browser_evidence, dict) and browser_evidence:
        add_unique(event_types, "operational_state")
        add_unique(reasons, "browser evidence was captured for a task that depended on runtime inspection")
        if browser_evidence.get("api_reusable") is True:
            add_unique(event_types, "project_state")
            add_unique(reasons, "browser work identified an API-reusable path worth keeping as reusable workflow state")

    retrieval_evidence = context.harness_meta.get("retrieval_evidence")
    if isinstance(retrieval_evidence, dict) and retrieval_evidence:
        add_unique(event_types, "operational_state")
        add_unique(reasons, "retrieval escalation evidence was captured for blocked or degraded access handling")
        exit_classification = str(retrieval_evidence.get("exit_classification") or "").casefold()
        if exit_classification in {"auth_required", "unsafe", "human_approval_needed"}:
            add_unique(event_types, "knowledge_state")
            add_unique(reasons, "retrieval exit classification should stay visible as durable operator context")

    return event_types, reasons


def infer_recommended_layers(event_types: list[str], command_blob: str) -> list[str]:
    layers = ["daily_memory"]
    if "project_state" in event_types or "operational_state" in event_types:
        layers.append("long_term_memory")
    if "knowledge_state" in event_types:
        layers.append("notes")
    if "ontology" in command_blob or "entity" in command_blob or "relation" in command_blob:
        layers.append("ontology")
    return layers


def infer_retention_profile(event_types: list[str]) -> dict:
    if not event_types:
        return {"tier": "ephemeral", "decay_hint": "drop_if_unreferenced"}
    if "knowledge_state" in event_types:
        return {"tier": "durable_knowledge", "decay_hint": "keep_until_superseded"}
    if "project_state" in event_types or "operational_state" in event_types:
        return {"tier": "durable_operational", "decay_hint": "keep_until_workflow_changes"}
    return {"tier": "working_memory", "decay_hint": "downrank_if_stale"}


def infer_claim_state(task: dict, event_types: list[str], harness_meta: dict, touched_paths: list[str]) -> dict:
    source_count = 0
    if task.get("checkpoint_paths"):
        source_count += 1
    if harness_meta.get("browser_evidence"):
        source_count += 1
    if harness_meta.get("retrieval_evidence"):
        source_count += 1
    if touched_paths:
        source_count += 1
    confidence_hint = "low"
    if task.get("status") == "completed" and source_count >= 2:
        confidence_hint = "high"
    elif task.get("status") in {"completed", "handoff_required"} and event_types:
        confidence_hint = "medium"
    return {
        "last_confirmed_at": task_timestamp(task),
        "source_count": source_count,
        "confidence_hint": confidence_hint,
        "recency_hint": "fresh" if event_types else "unscoped",
    }


def infer_supersession(task: dict, touched_paths: list[str], *, load_recent_final_tasks: Callable[[], list[dict]]) -> dict:
    status = str(task.get("status") or "")
    if status not in {"completed", "handoff_required"}:
        return {"state": "not_applicable", "supersedes_task_ids": []}
    current_name = normalized_task_name(str(task.get("task_name") or ""))
    current_skill = str(task.get("skill") or "")
    current_runtime = str(task.get("runtime_target") or "")
    matches: list[dict] = []
    for entry in reversed(load_recent_final_tasks()):
        score = 0
        if current_name and current_name == normalized_task_name(str(entry.get("task_name") or "")):
            score += 4
        if current_skill and current_skill == entry.get("skill"):
            score += 2
        if current_runtime and current_runtime == entry.get("runtime_target"):
            score += 1
        matched_paths = overlapping_paths(touched_paths, list((entry.get("memory_capture") or {}).get("touched_paths", [])))
        if matched_paths:
            score += 2
        if score < 4:
            continue
        matches.append(
            {
                "task_id": entry.get("task_id"),
                "task_name": entry.get("task_name"),
                "status": entry.get("status"),
                "score": score,
                "matched_paths": matched_paths,
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
        "matched_tasks": matches,
    }


def infer_crystallization(task: dict, event_types: list[str], reasons: list[str], touched_paths: list[str]) -> dict:
    affected: list[str] = []
    if task.get("skill"):
        affected.append(f"skill:{task['skill']}")
    if task.get("runtime_target"):
        affected.append(f"runtime:{task['runtime_target']}")
    affected.extend(f"event:{item}" for item in event_types)
    affected.extend(touched_paths[:4])
    return {
        "question": task.get("task_name") or "What changed in this run?",
        "action": task.get("command_preview") or task.get("task_name") or "unknown action",
        "result": f"{task.get('status')} with {summarize_paths(touched_paths)}",
        "lesson": reasons[0] if reasons else "No durable lesson inferred.",
        "affected_entities": affected[:8],
    }


def infer_review_flags(task: dict, claim_state: dict, supersession: dict, touched_paths: list[str]) -> list[dict]:
    flags: list[dict] = []
    name_blob = str(task.get("task_name") or "").casefold()
    if supersession.get("supersedes_task_ids"):
        flags.append({"type": "supersession_review", "severity": "medium"})
    if claim_state.get("confidence_hint") == "low":
        flags.append({"type": "low_confidence_review", "severity": "low"})
    if touched_paths and any(token in name_blob for token in ("replace", "rewrite", "rename", "correct", "refresh", "update", "fix", "supersede")):
        flags.append({"type": "contradiction_review", "severity": "medium"})
    return flags


def infer_suggested_entries(task: dict, event_types: list[str], layers: list[str], touched_paths: list[str]) -> dict[str, list[str]]:
    task_name = task.get("task_name") or "unnamed task"
    profile = task.get("profile") or "unknown"
    command_preview = task.get("command_preview") or ""
    suggestions: dict[str, list[str]] = {layer: [] for layer in layers}

    if "daily_memory" in suggestions:
        suggestions["daily_memory"].append(
            f"Record that `{task_name}` finished under `{profile}` with command `{command_preview}`."
        )
        if touched_paths:
            suggestions["daily_memory"].append(
                f"Capture the concrete workspace scope touched by this task: {summarize_paths(touched_paths)}."
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
            "If the change needs human-readable explanation, keep a short note instead of only raw logs."
        )

    if "ontology" in suggestions:
        suggestions["ontology"].append(
            "Update entities or relations only when the task changed stable people, assets, organizations, workflows, or named systems."
        )

    return suggestions


def build_memory_capture_plan(
    task: dict,
    *,
    touched_paths: list[str] | None = None,
    load_recent_final_tasks: Callable[[], list[dict]],
) -> dict:
    touched_paths = list(touched_paths or [])
    context = build_context(task, touched_paths)
    event_types, reasons = infer_event_types(context)

    status = task.get("status")
    profile = str(task.get("profile") or "")
    relevant = status in {"completed", "handoff_required"} and bool(event_types)
    if not relevant and status == "failed" and profile in {"service_ops", "risky_edit", "remote_handoff"}:
        relevant = True
        add_unique(reasons, "failure on a high-impact profile is itself durable operational context")

    layers = infer_recommended_layers(event_types, context.command_blob) if relevant else []
    suggestions = infer_suggested_entries(task, event_types, layers, touched_paths) if relevant else {}
    claim_state = infer_claim_state(task, event_types, context.harness_meta, touched_paths)
    retention = infer_retention_profile(event_types)
    supersession = infer_supersession(task, touched_paths, load_recent_final_tasks=load_recent_final_tasks)
    crystallization = infer_crystallization(task, event_types, reasons, touched_paths)
    review_flags = infer_review_flags(task, claim_state, supersession, touched_paths)

    return {
        "relevant": relevant,
        "priority": "required" if relevant else "none",
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
        "summary": (
            "Durable capture should be planned before the task is treated as operationally complete."
            if relevant
            else "No durable capture plan is recommended for this task."
        ),
        "task_exit_code": task.get("exit_code"),
        "touched_paths": touched_paths,
    }
