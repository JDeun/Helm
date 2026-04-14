#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from helm_workspace import get_workspace_layout


WORKSPACE = get_workspace_layout().root
PROFILE_FILE = WORKSPACE / "references" / "execution_profiles.json"
POLICY_FILE = WORKSPACE / "references" / "skill_profile_policies.json"
HARNESS_POLICY_FILE = WORKSPACE / "references" / "adaptive_harness_policy.json"
CONTRACTS_FILE = WORKSPACE / "references" / "skill_contracts.json"
TASK_LEDGER = get_workspace_layout().state_root / "task-ledger.jsonl"


def load_json(path: Path, default: object) -> object:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def load_profiles() -> dict[str, dict]:
    data = load_json(PROFILE_FILE, {"profiles": {}})
    return data.get("profiles", {}) if isinstance(data, dict) else {}


def load_skill_policies() -> dict[str, dict]:
    data = load_json(POLICY_FILE, {"skills": {}})
    return data.get("skills", {}) if isinstance(data, dict) else {}


def load_harness_policy() -> dict:
    data = load_json(HARNESS_POLICY_FILE, {})
    return data if isinstance(data, dict) else {}


def load_skill_contracts() -> dict[str, dict]:
    data = load_json(CONTRACTS_FILE, {})
    return data if isinstance(data, dict) else {}


def resolve_model_tier(policy: dict, model: str | None, model_tier: str | None) -> str:
    tiers = (policy.get("tiers") or {}).keys()
    if model_tier in tiers:
        return str(model_tier)
    aliases = policy.get("model_aliases", {})
    if model and model in aliases:
        return str(aliases[model])
    if model_tier:
        return str(model_tier)
    return "frontier"


def enforcement_rank(policy: dict, level: str) -> int:
    order = policy.get("enforcement_order", ["light", "balanced", "strict"])
    try:
        return order.index(level)
    except ValueError:
        return 0


def max_enforcement(policy: dict, *levels: str) -> str:
    filtered = [level for level in levels if level]
    if not filtered:
        return "light"
    return max(filtered, key=lambda level: enforcement_rank(policy, level))


def resolve_skill_contract(skill: str | None) -> dict:
    if not skill:
        return {}
    contracts = load_skill_contracts()
    contract = contracts.get(skill)
    if contract:
        return contract
    policies = load_skill_policies()
    policy = policies.get(skill, {})
    return {
        "context": {"required": False},
        "require_finalization_written": True,
        "allowed_profiles": policy.get("allowed_profiles", []),
        "default_profile": policy.get("default_profile"),
        "narrow_runner_required": False,
    }


def resolve_enforcement_level(model: str | None, model_tier: str | None, profile: str, contract: dict) -> tuple[str, str]:
    policy = load_harness_policy()
    tier = resolve_model_tier(policy, model, model_tier)
    tier_policy = (policy.get("tiers") or {}).get(tier, {})
    enforcement = str(tier_policy.get("default_enforcement", "light"))
    enforcement = max_enforcement(policy, enforcement, str((tier_policy.get("profile_overrides") or {}).get(profile, "")))
    if (contract.get("context") or {}).get("required"):
        enforcement = max_enforcement(policy, enforcement, "balanced")
    if contract.get("narrow_runner_required") and profile in {"service_ops", "risky_edit"}:
        enforcement = max_enforcement(policy, enforcement, "strict")
    return tier, enforcement


def build_hydration_commands(contract: dict) -> list[list[str]]:
    context = contract.get("context") or {}
    if not context.get("required"):
        return []
    commands: list[list[str]] = [
        [
            "python3",
            str(WORKSPACE / "scripts" / "ops_memory_query.py"),
            str(context.get("query") or ""),
            "--include",
            *[str(item) for item in context.get("include", [])],
            "--limit",
            str(context.get("limit", 6)),
        ]
    ]
    failed_include = context.get("failed_include")
    if failed_include:
        commands.append(
            [
                "python3",
                str(WORKSPACE / "scripts" / "ops_memory_query.py"),
                "--include",
                *[str(item) for item in failed_include],
                "--failed-only",
                "--limit",
                str(context.get("failed_limit", 6)),
            ]
        )
    return commands


def append_check(checks: list[dict], name: str, ok: bool, detail: str) -> None:
    checks.append({"name": name, "ok": ok, "detail": detail})


