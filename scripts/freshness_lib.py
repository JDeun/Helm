#!/usr/bin/env python3
"""Connector freshness substrate (Helm architecture design §3).

This module is the canonical Python implementation of the Connector
Freshness Substrate described in the 2026-05-21 Helm architecture design.

It exposes the five standard fields per connector:

  * ``last_seen``              (ISO8601) most recent observation attempt
  * ``last_success``           (ISO8601) most recent successful observation
  * ``freshness_sla_minutes``  (int)     SLA budget per connector
  * ``stale_reason``           enum-like ("none" | "network_error" |
                               "auth_expired" | "rate_limited" |
                               "not_scheduled" | "blocked")
  * ``risk_class``             ("low" | "medium" | "high")

and provides a Telegram-side gate (``freshness_gate``) that returns
one of three branches: ``fresh``, ``stale_low``, ``stale_high``.

Storage layout (design §3.1):

  * Helm canonical:    ``~/.helm/state/connector-freshness.json``
  * OpenClaw mirror:   ``~/.openclaw/state/connector-freshness.json``

This implementation is conservative — it never imports network code, never
touches Telegram or Google Workspace APIs directly. Probes record results
through ``record_attempt`` / ``record_success`` so the substrate stays
testable and is the single source of truth for the gate.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Literal

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ---------------------------------------------------------------------------
# Constants


StaleReason = Literal[
    "none",
    "network_error",
    "auth_expired",
    "rate_limited",
    "not_scheduled",
    "blocked",
    "unknown",
]

RiskClass = Literal["low", "medium", "high"]

GateBranch = Literal["fresh", "stale_low", "stale_high"]


# Connector defaults map directly to design §3.2 (Phase 1 table).
# The dict is intentionally exhaustive: any connector_id used by callers
# must be registered here so the risk_class never falls back silently.
#
# Intentional divergence from the legacy OpenClaw probe
# (``~/.openclaw/workspace/scripts/connector_freshness.py``):
#
#   connector       | Helm (this file) | OpenClaw legacy probe
#   ----------------+------------------+-----------------------
#   google_calendar | 10 minutes       | 15 minutes
#   google_sheets   | 30 minutes       | 60 minutes
#   google_drive    | 60 minutes       | 120 minutes
#   google_gmail    | 30 minutes       | 30 minutes  (same)
#
# Helm's tighter SLAs encode the design §3.2 Phase-1 budget — the gate
# is run on every Telegram answer that asserts a fact about a live
# source, so a smaller SLA reduces the window in which we may answer
# from stale memory. The OpenClaw legacy probe was written *before* the
# Helm substrate existed and used hand-picked looser thresholds suited
# to a slower background refresh loop. The long-term plan
# (see issues #7/#8 in the 2026-05-21 Helm full review) is for the
# OpenClaw probe to delegate to ``record_attempt`` / ``mirror_state``
# here and adopt these defaults; until then the legacy thresholds are
# considered authoritative only for the legacy file path
# ``~/.openclaw/connector-freshness.json`` and the new substrate at
# ``~/.helm/state/connector-freshness.json`` uses the values below.
CONNECTOR_DEFAULTS: dict[str, dict[str, Any]] = {
    # high-risk: live source required before facts are asserted
    "google_calendar": {"risk_class": "high", "freshness_sla_minutes": 10},
    "google_sheets": {"risk_class": "high", "freshness_sla_minutes": 30},
    "telegram": {"risk_class": "high", "freshness_sla_minutes": 1},
    # medium-risk
    "google_gmail": {"risk_class": "medium", "freshness_sla_minutes": 30},
    "google_drive": {"risk_class": "medium", "freshness_sla_minutes": 60},
    "notion": {"risk_class": "medium", "freshness_sla_minutes": 60},
    # low-risk / background substrate
    "obsidian_vault": {"risk_class": "low", "freshness_sla_minutes": 5},
    "github_briefing": {"risk_class": "low", "freshness_sla_minutes": 720},
}

_VALID_STALE_REASONS: frozenset[str] = frozenset(
    {
        "none",
        "network_error",
        "auth_expired",
        "rate_limited",
        "not_scheduled",
        "blocked",
        "unknown",
    }
)

_VALID_RISK_CLASSES: frozenset[str] = frozenset({"low", "medium", "high"})

STATE_VERSION = 1
NAMESPACE = "connector-freshness"  # distinct from ai-briefing-source-health


# ---------------------------------------------------------------------------
# Dataclasses


@dataclass(frozen=True)
class ConnectorRecord:
    """Five-field standard record for one connector (§3.1)."""

    connector_id: str
    risk_class: RiskClass
    freshness_sla_minutes: int
    last_seen: str | None = None       # ISO8601, last attempt
    last_success: str | None = None    # ISO8601, last successful attempt
    stale_reason: StaleReason = "none"
    display_name: str | None = None

    def to_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        # drop optional Nones for cleaner state files
        return {k: v for k, v in payload.items() if v is not None}


@dataclass(frozen=True)
class FreshnessAssessment:
    """Result of evaluating a connector against `now`."""

    connector_id: str
    risk_class: RiskClass
    fresh: bool
    age_seconds: int | None
    freshness_sla_minutes: int
    stale_reason: StaleReason
    last_seen: str | None
    last_success: str | None

    def to_payload(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class GateDecision:
    """Outcome of the Telegram-side freshness gate (§3.3)."""

    branch: GateBranch                # "fresh" | "stale_low" | "stale_high"
    pass_: bool                       # True iff fresh
    assessments: tuple[FreshnessAssessment, ...]
    stale_high: tuple[str, ...]       # high-risk connectors that are stale
    stale_low: tuple[str, ...]        # low/medium-risk that are stale
    annotate_memory_estimate: bool    # true when answer must be marked as estimate
    refetch_targets: tuple[str, ...]  # high-risk connectors caller should refetch

    def to_payload(self) -> dict[str, Any]:
        return {
            "branch": self.branch,
            "pass": self.pass_,
            "stale_high": list(self.stale_high),
            "stale_low": list(self.stale_low),
            "annotate_memory_estimate": self.annotate_memory_estimate,
            "refetch_targets": list(self.refetch_targets),
            "assessments": [a.to_payload() for a in self.assessments],
        }


# ---------------------------------------------------------------------------
# Time helpers (delegate to scripts.time_helpers for repo-wide consistency).


def utc_now() -> datetime:
    """Return the current UTC time as a timezone-aware ``datetime``.

    Thin wrapper kept so existing ``from scripts.freshness_lib import
    utc_now`` callers continue to work; new code should import from
    :mod:`scripts.time_helpers` directly.
    """
    from scripts.time_helpers import utc_now as _utc_now
    return _utc_now()


def utc_now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string.

    Thin wrapper around :func:`scripts.time_helpers.utc_now_iso`; kept
    so legacy imports of ``freshness_lib.utc_now_iso`` keep resolving.
    """
    from scripts.time_helpers import utc_now_iso as _utc_now_iso
    return _utc_now_iso()


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        normalized = value.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


