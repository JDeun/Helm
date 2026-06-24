from __future__ import annotations

import argparse
import json

from scripts.skill_intake_lib import classify_candidate, validate_candidate


def cmd_skill_intake(args: argparse.Namespace) -> int:
    if args.skill_intake_command == "classify":
        payload = classify_candidate(args.name, args.description or "")
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        else:
            print(f"name={payload['name']}")
            print(f"risk_class={payload['risk_class']}")
            print(f"default_action={payload['default_action']}")
            print(f"rationale={payload['rationale']}")
        return 0
    if args.skill_intake_command == "validate":
        payload = {"name": args.name, "risk_class": args.risk_class, "default_action": args.default_action}
        result = validate_candidate(payload)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        else:
            print("validation=ok" if result["ok"] else "validation=failed")
            for issue in result["issues"]:
                print(f"issue={issue}")
        return 0 if result["ok"] else 1
    return 1
