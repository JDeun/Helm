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
from scripts.route_contract_lib import applicable_downgrade_steps, infer_route_decision, validate_route_decision
from scripts.retrieval_policy_lib import build_retrieval_plan
from scripts.skill_manifest_lib import load_skill_contract_manifests, load_skill_policies as load_manifest_policies


WORKSPACE = get_workspace_layout().root
PROFILE_FILE = WORKSPACE / "references" / "execution_profiles.json"
POLICY_FILE = WORKSPACE / "references" / "skill_profile_policies.json"
HARNESS_POLICY_FILE = WORKSPACE / "references" / "adaptive_harness_policy.json"
TASK_LEDGER = get_workspace_layout().state_root / "task-ledger.jsonl"


def load_json(path: Path, default: object) -> object:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"warning: ignoring malformed JSON file {path}: {exc}", file=sys.stderr)
        return default


def load_profiles() -> dict[str, dict]:
    data = load_json(PROFILE_FILE, {"profiles": {}})
    return data.get("profiles", {}) if isinstance(data, dict) else {}


def load_skill_policies() -> dict[str, dict]:
    return load_manifest_policies(WORKSPACE, POLICY_FILE)


def load_harness_policy() -> dict:
    data = load_json(HARNESS_POLICY_FILE, {})
    policy = data if isinstance(data, dict) else {}
    overlay_root = WORKSPACE / "references" / "adaptive_harness.d"
    if overlay_root.exists():
        for overlay_path in sorted(overlay_root.glob("*.json")):
            overlay = load_json(overlay_path, {})
            if not isinstance(overlay, dict):
                continue
            if "model_aliases" in overlay and isinstance(overlay["model_aliases"], dict):
                merged_aliases = dict(policy.get("model_aliases", {}))
                merged_aliases.update(overlay["model_aliases"])
                policy["model_aliases"] = merged_aliases
            if "tiers" in overlay and isinstance(overlay["tiers"], dict):
                merged_tiers = dict(policy.get("tiers", {}))
                for tier_name, tier_payload in overlay["tiers"].items():
                    current = dict(merged_tiers.get(tier_name, {}))
                    if isinstance(tier_payload, dict):
                        for key, value in tier_payload.items():
                            if key == "profile_overrides" and isinstance(value, dict):
                                merged = dict(current.get("profile_overrides", {}))
                                merged.update(value)
                                current[key] = merged
                            else:
                                current[key] = value
                    merged_tiers[tier_name] = current
                policy["tiers"] = merged_tiers
            if "validation" in overlay and isinstance(overlay["validation"], dict):
                merged_validation = dict(policy.get("validation", {}))
                merged_validation.update(overlay["validation"])
                policy["validation"] = merged_validation
            if "enforcement_order" in overlay and isinstance(overlay["enforcement_order"], list):
                policy["enforcement_order"] = overlay["enforcement_order"]
    return policy


def base_skill_contract(skill: str | None) -> dict:
    policies = load_skill_policies()
    policy = policies.get(skill or "", {})
    return {
        "context": {"required": False},
        "require_finalization_written": True,
        "browser_work": {"required": False, "required_fields": ["reason", "evidence", "api_reusable", "next_action"]},
        "retrieval_policy": {"required": False, "required_fields": ["attempt_stage", "exit_classification", "recovery_artifact"]},
        "file_intake": {"required": False, "required_fields": ["path", "claimed_type", "detected_type", "detector", "route_decision"]},
        "route_decision": {
            "required": False,
            "task_type": "generic",
            "required_fields": ["task_type", "stage", "chosen_tool", "next_action"],
            "allowed_stages": ["classify", "input_check", "route", "execute", "review", "write"],
            "allowed_next_actions": ["ask", "run", "stop", "review"],
            "tool_rules": [],
        },
        "result_contract": {"required": False},
        "failure_downgrade": {"steps": []},
        "allowed_profiles": policy.get("allowed_profiles", []),
        "default_profile": policy.get("default_profile"),
        "runner": {},
    }


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
    contracts = load_skill_contract_manifests(WORKSPACE)
    contract = contracts.get(skill)
    if contract:
        resolved = base_skill_contract(skill)
        resolved.update(contract)
        return resolved
    return base_skill_contract(skill)


