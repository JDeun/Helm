#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from helm_workspace import get_workspace_layout
from scripts.adaptive_harness_lib import entry_evidence_requirements, resolve_skill_contract


WORKSPACE = get_workspace_layout().root
TASK_LEDGER = get_workspace_layout().state_root / "task-ledger.jsonl"


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
    capture_states = Counter(
        (entry.get("memory_capture") or {}).get("finalization_status", "unknown") for entry in entries
    )
    harness_levels = Counter((((entry.get("meta") or {}).get("harness") or {}).get("enforcement_level", "-")) for entry in entries)
    harness_models = Counter((((entry.get("meta") or {}).get("harness") or {}).get("model_tier", "-")) for entry in entries)
    browser_evidence = Counter()
    retrieval_evidence = Counter()
    retrieval_exit_classes = Counter()
    retrieval_next_stages = Counter()
    missing_required_browser_by_skill = Counter()
    missing_required_retrieval_by_skill = Counter()
    for entry in entries:
        harness = ((entry.get("meta") or {}).get("harness") or {})
        contract = resolve_skill_contract(entry.get("skill"))
        requirements = entry_evidence_requirements(entry, contract) if contract else {"browser_work": False, "retrieval_policy": False}
        browser_payload = harness.get("browser_evidence")
        retrieval_payload = harness.get("retrieval_evidence")

        if isinstance(browser_payload, dict):
            browser_evidence["inferred" if browser_payload.get("inferred") else "present"] += 1
        else:
            browser_evidence["missing_required" if requirements["browser_work"] else "missing_optional"] += 1
            if requirements["browser_work"]:
                missing_required_browser_by_skill[entry.get("skill", "-") or "-"] += 1

        if isinstance(retrieval_payload, dict):
            retrieval_evidence["inferred" if retrieval_payload.get("inferred") else "present"] += 1
            retrieval_exit_classes[str(retrieval_payload.get("exit_classification", "-"))] += 1
            retrieval_next_stages[str(retrieval_payload.get("next_attempt_stage") or "none")] += 1
        else:
            retrieval_evidence["missing_required" if requirements["retrieval_policy"] else "missing_optional"] += 1
            if requirements["retrieval_policy"]:
                missing_required_retrieval_by_skill[entry.get("skill", "-") or "-"] += 1
    print("Status counts:")
    for key, value in sorted(statuses.items()):
        print(f"  {key}: {value}")
    print("Profile counts:")
    for key, value in sorted(profiles.items()):
        print(f"  {key}: {value}")
    print("Skill counts:")
    for key, value in sorted(skills.items()):
        print(f"  {key}: {value}")
    print("Finalization counts:")
    for key, value in sorted(capture_states.items()):
        print(f"  {key}: {value}")
    print("Harness enforcement counts:")
    for key, value in sorted(harness_levels.items()):
        print(f"  {key}: {value}")
    print("Harness model-tier counts:")
    for key, value in sorted(harness_models.items()):
        print(f"  {key}: {value}")
    print("Browser evidence counts:")
    for key, value in sorted(browser_evidence.items()):
        print(f"  {key}: {value}")
    print("Retrieval evidence counts:")
    for key, value in sorted(retrieval_evidence.items()):
        print(f"  {key}: {value}")
    print("Retrieval exit classifications:")
    for key, value in sorted(retrieval_exit_classes.items()):
        print(f"  {key}: {value}")
    print("Retrieval next-attempt stages:")
    for key, value in sorted(retrieval_next_stages.items()):
        print(f"  {key}: {value}")
    print("Missing required browser evidence by skill:")
    for key, value in sorted(missing_required_browser_by_skill.items()):
        print(f"  {key}: {value}")
    print("Missing required retrieval evidence by skill:")
    for key, value in sorted(missing_required_retrieval_by_skill.items()):
        print(f"  {key}: {value}")


def recent(entries: list[dict], limit: int, json_output: bool) -> None:
    window = entries[-limit:]
    if json_output:
        print(json.dumps(window, indent=2, ensure_ascii=False))
        return
    for entry in window:
        harness = ((entry.get("meta") or {}).get("harness") or {})
        browser_flag = "B" if isinstance(harness.get("browser_evidence"), dict) else "-"
        retrieval = harness.get("retrieval_evidence")
        retrieval_flag = retrieval.get("exit_classification", "-") if isinstance(retrieval, dict) else "-"
        print(
            f"{entry.get('started_at', '-')} "
            f"{entry.get('status', '-'):>9} "
            f"{entry.get('profile', '-'):>14} "
            f"{(entry.get('skill') or '-'):>22} "
            f"{entry.get('task_name', '-')} "
            f"[browser={browser_flag} retrieval={retrieval_flag}]"
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
