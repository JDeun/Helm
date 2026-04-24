from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from commands import (
    SCRIPT_ROOT,
    discover_workspace,
    target_root,
)


def run_script(script_name: str, script_args: list[str], workspace: Path | None = None) -> int:
    script_path = SCRIPT_ROOT / script_name
    env = os.environ.copy()
    if workspace is not None:
        env["HELM_WORKSPACE"] = str(workspace)
    result = subprocess.run([sys.executable, str(script_path), *script_args], env=env)
    return result.returncode


def cmd_harness(args: argparse.Namespace) -> int:
    root = target_root(args.path) if args.path else discover_workspace().root
    return run_script("adaptive_harness.py", args.args, root)