def resolve_enforcement_level(model: str | None, model_tier: str | None, profile: str, contract: dict) -> tuple[str, str]:
    policy = load_harness_policy()
    tier = resolve_model_tier(policy, model, model_tier)
    tier_policy = (policy.get("tiers") or {}).get(tier, {})
    enforcement = str(tier_policy.get("default_enforcement", "light"))
    enforcement = max_enforcement(policy, enforcement, str((tier_policy.get("profile_overrides") or {}).get(profile, "")))
    if (contract.get("context") or {}).get("required"):
        enforcement = max_enforcement(policy, enforcement, "balanced")
    runner = contract.get("runner") or {}
    if runner.get("strict_required") and profile in {"service_ops", "risky_edit"}:
        enforcement = max_enforcement(policy, enforcement, "strict")
    return tier, enforcement


def build_hydration_commands(contract: dict) -> list[list[str]]:
    context = contract.get("context") or {}
    if not context.get("required"):
        return []
    include = [str(item) for item in context.get("include", []) if str(item)]
    commands: list[list[str]] = [
        [
            "python3",
            str(WORKSPACE / "scripts" / "ops_memory_query.py"),
            str(context.get("query") or ""),
        ]
    ]
    if include:
        commands[0].extend(["--include", *include])
    commands[0].extend(["--limit", str(context.get("limit", 6))])
    failed_include = context.get("failed_include")
    if failed_include:
        failed_include_values = [str(item) for item in failed_include if str(item)]
        if not failed_include_values:
            return commands
        commands.append(
            [
                "python3",
                str(WORKSPACE / "scripts" / "ops_memory_query.py"),
                "--failed-only",
                "--limit",
                str(context.get("failed_limit", 6)),
            ]
        )
        commands[-1][3:3] = ["--include", *failed_include_values]
    return commands


def append_check(checks: list[dict], name: str, ok: bool, detail: str) -> None:
    checks.append({"name": name, "ok": ok, "detail": detail})


def validate_evidence_payload(
    payload: dict | None,
    required_fields: list[str],
    *,
    label: str,
) -> tuple[bool, str]:
    if payload is None:
        return False, f"{label} missing"

    missing: list[str] = []
    for field in required_fields:
        value = payload.get(field)
        if value is None:
            missing.append(field)
            continue
        if isinstance(value, str) and not value.strip():
            missing.append(field)
            continue
        if isinstance(value, list) and not value:
            missing.append(field)
    if missing:
        return False, f"{label} missing required fields: {', '.join(missing)}"
    return True, f"{label} satisfied"


def evidence_hints(section: dict) -> list[str]:
    raw = section.get("when_any")
    if not isinstance(raw, list):
        return []
    return [str(item).casefold() for item in raw if str(item).strip()]


def evidence_requirement_active(section: dict, *, blob: str) -> bool:
    if section.get("required"):
        return True
    hints = evidence_hints(section)
    return bool(hints) and any(hint in blob for hint in hints)


def parse_evidence_json(raw: str | None, *, label: str) -> dict | None:
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{label} must be valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise SystemExit(f"{label} must decode to a JSON object")
    return payload


def _command_blob_from_entry(entry: dict) -> str:
    harness_meta = ((entry.get("meta") or {}).get("harness") or {})
    parts = [
        entry.get("task_name"),
        entry.get("command_preview"),
        entry.get("runtime_target"),
        entry.get("runtime_note"),
        entry.get("failure_stage"),
        entry.get("failure_reason"),
        harness_meta.get("user_request"),
    ]
    return " ".join(str(part or "") for part in parts).casefold()


def entry_evidence_requirements(entry: dict, contract: dict) -> dict[str, bool]:
    blob = _command_blob_from_entry(entry)
    return {
        "browser_work": evidence_requirement_active(contract.get("browser_work") or {}, blob=blob),
        "retrieval_policy": evidence_requirement_active(contract.get("retrieval_policy") or {}, blob=blob),
        "file_intake": evidence_requirement_active(contract.get("file_intake") or {}, blob=blob),
    }


