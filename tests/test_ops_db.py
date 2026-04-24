from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.ops_db import db_path_for_state_root, init_db, rebuild_index, index_task_entry, verify_index, query_tasks, query_guard_decisions, _connect


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


def test_streaming_rebuild_large_file(tmp_path: Path) -> None:
    """Rebuild should work with streaming, not load entire file into memory."""
    state_root = tmp_path / ".helm"
    state_root.mkdir()
    ledger = state_root / "task-ledger.jsonl"
    with ledger.open("w", encoding="utf-8") as f:
        for i in range(100):
            f.write(json.dumps({"task_id": f"task-{i:04d}", "status": "completed", "profile": "inspect_local"}) + "\n")
    result = rebuild_index(state_root=state_root)
    assert result["task_rows"] == 100


def test_query_tasks_filter_by_status(tmp_path: Path) -> None:
    state_root = tmp_path / ".helm"
    state_root.mkdir()
    ledger = state_root / "task-ledger.jsonl"
    entries = [
        {"task_id": "t1", "status": "completed", "profile": "inspect_local"},
        {"task_id": "t2", "status": "failed", "profile": "workspace_edit"},
        {"task_id": "t3", "status": "completed", "profile": "workspace_edit"},
    ]
    ledger.write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")
    rebuild_index(state_root=state_root)
    failed = query_tasks(state_root=state_root, status="failed")
    assert len(failed) == 1
    assert failed[0]["task_id"] == "t2"


def test_query_tasks_filter_by_profile(tmp_path: Path) -> None:
    state_root = tmp_path / ".helm"
    state_root.mkdir()
    ledger = state_root / "task-ledger.jsonl"
    entries = [
        {"task_id": "t1", "status": "completed", "profile": "inspect_local"},
        {"task_id": "t2", "status": "failed", "profile": "workspace_edit"},
    ]
    ledger.write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")
    rebuild_index(state_root=state_root)
    ws_tasks = query_tasks(state_root=state_root, profile="workspace_edit")
    assert len(ws_tasks) == 1


def test_query_guard_decisions_filter_by_action(tmp_path: Path) -> None:
    state_root = tmp_path / ".helm"
    state_root.mkdir()
    db = db_path_for_state_root(state_root)
    init_db(db)
    entries = [
        {"task_id": "t1", "guard": {"action": "deny", "risk_score": 1.0, "selected_profile": "inspect_local", "matched_rules": ["deny.rm_root"], "reasons": ["abs deny"], "classification": {}}},
        {"task_id": "t2", "guard": {"action": "allow", "risk_score": 0.0, "selected_profile": "inspect_local", "matched_rules": [], "reasons": [], "classification": {}}},
    ]
    for e in entries:
        index_task_entry(state_root=state_root, entry=e, source_file="test.jsonl", source_line=1)
    deny_decisions = query_guard_decisions(state_root=state_root, action="deny")
    assert len(deny_decisions) == 1
    assert deny_decisions[0]["action"] == "deny"


def test_auto_uuid_for_null_task_id(tmp_path: Path) -> None:
    state_root = tmp_path / ".helm"
    state_root.mkdir()
    db = db_path_for_state_root(state_root)
    init_db(db)
    index_task_entry(state_root=state_root, entry={"task_id": None, "status": "completed"}, source_file="test.jsonl")
    index_task_entry(state_root=state_root, entry={"task_id": None, "status": "failed"}, source_file="test.jsonl")
    conn = sqlite3.connect(str(db))
    rows = conn.execute("SELECT task_id FROM task_index").fetchall()
    conn.close()
    ids = [r[0] for r in rows]
    assert len(ids) == 2
    assert ids[0] != ids[1]
    assert all(tid.startswith("auto-") for tid in ids)


def test_connect_helper_sets_pragmas(tmp_path: Path) -> None:
    db = tmp_path / "test.sqlite3"
    init_db(db)
    conn = _connect(db)
    journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    busy_timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
    conn.close()
    assert journal_mode == "wal"
    assert busy_timeout == 5000


def test_indexing_failure_warns_once(tmp_path: Path, capsys) -> None:
    """index_task_entry should warn once on failure, then be silent."""
    import scripts.ops_db as ops_db_mod
    ops_db_mod._INDEX_FAILURE_WARNED = False
    state_root = tmp_path / ".helm"
    state_root.mkdir()
    db = db_path_for_state_root(state_root)
    db.mkdir(parents=True)
    import warnings
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        index_task_entry(state_root=state_root, entry={"task_id": "t1"}, source_file="test.jsonl")
        index_task_entry(state_root=state_root, entry={"task_id": "t2"}, source_file="test.jsonl")
    sqlite_warnings = [x for x in w if "SQLite indexing failed" in str(x.message)]
    assert len(sqlite_warnings) <= 1
    ops_db_mod._INDEX_FAILURE_WARNED = False


def test_cmd_db_query_returns_results(tmp_path: Path) -> None:
    """Ensure the query function works end-to-end."""
    state_root = tmp_path / ".helm"
    state_root.mkdir()
    ledger = state_root / "task-ledger.jsonl"
    entries = [
        {"task_id": "t1", "status": "completed", "profile": "inspect_local"},
        {"task_id": "t2", "status": "failed", "profile": "risky_edit"},
    ]
    ledger.write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")
    rebuild_index(state_root=state_root)
    results = query_tasks(state_root=state_root, limit=10)
    assert len(results) == 2
