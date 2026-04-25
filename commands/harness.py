from __future__ import annotations

import argparse

from commands import (
    discover_workspace,
    run_script,
    target_root,
)


def cmd_harness(args: argparse.Namespace) -> int:
    root = target_root(args.path) if args.path else discover_workspace().root
    return run_script("adaptive_harness.py", args.args, root)