def infer_browser_evidence(entry: dict) -> dict | None:
    blob = _command_blob_from_entry(entry)
    browser_hints = ("browser", "playwright", "snapshot", "selector", "dom", "click", "safari", "chrome", "firefox", "network")
    if not any(hint in blob for hint in browser_hints):
        return None
    api_reusable = any(hint in blob for hint in ("api", "endpoint", "json", "network", "reuse"))
    evidence = entry.get("runtime_note") or entry.get("failure_reason") or entry.get("command_preview") or "task-ledger inferred browser work"
    next_action = (
        "Promote the discovered endpoint or network path into a cheaper non-browser retrieval route."
        if api_reusable
        else "Capture a more explicit network trace or selector note before repeating this browser-dependent workflow."
    )
    return {
        "reason": "Task metadata indicates live browser inspection or UI-dependent interaction was part of execution.",
        "evidence": str(evidence),
        "api_reusable": api_reusable,
        "next_action": next_action,
        "inferred": True,
        "inference_source": "task_ledger",
    }


def infer_retrieval_evidence(entry: dict) -> dict | None:
    blob = _command_blob_from_entry(entry)
    retrieval_hints = ("fetch", "retriev", "reader", "blocked", "403", "429", "waf", "auth", "browser", "network", "endpoint", "json", "spa", "challenge")
    if not any(hint in blob for hint in retrieval_hints):
        return None

    plan = build_retrieval_plan(
        current_stage="browser_snapshot" if any(hint in blob for hint in ("browser", "playwright", "snapshot", "selector", "dom")) else "cheap_fetch",
        error_text=entry.get("failure_reason"),
        body_hint=blob,
        browser_used=any(hint in blob for hint in ("browser", "playwright", "snapshot", "selector", "dom")),
        network_discovery=any(hint in blob for hint in ("network", "endpoint", "json", "api", "reuse")),
        auth_required=any(hint in blob for hint in ("auth", "login", "paywall")),
        unsafe=any(hint in blob for hint in ("unsafe",)),
        human_approval_needed=any(hint in blob for hint in ("approval",)),
    )
    return {
        "attempt_stage": plan["attempt_stage"],
        "exit_classification": plan["exit_classification"],
        "recovery_artifact": "inferred-from-task-ledger",
        "next_attempt_stage": plan["next_attempt_stage"],
        "reason": plan["reason"],
        "inferred": True,
        "inference_source": "task_ledger",
    }


def infer_missing_evidence(entry: dict, contract: dict) -> tuple[dict | None, dict | None]:
    harness_meta = ((entry.get("meta") or {}).get("harness") or {})
    browser_evidence = harness_meta.get("browser_evidence")
    retrieval_evidence = harness_meta.get("retrieval_evidence")
    blob = _command_blob_from_entry(entry)

    inferred_browser = None
    inferred_retrieval = None
    if evidence_requirement_active(contract.get("browser_work") or {}, blob=blob) and not isinstance(browser_evidence, dict):
        inferred_browser = infer_browser_evidence(entry)
    if evidence_requirement_active(contract.get("retrieval_policy") or {}, blob=blob) and not isinstance(retrieval_evidence, dict):
        inferred_retrieval = infer_retrieval_evidence(entry)
    return inferred_browser, inferred_retrieval


