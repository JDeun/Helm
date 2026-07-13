#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

try:
    from role_catalog import expand_role_markers
except ModuleNotFoundError:  # Helm package import
    from scripts.role_catalog import expand_role_markers


SHARED_PIPELINE_HINTS = (
    "cron", "job", "skill", "router", "workflow", "briefing", "memory", "ledger",
    "telegram", "calendar", "sheets", "obsidian", "automation", "release",
)
ROLE_ORDER = ("planner", "architect", "critic")
VALID_DECISIONS = {"approve", "revise", "block"}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def requires_consensus(profile: str, *, task_name: str = "", command: list[str] | None = None) -> bool:
    if profile in {"risky_edit", "service_ops"}:
        return True
    blob = " ".join([task_name, *(command or [])]).casefold()
    return profile == "workspace_edit" and any(hint in blob for hint in SHARED_PIPELINE_HINTS)


def _safe_relative_path(value: object) -> str:
    raw = str(value or "").strip().replace("\\", "/")
    path = PurePosixPath(raw)
    if not raw or path.is_absolute() or ".." in path.parts:
        raise ValueError(f"scope path must stay workspace-relative: {value!r}")
    normalized = str(path)
    if normalized in {"", "."}:
        raise ValueError("scope path may not be the whole workspace")
    return normalized.rstrip("/")


def normalize_scope(values: list[object]) -> list[str]:
    return list(dict.fromkeys(_safe_relative_path(value) for value in values))


def default_scope(task: dict) -> list[str]:
    configured = task.get("checkpoint_paths") or []
    if configured:
        return normalize_scope(list(configured))
    command_paths = []
    for token in (task.get("command") or [])[1:]:
        token = str(token)
        if token.startswith("-") or token.startswith("{"):
            continue
        candidate = Path(token)
        if not candidate.is_absolute() and candidate.parts and candidate.parts[0] in {
            "scripts", "skills", "docs", "references", "tests", "AGENTS.md", "TOOLS.md", "MEMORY.md"
        }:
            command_paths.append(token)
    if command_paths:
        return normalize_scope(command_paths)
    return ["scripts", "skills", "docs", "references", "tests", "AGENTS.md", "TOOLS.md"]


def build_plan(
    task: dict,
    *,
    scope: list[str] | None = None,
    verification_commands: list[list[str]] | None = None,
    acceptance_criteria: list[str] | None = None,
) -> dict:
    task_scope = normalize_scope(scope or default_scope(task))
    criteria = [str(item).strip() for item in acceptance_criteria or [] if str(item).strip()]
    if not criteria:
        criteria = ["primary command exits successfully", "all touched paths remain within declared scope"]
    verification = [
        {"kind": "command", "argv": list(command)}
        for command in verification_commands or []
    ]
    verification.extend(
        [
            {"kind": "process_exit", "expected": 0},
            {"kind": "filesystem_readback", "scope": task_scope},
        ]
    )
    checkpoint_id = task.get("checkpoint_id")
    rollback = (
        f"Restore checkpoint {checkpoint_id}." if checkpoint_id else
        "Stop on verification failure; inspect the scoped diff and revert only the affected paths with an operator-approved patch."
    )
    return {
        "schema_version": 1,
        "task_id": str(task.get("task_id") or ""),
        "objective": str(task.get("task_name") or task.get("command_preview") or "unnamed task"),
        "profile": str(task.get("profile") or ""),
        "scope": task_scope,
        "non_goals": ["paths outside declared scope", "unrequested external mutations", "unrelated refactors"],
        "tasks": [
            {
                "task_id": "task-1",
                "title": str(task.get("task_name") or "execute approved change"),
                "scope": task_scope,
                "acceptance_criteria": criteria,
            }
        ],
        "verification": verification,
        "rollback_note": rollback,
        "created_at": utc_now_iso(),
    }


def _validate_plan_shape(plan: dict) -> list[str]:
    findings: list[str] = []
    if not str(plan.get("objective") or "").strip():
        findings.append("objective is missing")
    try:
        scope = normalize_scope(list(plan.get("scope") or []))
    except ValueError as exc:
        findings.append(str(exc))
        scope = []
    if not scope:
        findings.append("scope is missing")
    if not isinstance(plan.get("non_goals"), list) or not any(str(item).strip() for item in plan.get("non_goals") or []):
        findings.append("non_goals are missing")
    if not isinstance(plan.get("tasks"), list) or not plan.get("tasks"):
        findings.append("atomic tasks are missing")
    if not isinstance(plan.get("verification"), list) or not plan.get("verification"):
        findings.append("verification plan is missing")
    if not str(plan.get("rollback_note") or "").strip():
        findings.append("rollback note is missing")
    return findings