def preflight_payload(
    *,
    skill: str | None,
    profile: str,
    model: str | None,
    model_tier: str | None,
    task_name: str | None,
    runtime_target: str | None,
    user_request: str | None,
    context_confirmed: bool,
    command: list[str],
) -> dict:
    harness_policy = load_harness_policy()
    contract = resolve_skill_contract(skill)
    tier, enforcement = resolve_enforcement_level(model, model_tier, profile, contract)
    checks: list[dict] = []

    skill_policies = load_skill_policies()
    allowed_profiles = (skill_policies.get(skill or "", {}) or {}).get("allowed_profiles", [])
    if skill and allowed_profiles and profile not in allowed_profiles:
        append_check(checks, "skill_profile", False, f"{skill} does not allow profile {profile}")
    else:
        append_check(checks, "skill_profile", True, "profile allowed for skill")

    required_task_profiles = set((harness_policy.get("validation") or {}).get("task_name_required_profiles", []))
    if profile in required_task_profiles and not task_name:
        append_check(checks, "task_name", False, f"{profile} requires --task-name")
    else:
        append_check(checks, "task_name", True, "task name present")

    required_target_profiles = set((harness_policy.get("validation") or {}).get("runtime_target_required_profiles", []))
    if profile in required_target_profiles and not runtime_target:
        append_check(checks, "runtime_target", False, f"{profile} requires --runtime-target")
    else:
        append_check(checks, "runtime_target", True, "runtime target satisfied")

    required_context_level = str((harness_policy.get("validation") or {}).get("context_required_at_or_above", "balanced"))
    context_required = bool((contract.get("context") or {}).get("required")) and enforcement_rank(harness_policy, enforcement) >= enforcement_rank(harness_policy, required_context_level)
    if context_required and not context_confirmed:
        append_check(checks, "context_hydration", False, "required context hydration has not been confirmed")
    else:
        append_check(checks, "context_hydration", True, "context hydration requirement satisfied")

    request_blob = f"{task_name or ''} {user_request or ''}".casefold()
    approval_keywords = [keyword.casefold() for keyword in contract.get("approval_keywords", [])]
    approval_needed = any(keyword in request_blob for keyword in approval_keywords)
    if approval_needed and profile != "remote_handoff":
        append_check(checks, "approval_boundary", False, "request looks account-bound or irreversible; route through remote_handoff or explicit approval flow")
    else:
        append_check(checks, "approval_boundary", True, "no extra approval boundary required")

    preferred_runner = contract.get("preferred_runner")
    if contract.get("narrow_runner_required") and enforcement == "strict":
        joined = " ".join(command)
        if preferred_runner and preferred_runner not in joined:
            append_check(checks, "preferred_runner", False, f"strict mode expects {preferred_runner}")
        else:
            append_check(checks, "preferred_runner", True, "preferred runner satisfied")
    else:
        append_check(checks, "preferred_runner", True, "no strict runner requirement")

    return {
        "task_id": str(uuid.uuid4()),
        "skill": skill,
        "profile": profile,
        "model": model,
        "model_tier": tier,
        "enforcement_level": enforcement,
        "contract": contract,
        "context_required": context_required,
        "hydration_commands": build_hydration_commands(contract),
        "checks": checks,
        "ok": all(item["ok"] for item in checks),
    }


def latest_task_entry(task_id: str) -> dict | None:
    entries = load_jsonl(TASK_LEDGER)
    for entry in reversed(entries):
        if entry.get("task_id") == task_id:
            return entry
    return None


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def postflight_payload(task_id: str, contract: dict, enforcement_level: str) -> dict:
    harness_policy = load_harness_policy()
    checks: list[dict] = []
    entry = latest_task_entry(task_id)
    if entry is None:
        append_check(checks, "task_ledger_entry", False, "task id not found in ledger")
        return {"task_id": task_id, "checks": checks, "ok": False, "entry": None}

    append_check(checks, "task_ledger_entry", True, "task entry found")
    status = entry.get("status")
    append_check(checks, "task_status", status in {"completed", "handoff_required"}, f"status={status}")

    required_level = str((harness_policy.get("validation") or {}).get("finalization_must_not_be_pending_at_or_above", "balanced"))
    finalization_status = (entry.get("memory_capture") or {}).get("finalization_status", "unknown")
    if contract.get("require_finalization_written") and enforcement_rank(harness_policy, enforcement_level) >= enforcement_rank(harness_policy, required_level):
        ok = finalization_status not in {"capture_planned", "capture_partial", "unknown"}
        append_check(checks, "finalization", ok, f"finalization_status={finalization_status}")
    else:
        append_check(checks, "finalization", True, f"finalization_status={finalization_status}")

    return {
        "task_id": task_id,
        "entry": entry,
        "checks": checks,
        "ok": all(item["ok"] for item in checks),
    }