def infer_result_consistency(entry: dict) -> dict:
    harness_meta = ((entry.get("meta") or {}).get("harness") or {})
    status = str(entry.get("status") or "unknown")
    evidence_count = 0
    if harness_meta.get("browser_evidence"):
        evidence_count += 1
    if harness_meta.get("retrieval_evidence"):
        evidence_count += 1
    if entry.get("checkpoint_paths"):
        evidence_count += 1
    warnings: list[str] = []
    grounded = evidence_count > 0 or (status in {"completed", "handoff_required"} and entry.get("profile") == "inspect_local")
    if status == "completed" and evidence_count == 0 and entry.get("profile") in {"service_ops", "risky_edit", "remote_handoff"}:
        warnings.append("high-impact completion has no explicit evidence buckets")
    return {
        "outcome": status,
        "grounded": grounded,
        "evidence_summary": f"{evidence_count} evidence bucket(s)",
        "warnings": warnings,
    }


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
    browser_evidence: dict | None,
    retrieval_evidence: dict | None,
    file_intake_evidence: dict | None,
    route_decision: dict | None,
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
    command_blob = " ".join(command).casefold()
    evidence_blob = f"{request_blob} {runtime_target or ''} {command_blob}".casefold()
    approval_keywords = [keyword.casefold() for keyword in contract.get("approval_keywords", [])]
    approval_needed = any(keyword in request_blob for keyword in approval_keywords)
    if approval_needed and profile != "remote_handoff":
        append_check(checks, "approval_boundary", False, "request looks account-bound or irreversible; route through remote_handoff or explicit approval flow")
    else:
        append_check(checks, "approval_boundary", True, "no extra approval boundary required")

    runner = contract.get("runner") or {}
    preferred_runner = runner.get("entrypoint")
    if runner.get("strict_required") and enforcement == "strict":
        joined = " ".join(command)
        if preferred_runner and preferred_runner not in joined:
            append_check(checks, "preferred_runner", False, f"strict mode expects {preferred_runner}")
        else:
            append_check(checks, "preferred_runner", True, "preferred runner satisfied")
    else:
        append_check(checks, "preferred_runner", True, "no strict runner requirement")

    browser_work = contract.get("browser_work") or {}
    browser_fields = [str(item) for item in browser_work.get("required_fields", []) if str(item)]
    if browser_evidence is not None:
        ok, detail = validate_evidence_payload(browser_evidence, browser_fields, label="browser_evidence")
        append_check(checks, "browser_evidence_shape", ok, detail)
    elif evidence_requirement_active(browser_work, blob=evidence_blob):
        append_check(
            checks,
            "browser_evidence_contract",
            True,
            "browser evidence is required for this request shape; attach it before treating the task as complete",
        )

    retrieval_policy = contract.get("retrieval_policy") or {}
    retrieval_fields = [str(item) for item in retrieval_policy.get("required_fields", []) if str(item)]
    if retrieval_evidence is not None:
        ok, detail = validate_evidence_payload(retrieval_evidence, retrieval_fields, label="retrieval_evidence")
        append_check(checks, "retrieval_evidence_shape", ok, detail)
    elif evidence_requirement_active(retrieval_policy, blob=evidence_blob):
        append_check(
            checks,
            "retrieval_evidence_contract",
            True,
            "retrieval evidence is required for this request shape; attach it before treating the task as complete",
        )

    file_intake = contract.get("file_intake") or {}
    file_intake_fields = [str(item) for item in file_intake.get("required_fields", []) if str(item)]
    if file_intake_evidence is not None:
        ok, detail = validate_evidence_payload(file_intake_evidence, file_intake_fields, label="file_intake_evidence")
        append_check(checks, "file_intake_evidence_shape", ok, detail)
    elif evidence_requirement_active(file_intake, blob=evidence_blob):
        append_check(
            checks,
            "file_intake_evidence_contract",
            True,
            "file intake evidence is required for this request shape; attach it before treating the task as complete",
        )

    resolved_route_decision = route_decision or infer_route_decision(
        command=command,
        request=user_request or task_name,
        contract=contract,
    )
    route_ok, route_detail = validate_route_decision(
        resolved_route_decision,
        contract,
        command=command,
    )
    append_check(checks, "route_decision", route_ok, route_detail)

    downgrade_hints: list[dict] = []
    for check in checks:
        if not check["ok"]:
            downgrade_hints.extend(applicable_downgrade_steps(contract, check["name"]))

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
        "browser_evidence": browser_evidence,
        "retrieval_evidence": retrieval_evidence,
        "file_intake_evidence": file_intake_evidence,
        "route_decision": resolved_route_decision,
        "downgrade_hints": downgrade_hints,
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
    rows: list[dict] = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            print(f"warning: ignoring malformed JSONL line {lineno} in {path}: {exc}", file=sys.stderr)
            continue
        if not isinstance(payload, dict):
            print(f"warning: ignoring non-object JSONL line {lineno} in {path}", file=sys.stderr)
            continue
        rows.append(payload)
    return rows


