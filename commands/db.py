from __future__ import annotations

import argparse
import json

from commands import state_root_for, target_root
from scripts.ops_db import db_path_for_state_root, init_db, rebuild_index, verify_index


def cmd_db_init(args: argparse.Namespace) -> int:
    root = target_root(args.path)
    state_root = state_root_for(root)
    db = db_path_for_state_root(state_root)
    init_db(db)
    print(f"ops_db_path={db}")
    print("status=initialized")
    return 0


def cmd_db_rebuild(args: argparse.Namespace) -> int:
    root = target_root(args.path)
    state_root = state_root_for(root)
    result = rebuild_index(state_root=state_root)
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        for key, value in result.items():
            print(f"{key}={value}")
    return 0


def cmd_db_verify(args: argparse.Namespace) -> int:
    root = target_root(args.path)
    state_root = state_root_for(root)
    result = verify_index(state_root=state_root)
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        for key, value in result.items():
            print(f"{key}={value}")
    return 0


def cmd_db_status(args: argparse.Namespace) -> int:
    root = target_root(args.path)
    state_root = state_root_for(root)
    db = db_path_for_state_root(state_root)
    status = "present" if db.exists() else "missing"
    payload = {"ops_db_path": str(db), "status": status}
    if db.exists():
        try:
            import sqlite3
            conn = sqlite3.connect(str(db))
            for table in ("task_index", "guard_decision_index", "discovery_snapshot_index"):
                count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                payload[f"{table}_rows"] = count
            conn.close()
        except Exception:
            payload["error"] = "failed to query database"
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        for key, value in payload.items():
            print(f"{key}={value}")
    return 0
