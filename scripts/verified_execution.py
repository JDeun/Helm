#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

try:
    from consensus_plan_gate import normalize_scope
    from role_catalog import expand_role_markers
    from task_state_bundle import write_task_state_bundle
except ModuleNotFoundError:
    from scripts.consensus_plan_gate import normalize_scope
    from scripts.role_catalog import expand_role_markers
    from scripts.task_state_bundle import write_task_state_bundle


MAX_ATTEMPTS = 3
EXECUTOR_ROLE_MARKER = "[role:executor]"
VERIFIER_ROLE_MARKER = "[role:verifier]"
ARCHITECT_ROLE_MARKER = "[role:architect]"
SECRET_RE = re.compile(r"(?i)(bearer\s+)[-A-Za-z0-9._~+/]+=*|\b(?:sk|ghp|github_pat|xox[baprs])-[-A-Za-z0-9_]{8,}\b")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def _safe_id(value: object, *, label: str) -> str:
    text = str(value or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", text):
        raise ValueError(f"{label} must start with a letter or digit and contain only letters, digits, dot, underscore, or hyphen")
    return text


def validate_plan(plan: dict) -> dict:
    if not isinstance(plan, dict):
        raise ValueError("verified execution plan must be an object")
    plan_id = _safe_id(plan.get("task_id"), label="plan task_id")
    if not str(plan.get("objective") or "").strip():
        raise ValueError("verified execution plan requires objective")
    profile = str(plan.get("profile") or "risky_edit")
    tasks = plan.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise ValueError("verified execution plan requires atomic tasks")
    seen = set()
    normalized_tasks = []
    for index, raw in enumerate(tasks, start=1):
        if not isinstance(raw, dict):
            raise ValueError(f"task {index} must be an object")
        task = dict(raw)
        task_id = _safe_id(task.get("task_id") or f"task-{index}", label=f"task {index} id")
        if task_id in seen:
            raise ValueError(f"duplicate atomic task id: {task_id}")
        seen.add(task_id)
        command = task.get("command")
        if not isinstance(command, list) or not command or not all(isinstance(token, str) and token for token in command):
            raise ValueError(f"task {task_id} requires a non-empty command string array")
        criteria = task.get("acceptance_criteria")
        if not isinstance(criteria, list) or not any(str(item).strip() for item in criteria):
            raise ValueError(f"task {task_id} requires acceptance criteria")
        evidence_commands = task.get("evidence_commands")
        if not isinstance(evidence_commands, list) or not evidence_commands:
            raise ValueError(f"task {task_id} requires at least one evidence command")
        for evidence in evidence_commands:
            if not isinstance(evidence, list) or not evidence or not all(isinstance(token, str) and token for token in evidence):
                raise ValueError(f"task {task_id} evidence commands must be non-empty string arrays")
        attempts = int(task.get("max_attempts", MAX_ATTEMPTS))
        if attempts < 1 or attempts > MAX_ATTEMPTS:
            raise ValueError(f"task {task_id} max_attempts must be between 1 and {MAX_ATTEMPTS}")
        raw_scope = task.get("scope") or plan.get("scope") or []
        if not isinstance(raw_scope, list) or not raw_scope or not all(isinstance(path, str) and path for path in raw_scope):
            raise ValueError(f"task {task_id} requires scope")
        try:
            scope = normalize_scope(raw_scope)
        except ValueError as exc:
            raise ValueError(f"task {task_id} has unsafe scope: {exc}") from exc
        task.update(
            {
                "task_id": task_id,
                "command": command,
                "acceptance_criteria": [str(item) for item in criteria],
                "evidence_commands": evidence_commands,
                "max_attempts": attempts,
                "scope": scope,
            }
        )
        normalized_tasks.append(task)
    return {**plan, "task_id": plan_id, "profile": profile, "tasks": normalized_tasks}


def _redact_and_truncate(value: str, limit: int = 12000) -> str:
    clean = SECRET_RE.sub("[REDACTED]", value or "")
    return clean if len(clean) <= limit else clean[:limit] + "\n...[truncated]"


def _failure_fingerprint(result: dict) -> str:
    tail = str(result.get("stderr") or result.get("stdout") or result.get("reason") or "")[-2000:]
    normalized = re.sub(r"\b\d+(?:\.\d+)?\b", "#", tail.casefold())
    payload = f"{result.get('exit_code')}\n{normalized}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _role_input(marker: str) -> dict:
    expanded = expand_role_markers(marker)
    role = expanded["role"]
    return {
        "role_marker": f"[role:{role['role_id']}]",
        "role_prompt": role["prompt"],
        "role_contract": role.get("output_contract", []),
        "expanded_role_input": expanded["expanded"],
    }


def _latest_task_row(ledger: Path, task_id: str) -> dict | None:
    if not ledger.exists():
        return None
    latest = None
    for line in ledger.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict) and row.get("task_id") == task_id:
            latest = row
    return latest


