from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RetrievalClassification:
    exit_classification: str
    attempt_stage: str
    reason: str
    should_stop: bool


def classify_retrieval(
    *,
    status_code: int | None = None,
    error_text: str | None = None,
    body_hint: str | None = None,
    browser_used: bool = False,
    network_discovery: bool = False,
    auth_required: bool = False,
    unsafe: bool = False,
    human_approval_needed: bool = False,
) -> RetrievalClassification:
    blob = " ".join(str(item or "") for item in (error_text, body_hint)).casefold()

    if unsafe:
        return RetrievalClassification("unsafe", "stop", "Unsafe retrieval conditions detected.", True)
    if human_approval_needed:
        return RetrievalClassification("human_approval_needed", "stop", "Human approval is required before escalation.", True)
    if auth_required or any(token in blob for token in ("login", "sign in", "paywall", "auth required")):
        return RetrievalClassification("auth_required", "stop", "Authentication wall detected.", True)
    if network_discovery:
        return RetrievalClassification("api_reusable", "browser_network", "Browser network inspection exposed a reusable endpoint.", False)
    if browser_used:
        return RetrievalClassification("browser_required", "browser_snapshot", "Browser inspection was required to access or understand the page.", False)
    if status_code in {401, 402, 407}:
        return RetrievalClassification("auth_required", "stop", f"Status {status_code} indicates authentication or payment is required.", True)
    if status_code in {403, 429} or any(token in blob for token in ("403", "429", "waf", "challenge", "captcha", "rate limit", "forbidden")):
        return RetrievalClassification("fetch_blocked", "cheap_fetch", "Lightweight fetch path appears blocked or challenged.", False)
    if any(token in blob for token in ("javascript", "enable js", "spa shell", "empty app", "hydration")):
        return RetrievalClassification("browser_required", "browser_snapshot", "The page appears JS-dependent or returned only a shell.", False)
    if any(token in blob for token in ("timeout", "connection reset", "tls", "ssl", "handshake")):
        return RetrievalClassification("transport_failure", "cheap_fetch", "Transport layer failed before a useful response arrived.", False)
    return RetrievalClassification("completed", "cheap_fetch", "No blocked-retrieval signal detected.", False)


def next_stage(current_stage: str | None, classification: str, *, browser_allowed: bool = True) -> str | None:
    if classification in {"auth_required", "unsafe", "human_approval_needed", "api_reusable", "completed"}:
        return None
    stage = current_stage or "cheap_fetch"
    if classification == "browser_required" and browser_allowed:
        if stage in {"cheap_fetch", "transformed_url"}:
            return "browser_snapshot"
        if stage == "browser_snapshot":
            return "browser_network"
        return None
    if classification in {"fetch_blocked", "transport_failure"}:
        if stage == "cheap_fetch":
            return "transformed_url"
        if stage == "transformed_url" and browser_allowed:
            return "browser_snapshot"
        if stage == "browser_snapshot" and browser_allowed:
            return "browser_network"
    return None


def build_retrieval_plan(
    *,
    current_stage: str | None,
    status_code: int | None = None,
    error_text: str | None = None,
    body_hint: str | None = None,
    browser_used: bool = False,
    network_discovery: bool = False,
    auth_required: bool = False,
    unsafe: bool = False,
    human_approval_needed: bool = False,
    browser_allowed: bool = True,
) -> dict:
    classification = classify_retrieval(
        status_code=status_code,
        error_text=error_text,
        body_hint=body_hint,
        browser_used=browser_used,
        network_discovery=network_discovery,
        auth_required=auth_required,
        unsafe=unsafe,
        human_approval_needed=human_approval_needed,
    )
    return {
        "attempt_stage": current_stage or classification.attempt_stage,
        "exit_classification": classification.exit_classification,
        "reason": classification.reason,
        "next_attempt_stage": next_stage(current_stage, classification.exit_classification, browser_allowed=browser_allowed),
        "should_stop": classification.should_stop,
        "browser_allowed": browser_allowed,
    }
