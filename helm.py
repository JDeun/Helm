from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path

from helm_context import configured_context_sources
from helm_workspace import DEFAULT_WORKSPACE

from commands import (
    read_json,
    read_jsonl,
    relative_or_absolute,
    state_root_for,
    memory_review_queue_count_for,
    latest_snapshot_path,
    _warn_parse_failure,
)
from commands.checkpoint import (
    cmd_checkpoint,
    cmd_checkpoint_create,
    cmd_checkpoint_finalize,
    cmd_checkpoint_list,
    cmd_checkpoint_preview,
    cmd_checkpoint_recommend,
    cmd_checkpoint_restore,
    cmd_checkpoint_show,
)
from commands.context import (
    build_capability_diff_payload,
    build_onboarding_payload,
    build_run_contract_payload,
    build_session_card_payload,
    cmd_adopt,
    cmd_context,
    cmd_onboard,
    cmd_sources,
    latest_tasks,
    load_draft_assessments,
    task_finalization_status,
)
from commands.doctor import cmd_doctor, cmd_survey
from commands.harness import cmd_harness
from commands.memory import cmd_memory
from commands.ops import cmd_ops
from commands.profile import cmd_profile
from commands.skill import cmd_skill, cmd_skill_approve, cmd_skill_diff, cmd_skill_reject, cmd_skill_review
from commands.status import (
    cmd_capability_diff,
    cmd_detect,
    cmd_init,
    cmd_report,
    cmd_run_contract,
    cmd_status,
    format_report_markdown,
)
from commands.validate import cmd_validate
from commands.db import cmd_db_init, cmd_db_rebuild, cmd_db_verify, cmd_db_status


# These functions are defined here (not just imported) so tests can patch
# helm.configured_context_sources and helm.build_status_payload directly.

def build_status_payload(root: Path) -> dict:
    from commands import detect_layout
    layout = detect_layout(root)
    state_root = state_root_for(root)
    context_sources = configured_context_sources(root)
    task_entries = latest_tasks(read_jsonl(state_root / "task-ledger.jsonl"))
    command_entries = read_jsonl(state_root / "command-log.jsonl")
    checkpoints = read_json(state_root / "checkpoints" / "index.json", [])
    draft_assessments = load_draft_assessments(root)
    recent_tasks = task_entries[-10:]
    failed_commands = [entry for entry in command_entries[-100:] if entry.get("exit_code") not in (0, None)]
    finalization_counts = Counter(
        (entry.get("memory_capture") or {}).get("finalization_status", "unknown")
        for entry in recent_tasks
    )
    memory_operations = read_jsonl(state_root / "memory-operations.jsonl")
    crystallized_sessions = read_jsonl(state_root / "crystallized-sessions.jsonl")
    return {
        "workspace": str(root),
        "layout": layout.kind,
        "state_dir": relative_or_absolute(state_root, root),
        "context_sources": [
            {"name": source.name, "kind": source.kind, "root": str(source.root), "mode": source.mode}
            for source in context_sources
        ],
        "task_status_counts": dict(Counter(entry.get("status", "unknown") for entry in recent_tasks)),
        "finalization_counts": dict(finalization_counts),
        "recent_tasks": recent_tasks[-5:],
        "recent_failed_commands": failed_commands[-5:],
        "recent_checkpoints": checkpoints[-5:],
        "draft_assessments": draft_assessments[-5:],
        "recent_memory_operations": memory_operations[-5:],
        "recent_crystallized_sessions": crystallized_sessions[-5:],
        "memory_review_queue_count": memory_review_queue_count_for(root),
        "session_card": build_session_card_payload(root),
    }


