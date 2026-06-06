from __future__ import annotations

from scripts.social_privacy_gate import (
    DERIVED_DATA_LABELS,
    DOSSIER,
    RAW_PUBLIC_POST,
    SENSITIVE_INFERENCE,
    SUMMARY,
    SocialRiskLevel,
    evaluate_social_privacy,
)


def test_high_risk_personal_timeline_dossier_reports_missing_requirements() -> None:
    decision = evaluate_social_privacy(
        "이 사람 Bluesky 최근 글 전부 수집해서 정치 성향 dossier 만들어줘"
    )

    assert decision.risk == SocialRiskLevel.HIGH
    assert DOSSIER in decision.derived_data_labels
    assert SENSITIVE_INFERENCE in decision.derived_data_labels
    assert decision.missing_requirements == {
        "consent": True,
        "purpose": True,
        "scope": True,
        "retention": True,
    }
    assert decision.allowed_without_clarification is False


def test_single_post_summary_is_low_risk() -> None:
    decision = evaluate_social_privacy("이 포스트 하나 요약해줘: https://bsky.app/profile/example/post/123")

    assert decision.risk == SocialRiskLevel.LOW
    assert RAW_PUBLIC_POST in decision.derived_data_labels
    assert SUMMARY in decision.derived_data_labels
    assert decision.missing_requirements == {}
    assert decision.allowed_without_clarification is True


def test_own_account_scope_limited_positioning_is_medium_risk() -> None:
    decision = evaluate_social_privacy(
        "내 계정 최근 50개 글로 LinkedIn 포지셔닝 점검해줘. 저장하지 마."
    )

    assert decision.risk == SocialRiskLevel.MEDIUM
    assert decision.max_posts == 50
    assert decision.is_data_subject is True
    assert decision.has_consent is True
    assert decision.has_purpose is True
    assert decision.has_scope is True
    assert decision.has_retention is True
    assert decision.missing_requirements == {}


def test_derived_data_labels_policy_is_stable() -> None:
    assert DERIVED_DATA_LABELS == (
        "raw_public_post",
        "summary",
        "derived_preference",
        "sensitive_inference",
        "dossier",
    )
