from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.freshness_lib import (  # noqa: E402
    CONNECTOR_DEFAULTS,
    NAMESPACE,
    STATE_VERSION,
    ConnectorRecord,
    assess_record,
    classify_openclaw_error,
    connector_defaults,
    freshness_gate,
    get_record,
    import_openclaw_payload,
    list_records,
    load_state,
    mirror_state,
    parse_iso,
    record_failure,
    record_success,
    save_state,
)


# ---------------------------------------------------------------------------
# Defaults


def test_phase1_defaults_match_design_doc() -> None:
    """Design §3.2 risk_class and SLA assignment must be honoured."""
    high = {"google_calendar", "google_sheets", "telegram"}
    medium = {"google_gmail", "google_drive", "notion"}
    low = {"obsidian_vault", "github_briefing"}

    for cid in high:
        defaults = connector_defaults(cid)
        assert defaults["risk_class"] == "high", cid
    for cid in medium:
        defaults = connector_defaults(cid)
        assert defaults["risk_class"] == "medium", cid
    for cid in low:
        defaults = connector_defaults(cid)
        assert defaults["risk_class"] == "low", cid

    assert connector_defaults("google_calendar")["freshness_sla_minutes"] == 10
    assert connector_defaults("google_sheets")["freshness_sla_minutes"] == 30
    assert connector_defaults("telegram")["freshness_sla_minutes"] == 1
    assert connector_defaults("google_gmail")["freshness_sla_minutes"] == 30
    assert connector_defaults("google_drive")["freshness_sla_minutes"] == 60
    assert connector_defaults("notion")["freshness_sla_minutes"] == 60


def test_unknown_connector_defaults_to_medium_risk() -> None:
    defaults = connector_defaults("totally_unknown_connector")
    assert defaults["risk_class"] == "medium"
    assert defaults["freshness_sla_minutes"] == 60


def test_connector_defaults_strict_raises_on_unknown() -> None:
    with pytest.raises(KeyError):
        connector_defaults("totally_unknown_connector", strict=True)
    # Known connectors must still work in strict mode.
    assert connector_defaults("google_calendar", strict=True)["risk_class"] == "high"


# ---------------------------------------------------------------------------
# State I/O


def test_load_state_returns_empty_state_for_missing_path(tmp_path: Path) -> None:
    state = load_state(tmp_path / "missing.json")
    assert state["version"] == STATE_VERSION
    assert state["namespace"] == NAMESPACE
    assert state["connectors"] == {}


def test_save_and_load_round_trip(tmp_path: Path) -> None:
    state = load_state(tmp_path / "state.json")
    record_success(
        state,
        "google_calendar",
        at="2026-05-21T11:00:00+00:00",
        display_name="Google Calendar",
    )
    target = tmp_path / "state.json"
    save_state(state, target)
    assert target.exists()

    reloaded = load_state(target)
    record = get_record(reloaded, "google_calendar")
    assert record.connector_id == "google_calendar"
    assert record.last_success == "2026-05-21T11:00:00+00:00"
    assert record.risk_class == "high"  # honors §3.2
    assert record.stale_reason == "none"


def test_save_state_uses_atomic_replace(tmp_path: Path) -> None:
    target = tmp_path / "atomic.json"
    state = load_state(target)
    record_success(state, "notion", at="2026-05-21T11:00:00+00:00")
    save_state(state, target)
    # After atomic write there should be no .tmp left behind.
    leftovers = [p for p in tmp_path.iterdir() if p.name != "atomic.json"]
    assert leftovers == []


def test_mirror_state_writes_separate_file(tmp_path: Path) -> None:
    primary = tmp_path / "primary.json"
    mirror = tmp_path / "mirror.json"
    state = load_state(primary)
    record_success(state, "google_sheets", at="2026-05-21T11:00:00+00:00")
    save_state(state, primary)
    mirror_state(state, mirror)
    assert primary.exists()
    assert mirror.exists()
    assert primary.read_text(encoding="utf-8") == mirror.read_text(encoding="utf-8")


def test_namespace_is_distinct_from_ai_briefing_health() -> None:
    # The standard state file MUST carry the connector-freshness namespace
    # so it never collides with ai-briefing-source-health.json. (Design
    # constraint from the freshness task brief.)
    state = load_state(Path("/nonexistent.json"))
    assert state["namespace"] == "connector-freshness"
    assert state["namespace"] != "ai-briefing-source-health"


