from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from scripts.action_scope import (
    MUTABLE_RESOURCES,
    ActionScopeDecision,
    ActionScopeKind,
    attempted_action_allowed,
    detect_verbs,
    evaluate,
    extract_targets,
)


REPO_ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# Korean verb detection
# ---------------------------------------------------------------------------


def test_korean_inspect_verb_locks_to_inspect() -> None:
    decision = evaluate("캘린더 일정 확인해봐")
    assert decision.locked_scope == ActionScopeKind.INSPECT
    assert decision.allowed is True
    assert decision.requires_target is False
    # No mutation may slip past inspect.
    assert decision.forbids(ActionScopeKind.EDIT)
    assert decision.forbids(ActionScopeKind.DELETE)
    assert decision.forbids(ActionScopeKind.EXTERNAL_SEND)


def test_korean_delete_with_target_is_allowed() -> None:
    decision = evaluate("성균관대 일정 삭제해")
    assert decision.locked_scope == ActionScopeKind.DELETE
    # `성균관대` is bare Korean text without quoting or path, but we
    # accept the explicit target via the override path - more
    # importantly the gate refuses if no target is supplied.
    decision_with_target = evaluate(
        "성균관대 일정 삭제해", explicit_targets=("성균관대 일정",)
    )
    assert decision_with_target.allowed is True
    assert decision_with_target.requires_target is True
    assert "성균관대 일정" in decision_with_target.targets


def test_korean_delete_without_target_is_refused() -> None:
    decision = evaluate("삭제해")
    assert decision.locked_scope == ActionScopeKind.DELETE
    assert decision.requires_target is True
    assert decision.refusal_reason == "missing_explicit_target"
    assert decision.allowed is False


def test_korean_save_verb_locks_to_save_not_edit() -> None:
    # "문서로 남겨" is the canonical save-only utterance that caused the
    # past "skill modified instead of documented" incident.
    decision = evaluate("이거 문서로 남겨")
    assert decision.locked_scope == ActionScopeKind.SAVE
    assert decision.forbids(ActionScopeKind.EDIT) is True
    assert decision.forbids(ActionScopeKind.DELETE) is True


def test_korean_external_send_requires_target() -> None:
    decision = evaluate("Telegram으로 보내", explicit_targets=("Telegram",))
    assert decision.locked_scope == ActionScopeKind.EXTERNAL_SEND
    assert decision.allowed is True


def test_narrowest_verb_wins_when_both_present() -> None:
    # If a single message contains both inspect and delete verbs, the
    # narrowest (inspect) wins - we never auto-promote.
    decision = evaluate("일정 확인해보고 필요하면 삭제해")
    assert decision.locked_scope == ActionScopeKind.INSPECT


# ---------------------------------------------------------------------------
# English verb detection
# ---------------------------------------------------------------------------


def test_english_inspect_verb() -> None:
    decision = evaluate("Check the calendar events for tomorrow")
    assert decision.locked_scope == ActionScopeKind.INSPECT


def test_english_edit_with_quoted_target() -> None:
    decision = evaluate('Update the "household-ledger" sheet header')
    assert decision.locked_scope == ActionScopeKind.EDIT
    assert "household-ledger" in decision.targets
    assert decision.allowed is True


def test_english_delete_without_target_refused() -> None:
    decision = evaluate("delete it")
    assert decision.locked_scope == ActionScopeKind.DELETE
    assert decision.refusal_reason == "missing_explicit_target"


def test_english_external_send_push() -> None:
    decision = evaluate("Push the branch", explicit_targets=("main",))
    assert decision.locked_scope == ActionScopeKind.EXTERNAL_SEND
    assert decision.allowed is True


def test_no_verb_detected_returns_refusal() -> None:
    decision = evaluate("그냥 한 마디")
    assert decision.locked_scope is None
    assert decision.refusal_reason == "no_verb_detected"
    assert decision.allowed is False


# ---------------------------------------------------------------------------
# Verb detection edge cases
# ---------------------------------------------------------------------------


def test_detect_verbs_dedups_overlap() -> None:
    matches = detect_verbs("확인해봐")
    # "확인해" and "확인해봐" both match; the longer must win.
    assert len(matches) == 1
    assert matches[0].verb == "확인해봐"
    assert matches[0].scope == ActionScopeKind.INSPECT


def test_detect_verbs_ignores_english_substring_within_word() -> None:
    # "checkpoint" must NOT trigger inspect, because "check" is matched
    # with word boundaries.
    matches = detect_verbs("Read the checkpoint file")
    scopes = {m.scope for m in matches}
    assert ActionScopeKind.INSPECT in scopes
    # The matched verb should be "Read", not "check".
    verbs = {m.verb.lower() for m in matches}
    assert "check" not in verbs
    assert "read" in verbs