# ---------------------------------------------------------------------------
# Paths


def _default_helm_state_path() -> Path:
    """Canonical Helm freshness path (~/.helm/state/connector-freshness.json)."""
    try:
        from helm_workspace import get_workspace_layout  # type: ignore
        layout = get_workspace_layout()
        return layout.state_root / "connector-freshness.json"
    except Exception:
        return Path.home() / ".helm" / "state" / "connector-freshness.json"


def _default_openclaw_mirror_path() -> Path:
    """OpenClaw mirror path (~/.openclaw/state/connector-freshness.json).

    This is the location §3.1 designates as the OpenClaw mirror. It is
    intentionally separate from ``ai-briefing-source-health.json`` so the
    AI briefing source-health probe and this connector substrate never
    fight over the same key space.
    """
    env_override = os.environ.get("OPENCLAW_DIR")
    base = Path(env_override).expanduser() if env_override else Path.home() / ".openclaw"
    return base / "state" / "connector-freshness.json"


# ---------------------------------------------------------------------------
# State I/O


def _empty_state() -> dict[str, Any]:
    return {
        "version": STATE_VERSION,
        "namespace": NAMESPACE,
        "connectors": {},
    }


def load_state(path: Path | None = None) -> dict[str, Any]:
    target = path or _default_helm_state_path()
    if not target.exists():
        return _empty_state()
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _empty_state()
    if not isinstance(payload, dict):
        return _empty_state()
    payload.setdefault("version", STATE_VERSION)
    payload.setdefault("namespace", NAMESPACE)
    connectors = payload.get("connectors")
    if not isinstance(connectors, dict):
        payload["connectors"] = {}
    return payload


