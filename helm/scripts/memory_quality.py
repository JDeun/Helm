"""Conservative memory quality labels and label-only decay."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any


STATUSES = frozenset({"raw", "candidate", "promoted", "stale", "deprecated"})
LIVE_SOURCE_RE = re.compile(
    r"calendar|schedule|amount|price|briefing|latest|current|file exists|modified|"
    r"일정|금액|가격|브리핑|최신|현재|파일 존재|수정 여부",
    re.IGNORECASE,
)
DECAY_DAYS = {"raw": 30, "candidate": 60, "promoted": 90, "stale": 0, "deprecated": 0}


def _parse(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def _freshness(last_confirmed_at: object, *, now: datetime) -> tuple[str, int | None]:
    confirmed = _parse(last_confirmed_at)
    if confirmed is None:
        return "stale", None
    age = max(0, (now - confirmed).days)
    return ("fresh" if age <= 30 else "aging" if age <= 90 else "stale"), age


def build_memory_label(
    task: dict[str, Any],
    claim_state: dict[str, Any],
    event_types: list[str],
    review_flags: list[dict[str, Any]],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    reference = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    metadata = dict(((task.get("meta") or {}).get("memory_quality") or {}))
    freshness, age_days = _freshness(claim_state.get("last_confirmed_at"), now=reference)
    blob = " ".join(str(task.get(key) or "") for key in ("task_name", "command_preview", "runtime_note"))
    requires_live = bool(metadata.get("requires_live_source")) or task.get("profile") == "service_ops" or bool(LIVE_SOURCE_RE.search(blob))
    status = "raw"
    deprecation_reason = str(metadata.get("deprecation_reason") or "").strip() or None
    if task.get("status") == "failed":
        status = "raw"
    elif metadata.get("deprecated") or deprecation_reason:
        status = "deprecated"
    elif freshness == "stale":
        status = "stale"
    elif (
        metadata.get("promotion_approved") is True
        and metadata.get("promotion_evidence")
        and claim_state.get("confidence_hint") in {"medium", "high"}
        and not review_flags
        and task.get("status") == "completed"
    ):
        status = "promoted"
    elif task.get("status") in {"completed", "handoff_required"} and event_types:
        status = "candidate"
    return {
        "memory_id": str(metadata.get("memory_id") or task.get("task_id") or "unassigned"),
        "status": status,
        "freshness": freshness,
        "age_days": age_days,
        "confidence": str(claim_state.get("confidence_hint") or "low"),
        "requires_live_source": requires_live,
        "deprecation_reason": deprecation_reason,
        "memory_kind": "error_prevention" if task.get("status") == "failed" else "experience",
        "automatic_action": "downrank_only",
        "deletion_eligible": False,
        "last_confirmed_at": claim_state.get("last_confirmed_at"),
    }


def decay_memory_label(label: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    result = dict(label)
    status = str(result.get("status") or "raw")
    if status not in STATUSES:
        raise ValueError(f"unknown memory status: {status}")
    if status in {"stale", "deprecated"}:
        return result
    reference = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    freshness, age_days = _freshness(result.get("last_confirmed_at"), now=reference)
    result.update({"freshness": freshness, "age_days": age_days, "deletion_eligible": False, "automatic_action": "downrank_only"})
    if age_days is None or age_days > DECAY_DAYS[status]:
        result.update(
            {
                "status": "stale",
                "freshness": "stale",
                "decayed_at": reference.isoformat(),
                "decay_reason": "freshness threshold exceeded; live revalidation required before reuse",
            }
        )
    return result


def transition_memory_label(
    label: dict[str, Any],
    target: str,
    *,
    evidence: str | None = None,
    approved: bool = False,
    reason: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = str(label.get("status") or "raw")
    if current not in STATUSES or target not in STATUSES:
        raise ValueError("invalid memory quality transition")
    allowed = {("raw", "candidate"), ("stale", "candidate"), ("candidate", "promoted")}
    if target in {"stale", "deprecated"}:
        pass
    elif (current, target) not in allowed:
        raise ValueError(f"memory transition not allowed: {current}->{target}")
    if target in {"candidate", "promoted"} and not evidence:
        raise ValueError(f"{target} requires revalidation evidence")
    if target == "promoted" and (not approved or label.get("confidence") == "low"):
        raise ValueError("promotion requires approval and medium/high confidence")
    if target == "deprecated" and (not approved or not reason):
        raise ValueError("deprecation requires approval and a reason")
    result = dict(label)
    transitioned_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
    result["status"] = target
    result["transitioned_at"] = transitioned_at
    result["transition_evidence"] = evidence
    result["transition_approved"] = approved
    if target == "deprecated":
        result["deprecation_reason"] = reason
    if target in {"candidate", "promoted"}:
        result.update({"freshness": "fresh", "age_days": 0, "last_confirmed_at": transitioned_at})
    result["deletion_eligible"] = False
    return result
