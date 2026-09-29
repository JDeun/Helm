from __future__ import annotations

from graphlib import CycleError, TopologicalSorter
from pathlib import Path
import hashlib, json, shutil

try:
    # helm.scripts first: a top-level `task_state_bundle` on sys.path would otherwise
    # win this branch and silently replace it -- a decoy file was shown to
    # disable secret redaction entirely.
    from helm.scripts.task_state_bundle import write_task_state_bundle
except ImportError:  # running from a flat checkout
    from task_state_bundle import write_task_state_bundle


MUTATING_PROFILES = {"workspace_edit", "risky_edit", "service_ops"}
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


def _filesystem_evidence(path_value: str, workspace: Path) -> str | None:
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


def prepare_runtime_contract(task: dict, touched_paths: list[str], *, workspace: Path | None = None) -> None:
    workspace = (workspace or Path.cwd()).resolve()
    profile = str(task.get("profile") or "")
    harness = ((task.get("meta") or {}).get("harness") or {})
    loaded_context = list(task.get("observed_read_paths") or [])
    planned = list(touched_paths)
    if profile in MUTATING_PROFILES and task.get("checkpoint_paths"):
        planned = list(task["checkpoint_paths"])
    trace = list(task.get("retrieval_trace") or [])
    known = {(row.get("surface"), row.get("path")) for row in trace if isinstance(row, dict)}
    for path in loaded_context:
        surface = "skill_contract" if path.startswith("skills/") else "task_evidence"
        row = {"surface": surface, "level": "L1", "path": path, "evidence": "observed_read"}
        if (surface, path) not in known:
            trace.append(row)
    evidence = [
        ref for ref in _refs(task.get("evidence_refs")) + _refs(task.get("completion_evidence"))
        if not ref.startswith("service_readback:")
    ]
    evidence.extend(_trusted_service_refs(task))
    if task.get("exit_code") is not None:
        evidence.append(f"process_exit:{task['exit_code']}")
    argv = [str(item) for item in task.get("command") or []]
    runner = Path(argv[0]).name.casefold() if argv else ""
    # ponytail: recognize direct Python test runners only; extend when a real wrapper needs evidence.
    test_passed = task.get("exit_code") == 0 and (
        runner in {"pytest", "py.test"}
        or runner.startswith("python")
        and len(argv) >= 3
        and argv[1] == "-m"
        and argv[2].casefold() in {"pytest", "unittest"}
    )
    if test_passed:
        evidence.append("test_result:passed")
    file_evidence = {
        path: ref
        for path in touched_paths
        if (ref := _filesystem_evidence(path, workspace)) is not None
    }
    evidence.extend(file_evidence.values())
    evidence = list(dict.fromkeys(map(str, evidence)))
    claims = list(task.get("completion_claims") or [])
    claim_ids: set[str] = set()
    for item in claims:
        if isinstance(item, dict):
            value = item.get("claim_id") or item.get("criterion_id")
            if isinstance(value, str) and value.strip():
                claim_ids.add(value.strip())
    if task.get("status") == "completed" and task.get("exit_code") == 0:
        if "command_completed" not in claim_ids:
            claims.append({"claim_id": "command_completed", "claim": "command_completed", "evidence_type": "process_exit", "evidence_refs": ["process_exit:0"]})
            claim_ids.add("command_completed")
        if test_passed and "tests_passed" not in claim_ids:
            claims.append(
                {
                    "claim_id": "tests_passed",
                    "claim": "tests_passed",
                    "evidence_type": "test_result",
                    "evidence_refs": ["test_result:passed"],
                    "depends_on": ["command_completed"],
                }
            )
            claim_ids.add("tests_passed")
        if profile == "service_ops" and "service_change_verified" not in claim_ids:
            service_refs = [ref for ref in evidence if ref.startswith("service_readback:") and ref != "service_readback:"]
            claims.append(
                {
                    "claim_id": "service_change_verified",
                    "claim": "service_change_verified",
                    "evidence_type": "service_readback",
                    "evidence_refs": service_refs or ["service_readback:required"],
                    "depends_on": ["command_completed"],
                }
            )
            claim_ids.add("service_change_verified")
    for path in touched_paths:
        claim_id = f"file_changed:{path}"
        if claim_id not in claim_ids:
            expected_ref = file_evidence.get(path) or f"filesystem_stat:{path}"
            claims.append(
                {
                    "claim_id": claim_id,
                    "claim": claim_id,
                    "evidence_type": "filesystem_stat",
                    "evidence_refs": [expected_ref],
                }
            )
            claim_ids.add(claim_id)
    task["evidence_refs"] = evidence
    task["completion_claims"] = claims
    task["retrieval_trace"] = trace
    task["active_workspace"] = {
        "in_scope_targets": list(task.get("checkpoint_paths") or touched_paths),
        "loaded_context": loaded_context,
        "planned_mutations": planned,
        "pending_claims": claims,
        "evidence_refs": evidence,
        "retrieval_trace": trace,
    }


