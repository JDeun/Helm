#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from helm_workspace import get_workspace_layout
from scripts.memory_capture import build_memory_capture_plan
from scripts.state_io import append_jsonl_atomic
from scripts.command_guard import evaluate_command_guard, decision_to_json
from scripts.state_snapshot import latest_snapshot_path, write_state_snapshot
from scripts.skill_manifest_lib import (
    load_skill_policies as load_manifest_policies,
    load_skill_contract_manifests,
    load_profiles as load_manifest_profiles,
    manifest_audit,
    manifest_quality_audit,
    validate_contract_manifest,
)


EXIT_GUARD_REQUIRE_APPROVAL = 24
EXIT_GUARD_DENY = 25

WORKSPACE = get_workspace_layout().root
PROFILE_FILE = WORKSPACE / "references" / "execution_profiles.json"
POLICY_FILE = WORKSPACE / "references" / "skill_profile_policies.json"
CHECKPOINT_SCRIPT = WORKSPACE / "scripts" / "workspace_checkpoint.py"
TASK_LEDGER = get_workspace_layout().state_root / "task-ledger.jsonl"
CHECKPOINT_INDEX = get_workspace_layout().checkpoints_root / "index.json"
STATE_ROOT = get_workspace_layout().state_root


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


_MINIMAL_ENV_KEYS = {
    "PATH", "HOME", "LANG", "LC_ALL", "TERM", "SHELL",
    "USER", "LOGNAME", "TMPDIR", "TMP", "TEMP",
    "SYSTEMROOT", "COMSPEC",
}

_WORKSPACE_ENV_KEYS = {
    "PWD", "OLDPWD", "VIRTUAL_ENV", "CONDA_DEFAULT_ENV", "CONDA_PREFIX",
}


def _minimal_env(*, extra_keys: set[str] | None = None) -> dict[str, str]:
    """Return a minimal environment dict with only safe, non-secret variables."""
    keep = _MINIMAL_ENV_KEYS | (extra_keys or set())
    env: dict[str, str] = {}
    for key, value in os.environ.items():
        if key in keep or key.startswith("HELM_") or key.startswith("OPENCLAW_"):
            env[key] = value
    return env


def iso_to_compact(value: str | None) -> str | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _load_json_object(path: Path, *, label: str) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SystemExit(f"Missing {label}: {path}")
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid {label}: {exc}")
    if not isinstance(payload, dict):
        raise SystemExit(f"Invalid {label}: expected JSON object")
    return payload


def _load_json_array(path: Path, *, label: str) -> list[object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SystemExit(f"Missing {label}: {path}")
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid {label}: {exc}")
    if not isinstance(payload, list):
        raise SystemExit(f"Invalid {label}: expected JSON array")
    return payload


def _load_json_object_lines(path: Path, *, label: str) -> list[dict]:
    if not path.exists():
        return []
    entries: list[dict] = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"Invalid {label} line {lineno}: {exc}")
        if not isinstance(payload, dict):
            raise SystemExit(f"Invalid {label} line {lineno}: expected JSON object")
        entries.append(payload)
    return entries


def load_profiles() -> dict[str, dict]:
    data = _load_json_object(PROFILE_FILE, label="execution profile file")
    profiles = data.get("profiles")
    if not isinstance(profiles, dict):
        raise SystemExit("Invalid execution profile file: missing `profiles` object")
    return profiles


def load_policies() -> dict[str, dict]:
    return load_manifest_policies(WORKSPACE, POLICY_FILE)


def ensure_ledger_dir() -> None:
    TASK_LEDGER.parent.mkdir(parents=True, exist_ok=True)


def append_ledger(entry: dict) -> None:
    from scripts.state_io import append_jsonl_atomic
    append_jsonl_atomic(TASK_LEDGER, entry)


def _best_effort_index(task: dict) -> None:
    try:
        from scripts.ops_db import db_path_for_state_root, index_task_entry
        index_task_entry(state_root=STATE_ROOT, entry=task, source_file="task-ledger.jsonl")
    except Exception:
        pass


