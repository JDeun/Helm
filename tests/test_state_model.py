# tests/test_state_model.py
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from helm_state_model import (
    APPLIED_PHRASES,
    CAPTURED_PHRASES,
    PROMOTED_PHRASES,
    PhraseLintError,
    PromotionEvidence,
    RAW_CAPTURE_RETENTION_DAYS,
    State,
    TransitionError,
    allowed_transitions,
    assert_transition,
    can_promote,
    can_transition,
    is_prunable,
    lint_telegram_phrase,
    promotion_reason,
    retention_tier,
)


# ---------------------------------------------------------------------------
# State enum
# ---------------------------------------------------------------------------


def test_state_values_match_design() -> None:
    assert {s.value for s in State} == {
        "captured",
        "reviewed",
        "applied",
        "promoted",
        "rejected",
    }


def test_state_parse_accepts_str_and_instance() -> None:
    assert State.parse("captured") is State.CAPTURED
    assert State.parse("PROMOTED") is State.PROMOTED
    assert State.parse(State.APPLIED) is State.APPLIED


def test_state_parse_rejects_unknown() -> None:
    with pytest.raises(ValueError):
        State.parse("nonsense")
    with pytest.raises(TypeError):
        State.parse(123)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Transition graph
# ---------------------------------------------------------------------------


def test_forward_path_happy() -> None:
    assert can_transition(State.CAPTURED, State.REVIEWED)
    assert can_transition(State.REVIEWED, State.APPLIED)
    # applied -> promoted is gated separately, but the static graph allows it.
    assert can_transition(State.APPLIED, State.PROMOTED)


def test_any_to_rejected_allowed_except_from_rejected() -> None:
    for src in (State.CAPTURED, State.REVIEWED, State.APPLIED, State.PROMOTED):
        assert can_transition(src, State.REJECTED), src
    assert not can_transition(State.REJECTED, State.CAPTURED)


def test_reviewed_to_captured_forbidden() -> None:
    """Design §5.2: 역행 금지 (reviewed -> captured)."""

    assert not can_transition(State.REVIEWED, State.CAPTURED)
    with pytest.raises(TransitionError):
        assert_transition(State.REVIEWED, State.CAPTURED)


def test_promoted_to_applied_requires_incident_or_user() -> None:
    assert not can_transition(State.PROMOTED, State.APPLIED)
    assert can_transition(State.PROMOTED, State.APPLIED, incident_id="INC-1")
    assert can_transition(State.PROMOTED, State.APPLIED, user_explicit=True)


def test_allowed_transitions_returns_frozenset() -> None:
    out = allowed_transitions(State.CAPTURED)
    assert isinstance(out, frozenset)
    assert State.REVIEWED in out and State.REJECTED in out


def test_rejected_is_terminal_in_auto_graph() -> None:
    assert allowed_transitions(State.REJECTED) == frozenset()


# ---------------------------------------------------------------------------
# applied -> promoted 3-way gate
# ---------------------------------------------------------------------------


def test_promotion_user_explicit() -> None:
    ev = PromotionEvidence(user_explicit=True)
    assert can_promote(ev)
    assert promotion_reason(ev) == "user_explicit"


def test_promotion_recurrence_threshold() -> None:
    ev = PromotionEvidence(recurrence_count=3, recurrence_threshold=3)
    assert can_promote(ev)
    assert promotion_reason(ev) == "recurrence_threshold"


def test_promotion_recurrence_blocked_by_side_effects() -> None:
    ev = PromotionEvidence(
        recurrence_count=5,
        recurrence_threshold=3,
        side_effects_observed=True,
    )
    assert not can_promote(ev)


def test_promotion_confidence_scope_clause() -> None:
    ev = PromotionEvidence(confidence=0.96, scope="ops/low")
    assert can_promote(ev)
    assert promotion_reason(ev) == "confidence_scope_ops_low"


def test_promotion_confidence_just_below_threshold_blocks() -> None:
    ev = PromotionEvidence(confidence=0.94, scope="ops/low")
    assert not can_promote(ev)


def test_promotion_confidence_high_but_wrong_scope_blocks() -> None:
    # High confidence but scope is not ops/low.
    ev = PromotionEvidence(confidence=0.99, scope="household/budget")
    assert not can_promote(ev)


def test_promotion_no_clause_returns_none() -> None:
    ev = PromotionEvidence()
    assert not can_promote(ev)
    assert promotion_reason(ev) is None


# ---------------------------------------------------------------------------
# Retention tier (raw capture vs durable)
# ---------------------------------------------------------------------------


def test_retention_tier_classification() -> None:
    assert retention_tier(State.CAPTURED) == "raw_capture"
    assert retention_tier(State.REJECTED) == "raw_capture"
    assert retention_tier(State.REVIEWED) == "durable"
    assert retention_tier(State.APPLIED) == "durable"
    assert retention_tier(State.PROMOTED) == "durable"