def save_state(payload: dict[str, Any], path: Path | None = None) -> Path:
    target = path or _default_helm_state_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    # atomic write via tempfile.replace
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=target.parent,
        prefix=f"{target.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        handle.write(
            json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
        )
        tmp_path = Path(handle.name)
    tmp_path.replace(target)
    return target


def mirror_state(payload: dict[str, Any], mirror_path: Path | None = None) -> Path:
    """Write the OpenClaw mirror copy (~/.openclaw/state/connector-freshness.json)."""
    target = mirror_path or _default_openclaw_mirror_path()
    return save_state(payload, target)


# ---------------------------------------------------------------------------
# Registry helpers


def connector_defaults(
    connector_id: str, *, strict: bool = False
) -> dict[str, Any]:
    """Return the default risk_class / SLA for a connector.

    Unknown connector ids silently fall back to ``risk_class=medium``
    and ``freshness_sla_minutes=60``. Pass ``strict=True`` (e.g. from a
    CI sanity check) to raise ``KeyError`` instead — useful for asserting
    that every connector_id used by the runtime is registered in
    :data:`CONNECTOR_DEFAULTS`.
    """
    base = CONNECTOR_DEFAULTS.get(connector_id)
    if base is None:
        if strict:
            raise KeyError(
                f"connector_id={connector_id!r} not registered in CONNECTOR_DEFAULTS"
            )
        base = {}
    return {
        "risk_class": base.get("risk_class", "medium"),
        "freshness_sla_minutes": int(base.get("freshness_sla_minutes", 60)),
    }


def _coerce_record(connector_id: str, raw: dict[str, Any] | None) -> ConnectorRecord:
    defaults = connector_defaults(connector_id)
    raw = raw or {}
    risk_class = raw.get("risk_class") or defaults["risk_class"]
    if risk_class not in _VALID_RISK_CLASSES:
        risk_class = defaults["risk_class"]
    stale_reason = raw.get("stale_reason") or "none"
    if stale_reason not in _VALID_STALE_REASONS:
        stale_reason = "unknown"
    sla_raw = raw.get("freshness_sla_minutes")
    # Distinguish "missing/None" (use default) from a legitimate 0 SLA value.
    # `or` would conflate 0 with missing.
    try:
        sla = int(sla_raw) if sla_raw is not None else int(defaults["freshness_sla_minutes"])
    except (TypeError, ValueError):
        sla = int(defaults["freshness_sla_minutes"])
    return ConnectorRecord(
        connector_id=connector_id,
        risk_class=risk_class,  # type: ignore[arg-type]
        freshness_sla_minutes=sla,
        last_seen=raw.get("last_seen"),
        last_success=raw.get("last_success"),
        stale_reason=stale_reason,  # type: ignore[arg-type]
        display_name=raw.get("display_name"),
    )


def get_record(state: dict[str, Any], connector_id: str) -> ConnectorRecord:
    connectors = state.get("connectors") or {}
    return _coerce_record(connector_id, connectors.get(connector_id))


def list_records(state: dict[str, Any]) -> list[ConnectorRecord]:
    connectors = state.get("connectors") or {}
    return [
        _coerce_record(cid, raw)
        for cid, raw in sorted(connectors.items())
        if isinstance(raw, dict) and cid
    ]


# ---------------------------------------------------------------------------
# Mutators (probes call into these)


def _put_record(state: dict[str, Any], record: ConnectorRecord) -> None:
    state.setdefault("connectors", {})
    state["connectors"][record.connector_id] = record.to_payload()


