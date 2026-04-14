#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess

from adaptive_harness_lib import (
    WORKSPACE,
    load_harness_policy,
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
            "user_request": args.request
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

    preflight = subparsers.add_parser("preflight", parents=[common], help="Validate a task before execution.")
    preflight.add_argument("command", nargs=argparse.REMAINDER)
    preflight.set_defaults(func=cmd_preflight)

    run = subparsers.add_parser("run", parents=[common], help="Run a task through preflight, execution, and postflight.")
    run.add_argument("--auto-hydrate", action="store_true")
    run.add_argument("command", nargs=argparse.REMAINDER)
    run.set_defaults(func=cmd_run)
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