def append_jsonl(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def postflight_payload(task_id: str, contract: dict, enforcement_level: str) -> dict:
    harness_policy = load_harness_policy()
    checks: list[dict] = []
    entry = latest_task_entry(task_id)
    return postflight_payload_for_entry(entry, task_id=task_id, contract=contract, enforcement_level=enforcement_level, harness_policy=harness_policy)


def postflight_payload_for_entry(
    entry: dict | None,
    *,
    task_id: str,
    contract: dict,
    enforcement_level: str,
    harness_policy: dict | None = None,
) -> dict:
    policy = harness_policy or load_harness_policy()
    checks: list[dict] = []
    if entry is None:
        append_check(checks, "task_ledger_entry", False, "task id not found in ledger")
        return {"task_id": task_id, "checks": checks, "ok": False, "entry": None}

    append_check(checks, "task_ledger_entry", True, "task entry found")
    status = entry.get("status")
    append_check(checks, "task_status", status in {"completed", "handoff_required"}, f"status={status}")

    required_level = str((policy.get("validation") or {}).get("finalization_must_not_be_pending_at_or_above", "balanced"))
    finalization_status = (entry.get("memory_capture") or {}).get("finalization_status", "unknown")
    if contract.get("require_finalization_written") and enforcement_rank(policy, enforcement_level) >= enforcement_rank(policy, required_level):
        ok = finalization_status not in {"capture_planned", "capture_partial", "unknown"}
        append_check(checks, "finalization", ok, f"finalization_status={finalization_status}")
    else:
        append_check(checks, "finalization", True, f"finalization_status={finalization_status}")

    harness_meta = ((entry.get("meta") or {}).get("harness") or {})
    blob = _command_blob_from_entry(entry)
    browser_work = contract.get("browser_work") or {}
    browser_fields = [str(item) for item in browser_work.get("required_fields", []) if str(item)]
    if evidence_requirement_active(browser_work, blob=blob):
        ok, detail = validate_evidence_payload(harness_meta.get("browser_evidence"), browser_fields, label="browser_evidence")
        append_check(checks, "browser_evidence", ok, detail)
    else:
        append_check(checks, "browser_evidence", True, "browser evidence not required")

    retrieval_policy = contract.get("retrieval_policy") or {}
    retrieval_fields = [str(item) for item in retrieval_policy.get("required_fields", []) if str(item)]
    if evidence_requirement_active(retrieval_policy, blob=blob):
        ok, detail = validate_evidence_payload(
            harness_meta.get("retrieval_evidence"),
            retrieval_fields,
            label="retrieval_evidence",
        )
        append_check(checks, "retrieval_evidence", ok, detail)
    else:
        append_check(checks, "retrieval_evidence", True, "retrieval evidence not required")

    file_intake = contract.get("file_intake") or {}
    file_intake_fields = [str(item) for item in file_intake.get("required_fields", []) if str(item)]
    if evidence_requirement_active(file_intake, blob=blob):
        ok, detail = validate_evidence_payload(
            harness_meta.get("file_intake_evidence"),
            file_intake_fields,
            label="file_intake_evidence",
        )
        append_check(checks, "file_intake_evidence", ok, detail)
    else:
        append_check(checks, "file_intake_evidence", True, "file intake evidence not required")

    route_ok, route_detail = validate_route_decision(
        harness_meta.get("route_decision"),
        contract,
        command=entry.get("command") or [],
    )
    append_check(checks, "route_decision", route_ok, route_detail)

    result_contract = contract.get("result_contract") or {}
    if result_contract.get("required"):
        result_consistency = infer_result_consistency(entry)
        ok = not result_consistency["warnings"] and (
            result_consistency["grounded"] or entry.get("status") in {"failed", "handoff_required"}
        )
        append_check(
            checks,
            "result_consistency",
            ok,
            f"outcome={result_consistency['outcome']} grounded={result_consistency['grounded']} warnings={len(result_consistency['warnings'])}",
        )
    else:
        append_check(checks, "result_consistency", True, "result consistency contract not required")

    return {
        "task_id": task_id,
        "entry": entry,
        "checks": checks,
        "ok": all(item["ok"] for item in checks),
    }


def postflight_payload_from_task(task_id: str) -> dict:
    entry = latest_task_entry(task_id)
    if entry is None:
        return postflight_payload_for_entry(None, task_id=task_id, contract={}, enforcement_level="light")
    skill = entry.get("skill")
    contract = resolve_skill_contract(skill)
    harness_meta = ((entry.get("meta") or {}).get("harness") or {})
    enforcement_level = str(harness_meta.get("enforcement_level") or "light")
    return postflight_payload_for_entry(entry, task_id=task_id, contract=contract, enforcement_level=enforcement_level)


def record_task_evidence(
    task_id: str,
    *,
    browser_evidence: dict | None,
    retrieval_evidence: dict | None,
    file_intake_evidence: dict | None,
) -> dict:
    entry = latest_task_entry(task_id)
    if entry is None:
        raise SystemExit(f"task id not found in ledger: {task_id}")
    meta = dict(entry.get("meta") or {})
    harness = dict(meta.get("harness") or {})
    if browser_evidence is not None:
        harness["browser_evidence"] = browser_evidence
    if retrieval_evidence is not None:
        harness["retrieval_evidence"] = retrieval_evidence
    if file_intake_evidence is not None:
        harness["file_intake_evidence"] = file_intake_evidence
    meta["harness"] = harness
    entry["meta"] = meta
    append_jsonl(TASK_LEDGER, entry)
    return entry


def ensure_task_evidence(task_id: str, contract: dict) -> dict | None:
    entry = latest_task_entry(task_id)
    if entry is None:
        return None
    inferred_browser, inferred_retrieval = infer_missing_evidence(entry, contract)
    if inferred_browser is None and inferred_retrieval is None:
        return entry
    return record_task_evidence(
        task_id,
        browser_evidence=inferred_browser,
        retrieval_evidence=inferred_retrieval,
        file_intake_evidence=None,
    )


def backfill_task_evidence(
    *,
    task_ids: list[str] | None = None,
    skill: str | None = None,
    limit: int | None = None,
    latest_only: bool = True,
) -> dict:
    entries = load_jsonl(TASK_LEDGER)
    if latest_only:
        latest_map: dict[str, dict] = {}
        for entry in entries:
            task_id = entry.get("task_id")
            if task_id:
                latest_map[task_id] = entry
        candidates = list(latest_map.values())
    else:
        candidates = [entry for entry in entries if entry.get("task_id")]

    if skill:
        candidates = [entry for entry in candidates if entry.get("skill") == skill]
    if task_ids:
        requested = set(task_ids)
        candidates = [entry for entry in candidates if entry.get("task_id") in requested]
    if limit is not None:
        candidates = candidates[-limit:]

    inspected = 0
    updated = 0
    browser_backfilled = 0
    retrieval_backfilled = 0
    skipped_without_contract = 0
    skipped_without_active_requirement = 0
    skipped_without_inference = 0
    touched_task_ids: list[str] = []

    for entry in candidates:
        task_id = entry.get("task_id")
        if not task_id:
            continue
        inspected += 1
        contract = resolve_skill_contract(entry.get("skill"))
        if not contract:
            skipped_without_contract += 1
            continue
        requirements = entry_evidence_requirements(entry, contract)
        if not any(requirements.values()):
            skipped_without_active_requirement += 1
            continue
        inferred_browser, inferred_retrieval = infer_missing_evidence(entry, contract)
        if inferred_browser is None and inferred_retrieval is None:
            skipped_without_inference += 1
            continue
        record_task_evidence(
            task_id,
            browser_evidence=inferred_browser,
            retrieval_evidence=inferred_retrieval,
            file_intake_evidence=None,
        )
        updated += 1
        if inferred_browser is not None:
            browser_backfilled += 1
        if inferred_retrieval is not None:
            retrieval_backfilled += 1
        touched_task_ids.append(task_id)

    return {
        "inspected": inspected,
        "updated": updated,
        "browser_backfilled": browser_backfilled,
        "retrieval_backfilled": retrieval_backfilled,
        "skipped_without_contract": skipped_without_contract,
        "skipped_without_active_requirement": skipped_without_active_requirement,
        "skipped_without_inference": skipped_without_inference,
        "task_ids": touched_task_ids,
    }
