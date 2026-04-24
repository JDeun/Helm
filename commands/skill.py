from __future__ import annotations

import argparse
import difflib
import json
import os
import subprocess
import sys
from pathlib import Path

from commands import (
    SCRIPT_ROOT,
    discover_workspace,
    relative_or_absolute,
    target_root,
)


def write_json_file(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def run_script(script_name: str, script_args: list[str], workspace: Path | None = None) -> int:
    script_path = SCRIPT_ROOT / script_name
    env = os.environ.copy()
    if workspace is not None:
        env["HELM_WORKSPACE"] = str(workspace)
    result = subprocess.run([sys.executable, str(script_path), *script_args], env=env)
    return result.returncode


def cmd_skill(args: argparse.Namespace) -> int:
    root = target_root(args.path) if args.path else discover_workspace().root
    return run_script("skill_capture.py", args.args, root)


def cmd_skill_diff(args: argparse.Namespace) -> int:
    root = target_root(args.path)
    draft_path = root / "skill_drafts" / args.name / "SKILL.md"
    live_path = root / "skills" / args.name / "SKILL.md"
    if not draft_path.exists():
        print(f"draft not found: {draft_path}", file=sys.stderr)
        return 1
    draft_lines = draft_path.read_text(encoding="utf-8").splitlines()
    if live_path.exists():
        live_lines = live_path.read_text(encoding="utf-8").splitlines()
        from_label = str(relative_or_absolute(live_path, root))
    else:
        live_lines = []
        from_label = "(no live skill)"
    diff = list(
        difflib.unified_diff(
            live_lines,
            draft_lines,
            fromfile=from_label,
            tofile=str(relative_or_absolute(draft_path, root)),
            lineterm="",
        )
    )
    if args.json:
        print(
            json.dumps(
                {
                    "workspace": str(root),
                    "draft": str(relative_or_absolute(draft_path, root)),
                    "live": str(relative_or_absolute(live_path, root)) if live_path.exists() else None,
                    "diff": diff,
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return 0
    if not diff:
        print("No differences found.")
        return 0
    print("\n".join(diff))
    return 0


def cmd_skill_review(args: argparse.Namespace) -> int:
    root = target_root(args.path)
    diff_args = argparse.Namespace(path=str(root), name=args.name, json=args.json)
    return cmd_skill_diff(diff_args)


def cmd_skill_approve(args: argparse.Namespace) -> int:
    root = target_root(args.path)
    script_args = ["promote-draft", "--name", args.name, "--approve"]
    if args.dry_run:
        script_args.append("--dry-run")
    return run_script("skill_capture.py", script_args, root)


def cmd_skill_reject(args: argparse.Namespace) -> int:
    root = target_root(args.path)
    draft_root = root / "skill_drafts" / args.name
    if not draft_root.exists():
        print(f"draft not found: {draft_root}", file=sys.stderr)
        return 1
    payload = {
        "draft": args.name,
        "status": "rejected",
        "reason": args.reason,
    }
    write_json_file(draft_root / "meta" / "rejection.json", payload)
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0
    print(f"draft={args.name}")
    print("status=rejected")
    print(f"reason={args.reason}")
    return 0
