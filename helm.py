from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from helm_workspace import DEFAULT_WORKSPACE

from commands.checkpoint import (
    cmd_checkpoint_create,
    cmd_checkpoint_finalize,
    cmd_checkpoint_list,
    cmd_checkpoint_preview,
    cmd_checkpoint_prune,
    cmd_checkpoint_protect,
    cmd_checkpoint_policy,
    cmd_checkpoint_recommend,
    cmd_checkpoint_restore,
    cmd_checkpoint_show,
)
from commands.context import (
    build_state_snapshot_payload,
    cmd_adopt,
    cmd_context,
    cmd_onboard,
    cmd_sources,
)
from commands.doctor import cmd_doctor, cmd_survey
from commands.harness import cmd_harness
from commands.health import cmd_health
from commands.memory import cmd_memory
from commands.ops import cmd_ops
from commands.privacy import cmd_privacy
from commands.profile import cmd_profile
from commands.skill import cmd_skill, cmd_skill_approve, cmd_skill_diff, cmd_skill_reject, cmd_skill_review
from commands.skill_lifecycle import (
    cmd_skill_lifecycle_archive,
    cmd_skill_lifecycle_events,
    cmd_skill_lifecycle_ledger,
    cmd_skill_lifecycle_negative_claims,
    cmd_skill_lifecycle_observe,
    cmd_skill_lifecycle_outcome_candidates,
    cmd_skill_lifecycle_outcome_report,
    cmd_skill_lifecycle_pin,
    cmd_skill_lifecycle_promote_from_trajectory,
    cmd_skill_lifecycle_report,
    cmd_skill_lifecycle_revalidate_claim,
    cmd_skill_lifecycle_restore,
    cmd_skill_lifecycle_revalidation_due,
    cmd_skill_lifecycle_scan,
    cmd_skill_lifecycle_selection_stats,
    cmd_skill_lifecycle_stale,
    cmd_skill_lifecycle_status,
    cmd_skill_lifecycle_umbrella,
    cmd_skill_lifecycle_unpin,
    cmd_skill_lifecycle_view,
)
from commands.status import (
    build_status_payload,
    cmd_capability_diff,
    cmd_dashboard,
    cmd_detect,
    cmd_init,
    cmd_report,
    cmd_run_contract,
    cmd_status,
    format_report_markdown,
)
from commands.task import (
    cmd_task_block,
    cmd_task_complete,
    cmd_task_doctor,
    cmd_task_list,
    cmd_task_mark_stale,
    cmd_task_reclaim,
    cmd_task_retry,
    cmd_task_show,
)
from commands.validate import cmd_validate
from commands.db import cmd_db_init, cmd_db_rebuild, cmd_db_verify, cmd_db_status, cmd_db_query
from commands.skill_promotion import cmd_skill_promotion
from commands.shadow_report import cmd_shadow_report
from commands.phase_modules import (
    cmd_action_scope_evaluate,
    cmd_compression_profiles,
    cmd_freshness_status,
    cmd_frontmatter_validate,
    cmd_memory_tree_status,
    cmd_state_lint,
)


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