def build_runner_command(plan: dict, task: dict, *, attempt: int, runner_path: Path) -> tuple[str, list[str]]:
    run_id = f"{plan['task_id']}-{task['task_id']}-a{attempt}"
    command = [
        sys.executable,
        str(runner_path),
        "run",
        str(plan["profile"]),
        "--task-id",
        run_id,
        "--parent-task-id",
        str(plan["task_id"]),
        "--task-name",
        str(task.get("title") or task["task_id"]),
        "--verified-attempt",
        "--meta-json",
        json.dumps({
            "role_marker": task["role_marker"],
            "role_prompt": task["role_prompt"],
            "role_contract": task["role_contract"],
            "expanded_role_input": task["expanded_role_input"],
            "verified_plan_id": plan["task_id"],
            "atomic_task_id": task["task_id"],
        }),
    ]
    for path in task["scope"]:
        command.extend(["--path", path])
    for criterion in task["acceptance_criteria"]:
        command.extend(["--acceptance", criterion])
    for evidence in task["evidence_commands"]:
        command.extend(["--evidence-command-json", json.dumps(evidence)])
    command.extend(["--", *task["command"]])
    return run_id, command


def default_executor(plan: dict, task: dict, attempt: int, *, workspace: Path, runner_path: Path) -> dict:
    run_id, command = build_runner_command(plan, task, attempt=attempt, runner_path=runner_path)
    result = subprocess.run(command, cwd=str(workspace), capture_output=True, text=True, shell=False)
    return {
        "run_id": run_id,
        "exit_code": result.returncode,
        "stdout": _redact_and_truncate(result.stdout),
        "stderr": _redact_and_truncate(result.stderr),
        "runner_command": command,
    }


