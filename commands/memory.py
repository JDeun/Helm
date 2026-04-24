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
from commands.context import build_recent_state_payload


def run_script(script_name: str, script_args: list[str], workspace: Path | None = None) -> int:
    script_path = SCRIPT_ROOT / script_name
    env = os.environ.copy()
    if workspace is not None:
        env["HELM_WORKSPACE"] = str(workspace)
    result = subprocess.run([sys.executable, str(script_path), *script_args], env=env)
    return result.returncode


def cmd_memory(args: argparse.Namespace) -> int:
    root = target_root(args.path) if args.path else discover_workspace().root
    help_parser = argparse.ArgumentParser(prog="helm memory")
    help_parser.add_argument(
        "subcommand",
        nargs="?",
        help="Supported: pending-captures, review-queue, audit-coherence, history, crystallize, op",
    )
    if not args.args:
        help_parser.print_help()
        return 0
    if args.args[0] in {"-h", "--help"}:
        help_parser.print_help()
        return 0
    subcommand, *remainder = args.args
    if subcommand == "pending-captures":
        parser = argparse.ArgumentParser(prog="helm memory pending-captures")
        parser.add_argument("--limit", type=int, default=20)
        parser.add_argument("--json", action="store_true")
        parsed = parser.parse_args(remainder)
        payload = build_recent_state_payload(root, parsed.limit, pending_only=True)
        if parsed.json:
            print(json.dumps(payload, indent=2, ensure_ascii=False))
            return 0
        if not payload["items"]:
            print("No pending durable captures found.")
            return 0
        for item in payload["items"]:
            print(
                f"{item['task_id']} profile={item['profile']} status={item['status']} "
                f"finalization={item['finalization_status']} confidence={item['claim_state'].get('confidence_hint', '-')} "
                f"layers={','.join(item['recommended_layers'])} review_flags={len(item['review_flags'])} "
                f"name={item['task_name']}"
            )
        return 0
    if subcommand in {"history", "crystallize", "op", "review-queue", "audit-coherence"}:
        return run_script("memory_ops.py", [subcommand, *remainder], root)
    print(f"Unknown memory subcommand: {subcommand}", file=sys.stderr)
    return 2