def build_state_snapshot_payload(root: Path, task_id: str | None = None) -> dict:
    state_root = state_root_for(root)
    tasks = latest_tasks(read_jsonl(state_root / "task-ledger.jsonl"))
    target = None
    if task_id:
        target = next((task for task in tasks if task.get("task_id") == task_id), None)
    elif tasks:
        target = next((task for task in reversed(tasks) if task.get("state_snapshot")), None)
    snapshot_meta = (target or {}).get("state_snapshot") or {}
    snapshot_path = None
    if snapshot_meta.get("path"):
        snapshot_path = root / snapshot_meta["path"]
    elif not task_id:
        snapshot_path = latest_snapshot_path(state_root)
    content = None
    if snapshot_path and snapshot_path.exists():
        try:
            content = snapshot_path.read_text(encoding="utf-8")
        except OSError as exc:
            _warn_parse_failure(snapshot_path, str(exc))
    return {
        "workspace": str(root),
        "task": target,
        "snapshot": snapshot_meta or None,
        "snapshot_path": str(snapshot_path) if snapshot_path else None,
        "content": content,
    }

ASCII_BANNER = r"""
██╗  ██╗███████╗██╗     ███╗   ███╗
██║  ██║██╔════╝██║     ████╗ ████║
███████║█████╗  ██║     ██╔████╔██║
██╔══██║██╔══╝  ██║     ██║╚██╔╝██║
██║  ██║███████╗███████╗██║ ╚═╝ ██║
╚═╝  ╚═╝╚══════╝╚══════╝╚═╝     ╚═╝

                   stability-first agent operations
"""

HELM_PRIMARY = "\033[38;2;230;236;244m"
HELM_ACCENT = "\033[38;2;105;162;255m"
HELM_MUTED = "\033[38;2;137;161;196m"
ANSI_RESET = "\033[0m"


def color_enabled() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    term = os.environ.get("TERM", "")
    return sys.stdout.isatty() and term.lower() != "dumb"


