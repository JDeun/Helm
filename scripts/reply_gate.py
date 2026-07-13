#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from graphlib import CycleError, TopologicalSorter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from commands import read_jsonl
from helm_workspace import get_workspace_layout

# Module-top import keeps the "advisory never raises" invariant: if
# scripts.advisory_log itself is missing or malformed, we fall back to a
# noop counter rather than letting ImportError escape from an except block
# (R6 Minor M1).
try:
    from scripts.advisory_log import record_advisory_failure as _record_advisory_failure
except Exception:  # noqa: BLE001 - intentional last-resort fallback
    def _record_advisory_failure(channel: str, exc: BaseException) -> None:
        return None


def _get_task_ledger() -> Path:
    return get_workspace_layout().state_root / "task-ledger.jsonl"


def load_entries(path: Path | None = None) -> list[dict]:
    if path is None:
        path = _get_task_ledger()
    if not path.exists():
        return []
    return read_jsonl(path)


def latest_entries(entries: list[dict]) -> list[dict]:
    by_task: dict[str, dict] = {}
    for entry in entries:
        task_id = entry.get("task_id")
        if task_id:
            by_task[task_id] = entry
    return list(by_task.values())


def select_entry(task_id: str | None) -> dict | None:
    entries = latest_entries(load_entries())
    if not entries:
        return None
    if task_id:
        return next((entry for entry in entries if entry.get("task_id") == task_id), None)
    entries.sort(key=lambda item: item.get("finished_at") or item.get("started_at") or "")
    return entries[-1]


def evaluate_claims(entry: dict) -> dict:
    def refs(value: object) -> list[str]:
        if isinstance(value, str):
            return [value] if value else []
        return [str(item) for item in value] if isinstance(value, list) else []

    claims = entry.get("completion_claims") or []
    active = entry.get("active_workspace") or ((entry.get("meta") or {}).get("active_workspace") or {})
    canonical_evidence = refs(entry.get("evidence_refs")) or refs(active.get("evidence_refs"))
    evidence_refs = {
        str(ref)
        for ref in canonical_evidence + refs(entry.get("completion_evidence"))
    }
    planned_mutations = active.get("planned_mutations") or []
    results: list[dict] = []
    for claim in claims:
        if not isinstance(claim, dict):
            results.append({"claim": str(claim), "ok": False, "reason": "claim_not_structured"})
            continue
        has_explicit_id = "claim_id" in claim or "criterion_id" in claim
        raw_claim_id = claim.get("claim_id") if "claim_id" in claim else claim.get("criterion_id")
        stable_id = raw_claim_id.strip() if isinstance(raw_claim_id, str) else ""
        claim_id = stable_id or str(claim.get("claim") or claim.get("text") or "unnamed")
        raw_required = claim.get("evidence_type")
        required = raw_required.strip() if isinstance(raw_required, str) else ""
        required_valid = bool(required and ":" not in required)
        claim_refs = set(refs(claim.get("evidence_refs")))
        candidates = claim_refs & evidence_refs if stable_id or claim_refs else evidence_refs
        matched = sorted(
            ref
            for ref in candidates
            if required_valid
            and ref.startswith(f"{required}:")
            and ref[len(required) + 1 :].strip()
            and not ref[len(required) + 1 :].lstrip().startswith(":")
        )
        raw_dependencies = claim.get("depends_on")
        dependencies_valid = raw_dependencies is None or (
            isinstance(raw_dependencies, list)
            and all(isinstance(item, str) and item.strip() for item in raw_dependencies)
        )
        dependencies = [item.strip() for item in raw_dependencies or []] if dependencies_valid else []
        dependencies_valid = dependencies_valid and (not has_explicit_id or bool(stable_id)) and (not dependencies or bool(stable_id))
        evidence_ok = bool(required_valid and matched)
        results.append(
            {
                "claim_id": claim_id,
                "claim": claim.get("claim") or claim.get("text") or "unnamed",
                "evidence_type": required,
                "evidence_refs": matched,
                "depends_on": dependencies,
                "missing_dependencies": [],
                "ok": evidence_ok,
                "reason": "evidence_present" if evidence_ok else "required_evidence_missing",
                "_stable_id": stable_id,
                "_evidence_ok": evidence_ok,
                "_dependencies_valid": dependencies_valid,
            }
        )

    id_counts: dict[str, int] = {}
    for item in results:
        if item.get("_stable_id"):
            id_counts[item["_stable_id"]] = id_counts.get(item["_stable_id"], 0) + 1
    by_id = {
        item["_stable_id"]: item
        for item in results
        if item.get("_stable_id") and id_counts[item["_stable_id"]] == 1 and item.get("_dependencies_valid")
    }
    dependency_ok: dict[str, bool] = {}
    dependency_cycle = False
    try:
        dependency_order = TopologicalSorter(
            {claim_id: item["depends_on"] for claim_id, item in by_id.items()}
        ).static_order()
        for claim_id in dependency_order:
            item = by_id.get(claim_id)
            dependency_ok[claim_id] = bool(
                item
                and item["_evidence_ok"]
                and all(dependency_ok.get(dep, False) for dep in item["depends_on"])
            )
    except CycleError:
        dependency_cycle = True

    for item in results:
        stable_id = item.get("_stable_id")
        if not item.get("claim_id"):
            continue
        if stable_id and id_counts[stable_id] > 1:
            item["ok"] = False
            item["reason"] = "duplicate_claim_id"
        elif not item.get("_dependencies_valid"):
            item["ok"] = False
            item["reason"] = "invalid_claim_dependencies"
        elif dependency_cycle and item["depends_on"]:
            item["ok"] = False
            item["reason"] = "claim_dependency_cycle"
        else:
            missing = [dep for dep in item["depends_on"] if not dependency_ok.get(dep, False)]
            item["missing_dependencies"] = missing
            item["ok"] = item["_evidence_ok"] and not missing
            if missing:
                item["reason"] = "prerequisite_claim_missing"
    for item in results:
        for key in ("_stable_id", "_evidence_ok", "_dependencies_valid"):
            item.pop(key, None)

    scope_violation = entry.get("profile") == "inspect_local" and bool(planned_mutations)
    return {
        "ok": all(item["ok"] for item in results) and not scope_violation,
        "claims": results,
        "refuter": {
            "scope_violation": scope_violation,
            "planned_mutations": planned_mutations,
            "missing_claims": [item["claim"] for item in results if not item["ok"]],
        },
        "arbiter": "pass" if all(item["ok"] for item in results) and not scope_violation else "hold",
    }


