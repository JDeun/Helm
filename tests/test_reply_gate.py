from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest
from unittest.mock import patch

from scripts.reply_gate import evaluate, evaluate_claims, latest_entries, load_entries, select_entry


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


def test_claim_gate_blocks_completion_claim_without_evidence() -> None:
    entry = {
        "task_id": "t-claim",
        "task_name": "write file",
        "profile": "workspace_edit",
        "status": "completed",
        "completion_claims": [{"claim": "file_written", "evidence_type": "filesystem_stat"}],
        "meta": {"harness": {"enforcement_level": "light"}},
        "memory_capture": {"finalization_status": "capture_written"},
    }

    result = evaluate(entry)

    assert result["ok"] is False
    assert result["claim_gate"]["arbiter"] == "hold"
    assert result["claim_gate"]["refuter"]["missing_claims"] == ["file_written"]


def test_claim_gate_accepts_matching_evidence_reference() -> None:
    entry = {
        "task_id": "t-claim",
        "task_name": "write file",
        "profile": "workspace_edit",
        "status": "completed",
        "completion_claims": [{
            "claim": "file_written",
            "evidence_type": "filesystem_stat",
            "evidence_refs": ["filesystem_stat:docs/result.md"],
        }],
        "evidence_refs": ["filesystem_stat:docs/result.md"],
        "meta": {"harness": {"enforcement_level": "light"}},
        "memory_capture": {"finalization_status": "capture_written"},
    }

    result = evaluate(entry)

    assert result["ok"] is True
    assert result["claim_gate"]["arbiter"] == "pass"


def test_claim_gate_refutes_inspect_profile_mutation() -> None:
    entry = {
        "task_id": "t-inspect",
        "task_name": "inspect files",
        "profile": "inspect_local",
        "status": "completed",
        "active_workspace": {"planned_mutations": ["write docs/result.md"]},
        "meta": {"harness": {"enforcement_level": "light"}},
        "memory_capture": {"finalization_status": "capture_written"},
    }

    result = evaluate(entry)

    assert result["ok"] is False
    assert result["claim_gate"]["refuter"]["scope_violation"] is True


def test_claim_gate_rejects_wrong_evidence_type() -> None:
    entry = {
        "task_id": "t-wrong-evidence",
        "task_name": "push repository",
        "profile": "workspace_edit",
        "status": "completed",
        "completion_claims": [{"claim": "pushed", "evidence_type": "remote_head"}],
        "evidence_refs": ["filesystem_stat:.git/HEAD"],
        "meta": {"harness": {"enforcement_level": "light"}},
        "memory_capture": {"finalization_status": "capture_written"},
    }

    result = evaluate(entry)

    assert result["ok"] is False
    assert result["claim_gate"]["refuter"]["missing_claims"] == ["pushed"]


def test_claim_gate_rejects_malformed_evidence_type_and_empty_value() -> None:
    entry = {
        "profile": "workspace_edit",
        "completion_claims": [
            {"claim": "numeric", "evidence_type": 7, "evidence_refs": ["7:any"]},
            {"claim": "empty", "evidence_type": "test", "evidence_refs": ["test:", "test::x"]},
        ],
        "completion_evidence": ["7:any", "test:", "test::x"],
    }

    assert evaluate_claims(entry)["ok"] is False

    entry["completion_claims"] = [{"claim": "valid", "evidence_type": "test", "evidence_refs": ["test:pytest"]}]
    entry["completion_evidence"] = ["test:pytest"]
    assert evaluate_claims(entry)["ok"] is True

    entry["completion_claims"] = [
        {"claim_id": ["bad"], "claim": "invalid id", "evidence_type": "test", "evidence_refs": ["test:pytest"]}
    ]
    assert evaluate_claims(entry)["ok"] is False


def test_claim_gate_rejects_unstructured_claim() -> None:
    entry = {
        "task_id": "t-string-claim",
        "task_name": "write file",
        "profile": "workspace_edit",
        "status": "completed",
        "completion_claims": ["done"],
        "evidence_refs": ["filesystem_stat:result.md"],
        "meta": {"harness": {"enforcement_level": "light"}},
        "memory_capture": {"finalization_status": "capture_written"},
    }

    result = evaluate(entry)

    assert result["ok"] is False
    assert result["claim_gate"]["claims"][0]["reason"] == "claim_not_structured"