def record_attempt(
    state: dict[str, Any],
    connector_id: str,
    *,
    success: bool,
    stale_reason: StaleReason | None = None,
    at: str | None = None,
    risk_class: RiskClass | None = None,
    freshness_sla_minutes: int | None = None,
    display_name: str | None = None,
) -> ConnectorRecord:
    """Record a connector probe attempt and persist to ``state`` in-place.

    The caller is responsible for persisting state via :func:`save_state`.
    This split keeps the mutation testable and lets callers batch many probe
    results into a single atomic write.

    Note on ``display_name``:
        A falsy value (``None`` or empty string ``""``) is treated as
        "do not change" — the previous record's ``display_name`` is
        preserved. The API intentionally does not support clearing the
        display name back to ``None`` once set; if a caller really
        needs to reset it, construct a new :class:`ConnectorRecord`
        explicitly and call :func:`_put_record`.
    """
    now = at or utc_now_iso()
    current = get_record(state, connector_id)
    defaults = connector_defaults(connector_id)

    next_risk = risk_class or current.risk_class or defaults["risk_class"]
    if next_risk not in _VALID_RISK_CLASSES:
        next_risk = defaults["risk_class"]
    next_sla = int(
        freshness_sla_minutes
        if freshness_sla_minutes is not None
        else current.freshness_sla_minutes
    )
    if success:
        next_stale_reason: StaleReason = "none"
    else:
        candidate = stale_reason or "unknown"
        if candidate not in _VALID_STALE_REASONS:
            candidate = "unknown"
        next_stale_reason = candidate  # type: ignore[assignment]

    record = ConnectorRecord(
        connector_id=connector_id,
        risk_class=next_risk,  # type: ignore[arg-type]
        freshness_sla_minutes=next_sla,
        last_seen=now,
        last_success=now if success else current.last_success,
        stale_reason=next_stale_reason,
        display_name=display_name or current.display_name,
    )
    _put_record(state, record)
    return record


def record_success(
    state: dict[str, Any],
    connector_id: str,
    *,
    at: str | None = None,
    risk_class: RiskClass | None = None,
    freshness_sla_minutes: int | None = None,
    display_name: str | None = None,
) -> ConnectorRecord:
    return record_attempt(
        state,
        connector_id,
        success=True,
        at=at,
        risk_class=risk_class,
        freshness_sla_minutes=freshness_sla_minutes,
        display_name=display_name,
    )


def record_failure(
    state: dict[str, Any],
    connector_id: str,
    *,
    stale_reason: StaleReason = "unknown",
    at: str | None = None,
    risk_class: RiskClass | None = None,
    freshness_sla_minutes: int | None = None,
    display_name: str | None = None,
) -> ConnectorRecord:
    return record_attempt(
        state,
        connector_id,
        success=False,
        stale_reason=stale_reason,
        at=at,
        risk_class=risk_class,
        freshness_sla_minutes=freshness_sla_minutes,
        display_name=display_name,
    )


# ---------------------------------------------------------------------------
# Assessment + Gate


def assess_record(
    record: ConnectorRecord,
    *,
    now: datetime | None = None,
    strict_high_risk: bool = False,
) -> FreshnessAssessment:
    """Assess a single connector record against the SLA.

    Parameters
    ----------
    record:
        The connector record to assess.
    now:
        Optional ``datetime`` to evaluate against (defaults to ``utc_now()``).
    strict_high_risk:
        When ``True``, a high-risk connector whose ``last_seen`` reflects a
        more recent failure than ``last_success`` is downgraded to
        ``fresh=False`` even if ``last_success`` is inside the SLA window.
        This is the opt-in behaviour described in design §3.4 — a
        rate-limited or auth-expired high-risk connector must not be
        silently answered against. The default (``False``) preserves the
        legacy contract that ``last_success`` alone gates the freshness
        boolean.
    """
    current = (now or utc_now()).astimezone(timezone.utc)
    last_success_dt = parse_iso(record.last_success)
    last_seen_dt = parse_iso(record.last_seen)
    age_seconds: int | None
    if last_success_dt is None:
        age_seconds = None
        fresh = False
        stale_reason: StaleReason = (
            record.stale_reason if record.stale_reason != "none" else "not_scheduled"
        )
    else:
        age_seconds = max(0, int((current - last_success_dt).total_seconds()))
        fresh = age_seconds <= record.freshness_sla_minutes * 60
        if fresh:
            # Even if the most recent attempt failed, the last successful
            # observation is still inside the SLA so the substrate treats
            # the connector as fresh. The stale_reason from the last
            # failure is preserved for diagnostic surfacing.
            stale_reason = "none"
            # Opt-in strict mode (§3.4): for high-risk connectors a
            # more-recent failure than last_success must downgrade
            # freshness so the gate can refetch or annotate rather than
            # silently answer.
            if (
                strict_high_risk
                and record.risk_class == "high"
                and record.stale_reason not in ("none", "not_scheduled")
                and last_seen_dt is not None
                and last_seen_dt > last_success_dt
            ):
                fresh = False
                stale_reason = record.stale_reason
        else:
            stale_reason = (
                record.stale_reason if record.stale_reason != "none" else "not_scheduled"
            )
    return FreshnessAssessment(
        connector_id=record.connector_id,
        risk_class=record.risk_class,
        fresh=fresh,
        age_seconds=age_seconds,
        freshness_sla_minutes=record.freshness_sla_minutes,
        stale_reason=stale_reason,
        last_seen=record.last_seen,
        last_success=record.last_success,
    )