def _advisory_phase_modules(entry: dict) -> dict:
    """Return advisory Phase-A / Phase-F evaluations for ``entry``.

    Advisory only: failures degrade silently. Never modifies the reply
    decision. Wired here so the reply payload carries the action-scope
    lock and the freshness gate verdict alongside the existing harness
    checks (R2 I1).

    The action-scope evaluation runs against the task name + (optional)
    goal so the gate sees the same user-facing string a Telegram intake
    would. The freshness gate runs against the canonical state file
    when present.
    """
    advisory: dict = {}
    try:
        from scripts.action_scope import evaluate as _scope_evaluate
        message_parts: list[str] = []
        task_name = entry.get("task_name")
        if isinstance(task_name, str) and task_name:
            message_parts.append(task_name)
        meta_goal = ((entry.get("meta") or {}).get("task_goal"))
        if isinstance(meta_goal, str) and meta_goal:
            message_parts.append(meta_goal)
        if message_parts:
            payload = _scope_evaluate("\n".join(message_parts)).as_dict()
            advisory["action_scope"] = {
                "locked_scope": payload.get("locked_scope"),
                "matched_verb": payload.get("matched_verb"),
                "allowed": payload.get("allowed"),
                "needs_live_source": payload.get("needs_live_source"),
                "advisory_only": True,
            }
    except (ImportError, AttributeError) as exc:
        # Wiring failure (action_scope missing or refactored). Record
        # so operators see a counter spike instead of a silent absence
        # in the reply payload.
        _record_advisory_failure(
            "reply_gate.action_scope.wiring", exc
        )
    except Exception as exc:
        _record_advisory_failure("reply_gate.action_scope", exc)
    try:
        from scripts.freshness_lib import (
            assess_record,
            list_records,
            load_state,
            utc_now,
        )

        state = load_state(None)
        records = list_records(state)
        now = utc_now()
        rollup: dict = {"connectors": []}
        any_stale = False
        for record in records:
            assessment = assess_record(record, now=now)
            rollup["connectors"].append(
                {
                    "connector_id": record.connector_id,
                    "fresh": assessment.fresh,
                    "stale_reason": assessment.stale_reason,
                }
            )
            if not assessment.fresh:
                any_stale = True
        rollup["any_stale"] = any_stale
        rollup["advisory_only"] = True
        advisory["freshness"] = rollup
    except (ImportError, AttributeError) as exc:
        _record_advisory_failure(
            "reply_gate.freshness.wiring", exc
        )
    except Exception as exc:
        _record_advisory_failure("reply_gate.freshness", exc)
    return advisory


