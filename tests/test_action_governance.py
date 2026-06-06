from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.action_governance import (  # noqa: E402
    DECISION_RECORD_FIELDS,
    append_decision_record,
    evaluate_governed_action,
    load_registry,
    validate_evidence_contract,
)


def test_registry_loads_initial_prd_actions() -> None:
    registry = load_registry()

    assert len(registry.actions) >= 8
    assert registry.get("telegram_outbound") is not None
    assert registry.get("google_calendar_create") is not None
    assert registry.get("google_sheets_ledger_append") is not None
    assert registry.get("file_write") is not None
    assert registry.get("git_commit") is not None
    assert registry.get("git_push") is not None
    assert registry.get("cron_remove") is not None
    assert registry.get("smart_home_high_risk_control") is not None


def test_registry_policy_version_is_recorded_in_decision() -> None:
    registry = load_registry()
    record = evaluate_governed_action(
        user_message="Edit `/tmp/a.txt`",
        action_id="file_write",
        target="/tmp/a.txt",
        registry=registry,
    )

    assert record.policy_version == registry.policy_version


def test_unknown_action_fails_closed() -> None:
    record = evaluate_governed_action(
        user_message="Edit `/tmp/a.txt`",
        action_id="unregistered_tool_mutation",
        target="/tmp/a.txt",
    )

    assert record.decision == "deny"
    assert record.reason == "unregistered_action"


def test_inspect_scope_blocks_mutating_action() -> None:
    record = evaluate_governed_action(
        user_message="Check `/tmp/a.txt`",
        action_id="file_write",
        target="/tmp/a.txt",
    )

    assert record.decision == "inspect_only"
    assert record.reason == "inspect_scope_for_mutation"


def test_mutating_action_requires_explicit_target() -> None:
    record = evaluate_governed_action(
        user_message="Edit the file",
        action_id="file_write",
    )

    assert record.decision == "deny"
    assert record.reason in {"missing_explicit_target", "no_verb_detected"}


def test_live_source_required_blocks_until_confirmed() -> None:
    record = evaluate_governed_action(
        user_message="Update `event-123`",
        action_id="google_calendar_update",
        target="event-123",
    )

    assert record.decision == "deny"
    assert record.reason == "live_source_required"
    assert record.live_source_requirement is True


def test_approval_required_after_live_source_confirmation() -> None:
    record = evaluate_governed_action(
        user_message="Push `main`",
        action_id="git_push",
        target="main",
        live_source_confirmed=True,
    )

    assert record.decision == "require_approval"
    assert record.reason == "approval_required"
    assert record.approval_status == "pending"


def test_approval_allows_when_all_policy_inputs_satisfied() -> None:
    record = evaluate_governed_action(
        user_message="Push `main`",
        action_id="git_push",
        target="main",
        live_source_confirmed=True,
        approval_status="approved",
    )

    assert record.decision == "allow"
    assert record.reason == "policy_satisfied"


def test_rejected_approval_denies_action() -> None:
    record = evaluate_governed_action(
        user_message="Push `main`",
        action_id="git_push",
        target="main",
        live_source_confirmed=True,
        approval_status="rejected",
    )

    assert record.decision == "deny"
    assert record.reason == "approval_rejected"


def test_decision_record_has_standard_fields_and_hash_only_by_default() -> None:
    record = evaluate_governed_action(
        user_message="Edit `/tmp/a.txt` with private detail",
        action_id="file_write",
        target="/tmp/a.txt",
    )
    payload = record.as_dict()

    assert tuple(payload.keys()) == DECISION_RECORD_FIELDS
    assert payload["user_message_hash"]
    assert payload["user_message_redacted"] is None


def test_decision_record_can_include_redacted_message_preview() -> None:
    record = evaluate_governed_action(
        user_message="Edit `/tmp/a.txt` with private detail",
        action_id="file_write",
        target="/tmp/a.txt",
        include_redacted_message=True,
    )

    assert record.user_message_redacted == "Edit `/tmp/a.txt` with private detail"


def test_file_write_evidence_accepts_git_diff() -> None:
    result = validate_evidence_contract("file_write", {"git_diff": "diff --git..."})

    assert result.ok is True
    assert result.satisfied == ["git_diff"]


def test_git_push_evidence_requires_local_and_remote_head() -> None:
    missing = validate_evidence_contract("git_push", {"local_head": "abc"})
    satisfied = validate_evidence_contract(
        "git_push",
        {
            "local_head": "abc",
            "remote_head": "abc",
        },
    )

    assert missing.ok is False
    assert missing.missing_all == ["remote_head"]
    assert satisfied.ok is True


def test_evidence_contract_requires_one_of_when_configured() -> None:
    result = validate_evidence_contract("telegram_outbound", {"provider_result": "ok"})

    assert result.ok is False
    assert result.missing_one_of == ["message_id", "send_result"]


def test_unknown_action_evidence_fails_closed() -> None:
    result = validate_evidence_contract("unknown", {"provider_result": "ok"})

    assert result.ok is False
    assert result.missing_all == ["registered_action"]


def test_append_decision_record_writes_jsonl(tmp_path: Path) -> None:
    path = tmp_path / "governance-decisions.jsonl"
    record = evaluate_governed_action(
        user_message="Edit `/tmp/a.txt`",
        action_id="file_write",
        target="/tmp/a.txt",
    )

    append_decision_record(path, record)
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    assert len(rows) == 1
    assert rows[0]["attempted_action"] == "file_write"
    assert rows[0]["decision"] == "allow"


def test_append_decision_record_rejects_incomplete_payload(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="missing fields"):
        append_decision_record(tmp_path / "bad.jsonl", {"decision": "allow"})
