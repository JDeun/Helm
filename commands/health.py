from __future__ import annotations

import argparse
import sys

from commands import discover_workspace, run_script, target_root


def cmd_health(args: argparse.Namespace) -> int:
    root = target_root(args.path) if args.path else discover_workspace().root
    help_parser = argparse.ArgumentParser(prog="helm health")
    help_parser.add_argument(
        "subcommand",
        nargs="?",
        help="Supported: probe, watch, select, state, launch",
    )
    if not args.args:
        help_parser.print_help()
        return 0
    if args.args[0] in {"-h", "--help"}:
        help_parser.print_help()
        return 0
    subcommand, *remainder = args.args
    if subcommand in {"probe", "watch", "select", "state", "launch"}:
        return run_script("model_health_probe.py", [subcommand, *remainder], root)
    print(f"Unknown health subcommand: {subcommand}", file=sys.stderr)
    return 2
