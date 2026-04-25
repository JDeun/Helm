#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from helm_workspace import get_workspace_layout


def _get_task_ledger() -> Path:
    return get_workspace_layout().state_root / "task-ledger.jsonl"


def load_entries(path: Path | None = None) -> list[dict]:
    ledger = path if path is not None else _get_task_ledger()
    if not ledger.exists():
        return []
    rows: list[dict] = []
    for lineno, line in enumerate(ledger.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            print(f"warning: ignoring malformed task ledger line {lineno}: {exc}", file=sys.stderr)
            continue
        if not isinstance(payload, dict):
            print(f"warning: ignoring non-object task ledger line {lineno}", file=sys.stderr)
            continue
        rows.append(payload)
    return rows


def latest_entries(entries: list[dict]) -> list[dict]:
    by_task: dict[str, dict] = {}
    for entry in entries:
        task_id = entry.get("task_id")
        if task_id:
            by_task[task_id] = entry
    return list(by_task.values())


def select_entry(task_id: str | None) -> dict | None:
    entries = latest_entries(load_entries())
    if not entries:
        return None
    if task_id:
        return next((entry for entry in entries if entry.get("task_id") == task_id), None)
    entries.sort(key=lambda item: item.get("finished_at") or item.get("started_at") or "")
    return entries[-1]


def evaluate(entry: dict | None) -> dict:
    if entry is None:
        return {"ok": False, "reason": "task_not_found", "task": None}
    harness = ((entry.get("meta") or {}).get("harness") or {})
    enforcement = harness.get("enforcement_level", "light")
    finalization = (entry.get("memory_capture") or {}).get("finalization_status", "unknown")
    status = entry.get("status")
    contract_present = bool(harness.get("skill_contract_present"))
    context_required = bool(harness.get("context_required"))
    context_satisfied = bool(harness.get("context_satisfied"))
    checks = [
        {"name": "task_status", "ok": status in {"completed", "handoff_required"}, "detail": f"status={status}"},
    ]
    if enforcement in {"balanced", "strict"}:
        checks.append(
            {
                "name": "task_name",
                "ok": bool(entry.get("task_name")),
                "detail": f"task_name={'present' if entry.get('task_name') else 'missing'}",
            }
        )
        checks.append(
            {
                "name": "skill_contract",
                "ok": contract_present,
                "detail": f"skill_contract_present={contract_present}",
            }
        )
    else:
        checks.append({"name": "task_name", "ok": True, "detail": "not required"})
        checks.append({"name": "skill_contract", "ok": True, "detail": f"skill_contract_present={contract_present}"})
    if enforcement in {"balanced", "strict"} and context_required:
        checks.append(
            {
                "name": "context_hydration",
                "ok": context_satisfied,
                "detail": f"context_required={context_required}, context_satisfied={context_satisfied}",
            }
        )
    else:
        checks.append(
            {
                "name": "context_hydration",
                "ok": True,
                "detail": f"context_required={context_required}, context_satisfied={context_satisfied}",
            }
        )
    if enforcement in {"balanced", "strict"}:
        checks.append(
            {
                "name": "finalization",
                "ok": finalization not in {"capture_planned", "capture_partial", "unknown"},
                "detail": f"finalization_status={finalization}",
            }
        )
    else:
        checks.append({"name": "finalization", "ok": True, "detail": f"finalization_status={finalization}"})
    return {
        "ok": all(check["ok"] for check in checks),
        "reason": "reply_allowed" if all(check["ok"] for check in checks) else "reply_blocked",
        "task": {
            "task_id": entry.get("task_id"),
            "task_name": entry.get("task_name"),
            "skill": entry.get("skill"),
            "profile": entry.get("profile"),
            "status": status,
            "enforcement_level": enforcement,
            "finalization_status": finalization,
            "skill_contract_present": contract_present,
        },
        "checks": checks,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Decide whether a task is safe to present as complete to the user.")
    parser.add_argument("--task-id")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    payload = evaluate(select_entry(args.task_id))
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print(json.dumps(payload, ensure_ascii=False))
    return 0 if payload["ok"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
