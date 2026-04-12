#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path


WORKSPACE = Path.home() / ".openclaw" / "workspace"
TASK_LEDGER = WORKSPACE / ".openclaw" / "task-ledger.jsonl"


def load_entries() -> list[dict]:
    if not TASK_LEDGER.exists():
        return []
    return [json.loads(line) for line in TASK_LEDGER.read_text(encoding="utf-8").splitlines() if line.strip()]


def latest_entries(entries: list[dict]) -> list[dict]:
    by_task: dict[str, dict] = {}
    for entry in entries:
        task_id = entry.get("task_id")
        if task_id:
            by_task[task_id] = entry
    return list(by_task.values())


def apply_filters(entries: list[dict], args: argparse.Namespace) -> list[dict]:
    filtered = entries
    if args.latest:
        filtered = latest_entries(filtered)
    if args.status:
        filtered = [entry for entry in filtered if entry.get("status") == args.status]
    if args.profile:
        filtered = [entry for entry in filtered if entry.get("profile") == args.profile]
    if args.skill:
        filtered = [entry for entry in filtered if entry.get("skill") == args.skill]
    if args.delivery_mode:
        filtered = [entry for entry in filtered if entry.get("delivery_mode") == args.delivery_mode]
    if args.failed_only:
        filtered = [entry for entry in filtered if entry.get("status") == "failed"]
    return filtered


def summary(entries: list[dict]) -> None:
    statuses = Counter(entry.get("status", "unknown") for entry in entries)
    profiles = Counter(entry.get("profile", "unknown") for entry in entries)
    skills = Counter(entry.get("skill", "-") or "-" for entry in entries)
    print("Status counts:")
    for key, value in sorted(statuses.items()):
        print(f"  {key}: {value}")
    print("Profile counts:")
    for key, value in sorted(profiles.items()):
        print(f"  {key}: {value}")
    print("Skill counts:")
    for key, value in sorted(skills.items()):
        print(f"  {key}: {value}")


def recent(entries: list[dict], limit: int, json_output: bool) -> None:
    window = entries[-limit:]
    if json_output:
        print(json.dumps(window, indent=2, ensure_ascii=False))
        return
    for entry in window:
        print(
            f"{entry.get('started_at', '-')} "
            f"{entry.get('status', '-'):>9} "
            f"{entry.get('profile', '-'):>14} "
            f"{(entry.get('skill') or '-'):>22} "
            f"{entry.get('task_name', '-')}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect the local task ledger created by run_with_profile.py.")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--summary", action="store_true")
    parser.add_argument("--status")
    parser.add_argument("--profile")
    parser.add_argument("--skill")
    parser.add_argument("--delivery-mode")
    parser.add_argument("--failed-only", action="store_true")
    parser.add_argument("--latest", action="store_true", help="Collapse repeated task states to the latest entry per task_id.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    entries = load_entries()
    if not entries:
        print("No task-ledger entries found.")
        return 0
    filtered = apply_filters(entries, args)
    if not filtered:
        print("No task-ledger entries matched the filters.")
        return 0
    if args.summary:
        summary(filtered)
    recent(filtered, args.limit, args.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