def render_banner() -> str:
    if not color_enabled():
        return ASCII_BANNER
    lines = ASCII_BANNER.splitlines()
    rendered: list[str] = []
    for line in lines:
        if not line.strip():
            rendered.append(line)
            continue
        if "stability-first" in line:
            rendered.append(f"{HELM_MUTED}{line}{ANSI_RESET}")
            continue
        if "████" in line or "██" in line:
            midpoint = max(1, len(line) // 2)
            left = line[:midpoint]
            right = line[midpoint:]
            rendered.append(f"{HELM_PRIMARY}{left}{HELM_ACCENT}{right}{ANSI_RESET}")
            continue
        rendered.append(f"{HELM_PRIMARY}{line}{ANSI_RESET}")
    return "\n".join(rendered)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=render_banner() + "\nHelm CLI for stability-first agent operations.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    detect = subparsers.add_parser("detect", help="Detect the workspace layout at a path.")
    detect.add_argument("--path", help="Workspace path to inspect. Defaults to the current directory.")
    detect.add_argument("--json", action="store_true")
    detect.set_defaults(func=cmd_detect)

    init = subparsers.add_parser("init", help="Initialize a Helm-native workspace.")
    init.add_argument("--path", help=f"Workspace path to initialize. Defaults to {DEFAULT_WORKSPACE}.")
    init.add_argument("--force", action="store_true", help="Overwrite reference files and MEMORY.md if they already exist.")
    init.add_argument("--json", action="store_true")
    init.set_defaults(func=cmd_init)

    doctor = subparsers.add_parser("doctor", help="Validate Helm workspace structure and references.")
    doctor.add_argument("--path", help="Workspace path to inspect. Defaults to the current directory.")
    doctor.add_argument("--json", action="store_true")
    doctor.set_defaults(func=cmd_doctor)

    survey = subparsers.add_parser("survey", help="Show onboarding guidance for external runtimes and note vaults.")
    survey.add_argument("--path", help=f"Helm workspace path. Defaults to {DEFAULT_WORKSPACE}.")
    survey.add_argument("--json", action="store_true")
    survey.set_defaults(func=cmd_survey)

    validate = subparsers.add_parser("validate", help="Validate execution profiles and skill policy consistency.")
    validate.add_argument("--path", help="Workspace path to inspect. Defaults to the current directory.")
    validate.add_argument("--json", action="store_true")
    validate.set_defaults(func=cmd_validate)

    status = subparsers.add_parser("status", help="Summarize recent Helm operational state.")
    status.add_argument("--path", help="Workspace path to inspect. Defaults to the current directory.")
    status.add_argument("--json", action="store_true")
    status.add_argument("--verbose", action="store_true")
    status.set_defaults(func=cmd_status)

    run_contract = subparsers.add_parser("run-contract", help="Show the latest run contract snapshot or one task's execution contract.")
    run_contract.add_argument("--path", help="Workspace path to inspect. Defaults to the current directory.")
    run_contract.add_argument("--task-id", help="Specific task id to inspect. Defaults to the latest task.")
    run_contract.add_argument("--json", action="store_true")
    run_contract.set_defaults(func=cmd_run_contract)

    capability_diff = subparsers.add_parser("capability-diff", help="Compare recent run capabilities across two task snapshots.")
    capability_diff.add_argument("--path", help="Workspace path to inspect. Defaults to the current directory.")
    capability_diff.add_argument("--older-task-id", help="Older task id to compare.")
    capability_diff.add_argument("--newer-task-id", help="Newer task id to compare.")
    capability_diff.add_argument("--json", action="store_true")
    capability_diff.set_defaults(func=cmd_capability_diff)

    adopt = subparsers.add_parser("adopt", help="Register an external workspace as a read-only context source.")
    adopt.add_argument("--path", help=f"Helm workspace path. Defaults to {DEFAULT_WORKSPACE}.")
    adopt.add_argument("--from-path", required=True, help="External workspace root to adopt as a context source.")
    adopt.add_argument("--name", help="Stable source name inside Helm.")
    adopt.add_argument("--kind", choices=["openclaw", "hermes", "generic"], help="Override the detected source kind.")
    adopt.add_argument("--json", action="store_true")
    adopt.set_defaults(func=cmd_adopt)

    onboard = subparsers.add_parser("onboard", help="Guide and optionally apply onboarding actions for external runtimes and note vaults.")
    onboard.add_argument("--path", help=f"Helm workspace path. Defaults to {DEFAULT_WORKSPACE}.")
    onboard.add_argument("--use-detected", action="store_true", help="Prepare onboarding actions from auto-detected candidates.")
    onboard.add_argument("--adopt-openclaw", help="Explicit OpenClaw workspace path to adopt read-only.")
    onboard.add_argument("--adopt-hermes", help="Explicit Hermes workspace path to adopt read-only.")
    onboard.add_argument("--adopt-obsidian", help="Explicit Obsidian vault or Markdown notes root to adopt read-only.")
    onboard.add_argument("--dry-run", action="store_true", help="Print the onboarding plan without applying it.")
    onboard.add_argument("--skip-checks", action="store_true", help="Do not run doctor, validate, and status after applying the onboarding plan.")
    onboard.add_argument("--json", action="store_true")
    onboard.set_defaults(func=cmd_onboard)

    sources = subparsers.add_parser("sources", help="List adopted external context sources and migration notes.")
    sources.add_argument("--path", help="Helm workspace path. Defaults to the current directory.")
    sources.add_argument("--json", action="store_true")
    sources.set_defaults(func=cmd_sources)

    profile = subparsers.add_parser("profile", help="Work with execution profiles and profiled runs.")
    profile.add_argument("--path", help="Workspace path to target.")
    profile.add_argument("args", nargs=argparse.REMAINDER)
    profile.set_defaults(func=cmd_profile)

    context = subparsers.add_parser("context", help="Query Helm memory, task, command, and checkpoint state.")
    context.add_argument("--path", help="Workspace path to target.")
    context.add_argument("args", nargs=argparse.REMAINDER)
    context.set_defaults(func=cmd_context)

    checkpoint = subparsers.add_parser("checkpoint", help="Create, inspect, restore, and recommend checkpoints.")
    checkpoint_subparsers = checkpoint.add_subparsers(dest="checkpoint_command", required=True)

    checkpoint_list = checkpoint_subparsers.add_parser("list", help="List recent checkpoints.")
    checkpoint_list.add_argument("--path", help="Workspace path to inspect. Defaults to the current directory.")
    checkpoint_list.add_argument("--limit", type=int, default=20)
    checkpoint_list.add_argument("--json", action="store_true")
    checkpoint_list.set_defaults(func=cmd_checkpoint_list)

    checkpoint_show = checkpoint_subparsers.add_parser("show", help="Show checkpoint metadata.")
    checkpoint_show.add_argument("checkpoint_id")
    checkpoint_show.add_argument("--path", help="Workspace path to inspect. Defaults to the current directory.")
    checkpoint_show.add_argument("--json", action="store_true")
    checkpoint_show.set_defaults(func=cmd_checkpoint_show)

    checkpoint_preview = checkpoint_subparsers.add_parser("preview", help="Preview files inside a checkpoint archive.")
    checkpoint_preview.add_argument("checkpoint_id")
    checkpoint_preview.add_argument("--path", help="Workspace path to inspect. Defaults to the current directory.")
    checkpoint_preview.set_defaults(func=cmd_checkpoint_preview)

    checkpoint_restore = checkpoint_subparsers.add_parser("restore", help="Restore files from a checkpoint archive.")
    checkpoint_restore.add_argument("checkpoint_id")
    checkpoint_restore.add_argument("--path", help="Workspace path to inspect. Defaults to the current directory.")
    checkpoint_restore.set_defaults(func=cmd_checkpoint_restore)

    checkpoint_create = checkpoint_subparsers.add_parser("create", help="Create a checkpoint for one or more paths.")
    checkpoint_create.add_argument("--path", dest="path", help="Workspace path to target. Defaults to the current directory.")
    checkpoint_create.add_argument("--label", required=True, help="Short checkpoint label.")
    checkpoint_create.add_argument("--include", action="append", required=True, help="Workspace-relative path to include. Repeatable.")
    checkpoint_create.set_defaults(func=cmd_checkpoint_create)

    checkpoint_recommend_sub = checkpoint_subparsers.add_parser("recommend", help="Recommend the checkpoint to use for a risky task.")
    checkpoint_recommend_sub.add_argument("--path", help="Workspace path to inspect. Defaults to the current directory.")
    checkpoint_recommend_sub.add_argument("--task-id", help="Specific task id to inspect. Defaults to the latest risky task.")
    checkpoint_recommend_sub.add_argument("--json", action="store_true")
    checkpoint_recommend_sub.set_defaults(func=cmd_checkpoint_recommend)

    checkpoint_finalize = checkpoint_subparsers.add_parser("finalize", help="Inspect finalization state together with the recommended checkpoint.")
    checkpoint_finalize.add_argument("--path", help="Workspace path to inspect. Defaults to the current directory.")
    checkpoint_finalize.add_argument("--task-id", help="Specific task id to inspect. Defaults to the latest risky task when applicable.")
    checkpoint_finalize.add_argument("--json", action="store_true")
    checkpoint_finalize.set_defaults(func=cmd_checkpoint_finalize)

    checkpoint_recommend = subparsers.add_parser("checkpoint-recommend", help="Recommend the checkpoint to use for a risky task.")
    checkpoint_recommend.add_argument("--path", help="Workspace path to inspect. Defaults to the current directory.")
    checkpoint_recommend.add_argument("--task-id", help="Specific task id to inspect. Defaults to the latest risky task.")
    checkpoint_recommend.add_argument("--json", action="store_true")
    checkpoint_recommend.set_defaults(func=cmd_checkpoint_recommend)

    skill = subparsers.add_parser("skill", help="Create and promote Helm skills.")
    skill.add_argument("--path", help="Workspace path to target.")
    skill.add_argument("args", nargs=argparse.REMAINDER)
    skill.set_defaults(func=cmd_skill)

    skill_diff = subparsers.add_parser("skill-diff", help="Show the diff between a draft skill and the live skill, if any.")
    skill_diff.add_argument("--path", help="Workspace path to inspect. Defaults to the current directory.")
    skill_diff.add_argument("--name", required=True, help="Draft skill slug under skill_drafts/.")
    skill_diff.add_argument("--json", action="store_true")
    skill_diff.set_defaults(func=cmd_skill_diff)

    skill_review = subparsers.add_parser("skill-review", help="Alias for reviewing a draft skill diff.")
    skill_review.add_argument("--path", help="Workspace path to inspect. Defaults to the current directory.")
    skill_review.add_argument("--name", required=True, help="Draft skill slug under skill_drafts/.")
    skill_review.add_argument("--json", action="store_true")
    skill_review.set_defaults(func=cmd_skill_review)

    skill_approve = subparsers.add_parser("skill-approve", help="Approve and promote a draft skill.")
    skill_approve.add_argument("--path", help="Workspace path to inspect. Defaults to the current directory.")
    skill_approve.add_argument("--name", required=True, help="Draft skill slug under skill_drafts/.")
    skill_approve.add_argument("--dry-run", action="store_true")
    skill_approve.set_defaults(func=cmd_skill_approve)

    skill_reject = subparsers.add_parser("skill-reject", help="Reject a draft skill and store the rejection reason.")
    skill_reject.add_argument("--path", help="Workspace path to inspect. Defaults to the current directory.")
    skill_reject.add_argument("--name", required=True, help="Draft skill slug under skill_drafts/.")
    skill_reject.add_argument("--reason", required=True, help="Short rejection reason.")
    skill_reject.add_argument("--json", action="store_true")
    skill_reject.set_defaults(func=cmd_skill_reject)

    ops = subparsers.add_parser("ops", help="Inspect daily, task, and command reports.")
    ops.add_argument("--path", help="Workspace path to target.")
    ops.add_argument("args", nargs=argparse.REMAINDER)
    ops.set_defaults(func=cmd_ops)

    memory = subparsers.add_parser("memory", help="Inspect finalization-driven durable memory work queues.")
    memory.add_argument("--path", help="Workspace path to target.")
    memory.add_argument("args", nargs=argparse.REMAINDER)
    memory.set_defaults(func=cmd_memory)

    harness = subparsers.add_parser("harness", help="Run adaptive harness preflight and guarded execution flows.")
    harness.add_argument("--path", help="Workspace path to target.")
    harness.add_argument("args", nargs=argparse.REMAINDER)
    harness.set_defaults(func=cmd_harness)

    report = subparsers.add_parser("report", help="Produce a high-level Helm operations report.")
    report.add_argument("--path", help="Workspace path to inspect. Defaults to the current directory.")
    report.add_argument("--limit", type=int, default=20)
    report.add_argument("--format", choices=["text", "json", "markdown"], default="text")
    report.set_defaults(func=cmd_report)

    db = subparsers.add_parser("db", help="Manage the SQLite operations index.")
    db_subparsers = db.add_subparsers(dest="db_command", required=True)

    db_init = db_subparsers.add_parser("init", help="Initialize the SQLite operations index.")
    db_init.add_argument("--path", help="Workspace path.")
    db_init.set_defaults(func=cmd_db_init)

    db_rebuild = db_subparsers.add_parser("rebuild", help="Rebuild index from JSONL source files.")
    db_rebuild.add_argument("--path", help="Workspace path.")
    db_rebuild.add_argument("--json", action="store_true")
    db_rebuild.set_defaults(func=cmd_db_rebuild)

    db_verify = db_subparsers.add_parser("verify", help="Compare JSONL and SQLite counts for drift.")
    db_verify.add_argument("--path", help="Workspace path.")
    db_verify.add_argument("--json", action="store_true")
    db_verify.set_defaults(func=cmd_db_verify)

    db_status = db_subparsers.add_parser("status", help="Show SQLite index status.")
    db_status.add_argument("--path", help="Workspace path.")
    db_status.add_argument("--json", action="store_true")
    db_status.set_defaults(func=cmd_db_status)

    return parser


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]

    passthrough = {
        "profile": cmd_profile,
        "context": cmd_context,
        "skill": cmd_skill,
        "ops": cmd_ops,
        "memory": cmd_memory,
        "harness": cmd_harness,
    }
    if argv and argv[0] in passthrough:
        command = argv[0]
        workspace: str | None = None
        forwarded: list[str] = []
        idx = 1
        while idx < len(argv):
            token = argv[idx]
            if token == "--path":
                if idx + 1 >= len(argv):
                    raise SystemExit("--path requires a value")
                workspace = argv[idx + 1]
                idx += 2
                continue
            forwarded.append(token)
            idx += 1
        args = argparse.Namespace(path=workspace, args=forwarded)
        return passthrough[command](args)

    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