# ---------------------------------------------------------------------------
# Mutators


def test_record_success_clears_stale_reason() -> None:
    state: dict = {"version": 1, "namespace": NAMESPACE, "connectors": {}}
    record_failure(state, "google_calendar", stale_reason="auth_expired", at="2026-05-21T10:00:00+00:00")
    record = get_record(state, "google_calendar")
    assert record.stale_reason == "auth_expired"
    assert record.last_seen == "2026-05-21T10:00:00+00:00"
    assert record.last_success is None

    record_success(state, "google_calendar", at="2026-05-21T10:05:00+00:00")
    record = get_record(state, "google_calendar")
    assert record.stale_reason == "none"
    assert record.last_seen == "2026-05-21T10:05:00+00:00"
    assert record.last_success == "2026-05-21T10:05:00+00:00"


def test_record_failure_preserves_prior_last_success() -> None:
    state: dict = {"version": 1, "namespace": NAMESPACE, "connectors": {}}
    record_success(state, "google_drive", at="2026-05-19T08:00:00+00:00")
    record_failure(state, "google_drive", stale_reason="auth_expired", at="2026-05-21T11:00:00+00:00")
    record = get_record(state, "google_drive")
    assert record.last_success == "2026-05-19T08:00:00+00:00"
    assert record.last_seen == "2026-05-21T11:00:00+00:00"
    assert record.stale_reason == "auth_expired"


def test_record_attempt_normalises_invalid_stale_reason() -> None:
    state: dict = {"version": 1, "namespace": NAMESPACE, "connectors": {}}
    record_failure(state, "google_gmail", stale_reason="garbage")  # type: ignore[arg-type]
    record = get_record(state, "google_gmail")
    assert record.stale_reason == "unknown"


def test_record_attempt_normalises_invalid_risk_class() -> None:
    state: dict = {"version": 1, "namespace": NAMESPACE, "connectors": {}}
    record_success(state, "google_gmail", risk_class="ultra_high")  # type: ignore[arg-type]
    record = get_record(state, "google_gmail")
    # Falls back to the §3.2 default for gmail (medium).
    assert record.risk_class == "medium"


# ---------------------------------------------------------------------------
# Assessment


def _record(
    *,
    connector_id: str = "google_calendar",
    last_success: str | None = "2026-05-21T11:00:00+00:00",
    last_seen: str | None = "2026-05-21T11:00:00+00:00",
    stale_reason: str = "none",
    risk_class: str | None = None,
    sla: int | None = None,
) -> ConnectorRecord:
    defaults = connector_defaults(connector_id)
    return ConnectorRecord(
        connector_id=connector_id,
        risk_class=(risk_class or defaults["risk_class"]),  # type: ignore[arg-type]
        freshness_sla_minutes=(sla or defaults["freshness_sla_minutes"]),
        last_seen=last_seen,
        last_success=last_success,
        stale_reason=stale_reason,  # type: ignore[arg-type]
    )


def test_assess_fresh_inside_sla() -> None:
    record = _record(last_success="2026-05-21T11:00:00+00:00")
    now = datetime(2026, 5, 21, 11, 5, 0, tzinfo=timezone.utc)
    assessment = assess_record(record, now=now)
    assert assessment.fresh is True
    assert assessment.age_seconds == 5 * 60
    assert assessment.stale_reason == "none"


def test_assess_stale_outside_sla() -> None:
    record = _record(last_success="2026-05-21T10:00:00+00:00")
    now = datetime(2026, 5, 21, 11, 0, 0, tzinfo=timezone.utc)
    assessment = assess_record(record, now=now)
    assert assessment.fresh is False
    assert assessment.age_seconds == 3600


def test_assess_unobserved_connector_is_stale() -> None:
    record = _record(last_success=None, last_seen=None, stale_reason="none")
    assessment = assess_record(record, now=datetime(2026, 5, 21, 11, 0, tzinfo=timezone.utc))
    assert assessment.fresh is False
    assert assessment.age_seconds is None
    assert assessment.stale_reason == "not_scheduled"


# ---------------------------------------------------------------------------
# Telegram gate (design §3.3)


def _state_with(records: list[ConnectorRecord]) -> dict:
    state = {"version": STATE_VERSION, "namespace": NAMESPACE, "connectors": {}}
    for record in records:
        state["connectors"][record.connector_id] = record.to_payload()
    return state


