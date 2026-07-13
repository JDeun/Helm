from __future__ import annotations

from datetime import datetime, timezone

import pytest

from scripts.memory_quality import build_memory_label, decay_memory_label, transition_memory_label


def test_failed_high_impact_run_is_error_prevention_memory() -> None:
    label = build_memory_label(
        {"task_id": "t1", "task_name": "calendar update", "profile": "service_ops", "status": "failed", "meta": {}},
        {"last_confirmed_at": "2026-07-13T00:00:00+00:00", "confidence_hint": "low"},
        ["operational_state"],
        [{"type": "failure"}],
        now=datetime(2026, 7, 13, tzinfo=timezone.utc),
    )
    assert label["status"] == "raw"
    assert label["memory_kind"] == "error_prevention"
    assert label["requires_live_source"] is True
    assert label["deletion_eligible"] is False


def test_failed_run_without_confirmation_stays_raw_error_prevention_memory() -> None:
    label = build_memory_label(
        {"task_id": "t-failed", "task_name": "deploy", "status": "failed", "meta": {}},
        {"confidence_hint": "low"},
        ["operational_state"],
        [{"type": "failure"}],
        now=datetime(2026, 7, 13, tzinfo=timezone.utc),
    )
    assert label["status"] == "raw"
    assert label["freshness"] == "stale"
    assert label["memory_kind"] == "error_prevention"


def test_promotion_requires_evidence_approval_and_confidence() -> None:
    candidate = {
        "memory_id": "m1",
        "status": "candidate",
        "freshness": "fresh",
        "confidence": "high",
        "last_confirmed_at": "2026-07-13T00:00:00+00:00",
    }
    with pytest.raises(ValueError, match="evidence"):
        transition_memory_label(candidate, "promoted", approved=True)
    with pytest.raises(ValueError, match="approval"):
        transition_memory_label(candidate, "promoted", evidence="two sources")
    transition_at = datetime(2026, 7, 14, tzinfo=timezone.utc)
    promoted = transition_memory_label(candidate, "promoted", evidence="two sources", approved=True, now=transition_at)
    assert promoted["status"] == "promoted"
    assert promoted["transitioned_at"] == transition_at.isoformat()
    assert promoted["last_confirmed_at"] == transition_at.isoformat()
    assert promoted["age_days"] == 0


def test_revalidated_stale_memory_refreshes_confirmation_timestamp() -> None:
    transition_at = datetime(2026, 7, 13, tzinfo=timezone.utc)
    candidate = transition_memory_label(
        {"memory_id": "m-stale", "status": "stale", "freshness": "stale", "confidence": "medium"},
        "candidate",
        evidence="live source readback passed",
        now=transition_at,
    )
    assert candidate["freshness"] == "fresh"
    assert candidate["last_confirmed_at"] == transition_at.isoformat()
    assert candidate["transitioned_at"] == transition_at.isoformat()
    still_candidate = decay_memory_label(candidate, now=datetime(2026, 7, 20, tzinfo=timezone.utc))
    assert still_candidate["status"] == "candidate"


def test_decay_only_downranks_and_never_deletes() -> None:
    label = {
        "memory_id": "m1",
        "status": "promoted",
        "freshness": "fresh",
        "confidence": "high",
        "last_confirmed_at": "2026-01-01T00:00:00+00:00",
    }
    stale = decay_memory_label(label, now=datetime(2026, 7, 13, tzinfo=timezone.utc))
    assert stale["status"] == "stale"
    assert stale["automatic_action"] == "downrank_only"
    assert stale["deletion_eligible"] is False


def test_explicit_promotion_metadata_does_not_bypass_review_flags() -> None:
    task = {
        "task_id": "t2",
        "task_name": "document stable rule",
        "status": "completed",
        "meta": {"memory_quality": {"promotion_approved": True, "promotion_evidence": "reviewed"}},
    }
    label = build_memory_label(
        task,
        {"last_confirmed_at": "2026-07-13T00:00:00+00:00", "confidence_hint": "high"},
        ["knowledge_state"],
        [{"type": "contradiction_review"}],
        now=datetime(2026, 7, 13, tzinfo=timezone.utc),
    )
    assert label["status"] == "candidate"
