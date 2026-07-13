from __future__ import annotations

import hashlib
import json
import shutil
from graphlib import CycleError, TopologicalSorter
from pathlib import Path

try:
    from task_state_bundle import write_task_state_bundle
except ModuleNotFoundError:
    from scripts.task_state_bundle import write_task_state_bundle


TRUSTED_SERVICE_PROVENANCE = {"evidence_gatherer_command", "actual_remote_readback", "actual_provider_readback"}


def _refs(value: object) -> list[str]:
    if isinstance(value, str):
        return [value] if value else []
    return [str(item) for item in value] if isinstance(value, list) else []


def _trusted_service_refs(task: dict) -> list[str]:
    rows = (task.get("evidence_gathering") or {}).get("service_results") or []
    refs = []
    for row in rows:
        if not isinstance(row, dict) or row.get("ok") is not True:
            continue
        if row.get("kind") != "service_readback" or row.get("provenance") not in TRUSTED_SERVICE_PROVENANCE:
            continue
        source = str(row.get("source") or row.get("reference") or "").strip()
        if source:
            refs.append(f"service_readback:{source}")
    return list(dict.fromkeys(refs))


def _file_ref(path_value: str, workspace: Path) -> str | None:
    candidate = workspace / path_value
    try:
        resolved = candidate.resolve(strict=False)
        relative = resolved.relative_to(workspace.resolve())
    except (OSError, ValueError):
        return None
    if not resolved.is_file():
        return None
    digest = hashlib.sha256(resolved.read_bytes()).hexdigest()
    return f"filesystem_stat:{relative}#sha256={digest}"


def prepare_runtime_contract(task: dict, touched_paths: list[str], *, workspace: Path) -> None:
    profile = str(task.get("profile") or "")
    evidence = [
        ref for ref in _refs(task.get("evidence_refs")) + _refs(task.get("completion_evidence"))
        if not ref.startswith("service_readback:")
    ]
    evidence.extend(_trusted_service_refs(task))
    if task.get("exit_code") is not None:
        evidence.append(f"process_exit:{task['exit_code']}")
    file_refs = {path: ref for path in touched_paths if (ref := _file_ref(path, workspace))}
    evidence.extend(file_refs.values())
    claims = list(task.get("completion_claims") or [])
    claim_ids = {
        str(item.get("claim_id")) for item in claims
        if isinstance(item, dict) and item.get("claim_id")
    }
    if task.get("exit_code") == 0 and "command_completed" not in claim_ids:
        claims.append({
            "claim_id": "command_completed",
            "claim": "command_completed",
            "evidence_type": "process_exit",
            "evidence_refs": ["process_exit:0"],
        })
        claim_ids.add("command_completed")
    if task.get("status") == "completed" and task.get("exit_code") == 0 and profile == "service_ops" and "service_change_verified" not in claim_ids:
        service_refs = [ref for ref in evidence if ref.startswith("service_readback:") and ref != "service_readback:"]
        claims.append({
            "claim_id": "service_change_verified",
            "claim": "service_change_verified",
            "evidence_type": "service_readback",
            "evidence_refs": service_refs or ["service_readback:required"],
            "depends_on": ["command_completed"],
        })
        claim_ids.add("service_change_verified")
    for path in touched_paths:
        claim_id = f"file_changed:{path}"
        if claim_id in claim_ids:
            continue
        claims.append({
            "claim_id": claim_id,
            "claim": claim_id,
            "evidence_type": "filesystem_stat",
            "evidence_refs": [file_refs.get(path) or f"filesystem_stat:{path}"],
        })
    task["evidence_refs"] = list(dict.fromkeys(evidence))
    task["completion_claims"] = claims
    task["active_workspace"] = {
        "in_scope_targets": list(task.get("checkpoint_paths") or touched_paths),
        "planned_mutations": touched_paths,
        "pending_claims": claims,
        "evidence_refs": task["evidence_refs"],
    }


