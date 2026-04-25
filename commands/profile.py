from __future__ import annotations

import argparse

from commands import (
    discover_workspace,
    run_script,
    target_root,
)


def cmd_profile(args: argparse.Namespace) -> int:
    root = target_root(args.path) if args.path else discover_workspace().root
    return run_script("run_with_profile.py", args.args, root)