def test_extract_targets_picks_paths_and_quotes() -> None:
    targets = extract_targets('Edit ~/.openclaw/MEMORY.md and "skill-x"')
    assert "~/.openclaw/MEMORY.md" in targets
    assert "skill-x" in targets


def test_extract_targets_korean_classifier_suffix_phrase() -> None:
    """Conservative noun+classifier heuristic: ``성균관대 일정`` is
    extracted, freeform prose is not."""
    targets = extract_targets("성균관대 일정 삭제해")
    assert "성균관대 일정" in targets


def test_extract_targets_korean_multiple_classifier_phrases() -> None:
    targets = extract_targets("개인 노트와 회의 메모 정리해줘")
    assert "개인 노트" in targets
    assert "회의 메모" in targets


def test_extract_targets_korean_freeform_prose_returns_empty() -> None:
    """Freeform Korean without a classifier must NOT be extracted."""
    targets = extract_targets("그냥 한 마디 알려줘")
    # Only the classifier suffixes seed targets — none here.
    assert targets == []


def test_korean_delete_with_classifier_target_auto_extracts() -> None:
    """End-to-end: the Korean heuristic should now auto-resolve
    ``성균관대 일정`` so the caller no longer must pass
    ``explicit_targets``."""
    decision = evaluate("성균관대 일정 삭제해")
    assert decision.locked_scope == ActionScopeKind.DELETE
    assert decision.requires_target is True
    # auto-extracted target satisfies the requirement now
    assert "성균관대 일정" in decision.targets
    assert decision.allowed is True
    assert decision.refusal_reason is None


# ---------------------------------------------------------------------------
# Live-source gating
# ---------------------------------------------------------------------------


def test_live_source_required_for_sheets_topic() -> None:
    decision = evaluate(
        "주택대출 납입액 알려줘",
        topics=("google_sheets",),
    )
    assert decision.locked_scope == ActionScopeKind.INSPECT
    assert decision.needs_live_source is True
    assert any("live source" in note for note in decision.annotations)


def test_live_source_required_for_social_profile_high_risk_topic() -> None:
    decision = evaluate(
        "이 계정 어떤 사람인지 알려줘",
        topics=("social_profile_high_risk",),
    )
    assert decision.locked_scope == ActionScopeKind.INSPECT
    assert decision.needs_live_source is True
    assert any("social_profile_high_risk" in note for note in decision.annotations)


def test_social_privacy_gate_blocks_high_risk_profile_without_requirements() -> None:
    decision = evaluate("이 사람 Bluesky 최근 글 전부 수집해서 정치 성향 dossier 알려줘")
    assert decision.locked_scope == ActionScopeKind.INSPECT
    assert decision.allowed is False
    assert decision.needs_live_source is True
    assert decision.refusal_reason == "social_privacy_requirements_missing"
    assert decision.privacy_risk == "high"
    assert decision.privacy_missing_requirements == {
        "consent": True,
        "purpose": True,
        "scope": True,
        "retention": True,
    }


def test_social_privacy_gate_allows_high_risk_when_requirements_are_present() -> None:
    decision = evaluate(
        "허락 받은 계정 최근 30개 글로 보안 점검 목적의 정치 성향 dossier 알려줘. 저장하지 마."
    )
    assert decision.locked_scope == ActionScopeKind.INSPECT
    assert decision.allowed is True
    assert decision.needs_live_source is True
    assert decision.privacy_risk == "high"
    assert decision.privacy_missing_requirements == {
        "consent": False,
        "purpose": False,
        "scope": False,
        "retention": False,
    }


def test_live_source_not_set_for_unrelated_topic() -> None:
    decision = evaluate("일정 확인해", topics=("openclaw_skills",))
    assert decision.needs_live_source is False


# ---------------------------------------------------------------------------
# Cross-check (resource binding)
# ---------------------------------------------------------------------------


def test_attempted_action_allowed_blocks_mutation_under_inspect() -> None:
    decision = evaluate("캘린더 확인해")
    ok, reason = attempted_action_allowed(
        decision, ActionScopeKind.DELETE, resource="google_calendar"
    )
    assert ok is False
    assert reason is not None
    assert "forbids" in reason


def test_attempted_action_allowed_permits_matching_scope() -> None:
    decision = evaluate(
        '성균관대 일정 삭제해', explicit_targets=("성균관대 일정",)
    )
    ok, reason = attempted_action_allowed(
        decision, ActionScopeKind.DELETE, resource="google_calendar"
    )
    assert ok is True
    assert reason is None


