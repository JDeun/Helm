from __future__ import annotations

import json
import sqlite3
import warnings as _warnings
from pathlib import Path

_SCHEMA_VERSION = "1"

_INDEX_FAILURE_WARNED = False

_INITIALIZED_DBS: set[str] = set()

_DDL = """
PRAGMA journal_mode=WAL;
PRAGMA busy_timeout=5000;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS schema_info (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS task_index (
    task_id TEXT PRIMARY KEY,
    task_name TEXT,
    skill TEXT,
    profile TEXT,
    status TEXT,
    started_at TEXT,
    finished_at TEXT,
    source_file TEXT NOT NULL,
    source_line INTEGER NOT NULL,
    raw_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS command_index (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT,
    command_preview TEXT,
    profile TEXT,
    exit_code INTEGER,
    started_at TEXT,
    finished_at TEXT,
    source_file TEXT NOT NULL,
    source_line INTEGER NOT NULL,
    raw_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS guard_decision_index (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT,
    action TEXT NOT NULL,
    risk_score REAL NOT NULL,
    selected_profile TEXT,
    recommended_profile TEXT,
    approved INTEGER NOT NULL DEFAULT 0,
    matched_rules_json TEXT NOT NULL,
    reasons_json TEXT NOT NULL,
    classification_json TEXT NOT NULL,
    created_at TEXT,
    source_file TEXT NOT NULL,
    source_line INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS discovery_snapshot_index (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT,
    runtime_kind TEXT,
    runtime_confidence REAL,
    runtime_model_state_json TEXT NOT NULL,
    helm_intelligence_state_json TEXT NOT NULL,
    hardware_json TEXT NOT NULL,
    strategy_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_task_status ON task_index(status);
CREATE INDEX IF NOT EXISTS idx_task_profile ON task_index(profile);
CREATE INDEX IF NOT EXISTS idx_guard_action ON guard_decision_index(action);
CREATE INDEX IF NOT EXISTS idx_guard_task_id ON guard_decision_index(task_id);
"""


def db_path_for_state_root(state_root: Path) -> Path:
    """Returns state_root / 'ops-index.sqlite3'."""
    return state_root / "ops-index.sqlite3"


def _check_schema_version(conn: sqlite3.Connection) -> None:
    try:
        row = conn.execute("SELECT value FROM schema_info WHERE key='schema_version'").fetchone()
        if row and row[0] != _SCHEMA_VERSION:
            import warnings
            warnings.warn(f"Database schema version {row[0]} != expected {_SCHEMA_VERSION}, consider rebuild")
    except sqlite3.OperationalError:
        pass  # schema_info table may not exist yet


def _connect(db_path: Path) -> sqlite3.Connection:
    """Open SQLite connection with standard pragmas."""
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    _check_schema_version(conn)
    return conn


def init_db(db_path: Path) -> None:
    """Create schema if missing. Sets WAL mode, busy_timeout=5000, foreign_keys=ON."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = _connect(db_path)
    try:
        conn.executescript(_DDL)
        conn.execute(
            "INSERT OR IGNORE INTO schema_info (key, value) VALUES (?, ?)",
            ("schema_version", _SCHEMA_VERSION),
        )
        conn.commit()
    finally:
        conn.close()


def _insert_task(conn: sqlite3.Connection, entry: dict, source_file: str, source_line: int) -> None:
    from uuid import uuid4
    task_id = entry.get("task_id")
    if not task_id:
        task_id = f"auto-{uuid4().hex[:12]}"
    conn.execute(
        """
        INSERT OR REPLACE INTO task_index
            (task_id, task_name, skill, profile, status, started_at, finished_at,
             source_file, source_line, raw_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            task_id,
            entry.get("task_name"),
            entry.get("skill"),
            entry.get("profile"),
            entry.get("status"),
            entry.get("started_at"),
            entry.get("finished_at"),
            source_file,
            source_line,
            json.dumps(entry, ensure_ascii=False),
        ),
    )


