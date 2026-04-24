from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.ops_db import db_path_for_state_root, init_db, rebuild_index, index_task_entry, verify_index


def test_init_db_creates_schema(tmp_path: Path) -> None:
    db = tmp_path / "ops-index.sqlite3"
    init_db(db)
    assert db.exists()
    conn = sqlite3.connect(str(db))
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    conn.close()
    assert "task_index" in tables
    assert "command_index" in tables
    assert "guard_decision_index" in tables
    assert "discovery_snapshot_index" in tables
    assert "schema_info" in tables


def test_rebuild_index_from_task_ledger(tmp_path: Path) -> None:
    state_root = tmp_path / ".helm"
    state_root.mkdir()
    ledger = state_root / "task-ledger.jsonl"
    entries = [
        {"task_id": "task-001", "task_name": "test", "profile": "inspect_local", "status": "completed", "started_at": "2026-04-24T10:00:00Z"},
        {"task_id": "task-002", "task_name": "edit", "profile": "workspace_edit", "status": "failed", "started_at": "2026-04-24T11:00:00Z"},
    ]
    ledger.write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")
    result = rebuild_index(state_root=state_root)
    assert result["task_rows"] == 2
    assert result["warnings"] == []


def test_rebuild_tolerates_malformed_jsonl_line(tmp_path: Path) -> None:
    state_root = tmp_path / ".helm"
    state_root.mkdir()
    ledger = state_root / "task-ledger.jsonl"
    ledger.write_text(
        '{"task_id": "task-001", "status": "completed"}\n'
        'NOT VALID JSON\n'
        '{"task_id": "task-002", "status": "failed"}\n',
        encoding="utf-8",
    )
    result = rebuild_index(state_root=state_root)
    assert result["task_rows"] == 2
    assert len(result["warnings"]) == 1


def test_guard_decision_indexed(tmp_path: Path) -> None:
    state_root = tmp_path / ".helm"
    state_root.mkdir()
    db = db_path_for_state_root(state_root)
    init_db(db)
    entry = {
        "task_id": "task-001",
        "guard": {
            "enabled": True,
            "action": "deny",
            "risk_score": 1.0,
            "selected_profile": "inspect_local",
            "recommended_profile": None,
            "approved": False,
            "matched_rules": ["deny.rm_root"],
            "reasons": ["absolute deny"],
            "classification": {"normalized_command": "rm -rf /"},
        },
    }
    index_task_entry(state_root=state_root, entry=entry, source_file="task-ledger.jsonl", source_line=1)
    conn = sqlite3.connect(str(db))
    rows = conn.execute("SELECT action, risk_score FROM guard_decision_index").fetchall()
    conn.close()
    assert len(rows) == 1
    assert rows[0][0] == "deny"
    assert rows[0][1] == 1.0


def test_verify_index_reports_no_drift(tmp_path: Path) -> None:
    state_root = tmp_path / ".helm"
    state_root.mkdir()
    ledger = state_root / "task-ledger.jsonl"
    ledger.write_text('{"task_id": "task-001", "status": "completed"}\n', encoding="utf-8")
    rebuild_index(state_root=state_root)
    result = verify_index(state_root=state_root)
    assert result["drift"] is False


def test_sqlite_failure_does_not_delete_jsonl(tmp_path: Path) -> None:
    state_root = tmp_path / ".helm"
    state_root.mkdir()
    ledger = state_root / "task-ledger.jsonl"
    ledger.write_text('{"task_id": "task-001"}\n', encoding="utf-8")
    # Make DB path a directory to force SQLite failure
    db = db_path_for_state_root(state_root)
    db.mkdir(parents=True)
    # index_task_entry should not raise and should not delete JSONL
    index_task_entry(state_root=state_root, entry={"task_id": "task-002"}, source_file="task-ledger.jsonl")
    assert ledger.exists()
    assert ledger.read_text(encoding="utf-8").strip() == '{"task_id": "task-001"}'


def test_db_path_for_state_root(tmp_path: Path) -> None:
    result = db_path_for_state_root(tmp_path / ".helm")
    assert result == tmp_path / ".helm" / "ops-index.sqlite3"
