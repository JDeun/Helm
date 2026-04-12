#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path


WORKSPACE = Path.home() / ".openclaw" / "workspace"
COMMAND_LOG = WORKSPACE / ".openclaw" / "command-log.jsonl"


def load_entries() -> list[dict]:
    if not COMMAND_LOG.exists():
        return []
    return [json.loads(line) for line in COMMAND_LOG.read_text(encoding="utf-8").splitlines() if line.strip()]


def apply_filters(entries: list[dict], args: argparse.Namespace) -> list[dict]:
    filtered = entries
    if args.task_id:
        filtered = [entry for entry in filtered if entry.get("task_id") == args.task_id]
    if args.component:
        filtered = [entry for entry in filtered if entry.get("component") == args.component]
    if args.label:
        filtered = [entry for entry in filtered if entry.get("label") == args.label]
    if args.failed_only:
        filtered = [entry for entry in filtered if entry.get("exit_code") not in (0, None)]
    return filtered


def summary(entries: list[dict]) -> None:
    components = Counter(entry.get("component", "unknown") for entry in entries)
    labels = Counter(entry.get("label", "unknown") for entry in entries)
    exits = Counter(str(entry.get("exit_code", "unknown")) for entry in entries)
    task_ids = Counter(entry.get("task_id", "-") or "-" for entry in entries)
    print("Component counts:")
    for key, value in sorted(components.items()):
        print(f"  {key}: {value}")
    print("Exit-code counts:")
    for key, value in sorted(exits.items()):
        print(f"  {key}: {value}")
    print("Task counts:")
    for key, value in sorted(task_ids.items()):
        print(f"  {key}: {value}")
    print("Top labels:")
    for key, value in labels.most_common(10):
        print(f"  {key}: {value}")


def recent(entries: list[dict], limit: int, json_output: bool) -> None:
    window = entries[-limit:]
    if json_output:
        print(json.dumps(window, indent=2, ensure_ascii=False))
        return
    for entry in window:
        print(
            f"{entry.get('started_at', '-')} "
            f"{str(entry.get('exit_code', '-')):>4} "
            f"{(entry.get('task_id') or '-')[:8]:>8} "
            f"{entry.get('component', '-'):>18} "
            f"{entry.get('label', '-')}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect the low-level command log emitted by run_command().")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--summary", action="store_true")
    parser.add_argument("--task-id")
    parser.add_argument("--component")
    parser.add_argument("--label")
    parser.add_argument("--failed-only", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    entries = load_entries()
    if not entries:
        print("No command-log entries found.")
        return 0
    filtered = apply_filters(entries, args)
    if not filtered:
        print("No command-log entries matched the filters.")
        return 0
    if args.summary:
        summary(filtered)
    recent(filtered, args.limit, args.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