def freshness_gate(
    state: dict[str, Any],
    connector_ids: Iterable[str],
    *,
    now: datetime | None = None,
    strict_high_risk: bool = False,
) -> GateDecision:
    """Telegram-side freshness gate (design §3.3).

    Returns a :class:`GateDecision` whose ``branch`` is one of:

    * ``fresh``       — every connector inside SLA. LLM may answer.
    * ``stale_high``  — at least one high-risk connector is stale. Caller
                        must attempt a re-fetch; if re-fetch fails the
                        Telegram answer must say "확인 불가".
    * ``stale_low``   — only low/medium-risk connectors are stale. The
                        answer is allowed but must be tagged as
                        "메모리 기준 추정".

    Parameters
    ----------
    strict_high_risk:
        Opt-in flag (default ``False``). When ``True``, high-risk
        connectors with a recent *failure* observed after the last
        success are treated as stale even if ``last_success`` is inside
        the SLA window. This implements the design §3.4 guidance that a
        rate-limited / auth-expired high-risk connector should not be
        silently answered against.

    The gate never raises on unknown ``connector_ids`` — they are treated
    as unobserved (no ``last_success``) and inherit the default risk class
    so the answer stays conservative.
    """
    ids = list(connector_ids)
    assessments: list[FreshnessAssessment] = []
    for connector_id in ids:
        record = get_record(state, connector_id)
        assessments.append(
            assess_record(record, now=now, strict_high_risk=strict_high_risk)
        )

    stale_high = tuple(
        a.connector_id for a in assessments if not a.fresh and a.risk_class == "high"
    )
    stale_low = tuple(
        a.connector_id for a in assessments if not a.fresh and a.risk_class != "high"
    )

    if stale_high:
        branch: GateBranch = "stale_high"
        return GateDecision(
            branch=branch,
            pass_=False,
            assessments=tuple(assessments),
            stale_high=stale_high,
            stale_low=stale_low,
            annotate_memory_estimate=False,  # high-risk requires refetch, not estimate
            refetch_targets=stale_high,
        )
    if stale_low:
        branch = "stale_low"
        return GateDecision(
            branch=branch,
            pass_=False,
            assessments=tuple(assessments),
            stale_high=stale_high,
            stale_low=stale_low,
            annotate_memory_estimate=True,
            refetch_targets=tuple(),
        )
    branch = "fresh"
    return GateDecision(
        branch=branch,
        pass_=True,
        assessments=tuple(assessments),
        stale_high=stale_high,
        stale_low=stale_low,
        annotate_memory_estimate=False,
        refetch_targets=tuple(),
    )


# ---------------------------------------------------------------------------
# Compatibility bridge to existing OpenClaw connector_freshness.py


_OPENCLAW_REASON_MAP: dict[str, StaleReason] = {
    "": "none",
    "ok": "none",
    "insufficient authentication scopes": "auth_expired",
    "Request had insufficient authentication scopes": "auth_expired",
    "auth_expired": "auth_expired",
    "rate_limited": "rate_limited",
    "rate limited": "rate_limited",
    "network": "network_error",
    "network_error": "network_error",
    "blocked": "blocked",
    "not scheduled": "not_scheduled",
}


