from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from commands import (
    SCRIPT_ROOT,
    discover_workspace,
    target_root,
)
from commands.checkpoint import build_capture_state_payload


def run_script(script_name: str, script_args: list[str], workspace: Path | None = None) -> int:
    script_path = SCRIPT_ROOT / script_name
    env = os.environ.copy()
    if workspace is not None:
        env["HELM_WORKSPACE"] = str(workspace)
    result = subprocess.run([sys.executable, str(script_path), *script_args], env=env)
    return result.returncode


def cmd_ops(args: argparse.Namespace) -> int:
    root = target_root(args.path) if args.path else discover_workspace().root
    if not args.args:
        print("Use `helm ops daily|tasks|commands ...`", file=sys.stderr)
        return 2
    subcommand, *remainder = args.args
    if subcommand == "capture-state":
        parser = argparse.ArgumentParser(prog="helm ops capture-state")
        parser.add_argument("--limit", type=int, default=20)
        parser.add_argument("--json", action="store_true")
        parsed = parser.parse_args(remainder)
        payload = build_capture_state_payload(root, parsed.limit)
        if parsed.json:
            print(json.dumps(payload, indent=2, ensure_ascii=False))
            return 0
        print(f"workspace={payload['workspace']}")
        print("finalization_counts=" + json.dumps(payload["finalization_counts"], ensure_ascii=False, sort_keys=True))
        print(f"pending_tasks={len(payload['pending_tasks'])}")
        for task in payload["pending_tasks"][:10]:
            print(
                f"pending={task['task_id']} profile={task['profile']} "
                f"finalization={task['finalization_status']} confidence={task.get('confidence_hint') or '-'} "
                f"review_flags={len(task['review_flags'])} name={task['task_name']}"
            )
        return 0
    mapping = {
        "daily": "ops_daily_report.py",
        "tasks": "task_ledger_report.py",
        "commands": "command_log_report.py",
    }
    script_name = mapping.get(subcommand)
    if script_name is None:
        print(f"Unknown ops subcommand: {subcommand}", file=sys.stderr)
        return 2
    return run_script(script_name, remainder, root)