def evaluate_finalization(task: dict) -> dict:
    active = task.get("active_workspace") or {}
    evidence = {
        ref for ref in _refs(task.get("evidence_refs")) + _refs(task.get("completion_evidence"))
        if not ref.startswith("service_readback:")
    }
    evidence.update(_trusted_service_refs(task))
    results = []
    for claim in task.get("completion_claims") or []:
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
        claim_refs = set(_refs(claim.get("evidence_refs")))
        candidates = claim_refs & evidence
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
                "ok": evidence_ok,
                "evidence_refs": matched,
                "depends_on": dependencies,
                "missing_dependencies": [],
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

    explicit_scope_gate = task.get("scope_gate")
    # Defense-in-depth: a task that declared a consensus scope (mandatory for
    # risky_edit/service_ops) must arrive at finalization with a passing scope_gate.
    # If the gate is missing/invalid while mutations remain, fail closed instead of
    # silently passing -- this closes a latent dropped-gate regression without
    # changing behavior for consensus tasks (which always carry a passing gate) or
    # for plain workspace_edit (which declares no consensus_plan).
    has_declared_scope = bool(task.get("consensus_plan"))
    scope_gate_ok = isinstance(explicit_scope_gate, dict) and explicit_scope_gate.get("ok") is True
    scope_violation = (
        task.get("profile") == "inspect_local" and bool(active.get("planned_mutations"))
    ) or (
        isinstance(explicit_scope_gate, dict) and explicit_scope_gate.get("ok") is False
    ) or (
        has_declared_scope and not scope_gate_ok and bool(active.get("planned_mutations"))
    )
    remote_pending = task.get("profile") == "remote_handoff" and task.get("status") != "completed"
    ok = bool(results) and all(row["ok"] for row in results) and not scope_violation and not remote_pending
    return {
        "ok": ok,
        "arbiter": "pass" if ok else "hold",
        "claims": results,
        "refuter": {
            "scope_violation": scope_violation,
            "remote_execution_pending": remote_pending,
            "missing_claims": [row["claim"] for row in results if not row["ok"]],
        },
    }


def session_activity(task: dict, touched_paths: list[str]) -> dict:
    command = task.get("command") or []
    return {
        "tool_call_summary": task.get("command_preview") or " ".join(map(str, command)),
        "touched_paths": touched_paths,
        "generated_artifacts": task.get("generated_artifacts") or [],
        "checkpoint_id": task.get("checkpoint_id"),
        "evidence_refs": task.get("evidence_refs") or [],
        "final_claims": task.get("completion_claims") or [],
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
            digest = hashlib.sha256(target.read_bytes()).hexdigest()
            artifacts.append({"path": relative, "state": "present", "sha256": digest})
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
    readable = write_task_state_bundle(
        task,
        touched_paths=touched_paths,
        workspace=workspace,
        state_root=state_root,
    )
    return {
        **readable,
        "manifest": str(manifest_path.relative_to(workspace)),
        "artifact_count": len(artifacts),
    }
