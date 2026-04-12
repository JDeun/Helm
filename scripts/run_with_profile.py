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


WORKSPACE = Path.home() / ".openclaw" / "workspace"
PROFILE_FILE = WORKSPACE / "references" / "execution_profiles.json"
POLICY_FILE = WORKSPACE / "references" / "skill_profile_policies.json"
CHECKPOINT_SCRIPT = WORKSPACE / "scripts" / "workspace_checkpoint.py"
TASK_LEDGER = WORKSPACE / ".openclaw" / "task-ledger.jsonl"
CHECKPOINT_INDEX = WORKSPACE / ".openclaw" / "checkpoints" / "index.json"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def iso_to_compact(value: str | None) -> str | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def load_profiles() -> dict[str, dict]:
    data = json.loads(PROFILE_FILE.read_text(encoding="utf-8"))
    return data["profiles"]


def load_policies() -> dict[str, dict]:
    if not POLICY_FILE.exists():
        return {}
    data = json.loads(POLICY_FILE.read_text(encoding="utf-8"))
    return data.get("skills", {})


def ensure_ledger_dir() -> None:
    TASK_LEDGER.parent.mkdir(parents=True, exist_ok=True)


def append_ledger(entry: dict) -> None:
    ensure_ledger_dir()
    with TASK_LEDGER.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def task_stub(profile: str, args: argparse.Namespace, command: list[str]) -> dict:
    config = load_profiles()[profile]
    return {
        "task_id": str(uuid.uuid4()),
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
    }


def validate_skill_profile(skill: str | None, profile: str) -> None:
    if not skill:
        return
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
    result = subprocess.run(checkpoint_cmd, cwd=str(WORKSPACE), capture_output=True, text=True)
    if result.returncode != 0:
        return {"error": result.stderr.strip() or result.stdout.strip() or "checkpoint creation failed"}
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"error": "checkpoint output was not valid JSON", "raw_output": result.stdout.strip()}


def load_checkpoints() -> list[dict]:
    if not CHECKPOINT_INDEX.exists():
        return []
    return json.loads(CHECKPOINT_INDEX.read_text(encoding="utf-8"))


def latest_task_entries() -> list[dict]:
    if not TASK_LEDGER.exists():
        return []
    entries = [json.loads(line) for line in TASK_LEDGER.read_text(encoding="utf-8").splitlines() if line.strip()]
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
                f"python3 ~/.openclaw/workspace/scripts/workspace_checkpoint.py restore {checkpoint.get('checkpoint_id')}"
            )
        else:
            print("checkpoint_id=-")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    profiles = load_profiles()
    config = profiles[args.profile]
    command = args.command
    if not command:
        raise SystemExit("No command supplied. Use `-- <command> ...`")

    validate_skill_profile(args.skill, args.profile)

    task = task_stub(args.profile, args, command)
    append_ledger(task)

    checkpoint = run_checkpoint(args.profile, args)
    if checkpoint and checkpoint.get("error"):
        task["status"] = "failed"
        task["finished_at"] = utc_now_iso()
        task["failure_stage"] = "checkpoint"
        task["failure_reason"] = checkpoint["error"]
        append_ledger(task)
        return 1
    if checkpoint:
        task["checkpoint_id"] = checkpoint.get("checkpoint_id")
        task["checkpoint_label"] = checkpoint.get("label")

    if config["backend"] == "manual-remote":
        if not args.runtime_target:
            task["status"] = "failed"
            task["finished_at"] = utc_now_iso()
            task["failure_stage"] = "handoff"
            task["failure_reason"] = "remote_handoff requires --runtime-target"
            append_ledger(task)
            print("remote_handoff requires --runtime-target", file=sys.stderr)
            return 2
        task["status"] = "handoff_required"
        task["finished_at"] = utc_now_iso()
        append_ledger(task)
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

    if args.profile == "service_ops" and not args.task_name:
        print(
            "service_ops should include --task-name so the ledger stays readable.",
            file=sys.stderr,
        )

    task["status"] = "running"
    task["started_execution_at"] = utc_now_iso()
    append_ledger(task)

    child_env = os.environ.copy()
    child_env["OPENCLAW_TASK_ID"] = task["task_id"]
    if task.get("skill"):
        child_env["OPENCLAW_TASK_SKILL"] = str(task["skill"])
    child_env["OPENCLAW_TASK_PROFILE"] = str(task["profile"])
    if task.get("task_name"):
        child_env["OPENCLAW_TASK_NAME"] = str(task["task_name"])

    result = subprocess.run(command, cwd=str(WORKSPACE), env=child_env)

    task["finished_at"] = utc_now_iso()
    task["exit_code"] = result.returncode
    task["status"] = "completed" if result.returncode == 0 else "failed"
    append_ledger(task)
    return result.returncode


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a shell command under a declared execution profile.")
    subparsers = parser.add_subparsers(dest="command_name", required=True)

    listing = subparsers.add_parser("list", help="List configured execution profiles.")
    listing.set_defaults(func=cmd_list)

    show = subparsers.add_parser("show", help="Show one execution profile.")
    show.add_argument("profile", choices=sorted(load_profiles().keys()))
    show.set_defaults(func=cmd_show)

    policy = subparsers.add_parser("policy", help="Show skill-to-profile policy mappings.")
    policy.set_defaults(func=cmd_policy)

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
    parser.add_argument("profile", choices=sorted(load_profiles().keys()))
    parser.add_argument("--task-name", help="Human-readable task name, recommended for service_ops.")
    parser.add_argument("--skill", help="Owning skill slug for policy enforcement.")
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
