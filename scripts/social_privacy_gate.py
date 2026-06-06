#!/usr/bin/env python3
"""Deterministic privacy gate for social-profile / dossier requests."""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from enum import Enum


class SocialRiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


RAW_PUBLIC_POST = "raw_public_post"
SUMMARY = "summary"
DERIVED_PREFERENCE = "derived_preference"
SENSITIVE_INFERENCE = "sensitive_inference"
DOSSIER = "dossier"

DERIVED_DATA_LABELS: tuple[str, ...] = (
    RAW_PUBLIC_POST,
    SUMMARY,
    DERIVED_PREFERENCE,
    SENSITIVE_INFERENCE,
    DOSSIER,
)

HIGH_RISK_REQUIREMENTS: tuple[str, ...] = (
    "consent",
    "purpose",
    "scope",
    "retention",
)

DEFAULT_MAX_POSTS = 20
EXPLICIT_EXPANDED_MAX_POSTS = 100


@dataclass(frozen=True)
class SocialPrivacySignal:
    name: str
    evidence: str


@dataclass
class SocialPrivacyDecision:
    risk: SocialRiskLevel
    derived_data_labels: list[str] = field(default_factory=list)
    signals: list[SocialPrivacySignal] = field(default_factory=list)
    missing_requirements: dict[str, bool] = field(default_factory=dict)
    max_posts: int | None = None
    is_data_subject: bool = False
    has_consent: bool = False
    has_purpose: bool = False
    has_scope: bool = False
    has_retention: bool = False

    @property
    def allowed_without_clarification(self) -> bool:
        return self.risk != SocialRiskLevel.HIGH or not any(self.missing_requirements.values())

    def as_dict(self) -> dict:
        payload = asdict(self)
        payload["risk"] = self.risk.value
        payload["allowed_without_clarification"] = self.allowed_without_clarification
        return payload