def finalize_task(task: dict) -> None:
    task["memory_capture"] = build_memory_capture_plan(task)
    try:
        task["state_snapshot"] = write_state_snapshot(task, workspace=WORKSPACE, state_root=STATE_ROOT)
    except OSError as exc:
        task["state_snapshot_error"] = str(exc)
    append_ledger(task)
    _best_effort_index(task)


def task_stub(profile: str, args: argparse.Namespace, command: list[str]) -> dict:
    config = load_profiles()[profile]
    if args.meta_json:
        try:
            meta = json.loads(args.meta_json)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"Invalid --meta-json payload: {exc}")
        if not isinstance(meta, dict):
            raise SystemExit("Invalid --meta-json payload: expected JSON object")
    else:
        meta = {}
    return {
        "task_id": args.task_id or str(uuid.uuid4()),
        "task_name": args.task_name or " ".join(command[:3]),
        "skill": args.skill,
        "profile": profile,
        "backend": config["backend"],
        "runtime_backend": config.get("runtime_backend", config["backend"]),
        "runtime_target_kind": config.get("runtime_target_kind"),
        "runtime_target": args.runtime_target,
        "runtime_note": args.runtime_note,
        "isolation": config.get("isolation"),
        "handoff_required": config.get("handoff_required", False),
        "command": command,
        "command_preview": shlex.join(command),
        "started_at": utc_now_iso(),
        "status": "queued",
        "checkpoint_label": args.label,
        "checkpoint_paths": args.path or [],
        "checkpoint_id": None,
        "delivery_mode": args.delivery_mode,
        "meta": meta,
    }


def validate_skill_profile(skill: str | None, profile: str) -> None:
    if not skill:
        return
    profiles = load_manifest_profiles(PROFILE_FILE)
    manifests = load_skill_contract_manifests(WORKSPACE)
    manifest = manifests.get(skill)
    if manifest:
        issues = validate_contract_manifest(skill, manifest, profiles)
        if issues:
            raise SystemExit("Invalid skill manifest: " + "; ".join(issues))
    policies = load_policies()
    policy = policies.get(skill)
    if not policy:
        return
    allowed = policy.get("allowed_profiles", [])
    if allowed and profile not in allowed:
        raise SystemExit(
            f"Skill `{skill}` does not allow profile `{profile}`. "
            f"Allowed profiles: {', '.join(allowed)}"
        )


def run_checkpoint(profile: str, args: argparse.Namespace) -> dict | None:
    profiles = load_profiles()
    config = profiles[profile]
    if config["checkpoint"] != "required":
        return None
    label = args.label or f"{profile}-checkpoint"
    paths = args.path or ["scripts", "skills", "docs", "references", "AGENTS.md", "TOOLS.md"]
    checkpoint_cmd = ["python3", str(CHECKPOINT_SCRIPT), "create", "--label", label]
    for path in paths:
        checkpoint_cmd.extend(["--path", path])
    try:
        result = subprocess.run(
            checkpoint_cmd, cwd=str(WORKSPACE), capture_output=True, text=True, timeout=60
        )
    except subprocess.TimeoutExpired:
        return {"error": "checkpoint timed out after 60 seconds"}
    if result.returncode != 0:
        return {"error": result.stderr.strip() or result.stdout.strip() or "checkpoint creation failed"}
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"error": "checkpoint output was not valid JSON", "raw_output": result.stdout.strip()}


def load_checkpoints() -> list[dict]:
    if not CHECKPOINT_INDEX.exists():
        return []
    checkpoints = _load_json_array(CHECKPOINT_INDEX, label="checkpoint index")
    valid: list[dict] = []
    for idx, item in enumerate(checkpoints, start=1):
        if not isinstance(item, dict):
            raise SystemExit(f"Invalid checkpoint index entry {idx}: expected JSON object")
        valid.append(item)
    return valid