def test_raw_capture_retention_constant() -> None:
    assert RAW_CAPTURE_RETENTION_DAYS == 30


def test_is_prunable_only_after_window() -> None:
    now = datetime(2026, 6, 30, tzinfo=timezone.utc)
    fresh = now - timedelta(days=5)
    stale = now - timedelta(days=31)

    assert not is_prunable(State.CAPTURED, fresh, now=now)
    assert is_prunable(State.CAPTURED, stale, now=now)


def test_is_prunable_promoted_never() -> None:
    now = datetime(2026, 6, 30, tzinfo=timezone.utc)
    very_old = now - timedelta(days=365)
    assert not is_prunable(State.PROMOTED, very_old, now=now)


def test_is_prunable_handles_naive_datetime() -> None:
    now = datetime(2026, 6, 30, tzinfo=timezone.utc)
    naive_stale = datetime(2026, 5, 1)  # naive => treated as UTC
    assert is_prunable(State.CAPTURED, naive_stale, now=now)


# ---------------------------------------------------------------------------
# Telegram phrase lint
# ---------------------------------------------------------------------------


def test_promoted_phrase_requires_promoted_state() -> None:
    text = "운영 규칙으로 반영했습니다."
    with pytest.raises(PhraseLintError):
        lint_telegram_phrase(text, State.APPLIED)
    # Promoted is OK.
    lint_telegram_phrase(text, State.PROMOTED)


def test_applied_phrase_requires_applied_or_promoted() -> None:
    text = "정책에 1차 반영했습니다 (롤백 가능)."
    with pytest.raises(PhraseLintError):
        lint_telegram_phrase(text, State.CAPTURED)
    with pytest.raises(PhraseLintError):
        lint_telegram_phrase(text, State.REVIEWED)
    lint_telegram_phrase(text, State.APPLIED)
    lint_telegram_phrase(text, State.PROMOTED)


def test_captured_phrase_allowed_in_all_success_states() -> None:
    text = "Inbox에 저장했습니다."
    for state in (State.CAPTURED, State.REVIEWED, State.APPLIED, State.PROMOTED):
        lint_telegram_phrase(text, state)


def test_captured_phrase_rejected_when_state_rejected() -> None:
    with pytest.raises(PhraseLintError):
        lint_telegram_phrase("저장했습니다", State.REJECTED)


def test_lint_passes_innocuous_text() -> None:
    lint_telegram_phrase("확인 결과를 알려드립니다.", State.CAPTURED)
    lint_telegram_phrase("아직 처리 중입니다.", State.REVIEWED)


def test_phrase_lists_are_non_empty() -> None:
    # Sanity check that phrase lists were not accidentally truncated.
    assert PROMOTED_PHRASES
    assert APPLIED_PHRASES
    assert CAPTURED_PHRASES


# ---------------------------------------------------------------------------
# I6 negation guard
# Substring matching previously fired on denials containing a positive
# phrase. The new negation guard inspects the immediately-following clause
# and treats negation markers as non-assertions.
# ---------------------------------------------------------------------------


def test_promoted_phrase_negation_does_not_trigger_under_captured() -> None:
    """``운영 규칙으로 반영하지 않았습니다`` is a denial and must pass under captured."""
    lint_telegram_phrase("운영 규칙으로 반영하지 않았습니다.", State.CAPTURED)
    lint_telegram_phrase("운영 규칙으로 반영하지 않았어요.", State.REVIEWED)


def test_applied_phrase_negation_does_not_trigger_under_captured() -> None:
    """``반영했지만`` style hedges and ``반영하지 못`` denials must pass."""
    # Denial: "반영하지 못했습니다" contains "반영" as substring but is negative.
    lint_telegram_phrase("정책에 반영하지 못했습니다.", State.CAPTURED)
    # Hedged: "~기 어렵습니다" contains negation marker "어려".
    lint_telegram_phrase("정책에 반영하기 어렵습니다.", State.CAPTURED)


def test_captured_phrase_negation_does_not_trigger_under_rejected() -> None:
    """``저장하지 못`` should not trip the captured-vs-rejected check."""
    lint_telegram_phrase("저장하지 못했습니다.", State.REJECTED)


def test_negation_does_not_mask_unrelated_positive_assertion() -> None:
    """A clear positive assertion in its own sentence still fires the lint."""
    # Negation in a later sentence must NOT cancel an earlier positive assertion.
    text = "운영 규칙으로 반영했습니다. 다른 요청은 처리하지 않았습니다."
    with pytest.raises(PhraseLintError):
        lint_telegram_phrase(text, State.CAPTURED)


def test_negation_far_away_in_text_does_not_mask_assertion() -> None:
    """A negation 100 chars away must not cancel the assertion."""
    far_negation = "운영 규칙으로 반영했습니다" + ", " * 30 + "처리하지 않았습니다."
    with pytest.raises(PhraseLintError):
        lint_telegram_phrase(far_negation, State.CAPTURED)