def test_gate_branch_fresh_when_every_target_within_sla() -> None:
    now = datetime(2026, 5, 21, 11, 5, 0, tzinfo=timezone.utc)
    state = _state_with(
        [
            _record(connector_id="google_calendar", last_success="2026-05-21T11:00:00+00:00"),
            _record(connector_id="google_gmail", last_success="2026-05-21T11:00:00+00:00"),
        ]
    )
    decision = freshness_gate(state, ["google_calendar", "google_gmail"], now=now)
    assert decision.branch == "fresh"
    assert decision.pass_ is True
    assert decision.stale_high == ()
    assert decision.stale_low == ()
    assert decision.annotate_memory_estimate is False
    assert decision.refetch_targets == ()


def test_gate_branch_stale_high_when_high_risk_overdue() -> None:
    now = datetime(2026, 5, 21, 11, 30, 0, tzinfo=timezone.utc)
    state = _state_with(
        [
            # Calendar is high-risk with a 10-min SLA. 30 min old → stale.
            _record(connector_id="google_calendar", last_success="2026-05-21T11:00:00+00:00"),
            _record(connector_id="google_gmail", last_success="2026-05-21T11:25:00+00:00"),
        ]
    )
    decision = freshness_gate(state, ["google_calendar", "google_gmail"], now=now)
    assert decision.branch == "stale_high"
    assert decision.pass_ is False
    assert decision.stale_high == ("google_calendar",)
    assert decision.refetch_targets == ("google_calendar",)
    # In the high-risk branch the design says "재fetch 시도, 실패 시 확인 불가".
    # The caller (Telegram pipeline) handles the refetch step; the gate
    # itself must NOT silently mark the answer as "메모리 기준 추정".
    assert decision.annotate_memory_estimate is False


def test_gate_branch_stale_low_when_only_low_or_medium_overdue() -> None:
    now = datetime(2026, 5, 21, 13, 0, 0, tzinfo=timezone.utc)
    state = _state_with(
        [
            # Calendar fresh.
            _record(connector_id="google_calendar", last_success="2026-05-21T12:55:00+00:00"),
            # Gmail medium-risk, 30 min SLA. 60 min old → stale.
            _record(connector_id="google_gmail", last_success="2026-05-21T12:00:00+00:00"),
        ]
    )
    decision = freshness_gate(state, ["google_calendar", "google_gmail"], now=now)
    assert decision.branch == "stale_low"
    assert decision.pass_ is False
    assert decision.stale_high == ()
    assert decision.stale_low == ("google_gmail",)
    assert decision.annotate_memory_estimate is True
    assert decision.refetch_targets == ()


def test_gate_treats_unknown_connector_as_unobserved_high_or_medium_default() -> None:
    """Unknown connector_ids must NOT silently pass the gate.

    The gate defaults to risk_class=medium for unregistered ids, so an
    unobserved unknown connector should fall into the stale_low branch
    rather than ever returning ``branch="fresh"``.
    """
    state = _state_with([])
    decision = freshness_gate(state, ["mystery_connector"], now=datetime(2026, 5, 21, tzinfo=timezone.utc))
    assert decision.branch in {"stale_low", "stale_high"}
    assert decision.pass_ is False


def test_gate_default_treats_recent_high_risk_failure_as_fresh() -> None:
    """Default contract: last_success inside SLA → fresh, even if last
    attempt failed. The opt-in ``strict_high_risk`` flag changes this.
    """
    now = datetime(2026, 5, 21, 11, 5, 0, tzinfo=timezone.utc)
    # Calendar (high-risk, 10-min SLA) succeeded 5 min ago but the latest
    # attempt 30 seconds ago was rate-limited.
    rec = _record(
        connector_id="google_calendar",
        last_success="2026-05-21T11:00:00+00:00",
        last_seen="2026-05-21T11:04:30+00:00",
        stale_reason="rate_limited",
    )
    state = _state_with([rec])
    decision = freshness_gate(state, ["google_calendar"], now=now)
    assert decision.branch == "fresh"
    assert decision.pass_ is True