def latest_task_entries() -> list[dict]:
    entries = _load_json_object_lines(TASK_LEDGER, label="task ledger")
    by_task: dict[str, dict] = {}
    for entry in entries:
        task_id = entry.get("task_id")
        if task_id:
            by_task[task_id] = entry
    return list(by_task.values())


def suggest_checkpoint_for_task(entry: dict) -> dict | None:
    checkpoints = load_checkpoints()
    explicit = entry.get("checkpoint_id")
    if explicit:
        for checkpoint in checkpoints:
            if checkpoint.get("checkpoint_id") == explicit:
                return checkpoint
    started_compact = iso_to_compact(entry.get("started_at"))
    if started_compact:
        older = [cp for cp in checkpoints if cp.get("created_at", "") <= started_compact]
        if older:
            return older[-1]
    return checkpoints[-1] if checkpoints else None


def cmd_list(_: argparse.Namespace) -> int:
    profiles = load_profiles()
    for name, config in profiles.items():
        print(
            f"{name}\t{config['backend']}\t"
            f"runtime={config.get('runtime_backend', config['backend'])}\t"
            f"checkpoint={config['checkpoint']}\t{config['description']}"
        )
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    profiles = load_profiles()
    if args.profile not in profiles:
        known = ", ".join(sorted(profiles.keys()))
        print(f"Unknown profile: {args.profile!r}. Known profiles: {known}", file=sys.stderr)
        return 2
    config = profiles[args.profile]
    print(json.dumps(config, indent=2))
    return 0


def cmd_policy(_: argparse.Namespace) -> int:
    policies = load_policies()
    if not policies:
        print("No skill profile policies configured.")
        return 0
    print(json.dumps(policies, indent=2, ensure_ascii=False))
    return 0


def cmd_validate_manifests(args: argparse.Namespace) -> int:
    payload = manifest_audit(WORKSPACE, POLICY_FILE, PROFILE_FILE)
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print(f"manifest_count={payload['manifest_count']}")
        print(f"missing_contract_skills={len(payload['missing_contract_skills'])}")
        for skill in payload["missing_contract_skills"][:50]:
            print(f"missing={skill}")
        for issue in payload["issues"][:100]:
            print(f"issue={issue}")
        print(f"ok={payload['ok']}")
    return 0 if payload["ok"] else 1


def cmd_audit_manifest_quality(args: argparse.Namespace) -> int:
    payload = manifest_quality_audit(WORKSPACE, PROFILE_FILE)
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print(f"manifest_count={payload['manifest_count']}")
        print(f"flagged_count={payload['flagged_count']}")
        for item in payload["items"][:100]:
            print(f"skill={item['skill']}")
            print(f"allowed_profiles={','.join(item['allowed_profiles'])}")
            print(f"default_profile={item['default_profile']}")
            for warning in item["warnings"]:
                print(f"warning={warning}")
    return 0 if payload["ok"] else 2


def cmd_ledger(args: argparse.Namespace) -> int:
    if not TASK_LEDGER.exists():
        print("No task ledger entries found.")
        return 0
    lines = TASK_LEDGER.read_text(encoding="utf-8").splitlines()
    count = args.limit or 20
    for line in lines[-count:]:
        print(line)
    return 0