def cmd_dci(args: argparse.Namespace) -> int:
    forwarded: list[str] = []
    forwarded.extend(args.query or [])
    for item in args.include or []:
        forwarded.extend(["--include", item])
    if args.adapter:
        forwarded.extend(["--adapter", args.adapter])
    if args.mode:
        forwarded.extend(["--mode", args.mode])
    if args.since:
        forwarded.extend(["--since", args.since])
    if args.entity:
        forwarded.extend(["--entity", args.entity])
    if args.task_id:
        forwarded.extend(["--task-id", args.task_id])
    if args.limit is not None:
        forwarded.extend(["--limit", str(args.limit)])
    for flag in ("json", "summary", "failed_only", "latest_tasks", "explain_ranking"):
        if getattr(args, flag):
            forwarded.append("--" + flag.replace("_", "-"))
    return cmd_context(argparse.Namespace(path=args.path, args=forwarded))


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
    doctor.add_argument("--skip-discovery", action="store_true", help="Skip provider and hardware discovery (faster).")
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
    status.add_argument("--brief", action="store_true", help="Print a compact health-oriented status summary.")
    status.add_argument("--public", action="store_true", help="Redact local paths and captured command output from status payloads.")
    status.set_defaults(func=cmd_status)

    dashboard = subparsers.add_parser("dashboard", help="Show a compact local operations dashboard.")
    dashboard.add_argument("--path", help="Workspace path to inspect. Defaults to the current directory.")
    dashboard.add_argument("--json", action="store_true")
    dashboard.set_defaults(func=cmd_dashboard)

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

    dci = subparsers.add_parser("dci", help="Direct corpus interaction query alias with common options exposed.")
    dci.add_argument("query", nargs="*")
    dci.add_argument("--path", help="Workspace path to target.")
    dci.add_argument("--include", action="append")
    dci.add_argument("--adapter")
    dci.add_argument("--mode")
    dci.add_argument("--since")
    dci.add_argument("--entity")
    dci.add_argument("--task-id")
    dci.add_argument("--limit", type=int)
    dci.add_argument("--json", action="store_true")
    dci.add_argument("--summary", action="store_true")
    dci.add_argument("--failed-only", action="store_true")
    dci.add_argument("--latest-tasks", action="store_true")
    dci.add_argument("--explain-ranking", action="store_true")
    dci.set_defaults(func=cmd_dci)

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

    checkpoint_prune = checkpoint_subparsers.add_parser("prune", help="Plan or apply checkpoint retention pruning.")
    checkpoint_prune.add_argument("--path", help="Workspace path to inspect. Defaults to the current directory.")
    checkpoint_prune.add_argument("--keep-recent", type=int, help="Always keep the newest N checkpoints. Defaults to checkpoint policy.")
    checkpoint_prune.add_argument("--keep-days", type=int, help="Keep checkpoints newer than this many days. Defaults to checkpoint policy.")
    checkpoint_prune.add_argument("--max-total-mb", type=int, help="Prune additional unprotected checkpoints until archives fit under this size. Defaults to checkpoint policy.")
    checkpoint_prune.add_argument("--apply", action="store_true", help="Delete pruned checkpoint archives and update the index.")
    checkpoint_prune.add_argument("--json", action="store_true")
    checkpoint_prune.set_defaults(func=cmd_checkpoint_prune)

    checkpoint_protect = checkpoint_subparsers.add_parser("protect", help="Pin or unpin a checkpoint for retention.")
    checkpoint_protect.add_argument("checkpoint_id")
    checkpoint_protect.add_argument("--path", help="Workspace path to inspect. Defaults to the current directory.")
    checkpoint_protect.add_argument("--unprotect", action="store_true")
    checkpoint_protect.set_defaults(func=cmd_checkpoint_protect)

    checkpoint_policy_sub = checkpoint_subparsers.add_parser("policy", help="Show checkpoint retention policy defaults or references/checkpoint_policy.json.")
    checkpoint_policy_sub.add_argument("--path", help="Workspace path to inspect. Defaults to the current directory.")
    checkpoint_policy_sub.add_argument("--json", action="store_true")
    checkpoint_policy_sub.set_defaults(func=cmd_checkpoint_policy)

    checkpoint_recommend = subparsers.add_parser("checkpoint-recommend", help="Recommend the checkpoint to use for a risky task.")
    checkpoint_recommend.add_argument("--path", help="Workspace path to inspect. Defaults to the current directory.")
    checkpoint_recommend.add_argument("--task-id", help="Specific task id to inspect. Defaults to the latest risky task.")
    checkpoint_recommend.add_argument("--json", action="store_true")
    checkpoint_recommend.set_defaults(func=cmd_checkpoint_recommend)

    task = subparsers.add_parser("task", help="Inspect and append task state transitions.")
    task_subparsers = task.add_subparsers(dest="task_command", required=True)

    task_list = task_subparsers.add_parser("list", help="List latest task states.")
    task_list.add_argument("--path", help="Workspace path to inspect. Defaults to the current directory.")
    task_list.add_argument("--status", help="Filter by task status.")
    task_list.add_argument("--profile", help="Filter by execution profile.")
    task_list.add_argument("--skill", help="Filter by skill id.")
    task_list.add_argument("--limit", type=int, default=20)
    task_list.add_argument("--json", action="store_true")
    task_list.set_defaults(func=cmd_task_list)

    task_show = task_subparsers.add_parser("show", help="Show the latest state for a task.")
    task_show.add_argument("task_id")
    task_show.add_argument("--path", help="Workspace path to inspect. Defaults to the current directory.")
    task_show.add_argument("--json", action="store_true")
    task_show.set_defaults(func=cmd_task_show)

    task_block = task_subparsers.add_parser("block", help="Append a blocked state for a task.")
    task_block.add_argument("task_id")
    task_block.add_argument("--path", help="Workspace path to target. Defaults to the current directory.")
    task_block.add_argument("--reason", required=True)
    task_block.add_argument("--stage", default="manual")
    task_block.add_argument("--next-action")
    task_block.set_defaults(func=cmd_task_block)

    task_complete = task_subparsers.add_parser("complete", help="Append a completed state with explicit evidence.")
    task_complete.add_argument("task_id")
    task_complete.add_argument("--path", help="Workspace path to target. Defaults to the current directory.")
    task_complete.add_argument("--evidence", action="append", required=True)
    task_complete.add_argument("--next-action")
    task_complete.set_defaults(func=cmd_task_complete)

    task_retry = task_subparsers.add_parser("retry", help="Create a ready retry task from an existing task.")
    task_retry.add_argument("task_id")
    task_retry.add_argument("--path", help="Workspace path to target. Defaults to the current directory.")
    task_retry.add_argument("--reason")
    task_retry.add_argument("--new-task-id")
    task_retry.set_defaults(func=cmd_task_retry)

    task_mark_stale = task_subparsers.add_parser("mark-stale", help="Append a stale state for a stuck active task.")
    task_mark_stale.add_argument("task_id")
    task_mark_stale.add_argument("--path", help="Workspace path to target. Defaults to the current directory.")
    task_mark_stale.add_argument("--reason", required=True)
    task_mark_stale.add_argument("--stage", default="stale")
    task_mark_stale.add_argument("--next-action")
    task_mark_stale.set_defaults(func=cmd_task_mark_stale)

    task_reclaim = task_subparsers.add_parser("reclaim", help="Append a ready state for a stale or blocked task.")
    task_reclaim.add_argument("task_id")
    task_reclaim.add_argument("--path", help="Workspace path to target. Defaults to the current directory.")
    task_reclaim.add_argument("--reason", required=True)
    task_reclaim.add_argument("--owner-session-id")
    task_reclaim.add_argument("--next-action")
    task_reclaim.set_defaults(func=cmd_task_reclaim)

    task_doctor = task_subparsers.add_parser("doctor", help="Detect stale or inconsistent task states.")
    task_doctor.add_argument("--path", help="Workspace path to inspect. Defaults to the current directory.")
    task_doctor.add_argument("--stale-minutes", type=int, default=120)
    task_doctor.add_argument("--json", action="store_true")
    task_doctor.set_defaults(func=cmd_task_doctor)

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

    skill_lifecycle = subparsers.add_parser(
        "skill-lifecycle",
        aliases=["curator"],
        help="Track, report on, and curate skill lifecycle state without modifying SKILL.md.",
    )
    skill_lifecycle_subparsers = skill_lifecycle.add_subparsers(dest="skill_lifecycle_command", required=True)

    sl_scan = skill_lifecycle_subparsers.add_parser("scan", help="Reconcile usage.json with skills/ on disk.")
    sl_scan.add_argument("--path", help="Workspace path to target.")
    sl_scan.add_argument("--dry-run", action="store_true", help="Preview changes without writing usage.json.")
    sl_scan.add_argument("--json", action="store_true")
    sl_scan.set_defaults(func=cmd_skill_lifecycle_scan)

    sl_status = skill_lifecycle_subparsers.add_parser("status", help="Print a lifecycle summary.")
    sl_status.add_argument("--path", help="Workspace path to target.")
    sl_status.add_argument("--json", action="store_true")
    sl_status.set_defaults(func=cmd_skill_lifecycle_status)

    sl_report = skill_lifecycle_subparsers.add_parser("report", help="Produce a markdown or JSON lifecycle report.")
    sl_report.add_argument("--path", help="Workspace path to target.")
    sl_report.add_argument("--format", choices=["markdown", "json"], default="markdown")
    sl_report.add_argument("--out", help="Write the report to this file instead of stdout.")
    sl_report.set_defaults(func=cmd_skill_lifecycle_report)

    sl_pin = skill_lifecycle_subparsers.add_parser("pin", help="Mark a skill as pinned (protected from auto stale/archive).")
    sl_pin.add_argument("--path", help="Workspace path to target.")
    sl_pin.add_argument("skill", help="Skill id to pin.")
    sl_pin.set_defaults(func=cmd_skill_lifecycle_pin)

    sl_unpin = skill_lifecycle_subparsers.add_parser("unpin", help="Remove the pinned flag from a skill.")
    sl_unpin.add_argument("--path", help="Workspace path to target.")
    sl_unpin.add_argument("skill", help="Skill id to unpin.")
    sl_unpin.set_defaults(func=cmd_skill_lifecycle_unpin)

    sl_stale = skill_lifecycle_subparsers.add_parser("stale", help="List or apply stale-state transitions per policy.")
    sl_stale.add_argument("--path", help="Workspace path to target.")
    sl_stale.add_argument("--apply", action="store_true", help="Apply transitions. Defaults to dry-run.")
    sl_stale.add_argument("--json", action="store_true")
    sl_stale.set_defaults(func=cmd_skill_lifecycle_stale)

    sl_archive = skill_lifecycle_subparsers.add_parser("archive", help="Move a skill into skills/.archive/ (defaults to dry-run).")
    sl_archive.add_argument("--path", help="Workspace path to target.")
    sl_archive.add_argument("--apply", action="store_true", help="Apply the archive move. Defaults to dry-run.")
    sl_archive.add_argument("skill", help="Skill id to archive.")
    sl_archive.set_defaults(func=cmd_skill_lifecycle_archive)

    sl_restore = skill_lifecycle_subparsers.add_parser("restore", help="Move a skill out of skills/.archive/ back to skills/ (defaults to dry-run).")
    sl_restore.add_argument("--path", help="Workspace path to target.")
    sl_restore.add_argument("--apply", action="store_true", help="Apply the restore move. Defaults to dry-run.")
    sl_restore.add_argument("skill", help="Skill id to restore.")
    sl_restore.set_defaults(func=cmd_skill_lifecycle_restore)

    sl_events = skill_lifecycle_subparsers.add_parser("events", help="Print the lifecycle event log.")
    sl_events.add_argument("--path", help="Workspace path to target.")
    sl_events.add_argument("--skill", help="Filter by skill id.")
    sl_events.add_argument("--limit", type=int, default=None, help="Show only the last N events.")
    sl_events.add_argument("--json", action="store_true")
    sl_events.set_defaults(func=cmd_skill_lifecycle_events)

    sl_neg = skill_lifecycle_subparsers.add_parser("negative-claims", help="Scan SKILL.md for negative claim candidates.")
    sl_neg.add_argument("--path", help="Workspace path to target.")
    sl_neg.add_argument("--json", action="store_true")
    sl_neg.add_argument("--persist", action="store_true", help="Write detected claims into per-skill metadata (preserves prior status fields).")
    sl_neg.add_argument("--ttl-days", type=int, default=30, help="ttl_days field on newly persisted claims (default 30).")
    sl_neg.add_argument("--confidence", type=float, default=0.6, help="confidence field on newly persisted claims (default 0.6).")
    sl_neg.set_defaults(func=cmd_skill_lifecycle_negative_claims)

    sl_umb = skill_lifecycle_subparsers.add_parser("umbrella", help="Surface umbrella consolidation candidates.")
    sl_umb.add_argument("--path", help="Workspace path to target.")
    sl_umb.add_argument("--min-cluster-size", type=int, default=3, help="Minimum number of skills sharing a token to count as a cluster.")
    sl_umb.add_argument("--json", action="store_true")
    sl_umb.set_defaults(func=cmd_skill_lifecycle_umbrella)

    sl_ledger = skill_lifecycle_subparsers.add_parser("ledger", help="Show lifecycle events joined with the task ledger.")
    sl_ledger.add_argument("--path", help="Workspace path to target.")
    sl_ledger.add_argument("--skill", help="Filter by skill id.")
    sl_ledger.add_argument("--limit", type=int, default=None, help="Show only the last N events.")
    sl_ledger.add_argument("--json", action="store_true")
    sl_ledger.set_defaults(func=cmd_skill_lifecycle_ledger)

    sl_outcome_report = skill_lifecycle_subparsers.add_parser("outcome-report", help="Summarize skill outcome metadata v2.")
    sl_outcome_report.add_argument("--path", help="Workspace path to target.")
    sl_outcome_report.add_argument("--json", action="store_true")
    sl_outcome_report.set_defaults(func=cmd_skill_lifecycle_outcome_report)

    sl_outcome_candidates = skill_lifecycle_subparsers.add_parser("outcome-candidates", help="List skill outcome improvement candidates.")
    sl_outcome_candidates.add_argument("--path", help="Workspace path to target.")
    sl_outcome_candidates.add_argument("--limit", type=int, default=None)
    sl_outcome_candidates.add_argument("--json", action="store_true")
    sl_outcome_candidates.set_defaults(func=cmd_skill_lifecycle_outcome_candidates)

    sl_selection_stats = skill_lifecycle_subparsers.add_parser("selection-stats", help="Summarize skill selection reasons and evidence quality.")
    sl_selection_stats.add_argument("--path", help="Workspace path to target.")
    sl_selection_stats.add_argument("--skill", help="Filter by skill id.")
    sl_selection_stats.add_argument("--limit", type=int, default=None)
    sl_selection_stats.add_argument("--json", action="store_true")
    sl_selection_stats.set_defaults(func=cmd_skill_lifecycle_selection_stats)

    sl_promote_from_trajectory = skill_lifecycle_subparsers.add_parser(
        "promote-from-trajectory",
        help="Create a review-only skill draft from an outcome trajectory candidate.",
    )
    sl_promote_from_trajectory.add_argument("--path", help="Workspace path to target.")
    sl_promote_from_trajectory.add_argument("--task-id", help="Specific candidate task id. Defaults to latest candidate.")
    sl_promote_from_trajectory.add_argument("--name", required=True, help="Draft skill slug.")
    sl_promote_from_trajectory.add_argument("--description", required=True, help="One-line draft skill description.")
    sl_promote_from_trajectory.add_argument("--limit", type=int, default=None)
    sl_promote_from_trajectory.add_argument("--apply", action="store_true", help="Create the draft. Defaults to dry-run.")
    sl_promote_from_trajectory.add_argument("--json", action="store_true")
    sl_promote_from_trajectory.set_defaults(func=cmd_skill_lifecycle_promote_from_trajectory)

    sl_observe = skill_lifecycle_subparsers.add_parser("observe", help="Poll SKILL.md mtime/atime to record skill_patched and skill_viewed events.")
    sl_observe.add_argument("--path", help="Workspace path to target.")
    sl_observe.add_argument("--dry-run", action="store_true", help="Preview without writing.")
    sl_observe.add_argument("--json", action="store_true")
    sl_observe.set_defaults(func=cmd_skill_lifecycle_observe)

    sl_view = skill_lifecycle_subparsers.add_parser("view", help="Manually record a skill_viewed event (atime-independent).")
    sl_view.add_argument("--path", help="Workspace path to target.")
    sl_view.add_argument("skill", help="Skill id whose view to record.")
    sl_view.set_defaults(func=cmd_skill_lifecycle_view)

    sl_revalidate = skill_lifecycle_subparsers.add_parser(
        "revalidation-due",
        help="List persisted negative claims past their TTL window and need revalidation.",
    )
    sl_revalidate.add_argument("--path", help="Workspace path to target.")
    sl_revalidate.add_argument("--json", action="store_true")
    sl_revalidate.set_defaults(func=cmd_skill_lifecycle_revalidation_due)

    sl_revalidate_claim = skill_lifecycle_subparsers.add_parser(
        "revalidate-claim",
        help="Record or run a safe probe for a persisted negative claim.",
    )
    sl_revalidate_claim.add_argument("--path", help="Workspace path to target.")
    sl_revalidate_claim.add_argument("--skill", required=True, help="Skill id containing the claim.")
    sl_revalidate_claim.add_argument("--claim-id", required=True, help="Persisted claim_id to update.")
    sl_revalidate_claim.add_argument(
        "--status",
        choices=["needs_review", "still_valid", "resolved"],
        default="needs_review",
        help="Manual revalidation status when --probe is not used.",
    )
    sl_revalidate_claim.add_argument("--note", help="Optional manual revalidation note.")
    sl_revalidate_claim.add_argument(
        "--probe-command",
        help="Attach or replace the claim's probe_command before recording or running it.",
    )
    sl_revalidate_claim.add_argument(
        "--probe",
        action="store_true",
        help="Run the claim's probe_command if it matches negative_claim_safe_probe_prefixes.",
    )
    sl_revalidate_claim.add_argument("--timeout", type=int, default=30, help="Probe timeout in seconds.")
    sl_revalidate_claim.add_argument("--json", action="store_true")
    sl_revalidate_claim.set_defaults(func=cmd_skill_lifecycle_revalidate_claim)

    ops = subparsers.add_parser("ops", help="Inspect daily, task, and command reports.")
    ops.add_argument("--path", help="Workspace path to target.")
    ops.add_argument("args", nargs=argparse.REMAINDER)
    ops.set_defaults(func=cmd_ops)

    memory = subparsers.add_parser("memory", help="Inspect finalization-driven durable memory work queues.")
    memory.add_argument("--path", help="Workspace path to target.")
    memory.add_argument("args", nargs=argparse.REMAINDER)
    memory.set_defaults(func=cmd_memory)

    privacy = subparsers.add_parser("privacy", help="Scan, tokenize, and restore private text at agent/tool boundaries.")
    privacy.add_argument("--path", help="Workspace path to target.")
    privacy.add_argument("args", nargs=argparse.REMAINDER)
    privacy.set_defaults(func=cmd_privacy)

    health = subparsers.add_parser("health", help="Probe runtime model health and choose fallback candidates.")
    health.add_argument("--path", help="Workspace path to target.")
    health.add_argument("args", nargs=argparse.REMAINDER)
    health.set_defaults(func=cmd_health)

    harness = subparsers.add_parser("harness", help="Run adaptive harness preflight and guarded execution flows.")
    harness.add_argument("--path", help="Workspace path to target.")
    harness.add_argument("args", nargs=argparse.REMAINDER)
    harness.set_defaults(func=cmd_harness)

    report = subparsers.add_parser("report", help="Produce a high-level Helm operations report.")
    report.add_argument("--path", help="Workspace path to inspect. Defaults to the current directory.")
    report.add_argument("--limit", type=int, default=20)
    report.add_argument("--format", choices=["text", "json", "markdown", "html"], default="text")
    report.add_argument("--public", action="store_true", help="Redact local paths and captured command output from reports.")
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

    db_query = db_subparsers.add_parser("query", help="Query the SQLite operations index.")
    db_query.add_argument("--path", help="Workspace path.")
    db_query.add_argument("--status", help="Filter tasks by status (completed, failed, running).")
    db_query.add_argument("--profile", help="Filter tasks by profile.")
    db_query.add_argument("--guard-action", help="Query guard decisions by action (allow, warn, require_approval, deny).")
    db_query.add_argument("--task-id", help="Filter by task ID.")
    db_query.add_argument("--limit", type=int, default=50)
    db_query.add_argument("--json", action="store_true")
    db_query.set_defaults(func=cmd_db_query)

    # ---- Phase A-E design module entry points -------------------------------
    # Each command is an advisory read-only / in-memory check that surfaces
    # the new design modules (action_scope, freshness_lib, helm_state_model,
    # helm_frontmatter, memory_tree, compression) on the CLI so they
    # participate in CI rather than remaining unwired (see 2026-05-21 Helm
    # full review issue #6).
    action_scope = subparsers.add_parser(
        "action-scope",
        help="Evaluate the action-scope gate for a user message (advisory).",
    )
    action_scope_sub = action_scope.add_subparsers(
        dest="action_scope_command", required=True
    )
    as_eval = action_scope_sub.add_parser(
        "evaluate",
        help="Evaluate the gate against a message and print the decision JSON.",
    )
    as_eval.add_argument("--message", required=True, help="The current user message (raw text).")
    as_eval.add_argument(
        "--target", action="append", default=[],
        help="Explicit target identifier; may be repeated.",
    )
    as_eval.add_argument(
        "--topic", action="append", default=[],
        help="Topic identifier hint (e.g. google_sheets). May be repeated.",
    )
    as_eval.add_argument(
        "--attempt",
        choices=["inspect", "save", "edit", "delete", "external_send"],
        help="If given, also report whether this scope would be allowed.",
    )
    as_eval.add_argument(
        "--resource",
        help="Optional resource identifier used with --attempt (see MUTABLE_RESOURCES).",
    )
    as_eval.set_defaults(func=cmd_action_scope_evaluate)

    freshness = subparsers.add_parser(
        "freshness",
        help="Inspect the connector freshness substrate state.",
    )
    freshness_sub = freshness.add_subparsers(
        dest="freshness_command", required=True
    )
    fs_status = freshness_sub.add_parser(
        "status",
        help="Print the freshness substrate as a JSON or text report.",
    )
    fs_status.add_argument(
        "--state-path",
        help="Override path to connector-freshness.json (default: ~/.helm/state/connector-freshness.json).",
    )
    fs_status.add_argument(
        "--strict-high-risk", action="store_true",
        help="Apply design §3.4 strict-high-risk rule when computing fresh/stale.",
    )
    fs_status.add_argument("--json", action="store_true")
    fs_status.set_defaults(func=cmd_freshness_status)

    state_cmd = subparsers.add_parser(
        "state",
        help="Helm note lifecycle state machine helpers (Phase D, advisory).",
    )
    state_sub = state_cmd.add_subparsers(dest="state_command", required=True)
    s_lint = state_sub.add_parser(
        "lint-phrase",
        help="Lint outbound text against the assertion rules for a given note state.",
    )
    s_lint.add_argument(
        "--state", required=True,
        help="Note state (captured | reviewed | applied | promoted | rejected).",
    )
    s_lint.add_argument(
        "--text", required=True,
        help="The outbound text to lint. Pass '-' to read from stdin.",
    )
    s_lint.add_argument("--json", action="store_true")
    s_lint.set_defaults(func=cmd_state_lint)

    frontmatter = subparsers.add_parser(
        "frontmatter",
        help="Validate the Obsidian vault layout (Phase B, advisory).",
    )
    fm_sub = frontmatter.add_subparsers(dest="frontmatter_command", required=True)
    fm_validate = fm_sub.add_parser(
        "validate-vault",
        help="Verify the six-folder layout (00-Inbox / 10-Topics / ...).",
    )
    fm_validate.add_argument(
        "vault_root",
        help="Path to the Obsidian vault root.",
    )
    fm_validate.add_argument("--json", action="store_true")
    fm_validate.set_defaults(func=cmd_frontmatter_validate)

    memory_tree = subparsers.add_parser(
        "memory-tree",
        help="Inspect the memory tree on disk (Phase C, advisory).",
    )
    mt_sub = memory_tree.add_subparsers(dest="memory_tree_command", required=True)
    mt_status = mt_sub.add_parser(
        "status",
        help="Show the present source / topic / global summary files.",
    )
    mt_status.add_argument(
        "--root",
        help="Override memory tree root (default: ~/.helm/memory).",
    )
    mt_status.add_argument("--json", action="store_true")
    mt_status.set_defaults(func=cmd_memory_tree_status)

    compression = subparsers.add_parser(
        "compression",
        help="Inspect the compression profile registry (Phase E, advisory).",
    )
    comp_sub = compression.add_subparsers(dest="compression_command", required=True)
    comp_profiles = comp_sub.add_parser(
        "profiles",
        help="List the compression profiles registered with the default registry.",
    )
    comp_profiles.add_argument("--json", action="store_true")
    comp_profiles.set_defaults(func=cmd_compression_profiles)

    # ---- Wave 4: Skill-promotion pipeline ------------------------------------
    skill_promo = subparsers.add_parser(
        "skill-promotion",
        help="Skill scaffold candidate digest, approval, and state tracking (Wave 4).",
    )
    sp_sub = skill_promo.add_subparsers(dest="skill_promotion_command", required=True)

    sp_digest = sp_sub.add_parser("digest", help="Print the daily/weekly digest payload as JSON.")
    sp_digest.add_argument("--cadence", choices=["daily", "weekly"], default="daily")
    sp_digest.add_argument("--max", type=int, default=5, dest="max", metavar="N",
                           help="Maximum candidates to include (default: 5).")
    sp_digest.add_argument("--state-path", dest="state_path", default=None,
                           help="Override path to the promotion state file.")
    sp_digest.add_argument("--traces-dir", dest="traces_dir", default=None,
                           help="Override path to the traces directory.")
    sp_digest.set_defaults(func=cmd_skill_promotion, skill_promotion_command="digest")

    sp_approve = sp_sub.add_parser("approve", help="Manually approve a candidate.")
    sp_approve.add_argument("candidate_id", help="8-hex candidate id.")
    sp_approve.add_argument("--state-path", dest="state_path", default=None)
    sp_approve.set_defaults(func=cmd_skill_promotion, skill_promotion_command="approve")

    sp_reject = sp_sub.add_parser("reject", help="Manually reject a candidate.")
    sp_reject.add_argument("candidate_id", help="8-hex candidate id.")
    sp_reject.add_argument("--reason", default=None, help="Optional rejection reason.")
    sp_reject.add_argument("--state-path", dest="state_path", default=None)
    sp_reject.set_defaults(func=cmd_skill_promotion, skill_promotion_command="reject")

    sp_pending = sp_sub.add_parser("pending", help="List notified but unprocessed candidates.")
    sp_pending.add_argument("--json", action="store_true")
    sp_pending.add_argument("--state-path", dest="state_path", default=None)
    sp_pending.set_defaults(func=cmd_skill_promotion, skill_promotion_command="pending")

    sp_state_path = sp_sub.add_parser("state-path", help="Print the state file path in use.")
    sp_state_path.add_argument("--state-path", dest="state_path", default=None)
    sp_state_path.set_defaults(func=cmd_skill_promotion, skill_promotion_command="state-path")

    # ---- Wave 6: Shadow-mode report ------------------------------------------
    shadow_report = subparsers.add_parser(
        "shadow-report",
        help="Produce a shadow-mode aggregation report for enforce-readiness decisions (Wave 6).",
    )
    shadow_report.add_argument(
        "--since", type=int, default=14, metavar="DAYS",
        help="Reporting window in days (default: 14).",
    )
    shadow_report.add_argument(
        "--feature", action="append", default=None, dest="feature", metavar="NAME",
        help=(
            "Include only this feature. Repeatable. Default: all. "
            "Choices: browser_verifier, pause_gate, model_repair, "
            "synthetic_respond_inferred, skill_promotion, max_sessions_hits, "
            "cleanup_evidence_gate, all."
        ),
    )
    shadow_report.add_argument(
        "--format", choices=["md", "json"], default="md",
        help="Output format: md (default) or json.",
    )
    shadow_report.add_argument(
        "--with-recommendations", action="store_true",
        help="Append enforce-readiness recommendations to the output.",
    )
    shadow_report.add_argument(
        "--out", default=None, metavar="PATH",
        help="Write output to this path instead of stdout.",
    )
    shadow_report.set_defaults(func=cmd_shadow_report)

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
        "privacy": cmd_privacy,
        "health": cmd_health,
        "harness": cmd_harness,
    }
    if argv and argv[0] in passthrough:
        command = argv[0]
        workspace: str | None = None
        forwarded: list[str] = []
        idx = 1
        consumed_workspace = False
        while idx < len(argv):
            token = argv[idx]
            if token == "--path" and not consumed_workspace and not forwarded:
                if idx + 1 >= len(argv):
                    raise SystemExit("--path requires a value")
                workspace = argv[idx + 1]
                consumed_workspace = True
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