def test_claim_gate_uses_completion_evidence_and_enforces_prerequisites() -> None:
    entry = {
        "task_id": "t-dependent-claims",
        "task_name": "prepare merge",
        "profile": "workspace_edit",
        "status": "completed",
        "completion_claims": [
            {
                "claim_id": "merge_ready",
                "claim": "merge ready",
                "evidence_type": "review",
                "evidence_refs": ["review:passed"],
                "depends_on": ["verified"],
            },
            {
                "criterion_id": "verified",
                "claim": "verification passed",
                "evidence_type": "test",
                "evidence_refs": ["test:pytest"],
            },
        ],
        "evidence_refs": ["review:passed"],
        "active_workspace": {"evidence_refs": ["test:pytest"]},
        "completion_evidence": ["test:pytest"],
        "meta": {"harness": {"enforcement_level": "light"}},
        "memory_capture": {"finalization_status": "capture_written"},
    }

    assert evaluate(entry)["ok"] is True

    entry["completion_evidence"] = []
    claims = evaluate(entry)["claim_gate"]["claims"]
    merge_ready = next(item for item in claims if item.get("claim_id") == "merge_ready")
    assert merge_ready["ok"] is False
    assert merge_ready["missing_dependencies"] == ["verified"]

    legacy = {
        "profile": "workspace_edit",
        "completion_claims": [{"claim": "legacy", "evidence_type": "test"}],
        "evidence_refs": ["test:pytest"],
    }
    assert evaluate_claims(legacy)["ok"] is True
    legacy["completion_claims"][0]["claim_id"] = "strict"
    assert evaluate_claims(legacy)["ok"] is False


def test_claim_gate_rejects_dependency_cycles() -> None:
    entry = {
        "profile": "workspace_edit",
        "completion_claims": [
            {"claim_id": "a", "claim": "a", "evidence_type": "test", "depends_on": ["b"]},
            {"claim_id": "b", "claim": "b", "evidence_type": "review", "depends_on": ["a"]},
        ],
        "completion_evidence": ["test:passed", "review:passed"],
    }

    result = evaluate_claims(entry)

    assert result["ok"] is False
    assert all(item["reason"] == "claim_dependency_cycle" for item in result["claims"])


def test_reply_gate_blocks_recorded_openclaw_finalization_failure() -> None:
    entry = {
        "task_id": "t-recorded-gate",
        "task_name": "inspect",
        "profile": "inspect_local",
        "status": "completed",
        "finalization_gate": {"ok": False, "arbiter": "hold"},
        "meta": {"harness": {"enforcement_level": "light"}},
        "memory_capture": {"finalization_status": "capture_written"},
    }
    assert evaluate(entry)["ok"] is False

    entry["finalization_gate"] = "malformed"
    assert evaluate(entry)["ok"] is False


# ---------------------------------------------------------------------------
# Advisory Phase-A / Phase-F wiring (R2 I1)
# ---------------------------------------------------------------------------


def test_evaluate_attaches_advisory_action_scope_when_task_name_has_verb() -> None:
    """A task with a Korean verb in task_name surfaces an action_scope advisory."""
    entry = {
        "task_id": "t1",
        "task_name": "회의록 수정합니다",  # contains an EDIT verb
        "skill": "my-skill",
        "profile": "workspace_edit",
        "status": "completed",
        "meta": {"harness": {"enforcement_level": "light"}},
        "memory_capture": {"finalization_status": "capture_written"},
    }
    result = evaluate(entry)
    advisory = result.get("advisory") or {}
    # action_scope advisory should exist and identify the EDIT scope.
    assert "action_scope" in advisory
    assert advisory["action_scope"]["advisory_only"] is True
    assert advisory["action_scope"]["locked_scope"] == "edit"


def test_evaluate_advisory_failure_does_not_block_decision(monkeypatch) -> None:
    """If the action_scope module raises, advisory is omitted but ok= still valid."""
    entry = {
        "task_id": "t1",
        "task_name": "test",
        "status": "completed",
        "meta": {"harness": {"enforcement_level": "light"}},
        "memory_capture": {"finalization_status": "capture_written"},
    }

    def explode(*_args, **_kwargs):
        raise RuntimeError("synthetic action_scope failure")

    import scripts.action_scope as scope
    monkeypatch.setattr(scope, "evaluate", explode)

    result = evaluate(entry)
    # The reply decision must still be made.
    assert result["ok"] in (True, False)
    # action_scope advisory must have been swallowed.
    advisory = result.get("advisory") or {}
    assert "action_scope" not in advisory


def test_evaluate_advisory_failure_increments_counter(monkeypatch) -> None:
    """R5 M2: advisory swallow site records the failure for observability.

    Pre-R5 the bare ``except Exception: pass`` made an advisory-channel
    regression indistinguishable from "no advisory applicable". This
    test pins the counter contract so a future refactor cannot drop
    the breadcrumb.
    """
    from scripts.advisory_log import (
        reset_advisory_failures,
        snapshot_advisory_failures,
    )

    reset_advisory_failures()
    entry = {
        "task_id": "t1",
        "task_name": "test",
        "status": "completed",
        "meta": {"harness": {"enforcement_level": "light"}},
        "memory_capture": {"finalization_status": "capture_written"},
    }

    import scripts.action_scope as scope

    def explode(*_args, **_kwargs):
        raise RuntimeError("synthetic action_scope failure")

    monkeypatch.setattr(scope, "evaluate", explode)
    evaluate(entry)

    snapshot = snapshot_advisory_failures()
    # Generic channel key counts the failure, regardless of exception type.
    assert snapshot.get("reply_gate.action_scope", 0) >= 1
    # Type-qualified key allows callers to attribute the failure.
    assert snapshot.get("reply_gate.action_scope:RuntimeError", 0) >= 1
    reset_advisory_failures()