def test_attempted_action_blocks_save_to_existing_obsidian() -> None:
    # "문서로 남겨" is save scope; obsidian_vault_existing accepts only
    # edit/delete - new save targets must go to a fresh note.
    decision = evaluate("이거 문서로 남겨", explicit_targets=("vault-note",))
    ok, reason = attempted_action_allowed(
        decision, ActionScopeKind.SAVE, resource="obsidian_vault_existing"
    )
    assert ok is False
    assert reason is not None


def test_attempted_action_unknown_resource_is_rejected() -> None:
    decision = evaluate("저장해", explicit_targets=("x",))
    ok, reason = attempted_action_allowed(
        decision, ActionScopeKind.SAVE, resource="bogus_resource"
    )
    assert ok is False
    assert "unknown_resource" in (reason or "")


# ---------------------------------------------------------------------------
# Mutable-resource catalog
# ---------------------------------------------------------------------------


def test_mutable_resources_cover_design_spec() -> None:
    # Sanity check that all ten resources from design §6.4 are present.
    expected = {
        "google_calendar",
        "google_sheets",
        "cron_jobs",
        "openclaw_memory",
        "openclaw_skills",
        "helm_memory_tree",
        "obsidian_vault_existing",
        "downloads_existing",
        "telegram_outbound",
        "git_repository",
    }
    assert expected.issubset(set(MUTABLE_RESOURCES))


def test_mutable_resources_have_verbs_required() -> None:
    for name, meta in MUTABLE_RESOURCES.items():
        verbs = meta["verbs_required"]
        assert isinstance(verbs, tuple) and len(verbs) >= 1, name
        for verb in verbs:
            assert isinstance(verb, ActionScopeKind), name


# ---------------------------------------------------------------------------
# Incident-replay tests (regression locks)
# ---------------------------------------------------------------------------


def test_incident_skku_inspect_cannot_delete() -> None:
    """
    Replay of the Sungkyunkwan incident: user said "확인해", agent
    deleted the event. The gate must refuse delete here.
    """
    decision = evaluate("성균관대 일정 확인해")
    assert decision.locked_scope == ActionScopeKind.INSPECT
    ok, _ = attempted_action_allowed(
        decision, ActionScopeKind.DELETE, resource="google_calendar"
    )
    assert ok is False


def test_incident_document_only_does_not_modify_skill() -> None:
    """
    Replay of the "문서로 남겨" incident: user wanted a document,
    agent edited the skill. save scope must not pass an edit check.
    """
    decision = evaluate("이걸 문서로 남겨", explicit_targets=("ledger-note",))
    assert decision.locked_scope == ActionScopeKind.SAVE
    ok, _ = attempted_action_allowed(
        decision, ActionScopeKind.EDIT, resource="openclaw_skills"
    )
    assert ok is False


def test_incident_memory_only_assertion_marks_live_source_required() -> None:
    """
    Replay of the mortgage-memory over-estimate: the inspect on a
    Sheets-backed topic must surface needs_live_source so the answer
    is annotated as "메모리 기준 추정" if the sheet is not consulted.
    """
    decision = evaluate(
        "주택대출 납입액 얼마인지 알려줘",
        topics=("google_sheets",),
    )
    assert decision.locked_scope == ActionScopeKind.INSPECT
    assert decision.needs_live_source is True


# ---------------------------------------------------------------------------
# CLI smoke test
# ---------------------------------------------------------------------------


def test_cli_outputs_valid_json() -> None:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT)
    proc = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "action_scope.py"),
            "--message",
            "캘린더 일정 확인해봐",
        ],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["locked_scope"] == "inspect"
    assert payload["allowed"] is True


def test_cli_attempt_check_reports_blocked() -> None:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT)
    proc = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "action_scope.py"),
            "--message",
            "캘린더 확인해",
            "--attempt",
            "delete",
            "--resource",
            "google_calendar",
        ],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert proc.returncode == 0
    payload = json.loads(proc.stdout)
    assert payload["attempt"]["allowed"] is False


# ---------------------------------------------------------------------------
# Decision dict round-trip
# ---------------------------------------------------------------------------


def test_decision_as_dict_round_trip() -> None:
    decision = evaluate("일정 확인해")
    data = decision.as_dict()
    assert data["locked_scope"] == "inspect"
    assert data["allowed"] is True
    # JSON-serialisable
    json.dumps(data, ensure_ascii=False)