def test_gate_strict_high_risk_downgrades_recent_failure() -> None:
    """With ``strict_high_risk=True`` a high-risk connector that just
    failed must NOT be reported fresh — the answer should refetch or
    surface "확인 불가"."""
    now = datetime(2026, 5, 21, 11, 5, 0, tzinfo=timezone.utc)
    rec = _record(
        connector_id="google_calendar",
        last_success="2026-05-21T11:00:00+00:00",
        last_seen="2026-05-21T11:04:30+00:00",
        stale_reason="rate_limited",
    )
    state = _state_with([rec])
    decision = freshness_gate(
        state, ["google_calendar"], now=now, strict_high_risk=True
    )
    assert decision.branch == "stale_high"
    assert decision.pass_ is False
    assert decision.stale_high == ("google_calendar",)
    assert decision.refetch_targets == ("google_calendar",)
    # The failed assessment surfaces the original stale_reason so callers
    # can pick "확인 불가" with reason rate_limited.
    fail = decision.assessments[0]
    assert fail.fresh is False
    assert fail.stale_reason == "rate_limited"


def test_gate_strict_high_risk_does_not_affect_medium_or_low() -> None:
    """Strict mode only kicks in for high-risk connectors."""
    now = datetime(2026, 5, 21, 11, 5, 0, tzinfo=timezone.utc)
    # Gmail is medium-risk; even with a recent failure we keep "fresh".
    rec = _record(
        connector_id="google_gmail",
        last_success="2026-05-21T11:00:00+00:00",
        last_seen="2026-05-21T11:04:30+00:00",
        stale_reason="rate_limited",
    )
    state = _state_with([rec])
    decision = freshness_gate(
        state, ["google_gmail"], now=now, strict_high_risk=True
    )
    assert decision.branch == "fresh"


def test_assess_strict_high_risk_requires_failure_after_success() -> None:
    """If last_seen <= last_success, strict mode is a no-op (no observed
    failure)."""
    now = datetime(2026, 5, 21, 11, 5, 0, tzinfo=timezone.utc)
    rec = _record(
        connector_id="google_calendar",
        last_success="2026-05-21T11:00:00+00:00",
        last_seen="2026-05-21T10:59:00+00:00",
        stale_reason="none",
    )
    assessment = assess_record(rec, now=now, strict_high_risk=True)
    assert assessment.fresh is True


def test_gate_emits_assessments_in_input_order() -> None:
    now = datetime(2026, 5, 21, 11, 5, 0, tzinfo=timezone.utc)
    state = _state_with(
        [
            _record(connector_id="google_calendar", last_success="2026-05-21T11:00:00+00:00"),
            _record(connector_id="google_gmail", last_success="2026-05-21T11:00:00+00:00"),
            _record(connector_id="notion", last_success="2026-05-21T10:55:00+00:00"),
        ]
    )
    decision = freshness_gate(state, ["notion", "google_calendar"], now=now)
    assert [a.connector_id for a in decision.assessments] == ["notion", "google_calendar"]


# ---------------------------------------------------------------------------
# OpenClaw legacy import bridge


def test_classify_openclaw_error_maps_known_signals() -> None:
    assert classify_openclaw_error(None) == "none"
    assert classify_openclaw_error("") == "none"
    assert classify_openclaw_error(
        "Using keyring backend: keyring\nerror[api]: Request had insufficient authentication scopes."
    ) == "auth_expired"
    assert classify_openclaw_error("HTTP 429 rate limit hit") == "rate_limited"
    assert classify_openclaw_error("urllib timeout") == "network_error"
    assert classify_openclaw_error("Connection refused") == "network_error"
    assert classify_openclaw_error("403 blocked by policy") == "blocked"
    assert classify_openclaw_error("not scheduled today") == "not_scheduled"
    assert classify_openclaw_error("something exotic") == "unknown"


