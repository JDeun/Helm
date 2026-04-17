#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess

from adaptive_harness_lib import (
    WORKSPACE,
    backfill_task_evidence,
    ensure_task_evidence,
    load_harness_policy,
    parse_evidence_json,
    postflight_payload_from_task,
    record_task_evidence,
    resolve_skill_contract,
    preflight_payload,
    postflight_payload,
)


RUN_WITH_PROFILE = WORKSPACE / "scripts" / "run_with_profile.py"


def cmd_policy(_: argparse.Namespace) -> int:
    print(json.dumps(load_harness_policy(), indent=2, ensure_ascii=False))
    return 0


def cmd_contract(args: argparse.Namespace) -> int:
    print(json.dumps(resolve_skill_contract(args.skill), indent=2, ensure_ascii=False))
    return 0


def build_preflight(args: argparse.Namespace, *, context_confirmed: bool) -> dict:
    return preflight_payload(
        skill=args.skill,
        profile=args.profile,
        model=args.model,
        model_tier=args.model_tier,
        task_name=args.task_name,
        runtime_target=args.runtime_target,
        user_request=args.request,
        context_confirmed=context_confirmed,
        command=args.command,
        browser_evidence=parse_evidence_json(args.browser_evidence_json, label="--browser-evidence-json"),
        retrieval_evidence=parse_evidence_json(args.retrieval_evidence_json, label="--retrieval-evidence-json"),
    )


def cmd_preflight(args: argparse.Namespace) -> int:
    payload = build_preflight(args, context_confirmed=args.context_confirmed)
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0 if payload["ok"] else 2


def cmd_run(args: argparse.Namespace) -> int:
    context_confirmed = args.context_confirmed
    payload = build_preflight(args, context_confirmed=context_confirmed or args.auto_hydrate)
    hydration_outputs: list[dict] = []
    if not payload["ok"]:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 2

    if args.auto_hydrate and payload["hydration_commands"]:
        for command in payload["hydration_commands"]:
            result = subprocess.run(command, cwd=str(WORKSPACE), capture_output=True, text=True)
            hydration_outputs.append(
                {
                    "command": command,
                    "exit_code": result.returncode,
                    "stdout": (result.stdout or "").strip().splitlines()[:20],
                    "stderr": (result.stderr or "").strip(),
                }
            )
            if result.returncode != 0:
                print(json.dumps({"preflight": payload, "hydration": hydration_outputs}, indent=2, ensure_ascii=False))
                return result.returncode

    meta = {
        "harness": {
            "model": args.model,
            "model_tier": payload["model_tier"],
            "enforcement_level": payload["enforcement_level"],
            "skill_contract_present": bool(payload["contract"]),
            "auto_hydrate": args.auto_hydrate,
            "context_required": payload["context_required"],
            "context_satisfied": bool(args.context_confirmed or args.auto_hydrate or not payload["context_required"]),
            "hydration_commands": payload["hydration_commands"],
            "user_request": args.request,
            "browser_evidence": payload["browser_evidence"],
            "retrieval_evidence": payload["retrieval_evidence"],
        }
    }
    run_cmd = [
        "python3",
        str(RUN_WITH_PROFILE),
        "run",
        args.profile,
        "--task-id",
        payload["task_id"],
        "--meta-json",
        json.dumps(meta, ensure_ascii=False)
    ]
    if args.task_name:
        run_cmd.extend(["--task-name", args.task_name])
    if args.skill:
        run_cmd.extend(["--skill", args.skill])
    if args.label:
        run_cmd.extend(["--label", args.label])
    for item in args.path:
        run_cmd.extend(["--path", item])
    if args.runtime_target:
        run_cmd.extend(["--runtime-target", args.runtime_target])
    if args.runtime_note:
        run_cmd.extend(["--runtime-note", args.runtime_note])
    run_cmd.extend(["--delivery-mode", args.delivery_mode, "--"])
    run_cmd.extend(args.command)

    result = subprocess.run(run_cmd, cwd=str(WORKSPACE))
    ensure_task_evidence(payload["task_id"], payload["contract"])
    postflight = postflight_payload(payload["task_id"], payload["contract"], payload["enforcement_level"])
    output = {
        "preflight": payload,
        "hydration": hydration_outputs,
        "run_exit_code": result.returncode,
        "postflight": postflight
    }
    print(json.dumps(output, indent=2, ensure_ascii=False))
    if result.returncode != 0:
        return result.returncode
    return 0 if postflight["ok"] else 3


