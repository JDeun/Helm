from __future__ import annotations

import argparse
import json
from pathlib import Path

from commands import target_root
from scripts.loop_lib import find_loop, load_loop_file, validate_loop


def cmd_loops(args: argparse.Namespace) -> int:
    root = target_root(getattr(args, "path", None))
    command = args.loops_command
    if command == "validate":
        path = Path(args.file)
        if not path.is_absolute():
            path = root / path
        payload = load_loop_file(path)
        result = validate_loop(payload)
        result["path"] = str(path)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        else:
            print(f"loop={result.get('id')}")
            print("validation=ok" if result["ok"] else "validation=failed")
            for issue in result["issues"]:
                print(f"issue={issue}")
        return 0 if result["ok"] else 1
    if command == "inspect":
        found = find_loop(root, args.loop_id)
        if found is None:
            if args.json:
                print(json.dumps({"ok": False, "error": f"loop not found: {args.loop_id}"}, ensure_ascii=False, indent=2))
            else:
                print(f"loop not found: {args.loop_id}")
            return 1
        path, payload = found
        result = {"path": str(path), "loop": payload, "validation": validate_loop(payload)}
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        else:
            print(f"loop={payload.get('id')}")
            print(f"path={path}")
            print("validation=ok" if result["validation"]["ok"] else "validation=failed")
        return 0 if result["validation"]["ok"] else 1
    return 1