def evaluate(entry: dict | None) -> dict:
    if entry is None:
        return {"ok": False, "reason": "task_not_found", "task": None}
    harness = ((entry.get("meta") or {}).get("harness") or {})
    enforcement = harness.get("enforcement_level", "light")
    finalization = (entry.get("memory_capture") or {}).get("finalization_status", "unknown")
    status = entry.get("status")
    claim_gate = evaluate_claims(entry)
    recorded_gate = entry.get("finalization_gate")
    recorded_gate_ok = recorded_gate is None or (
        isinstance(recorded_gate, dict) and recorded_gate.get("ok") is True
    )
    contract_present = bool(harness.get("skill_contract_present"))
    context_required = bool(harness.get("context_required"))
    context_satisfied = bool(harness.get("context_satisfied"))
    checks = [
        {"name": "task_status", "ok": status in {"completed", "handoff_required"}, "detail": f"status={status}"},
        {
            "name": "claim_evidence",
            "ok": claim_gate["ok"] and recorded_gate_ok,
            "detail": f"arbiter={claim_gate['arbiter']}, claims={len(claim_gate['claims'])}",
        },
    ]
    if enforcement in {"balanced", "strict"}:
        checks.append(
            {
                "name": "task_name",
                "ok": bool(entry.get("task_name")),
                "detail": f"task_name={'present' if entry.get('task_name') else 'missing'}",
            }
        )
        checks.append(
            {
                "name": "skill_contract",
                "ok": contract_present,
                "detail": f"skill_contract_present={contract_present}",
            }
        )
    else:
        checks.append({"name": "task_name", "ok": True, "detail": "not required"})
        checks.append({"name": "skill_contract", "ok": True, "detail": f"skill_contract_present={contract_present}"})
    if enforcement in {"balanced", "strict"} and context_required:
        checks.append(
            {
                "name": "context_hydration",
                "ok": context_satisfied,
                "detail": f"context_required={context_required}, context_satisfied={context_satisfied}",
            }
        )
    else:
        checks.append(
            {
                "name": "context_hydration",
                "ok": True,
                "detail": f"context_required={context_required}, context_satisfied={context_satisfied}",
            }
        )
    if enforcement in {"balanced", "strict"}:
        checks.append(
            {
                "name": "finalization",
                "ok": finalization not in {"capture_planned", "capture_partial", "unknown"},
                "detail": f"finalization_status={finalization}",
            }
        )
    else:
        checks.append({"name": "finalization", "ok": True, "detail": f"finalization_status={finalization}"})
    payload: dict = {
        "ok": all(check["ok"] for check in checks),
        "reason": "reply_allowed" if all(check["ok"] for check in checks) else "reply_blocked",
        "task": {
            "task_id": entry.get("task_id"),
            "task_name": entry.get("task_name"),
            "skill": entry.get("skill"),
            "profile": entry.get("profile"),
            "status": status,
            "enforcement_level": enforcement,
            "finalization_status": finalization,
            "skill_contract_present": contract_present,
        },
        "checks": checks,
        "claim_gate": claim_gate,
    }
    # Advisory Phase-A / Phase-F wiring (R2 I1). Best-effort, never blocks.
    advisory = _advisory_phase_modules(entry)
    if advisory:
        payload["advisory"] = advisory
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Decide whether a task is safe to present as complete to the user.")
    parser.add_argument("--task-id")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    payload = evaluate(select_entry(args.task_id))
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print(json.dumps(payload, ensure_ascii=False))
    return 0 if payload["ok"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