def cmd_postflight(args: argparse.Namespace) -> int:
    entry = postflight_payload_from_task(args.task_id).get("entry")
    if entry is not None:
        ensure_task_evidence(args.task_id, resolve_skill_contract(entry.get("skill")))
    payload = postflight_payload_from_task(args.task_id)
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0 if payload["ok"] else 2


def cmd_record_evidence(args: argparse.Namespace) -> int:
    browser_evidence = parse_evidence_json(args.browser_evidence_json, label="--browser-evidence-json")
    retrieval_evidence = parse_evidence_json(args.retrieval_evidence_json, label="--retrieval-evidence-json")
    if browser_evidence is None and retrieval_evidence is None:
        raise SystemExit("Provide --browser-evidence-json or --retrieval-evidence-json")
    entry = record_task_evidence(
        args.task_id,
        browser_evidence=browser_evidence,
        retrieval_evidence=retrieval_evidence,
    )
    postflight = postflight_payload_from_task(args.task_id)
    print(json.dumps({"entry": entry, "postflight": postflight}, indent=2, ensure_ascii=False))
    return 0 if postflight["ok"] else 3


def cmd_backfill_evidence(args: argparse.Namespace) -> int:
    payload = backfill_task_evidence(
        task_ids=args.task_id or None,
        skill=args.skill,
        limit=args.limit,
        latest_only=not args.all_entries,
    )
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Adaptive harness for model-tier-aware task execution.")
    subparsers = parser.add_subparsers(dest="command_name", required=True)

    policy = subparsers.add_parser("policy", help="Show the adaptive harness policy.")
    policy.set_defaults(func=cmd_policy)

    contract = subparsers.add_parser("contract", help="Show the resolved skill contract.")
    contract.add_argument("--skill", required=True)
    contract.set_defaults(func=cmd_contract)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--skill")
    common.add_argument("--profile", required=True)
    common.add_argument("--model")
    common.add_argument("--model-tier")
    common.add_argument("--task-name")
    common.add_argument("--request", help="Natural-language task summary used for boundary checks.")
    common.add_argument("--runtime-target")
    common.add_argument("--runtime-note")
    common.add_argument("--label")
    common.add_argument("--path", action="append", default=[])
    common.add_argument("--delivery-mode", choices=["inline", "background", "announce", "none"], default="inline")
    common.add_argument("--context-confirmed", action="store_true")
    common.add_argument("--browser-evidence-json", help="Structured browser evidence JSON recorded in harness metadata.")
    common.add_argument("--retrieval-evidence-json", help="Structured retrieval evidence JSON recorded in harness metadata.")

    preflight = subparsers.add_parser("preflight", parents=[common], help="Validate a task before execution.")
    preflight.add_argument("command", nargs=argparse.REMAINDER)
    preflight.set_defaults(func=cmd_preflight)

    run = subparsers.add_parser("run", parents=[common], help="Run a task through preflight, execution, and postflight.")
    run.add_argument("--auto-hydrate", action="store_true")
    run.add_argument("command", nargs=argparse.REMAINDER)
    run.set_defaults(func=cmd_run)

    postflight = subparsers.add_parser("postflight", help="Evaluate postflight checks for an existing task.")
    postflight.add_argument("--task-id", required=True)
    postflight.set_defaults(func=cmd_postflight)

    record = subparsers.add_parser("record-evidence", help="Append browser or retrieval evidence to an existing task entry.")
    record.add_argument("--task-id", required=True)
    record.add_argument("--browser-evidence-json", help="Structured browser evidence JSON to persist.")
    record.add_argument("--retrieval-evidence-json", help="Structured retrieval evidence JSON to persist.")
    record.set_defaults(func=cmd_record_evidence)

    backfill = subparsers.add_parser("backfill-evidence", help="Infer and append missing browser or retrieval evidence for prior tasks.")
    backfill.add_argument("--task-id", action="append", help="Specific task id to backfill. Can be passed multiple times.")
    backfill.add_argument("--skill", help="Limit backfill to one skill.")
    backfill.add_argument("--limit", type=int, help="Only inspect the last N candidate tasks after filtering.")
    backfill.add_argument("--all-entries", action="store_true", help="Inspect every ledger row instead of latest rows per task id.")
    backfill.set_defaults(func=cmd_backfill_evidence)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    command = getattr(args, "command", None)
    if command is not None and command and command[0] == "--":
        args.command = command[1:]
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