def _criterion_result(criterion: str, row: dict) -> dict:
    text = " ".join(str(criterion).casefold().split())
    words = set(re.findall(r"[a-z0-9_.:/-]+|[가-힣]+", text))
    gate = row.get("finalization_gate") or {}
    scope = row.get("scope_gate") or {}
    gathered = row.get("evidence_gathering") or {}
    refs = [str(ref) for ref in row.get("evidence_refs") or []]
    base_ok = row.get("status") == "completed" and gate.get("ok") is True
    checks: list[tuple[str, bool, list[str]]] = []
    ambiguous = bool(words & {"no", "not", "never", "without", "fail", "fails", "failed", "failure", "nonzero", "error", "아님", "않음", "없음", "실패"})

    if words & {"test", "tests", "pytest", "unittest", "테스트"}:
        matches = [item for item in gathered.get("command_results") or [] if isinstance(item, dict) and item.get("kind") == "test"]
        expected = bool(words & {"pass", "passes", "passed", "succeed", "succeeds", "success", "green", "통과", "성공"})
        checks.append(("test", expected and bool(matches) and all(item.get("ok") is True for item in matches), ["command:" + " ".join(map(str, item.get("argv") or [])) for item in matches]))
    if words & {"scope", "scoped", "touched", "declared", "범위"}:
        expected = bool(words & {"within", "remain", "remains", "bounded", "inside", "범위", "유지"})
        checks.append(("scope", expected and scope.get("ok") is True, [str(path) for path in scope.get("touched_paths") or []]))
    if words & {"service", "readback", "remote", "provider", "서비스", "원격"}:
        matches = [
            item for item in gathered.get("service_results") or []
            if isinstance(item, dict)
            and item.get("kind") == "service_readback"
            and item.get("provenance") in {"evidence_gatherer_command", "actual_remote_readback", "actual_provider_readback"}
        ]
        expected = bool(words & {"pass", "passes", "passed", "succeed", "succeeds", "success", "verified", "valid", "통과", "성공", "검증"})
        checks.append(("service_readback", expected and bool(matches) and all(item.get("ok") is True for item in matches), [str(item.get("source") or item.get("reference")) for item in matches]))
    if words & {"file", "files", "artifact", "artifacts", "hash", "sha256", "파일", "산출물"}:
        matches = [item for item in gathered.get("file_results") or [] if isinstance(item, dict)]
        expected = bool(words & {"exists", "present", "matches", "valid", "created", "updated", "hash", "sha256", "존재", "일치", "생성", "갱신"})
        checks.append(("file_readback", expected and bool(matches) and all(item.get("ok") is True for item in matches), [str(item.get("path")) for item in matches]))
    if words & {"command", "process", "exit", "exits", "successfully", "성공", "종료"}:
        exit_refs = [ref for ref in refs if ref == "process_exit:0"]
        expected = bool(words & {"successfully", "success", "succeed", "succeeds", "zero", "성공", "정상"})
        checks.append(("process_exit", expected and row.get("exit_code") == 0 and bool(exit_refs), exit_refs))

    kind = "+".join(item[0] for item in checks) or None
    evidence_ok = bool(checks) and all(item[1] for item in checks)
    matched_refs = [ref for item in checks for ref in item[2]]
    if not checks and not ambiguous:
        claim_rows = {
            str(item.get("claim_id")): item
            for item in gate.get("claims") or []
            if isinstance(item, dict) and item.get("claim_id")
        }
        for claim in row.get("completion_claims") or []:
            if not isinstance(claim, dict):
                continue
            label = " ".join(str(claim.get("claim") or claim.get("claim_id") or "").casefold().split())
            claim_id = str(claim.get("claim_id") or "")
            if label != text or claim_rows.get(claim_id, {}).get("ok") is not True:
                continue
            kind = str(claim.get("evidence_type") or "claim")
            matched_refs = [str(ref) for ref in claim.get("evidence_refs") or [] if str(ref) in refs]
            evidence_ok = bool(matched_refs)
            break

    ok = bool(kind and not ambiguous and base_ok and evidence_ok)
    reason = (
        "criterion_evidence_passed" if ok else
        "criterion_not_bound_to_evidence" if kind is None or ambiguous else
        "ledger_finalization_failed" if not base_ok else
        "matching_evidence_failed"
    )
    return {
        "criterion": criterion,
        "ok": ok,
        "matched_evidence_kind": kind,
        "evidence_refs": matched_refs,
        "reason": reason,
    }


def default_verifier(plan: dict, task: dict, execution: dict, *, state_root: Path) -> dict:
    row = _latest_task_row(state_root / "task-ledger.jsonl", str(execution.get("run_id") or ""))
    criteria = [_criterion_result(criterion, row or {}) for criterion in task["acceptance_criteria"]]
    ok = bool(criteria) and all(item["ok"] for item in criteria)
    role_input = _role_input(VERIFIER_ROLE_MARKER)
    role_input.update({key: task[key] for key in role_input if key in task})
    return {
        **role_input,
        "decision": "pass" if ok else "fail",
        "ok": ok,
        "criteria": criteria,
        "ledger_row_found": row is not None,
        "operational_status": (row or {}).get("operational_status"),
        "failure_reason": None if ok else "one or more acceptance criteria lack matching independent evidence",
    }


def _transition_hint(failures: list[dict]) -> dict | None:
    try:
        try:
            from policy_transition import evaluate
        except ModuleNotFoundError:
            from scripts.policy_transition import evaluate
        history = [
            {
                "signature": {
                    "fingerprint": row["fingerprint"],
                    "tool": "verified_execution",
                    "error_class": "verification_failed",
                }
            }
            for row in failures
        ]
        return evaluate(history)
    except (ImportError, AttributeError):
        if len(failures) >= 2 and len({row["fingerprint"] for row in failures[-2:]}) == 1:
            return {"action": "stop_retry_and_diagnose", "reason": "same verified-execution failure repeated"}
    return None