def review_plan(plan: dict, role_id: str, *, round_number: int) -> dict:
    expanded_role = expand_role_markers(f"[role:{role_id}] review the candidate plan")
    role = expanded_role["role"]
    findings: list[str] = []
    required_changes: list[str] = []
    structural = _validate_plan_shape(plan)
    if role_id == "planner":
        findings.extend(item for item in structural if item in {"objective is missing", "atomic tasks are missing", "verification plan is missing"})
        for task in plan.get("tasks") or []:
            if not isinstance(task, dict) or not task.get("acceptance_criteria"):
                findings.append("each atomic task requires acceptance criteria")
                break
    elif role_id == "architect":
        findings.extend(item for item in structural if "scope" in item or "rollback" in item)
        try:
            plan_scope = normalize_scope(list(plan.get("scope") or []))
        except ValueError:
            plan_scope = []
        for task in plan.get("tasks") or []:
            if not isinstance(task, dict):
                continue
            try:
                task_scope = normalize_scope(list(task.get("scope") or []))
            except ValueError as exc:
                findings.append(str(exc))
                continue
            if not plan_scope or any(not path_within_scope(path, plan_scope) for path in task_scope):
                findings.append("atomic task scope exceeds plan scope")
    elif role_id == "critic":
        findings.extend(item for item in structural if item in {"non_goals are missing", "verification plan is missing", "rollback note is missing"})
        verification_kinds = {
            str(item.get("kind")) for item in plan.get("verification") or [] if isinstance(item, dict)
        }
        if "process_exit" not in verification_kinds:
            findings.append("verification lacks process exit evidence")
        if "filesystem_readback" not in verification_kinds:
            findings.append("verification lacks file readback evidence")
    else:
        raise ValueError(f"role is not part of the consensus gate: {role_id}")
    required_changes.extend(findings)
    decision = "approve" if not findings else "revise"
    if any("workspace-relative" in finding or "exceeds plan scope" in finding for finding in findings):
        decision = "block"
    return {
        "role": role_id,
        "role_marker": f"[role:{role_id}]",
        "role_prompt": role["prompt"],
        "expanded_role_input": expanded_role["expanded"],
        "role_contract": role.get("output_contract", []),
        "round": round_number,
        "decision": decision,
        "findings": findings,
        "required_changes": required_changes,
    }


def _repair_structural_gaps(plan: dict) -> dict:
    repaired = copy.deepcopy(plan)
    if not repaired.get("non_goals"):
        repaired["non_goals"] = ["paths outside declared scope", "unrequested external mutations"]
    if not repaired.get("rollback_note"):
        repaired["rollback_note"] = "Stop and revert only the declared scope after operator review."
    if not repaired.get("verification"):
        repaired["verification"] = [
            {"kind": "process_exit", "expected": 0},
            {"kind": "filesystem_readback", "scope": repaired.get("scope") or []},
        ]
    for index, task in enumerate(repaired.get("tasks") or []):
        if isinstance(task, dict) and not task.get("acceptance_criteria"):
            task["acceptance_criteria"] = ["task command exits successfully", "scope remains bounded"]
            task.setdefault("task_id", f"task-{index + 1}")
    return repaired


def run_consensus(plan: dict, *, max_rounds: int = 2) -> dict:
    if max_rounds < 1 or max_rounds > 2:
        raise ValueError("consensus max_rounds must be 1 or 2")
    candidate = copy.deepcopy(plan)
    rounds = []
    for round_number in range(1, max_rounds + 1):
        reviews = [review_plan(candidate, role, round_number=round_number) for role in ROLE_ORDER]
        decisions = {review["decision"] for review in reviews}
        rounds.append({"round": round_number, "reviews": reviews})
        if decisions == {"approve"}:
            return {"status": "approved", "ok": True, "round_count": round_number, "plan": candidate, "rounds": rounds}
        if "block" in decisions:
            break
        if round_number < max_rounds:
            candidate = _repair_structural_gaps(candidate)
    return {
        "status": "blocked",
        "ok": False,
        "round_count": len(rounds),
        "plan": candidate,
        "rounds": rounds,
        "reason": "planner/architect/critic did not reach unanimous approval within two rounds",
    }