def _insert_guard(conn: sqlite3.Connection, entry: dict, source_file: str, source_line: int) -> None:
    guard = entry.get("guard")
    if not isinstance(guard, dict):
        return
    action = guard.get("action")
    if not action:
        return
    conn.execute(
        """
        INSERT INTO guard_decision_index
            (task_id, action, risk_score, selected_profile, recommended_profile,
             approved, matched_rules_json, reasons_json, classification_json,
             created_at, source_file, source_line)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            entry.get("task_id"),
            action,
            guard.get("risk_score") or 0.0,
            guard.get("selected_profile"),
            guard.get("recommended_profile"),
            1 if guard.get("approved") else 0,
            json.dumps(guard.get("matched_rules") or [], ensure_ascii=False),
            json.dumps(guard.get("reasons") or [], ensure_ascii=False),
            json.dumps(guard.get("classification") or {}, ensure_ascii=False),
            guard.get("created_at") or entry.get("started_at"),
            source_file,
            source_line,
        ),
    )


def _insert_discovery(conn: sqlite3.Connection, entry: dict) -> None:
    discovery = entry.get("discovery")
    if not isinstance(discovery, dict):
        return
    created_at = discovery.get("created_at") or entry.get("started_at") or ""
    if not created_at:
        return
    runtime = discovery.get("runtime") or {}
    conn.execute(
        """
        INSERT INTO discovery_snapshot_index
            (task_id, runtime_kind, runtime_confidence,
             runtime_model_state_json, helm_intelligence_state_json,
             hardware_json, strategy_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            entry.get("task_id"),
            runtime.get("kind"),
            runtime.get("confidence"),
            json.dumps(runtime.get("model_state") or {}, ensure_ascii=False),
            json.dumps(discovery.get("helm_intelligence_state") or {}, ensure_ascii=False),
            json.dumps(discovery.get("hardware") or {}, ensure_ascii=False),
            json.dumps(discovery.get("strategy") or {}, ensure_ascii=False),
            created_at,
        ),
    )