def evaluate_finalization(task: dict) -> dict:
    evidence = {
        ref for ref in _refs(task.get("evidence_refs")) + _refs(task.get("completion_evidence"))
        if not ref.startswith("service_readback:")
    }
    evidence.update(_trusted_service_refs(task))
    rows = []
    id_counts: dict[str, int] = {}
    for claim in task.get("completion_claims") or []:
        if not isinstance(claim, dict):
            rows.append({"claim_id": "unnamed", "claim": str(claim), "ok": False, "reason": "claim_not_structured", "depends_on": []})
            continue
        claim_id = str(claim.get("claim_id") or claim.get("criterion_id") or "").strip()
        required = str(claim.get("evidence_type") or "").strip()
        dependencies = claim.get("depends_on") or []
        dependencies_valid = isinstance(dependencies, list) and all(isinstance(item, str) and item.strip() for item in dependencies)
        refs = set(_refs(claim.get("evidence_refs")))
        matched = sorted(ref for ref in refs & evidence if required and ref.startswith(required + ":"))
        row = {
            "claim_id": claim_id,
            "claim": claim.get("claim") or claim_id or "unnamed",
            "ok": bool(claim_id and required and matched and dependencies_valid),
            "reason": "evidence_present" if matched else "required_evidence_missing",
            "evidence_refs": matched,
            "depends_on": [item.strip() for item in dependencies] if dependencies_valid else [],
        }
        if not dependencies_valid:
            row["ok"] = False
            row["reason"] = "invalid_claim_dependencies"
        rows.append(row)
        if claim_id:
            id_counts[claim_id] = id_counts.get(claim_id, 0) + 1
    by_id = {row["claim_id"]: row for row in rows if row["claim_id"] and id_counts[row["claim_id"]] == 1}
    dependency_ok: dict[str, bool] = {}
    try:
        for claim_id in TopologicalSorter({key: row["depends_on"] for key, row in by_id.items()}).static_order():
            row = by_id.get(claim_id)
            dependency_ok[claim_id] = bool(row and row["ok"] and all(dependency_ok.get(dep, False) for dep in row["depends_on"]))
    except CycleError:
        for row in rows:
            if row["depends_on"]:
                row["ok"] = False
                row["reason"] = "claim_dependency_cycle"
    for row in rows:
        if row["claim_id"] and id_counts[row["claim_id"]] > 1:
            row["ok"] = False
            row["reason"] = "duplicate_claim_id"
        missing = [dep for dep in row["depends_on"] if not dependency_ok.get(dep, False)]
        if missing:
            row["ok"] = False
            row["reason"] = "prerequisite_claim_missing"
            row["missing_dependencies"] = missing
    scope = task.get("scope_gate")
    scope_violation = isinstance(scope, dict) and scope.get("ok") is False
    remote_pending = task.get("profile") == "remote_handoff" and task.get("status") != "completed"
    ok = bool(rows) and all(row["ok"] for row in rows) and not scope_violation and not remote_pending
    return {
        "ok": ok,
        "arbiter": "pass" if ok else "hold",
        "claims": rows,
        "refuter": {
            "scope_violation": scope_violation,
            "remote_execution_pending": remote_pending,
            "missing_claims": [row["claim"] for row in rows if not row["ok"]],
        },
    }


def freeze_task_bundle(task: dict, touched_paths: list[str], workspace: Path, state_root: Path) -> dict:
    root = state_root / "task-bundles" / str(task["task_id"])
    files = root / "files"
    files.mkdir(parents=True, exist_ok=True)
    artifacts = []
    for relative in touched_paths:
        source = workspace / relative
        target = files / relative
        try:
            source.resolve(strict=False).relative_to(workspace.resolve())
            target.resolve(strict=False).relative_to(files.resolve())
        except (OSError, ValueError):
            artifacts.append({"path": relative, "state": "unsafe_path"})
            continue
        if source.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            artifacts.append({"path": relative, "state": "present", "sha256": hashlib.sha256(target.read_bytes()).hexdigest()})
        else:
            artifacts.append({"path": relative, "state": "missing"})
    manifest = {
        "task_id": task["task_id"],
        "checkpoint_id": task.get("checkpoint_id"),
        "artifacts": artifacts,
        "evidence_refs": task.get("evidence_refs", []),
        "completion_claims": task.get("completion_claims", []),
        "finalization_gate": task.get("finalization_gate"),
        "scope_gate": task.get("scope_gate"),
        "command": task.get("command"),
    }
    manifest_path = root / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    readable = write_task_state_bundle(task, touched_paths=touched_paths, workspace=workspace, state_root=state_root)
    return {**readable, "manifest": str(manifest_path.relative_to(workspace)), "artifact_count": len(artifacts)}