def render_plan_markdown(result: dict) -> str:
    plan = result.get("plan") or {}
    lines = [
        "# Consensus plan",
        "",
        f"- Task ID: `{plan.get('task_id') or '-'}`",
        f"- Status: `{result.get('status')}`",
        f"- Profile: `{plan.get('profile') or '-'}`",
        f"- Objective: {plan.get('objective') or '-'}",
        f"- Consensus rounds: {result.get('round_count', 0)}",
        "",
        "## Scope",
        "",
        *[f"- `{path}`" for path in plan.get("scope") or []],
        "",
        "## Non-goals",
        "",
        *[f"- {item}" for item in plan.get("non_goals") or []],
        "",
        "## Tasks",
        "",
    ]
    for task in plan.get("tasks") or []:
        if not isinstance(task, dict):
            continue
        lines.append(f"### {task.get('task_id')}: {task.get('title')}")
        lines.append("")
        lines.extend(f"- Acceptance: {item}" for item in task.get("acceptance_criteria") or [])
        lines.append("")
    lines.extend(["## Verification", "", "```json", json.dumps(plan.get("verification") or [], indent=2, ensure_ascii=False), "```", "", "## Rollback", "", str(plan.get("rollback_note") or "-"), "", "## Reviews", ""])
    for round_row in result.get("rounds") or []:
        for review in round_row.get("reviews") or []:
            findings = "; ".join(review.get("findings") or []) or "none"
            lines.append(f"- round {review.get('round')} `[role:{review.get('role')}]`: **{review.get('decision')}** — {findings}")
    return "\n".join(lines).rstrip() + "\n"


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def write_consensus_artifacts(result: dict, *, state_root: Path) -> dict:
    task_id = str((result.get("plan") or {}).get("task_id") or "")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", task_id):
        raise ValueError("consensus plan requires a safe task_id")
    root = state_root / "task-bundles" / task_id
    plan_path = root / "plan.md"
    consensus_path = root / "consensus.json"
    _atomic_write(plan_path, render_plan_markdown(result))
    _atomic_write(consensus_path, json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    return {"root": str(root), "plan": str(plan_path), "consensus": str(consensus_path)}


def attach_consensus_plan(
    task: dict,
    *,
    state_root: Path,
    plan: dict | None = None,
    verification_commands: list[list[str]] | None = None,
    acceptance_criteria: list[str] | None = None,
) -> dict:
    candidate = plan or build_plan(
        task,
        verification_commands=verification_commands,
        acceptance_criteria=acceptance_criteria,
    )
    candidate["task_id"] = str(task.get("task_id") or candidate.get("task_id") or "")
    candidate["profile"] = str(task.get("profile") or candidate.get("profile") or "")
    result = run_consensus(candidate)
    result["artifacts"] = write_consensus_artifacts(result, state_root=state_root)
    task["consensus_plan"] = result
    if result["ok"]:
        evidence = list(task.get("evidence_refs") or [])
        evidence.append("consensus_plan:approved")
        task["evidence_refs"] = list(dict.fromkeys(evidence))
        claims = list(task.get("completion_claims") or [])
        if not any(isinstance(item, dict) and item.get("claim_id") == "consensus_plan_approved" for item in claims):
            claims.append({
                "claim_id": "consensus_plan_approved",
                "claim": "consensus_plan_approved",
                "evidence_type": "consensus_plan",
                "evidence_refs": ["consensus_plan:approved"],
            })
        task["completion_claims"] = claims
    return result


def path_within_scope(path: str, scope: list[str]) -> bool:
    try:
        target = _safe_relative_path(path)
    except ValueError:
        return False
    for raw in scope:
        allowed = _safe_relative_path(raw)
        if target == allowed or target.startswith(allowed + "/"):
            return True
    return False


def evaluate_scope(touched_paths: list[str], plan: dict | None) -> dict:
    scope = normalize_scope(list((plan or {}).get("scope") or [])) if plan else []
    violations = [path for path in touched_paths if not path_within_scope(path, scope)]
    return {"ok": bool(scope) and not violations, "scope": scope, "touched_paths": touched_paths, "violations": violations}


def main() -> int:
    parser = argparse.ArgumentParser(description="Create and validate a Planner/Architect/Critic consensus plan.")
    parser.add_argument("--task-json", required=True, help="Task JSON object.")
    parser.add_argument("--plan", type=Path, help="Optional proposed plan JSON.")
    parser.add_argument("--state-root", type=Path, default=Path(".openclaw"))
    args = parser.parse_args()
    try:
        task = json.loads(args.task_json)
        if not isinstance(task, dict):
            raise ValueError("task JSON must be an object")
        plan = json.loads(args.plan.read_text(encoding="utf-8")) if args.plan else None
        if plan is not None and not isinstance(plan, dict):
            raise ValueError("plan JSON must be an object")
        result = attach_consensus_plan(task, state_root=args.state_root, plan=plan)
    except (json.JSONDecodeError, OSError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