def execute_verified_plan(
    raw_plan: dict,
    *,
    workspace: Path,
    state_root: Path,
    runner_path: Path | None = None,
    executor: Callable[[dict, dict, int], dict] | None = None,
    verifier: Callable[[dict, dict, dict], dict] | None = None,
) -> dict:
    plan = validate_plan(raw_plan)
    workspace = workspace.resolve()
    state_root = state_root.resolve()
    runner = runner_path or Path(__file__).with_name("run_with_profile.py")
    executor_role = _role_input(EXECUTOR_ROLE_MARKER)
    verifier_role = _role_input(VERIFIER_ROLE_MARKER)
    architect_role = _role_input(ARCHITECT_ROLE_MARKER)
    records = []
    overall_status = "completed"
    blocker = None
    for task in plan["tasks"]:
        attempts = []
        failures = []
        passed = False
        for attempt_number in range(1, task["max_attempts"] + 1):
            executor_task = {**task, **executor_role}
            if executor is None:
                execution = default_executor(plan, executor_task, attempt_number, workspace=workspace, runner_path=runner)
            else:
                execution = executor(plan, executor_task, attempt_number)
            execution = {
                **execution,
                **executor_role,
            }
            verifier_task = {**task, **verifier_role}
            if verifier is None:
                verification = default_verifier(plan, verifier_task, execution, state_root=state_root)
            else:
                verification = verifier(plan, verifier_task, execution)
                verification = {
                    **verification,
                    **verifier_role,
                }
            attempt_row = {
                "attempt": attempt_number,
                "execution": execution,
                "verification": verification,
            }
            attempts.append(attempt_row)
            if execution.get("exit_code") == 0 and verification.get("ok") is True:
                passed = True
                break
            fingerprint = _failure_fingerprint(
                {
                    "exit_code": execution.get("exit_code"),
                    "stderr": execution.get("stderr") or verification.get("failure_reason"),
                    "stdout": execution.get("stdout"),
                }
            )
            failures.append({"attempt": attempt_number, "fingerprint": fingerprint})
            attempt_row["failure_fingerprint"] = fingerprint
            attempt_row["policy_transition"] = _transition_hint(failures)
            repeated = [row for row in failures if row["fingerprint"] == fingerprint]
            if len(repeated) >= MAX_ATTEMPTS:
                blocker = f"same failure fingerprint {fingerprint} repeated {MAX_ATTEMPTS} times"
                break
        records.append({"task_id": task["task_id"], "status": "passed" if passed else "blocked", "attempts": attempts})
        if not passed:
            overall_status = "blocked"
            blocker = blocker or f"atomic task {task['task_id']} exhausted its retry budget"
            break
    architect_input = {**architect_role, "task_statuses": [row["status"] for row in records]}
    final_architect = {
        **architect_role,
        "review_input": architect_input,
        "decision": "approve" if overall_status == "completed" and all(row["status"] == "passed" for row in records) else "block",
        "findings": [] if overall_status == "completed" else [blocker],
    }
    result = {
        "schema_version": 1,
        "task_id": plan["task_id"],
        "objective": plan["objective"],
        "profile": plan["profile"],
        "status": overall_status,
        "ok": overall_status == "completed" and final_architect["decision"] == "approve",
        "started_at": records[0]["attempts"][0]["execution"].get("started_at") if records and records[0]["attempts"] else None,
        "finished_at": utc_now_iso(),
        "blocker": blocker,
        "tasks": records,
        "final_architect_review": final_architect,
    }
    bundle_root = state_root / "task-bundles" / plan["task_id"]
    artifact_path = bundle_root / "verified-execution.json"
    _atomic_write_json(artifact_path, result)
    state_task = {
        "task_id": plan["task_id"],
        "task_name": plan["objective"],
        "profile": plan["profile"],
        "status": "completed" if result["ok"] else "blocked",
        "operational_status": "verified" if result["ok"] else "needs_verification",
        "blocked_reason": blocker,
        "next_action": "No further action required." if result["ok"] else "diagnose the repeated failure before resuming",
        "evidence_refs": [str(artifact_path)],
        "evidence_gathering": {"ok": result["ok"], "verified_execution_artifact": str(artifact_path)},
    }
    result["state_bundle"] = write_task_state_bundle(
        state_task,
        touched_paths=[],
        workspace=workspace,
        state_root=state_root,
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Run an atomic executor/evidence/verifier loop with a three-attempt cap.")
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    parser.add_argument("--state-root", type=Path)
    parser.add_argument("--runner", type=Path)
    args = parser.parse_args()
    try:
        plan = json.loads(args.plan.read_text(encoding="utf-8"))
        state_root = args.state_root or args.workspace / ".openclaw"
        result = execute_verified_plan(
            plan,
            workspace=args.workspace,
            state_root=state_root,
            runner_path=args.runner,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