_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    "single_post": (
        re.compile(r"\b(?:single|one)\s+(?:post|tweet|thread)\b", re.IGNORECASE),
        re.compile(r"\b(?:this|that)\s+(?:post|tweet|thread)\b", re.IGNORECASE),
        re.compile(r"(?:이|그)\s*(?:글|포스트|게시물|스레드)"),
        re.compile(r"https?://\S+", re.IGNORECASE),
    ),
    "social_source": (
        re.compile(r"\b(?:social|timeline|post|posts|tweet|tweets|bluesky|twitter|x\.com|threads|mastodon)\b", re.IGNORECASE),
        re.compile(r"(?:타임라인|게시물|포스트|글|트윗|블루스카이|트위터|소셜|SNS)", re.IGNORECASE),
    ),
    "bulk_collection": (
        re.compile(r"\b(?:all|entire|full|every|bulk|mass)\b.{0,24}\b(?:timeline|posts|tweets|history)\b", re.IGNORECASE),
        re.compile(r"\b(?:timeline|posts|tweets|history)\b.{0,16}\b(?:all|entire|full|every)\b", re.IGNORECASE),
        re.compile(r"(?:최근\s*글\s*전부|전체\s*(?:타임라인|글|게시물)|전부\s*(?:수집|분석)|대량\s*(?:수집|분석)|수천\s*개)"),
    ),
    "profiling": (
        re.compile(
            r"\b(?:profiling|personality|preferences?|tendenc(?:y|ies)|what kind of person)\b",
            re.IGNORECASE,
        ),
        re.compile(r"\bprofile\s+(?:this|that|the|them|account|person|user)\b", re.IGNORECASE),
        re.compile(r"(?:어떤\s*사람인지|성향\s*분석|취향|선호|행동\s*패턴|관계망|프로필|프로파일)"),
    ),
    "dossier": (
        re.compile(r"\b(?:dossier|representative quotes?|persuasion points?|weaknesses?|vulnerabilities)\b", re.IGNORECASE),
        re.compile(r"(?:dossier|대표\s*인용|설득\s*포인트|약점|취약점)"),
    ),
    "sensitive_inference": (
        re.compile(r"\b(?:political|health|medical|religion|religious|location pattern|relationship|sexual|mental health)\b", re.IGNORECASE),
        re.compile(r"(?:정치\s*성향|건강\s*상태|의료|종교|위치\s*패턴|연애|관계\s*추론|정신\s*건강)"),
    ),
    "self_account": (
        re.compile(r"\b(?:my|mine|own|me)\s+(?:account|profile|timeline|posts)\b", re.IGNORECASE),
        re.compile(r"(?:내|제|본인)\s*(?:계정|타임라인|글|게시물|프로필)"),
    ),
    "consent": (
        re.compile(r"\b(?:consent|permission|authorized|with approval)\b", re.IGNORECASE),
        re.compile(r"(?:동의|허락|승인|허가)"),
    ),
    "purpose": (
        re.compile(r"\b(?:security audit|backup|branding|positioning|portfolio|self[- ]?review)\b", re.IGNORECASE),
        re.compile(r"(?:보안\s*점검|백업|브랜딩|포지셔닝|포트폴리오|자기\s*점검)"),
    ),
    "scope": (
        re.compile(r"\b(?:recent|latest|last)\s+\d+\s+(?:posts|tweets|items)\b", re.IGNORECASE),
        re.compile(r"\b(?:past|last)\s+\d+\s+(?:days|weeks|months)\b", re.IGNORECASE),
        re.compile(r"(?:최근|최신|지난)\s*\d+\s*(?:개|일|주|개월|달)"),
        re.compile(r"(?:포함|제외)\s*(?:topic|topics|주제)"),
    ),
    "retention": (
        re.compile(r"\b(?:do not save|don't save|no storage|delete after|retention|temporary only)\b", re.IGNORECASE),
        re.compile(r"(?:저장하지|저장\s*금지|삭제\s*시점|보관\s*기간|임시로만|memory에\s*저장하지)"),
    ),
    "organization_account": (
        re.compile(r"\b(?:organization|company|project|product|release|news|brand)\s+(?:account|profile|posts)\b", re.IGNORECASE),
        re.compile(r"(?:회사|조직|프로젝트|제품|오픈소스|릴리스|뉴스)\s*(?:계정|게시물|소식)"),
    ),
    "memory_storage": (
        re.compile(r"\b(?:save|store|record|memory|archive)\b", re.IGNORECASE),
        re.compile(r"(?:저장|기록|메모리|아카이브)"),
    ),
}

_POST_LIMIT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(?:recent|latest|last|timeline|posts?|tweets?)\s+(\d{1,6})\b", re.IGNORECASE),
    re.compile(r"\b(\d{1,6})\s+(?:posts?|tweets?|timeline items?)\b", re.IGNORECASE),
    re.compile(r"(?:최근|최신|지난|타임라인|글|게시물|포스트)\s*(\d{1,6})\s*(?:개)?"),
    re.compile(r"(\d{1,6})\s*(?:개)\s*(?:글|게시물|포스트|트윗)"),
)


def _first_match(name: str, text: str) -> str | None:
    for pattern in _PATTERNS[name]:
        match = pattern.search(text)
        if match:
            return match.group(0)
    return None


def _extract_post_limit(text: str) -> int | None:
    for pattern in _POST_LIMIT_PATTERNS:
        match = pattern.search(text)
        if match:
            return int(match.group(1))
    return None


def _add_signal(signals: list[SocialPrivacySignal], name: str, evidence: str | None) -> bool:
    if not evidence:
        return False
    signals.append(SocialPrivacySignal(name=name, evidence=evidence))
    return True


def _add_label(labels: list[str], label: str) -> None:
    if label not in labels:
        labels.append(label)


