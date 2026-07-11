#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml


REQUIRED = {
    "name", "trigger_condition", "allowed_inputs", "required_live_sources",
    "mutable_surfaces", "forbidden_actions", "verification_steps",
    "final_reporting_rules", "handoff_conditions", "stop_conditions",
}


def validate_registry(payload: dict) -> list[str]:
    issues: list[str] = []
    units = payload.get("units") if isinstance(payload, dict) else None
    if not isinstance(units, list):
        return ["units must be a list"]
    names: set[str] = set()
    for index, unit in enumerate(units):
        if not isinstance(unit, dict):
            issues.append(f"units[{index}] must be an object")
            continue
        missing = REQUIRED - unit.keys()
        issues.extend(f"units[{index}] missing {field}" for field in sorted(missing))
        name = str(unit.get("name") or "")
        if name in names:
            issues.append(f"duplicate workflow name: {name}")
        names.add(name)
        for field in REQUIRED - {"name", "trigger_condition"}:
            if field in unit and not isinstance(unit[field], list):
                issues.append(f"units[{index}].{field} must be a list")
    return issues


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Helm workflow unit registry contracts.")
    parser.add_argument("path", nargs="?", default=str(Path(__file__).resolve().parents[1] / "references" / "workflow_units.yaml"))
    args = parser.parse_args()
    payload = yaml.safe_load(Path(args.path).read_text(encoding="utf-8"))
    issues = validate_registry(payload)
    print(json.dumps({"ok": not issues, "issues": issues}, indent=2))
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