def test_import_openclaw_payload_translates_aliases_and_errors() -> None:
    state: dict = {"version": STATE_VERSION, "namespace": NAMESPACE, "connectors": {}}
    legacy = {
        "version": 1,
        "connectors": {
            "google_calendar": {
                "last_success_at": "2026-05-21T11:00:00+00:00",
                "last_attempt_at": "2026-05-21T11:00:00+00:00",
                "last_error": "",
                "display_name": "Google Calendar",
                "stale_after_minutes": 15,
            },
            "google_drive": {
                "last_success_at": "2026-05-19T08:00:00+00:00",
                "last_attempt_at": "2026-05-21T11:00:00+00:00",
                "last_error": (
                    "Using keyring backend: keyring\n"
                    "error[api]: Request had insufficient authentication scopes."
                ),
                "display_name": "Google Drive",
                "stale_after_minutes": 120,
            },
        },
    }
    imported = import_openclaw_payload(state, legacy)
    by_id = {r.connector_id: r for r in imported}
    assert "google_calendar" in by_id
    cal = by_id["google_calendar"]
    assert cal.risk_class == "high"
    assert cal.freshness_sla_minutes == 15
    assert cal.last_success == "2026-05-21T11:00:00+00:00"
    assert cal.stale_reason == "none"
    drive = by_id["google_drive"]
    assert drive.risk_class == "medium"
    assert drive.stale_reason == "auth_expired"
    assert drive.last_success == "2026-05-19T08:00:00+00:00"


def test_import_openclaw_payload_ignores_malformed_entries() -> None:
    state: dict = {"version": STATE_VERSION, "namespace": NAMESPACE, "connectors": {}}
    legacy = {"version": 1, "connectors": {"google_calendar": "not a dict"}}
    imported = import_openclaw_payload(state, legacy)
    assert imported == []
    assert state["connectors"] == {}


def test_import_openclaw_payload_preserves_zero_sla_explicit() -> None:
    """A legitimate ``freshness_sla_minutes=0`` must not be overwritten by the default.

    Regression for R2 C1 (the third site of the R0 Critical #3
    truthiness pattern). Previously the ``or`` chain coerced ``0`` to
    the default 60-minute window without warning.
    """
    state: dict = {"version": STATE_VERSION, "namespace": NAMESPACE, "connectors": {}}
    legacy = {
        "version": 1,
        "connectors": {
            "google_calendar": {
                "last_success_at": "2026-05-21T11:00:00+00:00",
                "freshness_sla_minutes": 0,
            },
        },
    }
    imported = import_openclaw_payload(state, legacy)
    assert len(imported) == 1
    assert imported[0].freshness_sla_minutes == 0


def test_import_openclaw_payload_preserves_zero_stale_after_legacy() -> None:
    """Same as above but via the legacy ``stale_after_minutes`` key."""
    state: dict = {"version": STATE_VERSION, "namespace": NAMESPACE, "connectors": {}}
    legacy = {
        "version": 1,
        "connectors": {
            "google_drive": {
                "last_success_at": "2026-05-21T11:00:00+00:00",
                "stale_after_minutes": 0,
            },
        },
    }
    imported = import_openclaw_payload(state, legacy)
    assert len(imported) == 1
    assert imported[0].freshness_sla_minutes == 0


def test_import_openclaw_payload_falls_back_to_default_when_missing() -> None:
    """When neither SLA key is present, the connector default applies."""
    state: dict = {"version": STATE_VERSION, "namespace": NAMESPACE, "connectors": {}}
    legacy = {
        "version": 1,
        "connectors": {
            "google_calendar": {
                "last_success_at": "2026-05-21T11:00:00+00:00",
            },
        },
    }
    imported = import_openclaw_payload(state, legacy)
    assert len(imported) == 1
    # google_calendar default is 10 (per CONNECTOR_DEFAULTS).
    assert imported[0].freshness_sla_minutes == 10


def test_import_openclaw_payload_handles_invalid_sla_string() -> None:
    """A non-numeric SLA falls back to the default without raising."""
    state: dict = {"version": STATE_VERSION, "namespace": NAMESPACE, "connectors": {}}
    legacy = {
        "version": 1,
        "connectors": {
            "google_calendar": {
                "last_success_at": "2026-05-21T11:00:00+00:00",
                "freshness_sla_minutes": "not-a-number",
            },
        },
    }
    imported = import_openclaw_payload(state, legacy)
    assert len(imported) == 1
    assert imported[0].freshness_sla_minutes == 10  # default for google_calendar


# ---------------------------------------------------------------------------
# parse_iso defensiveness


def test_parse_iso_normalises_z_suffix() -> None:
    parsed = parse_iso("2026-05-21T11:00:00Z")
    assert parsed is not None
    assert parsed.tzinfo is timezone.utc


def test_parse_iso_returns_none_for_bad_input() -> None:
    assert parse_iso(None) is None
    assert parse_iso("") is None
    assert parse_iso("nope") is None