def classify_openclaw_error(last_error: str | None) -> StaleReason:
    """Map an OpenClaw ``last_error`` string to a standard ``stale_reason``.

    Existing OpenClaw ``connector_freshness.py`` stores a free-form
    ``last_error``. This helper normalizes the most common signals so the
    Helm substrate keeps a closed enum.
    """
    if not last_error:
        return "none"
    needle = str(last_error).strip().lower()
    if not needle:
        return "none"
    if "insufficient authentication" in needle or ("auth" in needle and "expired" in needle):
        return "auth_expired"
    if "rate limit" in needle or "429" in needle:
        return "rate_limited"
    if "timeout" in needle or "timed out" in needle or "network" in needle or "connection" in needle:
        return "network_error"
    if "blocked" in needle or "403" in needle:
        return "blocked"
    if "not scheduled" in needle:
        return "not_scheduled"
    return "unknown"


_OPENCLAW_CONNECTOR_ALIASES: dict[str, str] = {
    # historical openclaw ids → helm canonical ids
    "google_calendar": "google_calendar",
    "google_gmail": "google_gmail",
    "google_sheets": "google_sheets",
    "google_drive": "google_drive",
    "calendar": "google_calendar",
    "gmail": "google_gmail",
    "sheets": "google_sheets",
    "drive": "google_drive",
    "telegram": "telegram",
    "notion": "notion",
    "obsidian": "obsidian_vault",
    "obsidian_vault": "obsidian_vault",
    "github_briefing": "github_briefing",
}


def import_openclaw_payload(
    state: dict[str, Any], openclaw_payload: dict[str, Any]
) -> list[ConnectorRecord]:
    """Adopt entries from the legacy openclaw connector-freshness payload.

    Returns the list of imported :class:`ConnectorRecord` instances.
    """
    imported: list[ConnectorRecord] = []
    connectors = openclaw_payload.get("connectors")
    if not isinstance(connectors, dict):
        return imported
    for raw_id, entry in connectors.items():
        if not isinstance(entry, dict):
            continue
        canonical = _OPENCLAW_CONNECTOR_ALIASES.get(raw_id, raw_id)
        defaults = connector_defaults(canonical)
        last_error = entry.get("last_error")
        last_success = entry.get("last_success_at") or entry.get("last_success")
        last_seen = entry.get("last_attempt_at") or entry.get("last_seen") or last_success
        stale_reason = classify_openclaw_error(last_error)
        # Distinguish "missing/None" (use default) from a legitimate 0 SLA value.
        # `or` would conflate 0 with missing. Check both legacy keys explicitly.
        sla_raw = entry.get("freshness_sla_minutes")
        if sla_raw is None:
            sla_raw = entry.get("stale_after_minutes")
        try:
            sla_minutes = (
                int(sla_raw)
                if sla_raw is not None
                else int(defaults["freshness_sla_minutes"])
            )
        except (TypeError, ValueError):
            sla_minutes = int(defaults["freshness_sla_minutes"])
        record = ConnectorRecord(
            connector_id=canonical,
            risk_class=defaults["risk_class"],  # type: ignore[arg-type]
            freshness_sla_minutes=sla_minutes,
            last_seen=last_seen,
            last_success=last_success,
            stale_reason=stale_reason,
            display_name=entry.get("display_name") or canonical,
        )
        _put_record(state, record)
        imported.append(record)
    return imported


__all__ = [
    "CONNECTOR_DEFAULTS",
    "ConnectorRecord",
    "FreshnessAssessment",
    "GateDecision",
    "STATE_VERSION",
    "NAMESPACE",
    "assess_record",
    "classify_openclaw_error",
    "connector_defaults",
    "freshness_gate",
    "get_record",
    "import_openclaw_payload",
    "list_records",
    "load_state",
    "mirror_state",
    "parse_iso",
    "record_attempt",
    "record_failure",
    "record_success",
    "save_state",
    "utc_now",
    "utc_now_iso",
]