def cmd_rollback(args: argparse.Namespace) -> int:
    entries = latest_task_entries()
    if not entries:
        print("No task ledger entries found.")
        return 0
    target = None
    if args.task_id:
        for entry in entries:
            if entry.get("task_id") == args.task_id:
                target = entry
                break
    else:
        risky = [entry for entry in entries if entry.get("profile") == "risky_edit"]
        if risky:
            risky.sort(key=lambda item: item.get("started_at", ""))
            target = risky[-1]
    if target is None:
        print("No matching risky task found.")
        return 0
    checkpoint = suggest_checkpoint_for_task(target)
    payload = {
        "task_id": target.get("task_id"),
        "task_name": target.get("task_name"),
        "status": target.get("status"),
        "profile": target.get("profile"),
        "runtime_backend": target.get("runtime_backend") or target.get("backend"),
        "runtime_target": target.get("runtime_target"),
        "checkpoint": checkpoint,
    }
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print(f"task_id={payload['task_id']}")
        print(f"task_name={payload['task_name']}")
        print(f"profile={payload['profile']}")
        print(f"status={payload['status']}")
        print(f"runtime_backend={payload['runtime_backend']}")
        print(f"runtime_target={payload['runtime_target'] or '-'}")
        if checkpoint:
            print(f"checkpoint_id={checkpoint.get('checkpoint_id')}")
            print(f"checkpoint_label={checkpoint.get('label')}")
            print(
                "restore_command="
                f"python3 {CHECKPOINT_SCRIPT} restore {checkpoint.get('checkpoint_id')}"
            )
        else:
            print("checkpoint_id=-")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    profiles = load_profiles()
    if args.profile not in profiles:
        known = ", ".join(sorted(profiles.keys()))
        print(f"Unknown profile: {args.profile!r}. Known profiles: {known}", file=sys.stderr)
        return 2
    config = profiles[args.profile]
    command = args.command
    if not command:
        raise SystemExit("No command supplied. Use `-- <command> ...`")

    validate_skill_profile(args.skill, args.profile)

    task = task_stub(args.profile, args, command)
    append_ledger(task)
    _best_effort_index(task)

    checkpoint = run_checkpoint(args.profile, args)
    if checkpoint and checkpoint.get("error"):
        task["status"] = "failed"
        task["finished_at"] = utc_now_iso()
        task["failure_stage"] = "checkpoint"
        task["failure_reason"] = checkpoint["error"]
        finalize_task(task)
        return 1
    if checkpoint:
        task["checkpoint_id"] = checkpoint.get("checkpoint_id")
        task["checkpoint_label"] = checkpoint.get("label")

    # --- Guard evaluation (before any backend check) ---
    guard_mode_source = "cli" if getattr(args, "guard_mode", None) else "env"
    guard_mode = getattr(args, "guard_mode", None) or os.environ.get("HELM_GUARD_MODE", "enforce")
    guard_decision = None

    if guard_mode == "off" and guard_mode_source == "env":
        print("WARNING: Guard disabled via HELM_GUARD_MODE environment variable", file=sys.stderr)

    if guard_mode != "off":
        try:
            guard_decision = evaluate_command_guard(
                command=command,
                selected_profile=args.profile,
                profiles=profiles,
                workspace=WORKSPACE,
                task_name=getattr(args, "task_name", None),
                task_goal=getattr(args, "task_goal", None),
            )
        except Exception as exc:
            print(f"WARNING: Guard evaluation failed: {exc}. Defaulting to require_approval.", file=sys.stderr)
            from scripts.command_guard import GuardDecision, CommandClassification
            guard_decision = GuardDecision(
                action="require_approval",
                risk_score=0.5,
                score_breakdown={"guard_error": 0.5},
                selected_profile=args.profile,
                recommended_profile=None,
                reasons=tuple([f"guard evaluation error: {exc}"]),
                matched_rules=tuple(),
                classification=CommandClassification(
                    normalized_command=" ".join(command),
                    argv=tuple(command),
                    shell_wrapped=False,
                    shell_inner_command=None,
                    categories=tuple(["unknown"]),
                    matched_rules=tuple(),
                    writes_detected=False,
                    network_detected=False,
                    destructive_detected=False,
                    privilege_detected=False,
                    remote_detected=False,
                ),
                approval_required=True,
                approval_hint="--approve-risk",
            )

    task["guard"] = (
        decision_to_json(guard_decision) if guard_decision
        else {"enabled": False, "mode": guard_mode}
    )
    task["guard"]["source"] = guard_mode_source

    if guard_decision and getattr(args, "guard_json", False):
        print(json.dumps(decision_to_json(guard_decision), indent=2, ensure_ascii=False))
        return 0

    if guard_mode == "enforce" and guard_decision:
        if guard_decision.action == "deny":
            task["status"] = "blocked"
            task["failure_stage"] = "guard"
            task["failure_reason"] = "guard deny"
            append_ledger(task)
            _best_effort_index(task)
            print(f"GUARD DENY: {', '.join(guard_decision.reasons)}", file=sys.stderr)
            return EXIT_GUARD_DENY

        if guard_decision.action == "require_approval" and not getattr(args, "approve_risk", False):
            task["status"] = "blocked"
            task["failure_stage"] = "guard"
            task["failure_reason"] = "approval required"
            append_ledger(task)
            _best_effort_index(task)
            hint = guard_decision.approval_hint or "Use --approve-risk to proceed."
            print(f"GUARD APPROVAL REQUIRED: {', '.join(guard_decision.reasons)}", file=sys.stderr)
            print(f"Hint: {hint}", file=sys.stderr)
            return EXIT_GUARD_REQUIRE_APPROVAL

        if getattr(args, "approve_risk", False) and guard_decision.action == "require_approval":
            task["guard"]["approved"] = True
    # --- End guard evaluation ---

    # --- Backend-specific handling ---
    if config["backend"] == "manual-remote":
        if not args.runtime_target:
            task["status"] = "failed"
            task["finished_at"] = utc_now_iso()
            task["failure_stage"] = "handoff"
            task["failure_reason"] = "remote_handoff requires --runtime-target"
            finalize_task(task)
            print("remote_handoff requires --runtime-target", file=sys.stderr)
            return 2
        task["status"] = "handoff_required"
        task["finished_at"] = utc_now_iso()
        finalize_task(task)
        print(
            json.dumps(
                {
                    "task_id": task["task_id"],
                    "status": task["status"],
                    "runtime_target": args.runtime_target,
                    "runtime_note": args.runtime_note,
                    "command_preview": task["command_preview"],
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return 0

    task["status"] = "running"
    task["started_execution_at"] = utc_now_iso()
    append_ledger(task)
    _best_effort_index(task)

    writes_allowed = config.get("writes_allowed", True)
    network_allowed = config.get("network_allowed", True)
    if not writes_allowed and not network_allowed:
        child_env = _minimal_env()
    elif not network_allowed:
        child_env = _minimal_env(extra_keys=_WORKSPACE_ENV_KEYS)
    else:
        child_env = os.environ.copy()
    child_env["HELM_TASK_ID"] = task["task_id"]
    child_env["HELM_TASK_PROFILE"] = str(task["profile"])
    child_env["OPENCLAW_TASK_ID"] = task["task_id"]
    previous_snapshot = latest_snapshot_path(STATE_ROOT)
    if previous_snapshot:
        child_env["HELM_PREVIOUS_STATE_SNAPSHOT"] = str(previous_snapshot)
        child_env["OPENCLAW_PREVIOUS_STATE_SNAPSHOT"] = str(previous_snapshot)
        task["previous_state_snapshot"] = str(previous_snapshot)
    if task.get("skill"):
        child_env["HELM_TASK_SKILL"] = str(task["skill"])
        child_env["OPENCLAW_TASK_SKILL"] = str(task["skill"])
    if task.get("task_name"):
        child_env["HELM_TASK_NAME"] = str(task["task_name"])
        child_env["OPENCLAW_TASK_NAME"] = str(task["task_name"])
    child_env["OPENCLAW_TASK_PROFILE"] = str(task["profile"])

    raw_timeout = getattr(args, "timeout", 1800)
    if raw_timeout is not None and raw_timeout < 0:
        raw_timeout = 0
    timeout_seconds = raw_timeout or None  # 0 → None (no limit)
    try:
        result = subprocess.run(command, cwd=str(WORKSPACE), env=child_env, timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        task["finished_at"] = utc_now_iso()
        task["status"] = "timeout"
        task["failure_stage"] = "execution"
        task["failure_reason"] = f"command timed out after {timeout_seconds}s"
        finalize_task(task)
        print(
            f"TIMEOUT: command exceeded {timeout_seconds}s limit: {shlex.join(command)}",
            file=sys.stderr,
        )
        return 1

    task["finished_at"] = utc_now_iso()
    task["exit_code"] = result.returncode
    task["status"] = "completed" if result.returncode == 0 else "failed"
    finalize_task(task)
    return result.returncode


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a shell command under a declared execution profile.")
    subparsers = parser.add_subparsers(dest="command_name", required=True)

    listing = subparsers.add_parser("list", help="List configured execution profiles.")
    listing.set_defaults(func=cmd_list)

    show = subparsers.add_parser("show", help="Show one execution profile.")
    show.add_argument("profile", type=str)
    show.set_defaults(func=cmd_show)

    policy = subparsers.add_parser("policy", help="Show skill-to-profile policy mappings.")
    policy.set_defaults(func=cmd_policy)

    manifests = subparsers.add_parser("validate-manifests", help="Validate skill contract manifests.")
    manifests.add_argument("--json", action="store_true")
    manifests.set_defaults(func=cmd_validate_manifests)

    quality = subparsers.add_parser("audit-manifest-quality", help="Flag overly generic or weak manifest policies.")
    quality.add_argument("--json", action="store_true")
    quality.set_defaults(func=cmd_audit_manifest_quality)

    ledger = subparsers.add_parser("ledger", help="Show recent task-ledger entries.")
    ledger.add_argument("--limit", type=int, default=20)
    ledger.set_defaults(func=cmd_ledger)

    rollback = subparsers.add_parser("rollback", help="Suggest the checkpoint to use for a risky task.")
    rollback.add_argument("--task-id", help="Specific task id to inspect. Defaults to latest risky_edit task.")
    rollback.add_argument("--json", action="store_true")
    rollback.set_defaults(func=cmd_rollback)

    return parser


def parse_run_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a command with a declared execution profile.")
    parser.add_argument("command_name")
    parser.add_argument("profile", type=str)
    parser.add_argument("--task-name", help="Human-readable task name, recommended for service_ops.")
    parser.add_argument("--task-id", help="Explicit task id override for harness-controlled runs.")
    parser.add_argument("--skill", help="Owning skill slug for policy enforcement.")
    parser.add_argument("--meta-json", help="Structured metadata JSON to embed in the task ledger.")
    parser.add_argument("--label", help="Checkpoint label when the profile requires one.")
    parser.add_argument("--path", action="append", help="Checkpoint path override. May be repeated.")
    parser.add_argument("--runtime-target", help="Named runtime target such as local, ssh:host, container:name, or node label.")
    parser.add_argument("--runtime-note", help="Short note for backend/runtime handoff context.")
    parser.add_argument(
        "--delivery-mode",
        choices=["inline", "background", "announce", "none"],
        default="inline",
        help="Delivery mode for task-ledger context.",
    )
    parser.add_argument(
        "--guard-mode",
        choices=["enforce", "audit", "off"],
        default=None,
        help="Guard evaluation mode. Default: enforce (or HELM_GUARD_MODE env).",
    )
    parser.add_argument(
        "--approve-risk",
        action="store_true",
        help="Approve commands that require_approval. Does not override deny.",
    )
    parser.add_argument(
        "--guard-json",
        action="store_true",
        help="Print guard decision as JSON and exit without running the command.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=1800,
        help="Subprocess timeout in seconds (default: 1800 / 30 minutes). 0 disables the timeout.",
    )
    args, remainder = parser.parse_known_args()
    if remainder and remainder[0] == "--":
        remainder = remainder[1:]
    args.command = remainder
    return args


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "run":
        return cmd_run(parse_run_args())

    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
