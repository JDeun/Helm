from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest
from unittest.mock import patch

from scripts.reply_gate import evaluate, latest_entries, load_entries, select_entry


# ---------------------------------------------------------------------------
# load_entries
# ---------------------------------------------------------------------------

def test_load_entries_valid_jsonl(tmp_path: Path) -> None:
    ledger = tmp_path / "task-ledger.jsonl"
    rows = [
        {"task_id": "t1", "status": "completed"},
        {"task_id": "t2", "status": "failed"},
    ]
    ledger.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")

    result = load_entries(ledger)

    assert len(result) == 2
    assert result[0]["task_id"] == "t1"
    assert result[1]["task_id"] == "t2"


def test_load_entries_skips_malformed_lines(tmp_path: Path) -> None:
    ledger = tmp_path / "task-ledger.jsonl"
    ledger.write_text(
        '{"task_id":"good","status":"completed"}\nnot-json\n{"task_id":"also-good","status":"failed"}\n',
        encoding="utf-8",
    )

    result = load_entries(ledger)

    assert len(result) == 2
    assert result[0]["task_id"] == "good"
    assert result[1]["task_id"] == "also-good"


def test_load_entries_nonexistent_file(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist.jsonl"

    result = load_entries(missing)

    assert result == []


def test_load_entries_custom_path(tmp_path: Path) -> None:
    ledger = tmp_path / "custom.jsonl"
    ledger.write_text('{"task_id":"custom-1","status":"completed"}\n', encoding="utf-8")

    result = load_entries(ledger)

    assert len(result) == 1
    assert result[0]["task_id"] == "custom-1"


def test_load_entries_default_path_uses_task_ledger(tmp_path: Path) -> None:
    """When no path is provided, load_entries resolves via _get_task_ledger."""
    ledger = tmp_path / "task-ledger.jsonl"
    ledger.write_text('{"task_id":"default-1","status":"completed"}\n', encoding="utf-8")

    with patch("scripts.reply_gate._get_task_ledger", return_value=ledger):
        result = load_entries()

    assert len(result) == 1
    assert result[0]["task_id"] == "default-1"


# ---------------------------------------------------------------------------
# latest_entries — deduplication by task_id
# ---------------------------------------------------------------------------

def test_latest_entries_keeps_last_entry_per_task_id() -> None:
    entries = [
        {"task_id": "t1", "status": "failed", "finished_at": "2024-01-01T00:00:00Z"},
        {"task_id": "t1", "status": "completed", "finished_at": "2024-01-02T00:00:00Z"},
        {"task_id": "t2", "status": "completed", "finished_at": "2024-01-01T00:00:00Z"},
    ]

    result = latest_entries(entries)

    # t1 should be the last seen (completed), t2 also present
    by_id = {e["task_id"]: e for e in result}
    assert by_id["t1"]["status"] == "completed"
    assert by_id["t2"]["status"] == "completed"
    assert len(result) == 2


def test_latest_entries_entries_without_task_id_are_excluded() -> None:
    entries = [
        {"task_id": "t1", "status": "completed"},
        {"status": "completed"},  # no task_id
    ]

    result = latest_entries(entries)

    assert len(result) == 1
    assert result[0]["task_id"] == "t1"


# ---------------------------------------------------------------------------
# select_entry
# ---------------------------------------------------------------------------

def test_select_entry_with_specific_task_id(tmp_path: Path) -> None:
    ledger = tmp_path / "task-ledger.jsonl"
    rows = [
        {"task_id": "t1", "status": "completed", "finished_at": "2024-01-01T10:00:00Z"},
        {"task_id": "t2", "status": "completed", "finished_at": "2024-01-01T11:00:00Z"},
    ]
    ledger.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")

    with patch("scripts.reply_gate._get_task_ledger", return_value=ledger):
        result = select_entry("t1")

    assert result is not None
    assert result["task_id"] == "t1"


def test_select_entry_unknown_task_id_returns_none(tmp_path: Path) -> None:
    ledger = tmp_path / "task-ledger.jsonl"
    ledger.write_text('{"task_id":"t1","status":"completed"}\n', encoding="utf-8")

    with patch("scripts.reply_gate._get_task_ledger", return_value=ledger):
        result = select_entry("does-not-exist")

    assert result is None


def test_select_entry_without_task_id_returns_latest_by_timestamp(tmp_path: Path) -> None:
    ledger = tmp_path / "task-ledger.jsonl"
    rows = [
        {"task_id": "t1", "status": "completed", "finished_at": "2024-01-01T09:00:00Z"},
        {"task_id": "t2", "status": "completed", "finished_at": "2024-01-01T12:00:00Z"},
    ]
    ledger.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")

    with patch("scripts.reply_gate._get_task_ledger", return_value=ledger):
        result = select_entry(None)

    assert result is not None
    assert result["task_id"] == "t2"


def test_select_entry_empty_ledger_returns_none(tmp_path: Path) -> None:
    ledger = tmp_path / "task-ledger.jsonl"
    ledger.write_text("", encoding="utf-8")

    with patch("scripts.reply_gate._get_task_ledger", return_value=ledger):
        result = select_entry(None)

    assert result is None


# ---------------------------------------------------------------------------
# evaluate — light enforcement
# ---------------------------------------------------------------------------

def test_evaluate_none_entry_returns_task_not_found() -> None:
    result = evaluate(None)

    assert result["ok"] is False
    assert result["reason"] == "task_not_found"
    assert result["task"] is None


def test_evaluate_light_enforcement_always_passes_finalization() -> None:
    entry = {
        "task_id": "t1",
        "task_name": "do something",
        "status": "completed",
        "meta": {"harness": {"enforcement_level": "light"}},
        "memory_capture": {"finalization_status": "unknown"},
    }

    result = evaluate(entry)

    assert result["ok"] is True
    finalization_check = next(c for c in result["checks"] if c["name"] == "finalization")
    assert finalization_check["ok"] is True


def test_evaluate_light_enforcement_does_not_require_task_name() -> None:
    entry = {
        "task_id": "t1",
        "task_name": None,
        "status": "completed",
        "meta": {"harness": {"enforcement_level": "light"}},
        "memory_capture": {"finalization_status": "capture_written"},
    }

    result = evaluate(entry)

    task_name_check = next(c for c in result["checks"] if c["name"] == "task_name")
    assert task_name_check["ok"] is True


# ---------------------------------------------------------------------------
# evaluate — balanced enforcement
# ---------------------------------------------------------------------------

def test_evaluate_balanced_enforcement_requires_task_name() -> None:
    entry = {
        "task_id": "t1",
        "task_name": None,
        "status": "completed",
        "meta": {"harness": {
            "enforcement_level": "balanced",
            "skill_contract_present": True,
        }},
        "memory_capture": {"finalization_status": "capture_written"},
    }

    result = evaluate(entry)

    task_name_check = next(c for c in result["checks"] if c["name"] == "task_name")
    assert task_name_check["ok"] is False
    assert result["ok"] is False


def test_evaluate_balanced_enforcement_fails_on_unknown_finalization() -> None:
    entry = {
        "task_id": "t1",
        "task_name": "deploy service",
        "status": "completed",
        "meta": {"harness": {
            "enforcement_level": "balanced",
            "skill_contract_present": True,
        }},
        "memory_capture": {"finalization_status": "unknown"},
    }

    result = evaluate(entry)

    finalization_check = next(c for c in result["checks"] if c["name"] == "finalization")
    assert finalization_check["ok"] is False
    assert result["ok"] is False


def test_evaluate_balanced_enforcement_passes_with_all_fields() -> None:
    entry = {
        "task_id": "t1",
        "task_name": "deploy service",
        "status": "completed",
        "meta": {"harness": {
            "enforcement_level": "balanced",
            "skill_contract_present": True,
            "context_required": False,
            "context_satisfied": False,
        }},
        "memory_capture": {"finalization_status": "capture_written"},
    }

    result = evaluate(entry)

    assert result["ok"] is True
    assert result["reason"] == "reply_allowed"


def test_evaluate_failed_status_is_not_ok() -> None:
    entry = {
        "task_id": "t1",
        "task_name": "some task",
        "status": "failed",
        "meta": {"harness": {"enforcement_level": "light"}},
        "memory_capture": {"finalization_status": "capture_written"},
    }

    result = evaluate(entry)

    task_status_check = next(c for c in result["checks"] if c["name"] == "task_status")
    assert task_status_check["ok"] is False
    assert result["ok"] is False


def test_evaluate_result_contains_task_summary() -> None:
    entry = {
        "task_id": "t1",
        "task_name": "test task",
        "skill": "my-skill",
        "profile": "inspect_local",
        "status": "completed",
        "meta": {"harness": {
            "enforcement_level": "light",
            "skill_contract_present": True,
        }},
        "memory_capture": {"finalization_status": "capture_written"},
    }

    result = evaluate(entry)

    assert result["task"]["task_id"] == "t1"
    assert result["task"]["task_name"] == "test task"
    assert result["task"]["skill"] == "my-skill"
    assert result["task"]["profile"] == "inspect_local"
    assert result["task"]["enforcement_level"] == "light"