def rebuild_index(*, state_root: Path, db_path: Path | None = None) -> dict:
    """
    Rebuild SQLite index from JSONL source files.
    Returns {"task_rows": int, "command_rows": int, "guard_decision_rows": int, "discovery_rows": int, "warnings": list[str]}
    """
    if db_path is None:
        db_path = db_path_for_state_root(state_root)

    warnings: list[str] = []

    # Init DB (creates schema if missing)
    init_db(db_path)

    conn = _connect(db_path)
    try:
        # DROP and recreate data tables (keep schema_info)
        conn.executescript("""
            DROP TABLE IF EXISTS task_index;
            DROP TABLE IF EXISTS command_index;
            DROP TABLE IF EXISTS guard_decision_index;
            DROP TABLE IF EXISTS discovery_snapshot_index;
        """)
        conn.executescript(_DDL)
        conn.commit()

        task_rows = 0
        command_rows = 0
        guard_rows = 0
        discovery_rows = 0

        # Read task-ledger.jsonl
        ledger_path = state_root / "task-ledger.jsonl"
        if ledger_path.exists():
            try:
                with open(ledger_path, encoding="utf-8") as fh:
                    for lineno, line in enumerate(fh, start=1):
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            entry = json.loads(line)
                        except json.JSONDecodeError as exc:
                            warnings.append(f"task-ledger.jsonl line {lineno}: {exc}")
                            continue
                        if not isinstance(entry, dict):
                            warnings.append(f"task-ledger.jsonl line {lineno}: expected JSON object")
                            continue
                        _insert_task(conn, entry, "task-ledger.jsonl", lineno)
                        task_rows += 1
                        if entry.get("guard") and isinstance(entry["guard"], dict) and entry["guard"].get("action"):
                            _insert_guard(conn, entry, "task-ledger.jsonl", lineno)
                            guard_rows += 1
                        if entry.get("discovery") and isinstance(entry["discovery"], dict):
                            _insert_discovery(conn, entry)
                            discovery_rows += 1
            except OSError as exc:
                warnings.append(f"Could not read task-ledger.jsonl: {exc}")

        # Read command-log.jsonl if exists
        command_log_path = state_root / "command-log.jsonl"
        if command_log_path.exists():
            try:
                with open(command_log_path, encoding="utf-8") as fh:
                    for lineno, line in enumerate(fh, start=1):
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            entry = json.loads(line)
                        except json.JSONDecodeError as exc:
                            warnings.append(f"command-log.jsonl line {lineno}: {exc}")
                            continue
                        if not isinstance(entry, dict):
                            warnings.append(f"command-log.jsonl line {lineno}: expected JSON object")
                            continue
                        conn.execute(
                            """
                            INSERT INTO command_index
                                (task_id, command_preview, profile, exit_code, started_at, finished_at,
                                 source_file, source_line, raw_json)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                entry.get("task_id"),
                                entry.get("command_preview"),
                                entry.get("profile"),
                                entry.get("exit_code"),
                                entry.get("started_at"),
                                entry.get("finished_at"),
                                "command-log.jsonl",
                                lineno,
                                json.dumps(entry, ensure_ascii=False),
                            ),
                        )
                        command_rows += 1
            except OSError as exc:
                warnings.append(f"Could not read command-log.jsonl: {exc}")

        conn.commit()
    finally:
        conn.close()

    return {
        "task_rows": task_rows,
        "command_rows": command_rows,
        "guard_decision_rows": guard_rows,
        "discovery_rows": discovery_rows,
        "warnings": warnings,
    }


def index_task_entry(
    *,
    state_root: Path,
    entry: dict,
    source_file: str = "task-ledger.jsonl",
    source_line: int | None = None,
) -> None:
    """
    Best-effort index one task entry after JSONL append.
    Must not block task execution if SQLite fails.
    Wraps ALL operations in try/except — never raises.
    """
    global _INDEX_FAILURE_WARNED
    try:
        db_path = db_path_for_state_root(state_root)
        if str(db_path) not in _INITIALIZED_DBS:
            init_db(db_path)
            _INITIALIZED_DBS.add(str(db_path))
        conn = _connect(db_path)
        try:
            _insert_task(conn, entry, source_file, source_line if source_line is not None else 0)
            if entry.get("guard") and isinstance(entry["guard"], dict) and entry["guard"].get("action"):
                _insert_guard(conn, entry, source_file, source_line if source_line is not None else 0)
            if entry.get("discovery") and isinstance(entry["discovery"], dict):
                _insert_discovery(conn, entry)
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:
        if not _INDEX_FAILURE_WARNED:
            _warnings.warn(f"SQLite indexing failed: {exc}", stacklevel=2)
            _INDEX_FAILURE_WARNED = True


def verify_index(*, state_root: Path) -> dict:
    """
    Compare JSONL line count with SQLite row count and report drift.
    Returns {"jsonl_task_lines": int, "task_rows": int, "drift": bool}
    """
    ledger_path = state_root / "task-ledger.jsonl"
    jsonl_task_lines = 0
    if ledger_path.exists():
        try:
            with open(ledger_path, encoding="utf-8") as fh:
                jsonl_task_lines = sum(1 for line in fh if line.strip())
        except OSError:
            pass

    task_rows = 0
    db_path = db_path_for_state_root(state_root)
    if db_path.exists():
        try:
            conn = _connect(db_path)
            try:
                task_rows = conn.execute("SELECT COUNT(*) FROM task_index").fetchone()[0]
            finally:
                conn.close()
        except Exception:
            pass

    return {
        "jsonl_task_lines": jsonl_task_lines,
        "task_rows": task_rows,
        "drift": jsonl_task_lines != task_rows,
    }


def query_tasks(
    *,
    state_root: Path,
    status: str | None = None,
    profile: str | None = None,
    limit: int = 50,
) -> list[dict]:
    """Query task_index from SQLite."""
    db_path = db_path_for_state_root(state_root)
    if not db_path.exists():
        return []
    conn = _connect(db_path)
    try:
        query = "SELECT raw_json FROM task_index WHERE 1=1"
        params: list[str | int] = []
        if status:
            query += " AND status = ?"
            params.append(status)
        if profile:
            query += " AND profile = ?"
            params.append(profile)
        query += " ORDER BY started_at DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(query, params).fetchall()
        return [json.loads(r[0]) for r in rows]
    finally:
        conn.close()


def query_guard_decisions(
    *,
    state_root: Path,
    action: str | None = None,
    task_id: str | None = None,
    limit: int = 50,
) -> list[dict]:
    """Query guard_decision_index from SQLite."""
    db_path = db_path_for_state_root(state_root)
    if not db_path.exists():
        return []
    conn = _connect(db_path)
    try:
        query = "SELECT action, risk_score, selected_profile, task_id, reasons_json, matched_rules_json, created_at FROM guard_decision_index WHERE 1=1"
        params: list[str | int] = []
        if action:
            query += " AND action = ?"
            params.append(action)
        if task_id:
            query += " AND task_id = ?"
            params.append(task_id)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(query, params).fetchall()
        return [
            {
                "action": r[0],
                "risk_score": r[1],
                "selected_profile": r[2],
                "task_id": r[3],
                "reasons": json.loads(r[4]),
                "matched_rules": json.loads(r[5]),
                "created_at": r[6],
            }
            for r in rows
        ]
    finally:
        conn.close()