def evaluate_social_privacy(
    message: str,
    *,
    planned_tool: str | None = None,
    account_type: str | None = None,
    max_posts: int | None = None,
    is_data_subject: bool | None = None,
    has_consent: bool | None = None,
    purpose: str | None = None,
    scope: str | None = None,
    retention: str | None = None,
) -> SocialPrivacyDecision:
    """Classify social-data requests as low, medium, or high privacy risk.

    The detector is intentionally deterministic: it uses fixed lexical
    signals plus explicit caller metadata from planned tool calls.
    """
    text = f"{message} {planned_tool or ''}".strip()
    post_limit = max_posts if max_posts is not None else _extract_post_limit(text)
    signals: list[SocialPrivacySignal] = []

    single_post = _add_signal(signals, "single_post", _first_match("single_post", text))
    social_source = _add_signal(signals, "social_source", _first_match("social_source", text))
    bulk_collection = _add_signal(signals, "bulk_collection", _first_match("bulk_collection", text))
    profiling = _add_signal(signals, "profiling", _first_match("profiling", text))
    dossier = _add_signal(signals, "dossier", _first_match("dossier", text))
    sensitive = _add_signal(signals, "sensitive_inference", _first_match("sensitive_inference", text))
    memory_storage = _add_signal(signals, "memory_storage", _first_match("memory_storage", text))
    organization = account_type == "organization" or _add_signal(
        signals, "organization_account", _first_match("organization_account", text)
    )

    inferred_self = _add_signal(signals, "self_account", _first_match("self_account", text))
    subject_is_requester = bool(is_data_subject) if is_data_subject is not None else inferred_self

    consent_from_text = _add_signal(signals, "consent", _first_match("consent", text))
    consent_ok = bool(has_consent) if has_consent is not None else (subject_is_requester or consent_from_text)

    purpose_ok = bool(purpose) or _add_signal(signals, "purpose", _first_match("purpose", text))
    scope_ok = bool(scope) or post_limit is not None or _add_signal(signals, "scope", _first_match("scope", text))
    retention_ok = bool(retention) or _add_signal(signals, "retention", _first_match("retention", text))

    labels: list[str] = []
    if social_source or single_post:
        _add_label(labels, RAW_PUBLIC_POST)
    if single_post or "summary" in text.lower() or "요약" in text:
        _add_label(labels, SUMMARY)
    if profiling:
        _add_label(labels, DERIVED_PREFERENCE)
    if sensitive:
        _add_label(labels, SENSITIVE_INFERENCE)
    if dossier or (profiling and bulk_collection):
        _add_label(labels, DOSSIER)

    high_risk = False
    if sensitive or dossier:
        high_risk = True
    if memory_storage and profiling and not subject_is_requester:
        high_risk = True
    if post_limit is not None and post_limit > EXPLICIT_EXPANDED_MAX_POSTS and (profiling or not organization):
        _add_signal(signals, "expanded_post_limit", str(post_limit))
        high_risk = True
    if bulk_collection and (profiling or not organization):
        high_risk = True
    if profiling and not subject_is_requester and not organization and not scope_ok:
        high_risk = True

    if high_risk:
        missing = {
            "consent": not consent_ok and not organization,
            "purpose": not purpose_ok,
            "scope": not scope_ok,
            "retention": not retention_ok,
        }
        return SocialPrivacyDecision(
            risk=SocialRiskLevel.HIGH,
            derived_data_labels=labels,
            signals=signals,
            missing_requirements=missing,
            max_posts=post_limit,
            is_data_subject=subject_is_requester,
            has_consent=consent_ok,
            has_purpose=purpose_ok,
            has_scope=scope_ok,
            has_retention=retention_ok,
        )

    if organization or subject_is_requester or profiling or bulk_collection or post_limit:
        risk = SocialRiskLevel.MEDIUM
    else:
        risk = SocialRiskLevel.LOW

    if single_post and not profiling and not bulk_collection and not sensitive and not dossier:
        risk = SocialRiskLevel.LOW

    return SocialPrivacyDecision(
        risk=risk,
        derived_data_labels=labels,
        signals=signals,
        missing_requirements={},
        max_posts=post_limit,
        is_data_subject=subject_is_requester,
        has_consent=consent_ok,
        has_purpose=purpose_ok,
        has_scope=scope_ok,
        has_retention=retention_ok,
    )
