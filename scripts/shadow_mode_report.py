"""Shadow-mode aggregation report for Waves 1-3b feature-flagged surfaces.

Wave 6 — harness-engineering rollout.

See also :mod:`scripts.shadow_mode_recommendation` for the enforce-readiness
decision layer that consumes this report's output.

This module reads tail-sampled data from the task ledger, proxy-events JSONL,
and skill-promotion state file to produce a structured report that Kevin uses
to decide which features to flip from shadow to enforce.

Tail-sampling strategy
----------------------
``scripts.jsonl_io.read_jsonl(path, tail=N)`` is used for JSONL files because
it already implements a backwards byte-chunk scan that avoids loading whole
multi-MB ledgers (see ``scripts/jsonl_io.py`` for the algorithm).  The function
is documented to be order-preserving (chronological, oldest first) and silently
skips malformed lines — appropriate for display-oriented aggregation.

Window filter
-------------
Each JSONL entry is inspected for a timestamp field.  The following names are
tried in order: ``updated_at``, ``started_at``, ``timestamp``, ``created_at``,
``notified_at``.  Entries with no parseable timestamp are included in the
report but counted in ``data_freshness.unparseable_timestamp_count``.

Public API
----------
* :func:`generate_report` — aggregate all shadow-mode signals and return a
  nested dict matching the schema below.
* :func:`to_markdown` — render the dict as a markdown document.
"""

from __future__ import annotations

import json
import pathlib
from datetime import datetime, timedelta, timezone

from scripts.time_helpers import utc_now
from typing import Any

from scripts.jsonl_io import read_jsonl
from scripts.skill_promotion_state import load_state as _load_promotion_state

__all__ = ["generate_report", "to_markdown"]

# ---------------------------------------------------------------------------
# Default paths
# ---------------------------------------------------------------------------

_DEFAULT_LEDGER = (
    pathlib.Path.home() / ".openclaw" / "workspace" / ".openclaw" / "task-ledger.jsonl"
)
_DEFAULT_PROXY_EVENTS = (
    pathlib.Path.home()
    / ".openclaw"
    / "workspace"
    / ".openclaw"
    / "traces"
    / "proxy-events.jsonl"
)
_DEFAULT_SKILL_STATE = (
    pathlib.Path.home()
    / ".openclaw"
    / "workspace"
    / ".openclaw"
    / "skill-promotion-state.json"
)

# Recognised feature names (also accepted as filter values)
_ALL_FEATURES = [
    "browser_verifier",
    "pause_gate",
    "model_repair",
    "synthetic_respond_inferred",
    "skill_promotion",
    "max_sessions_hits",
    "cleanup_evidence_gate",
]

# Timestamp field names to probe, in order
_TS_FIELDS = ["updated_at", "started_at", "timestamp", "created_at", "notified_at"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_ts(entry: dict) -> datetime | None:
    """Return a UTC-aware datetime from the first recognised timestamp field.

    Fields are tried in the order defined by ``_TS_FIELDS``:
    ``updated_at``, ``started_at``, ``timestamp``, ``created_at``,
    ``notified_at`` — first field with a parseable value wins.  Because
    ``updated_at`` is tried first, the window filter uses the last-update
    time of an entry, *not* the task creation time.  This means a task that
    was updated recently will appear in the window even if it was created
    outside the window.
    """
    for field in _TS_FIELDS:
        raw = entry.get(field)
        if not raw:
            continue
        if isinstance(raw, (int, float)):
            try:
                return datetime.fromtimestamp(raw, tz=timezone.utc)
            except (OSError, OverflowError, ValueError):
                continue
        if isinstance(raw, str):
            # Try common ISO 8601 variants
            for fmt in (
                "%Y-%m-%dT%H:%M:%S.%f%z",
                "%Y-%m-%dT%H:%M:%S%z",
                "%Y-%m-%dT%H:%M:%S.%fZ",
                "%Y-%m-%dT%H:%M:%SZ",
                "%Y-%m-%d %H:%M:%S%z",
                "%Y-%m-%d %H:%M:%SZ",
            ):
                try:
                    dt = datetime.strptime(raw, fmt)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    return dt
                except ValueError:
                    continue
    return None


def _in_window(entry: dict, since: datetime) -> tuple[bool, bool]:
    """Return (in_window, ts_parseable).

    ``in_window`` is True when the entry is within the reporting window OR
    when no timestamp was found (include-on-missing policy).
    ``ts_parseable`` is False when no timestamp could be extracted.
    """
    dt = _parse_ts(entry)
    if dt is None:
        return True, False
    return dt >= since, True


def _safe_task_id(entry: dict) -> str | None:
    """Return a task_id that does not leak absolute user paths."""
    for key in ("task_id", "taskId", "id", "trace_id", "traceId"):
        v = entry.get(key)
        if v and isinstance(v, str):
            # Strip any absolute-path prefix to avoid leaking /Users/kevin/…
            if "/" in v or "\\" in v:
                v = pathlib.PurePosixPath(v).name
            return v
    return None


def _add_sample(samples: list, entry: dict) -> None:
    """Append a task_id sample if capacity allows (max 5)."""
    if len(samples) >= 5:
        return
    tid = _safe_task_id(entry)
    if tid and tid not in samples:
        samples.append(tid)


# ---------------------------------------------------------------------------
# Feature aggregators
# ---------------------------------------------------------------------------

def _agg_browser_verifier(
    ledger_entries: list[dict],
) -> dict:
    shadow_count = 0
    enforced_block_count = 0
    enforced_approval_count = 0
    breakdown: dict[str, int] = {}
    samples: list[str] = []

    for entry in ledger_entries:
        status = entry.get("status", "")
        if status == "browser_recon_shadow":
            shadow_count += 1
            _add_sample(samples, entry)
            # Count each decision flag
            decisions = entry.get("decisions", entry)
            for flag in (
                "allow_single_session",
                "block_mutation",
                "require_confirmation",
                "require_user_login",
                "require_cleanup_evidence",
                "allow_parallel",
                "pause_profile",
            ):
                val = decisions.get(flag)
                if val is True:
                    key = f"{flag}_true"
                    breakdown[key] = breakdown.get(key, 0) + 1
        elif status == "browser_blocked":
            enforced_block_count += 1
            _add_sample(samples, entry)
        elif status in ("browser_requires_approval", "browser_approved_with_risk"):
            enforced_approval_count += 1
            _add_sample(samples, entry)

    return {
        "shadow_count": shadow_count,
        "enforced_block_count": enforced_block_count,
        "enforced_approval_count": enforced_approval_count,
        "decision_breakdown": breakdown,
        "samples": samples,
    }


def _agg_pause_gate(ledger_entries: list[dict]) -> dict:
    blocked_count = 0
    samples: list[str] = []

    for entry in ledger_entries:
        if entry.get("status") == "blocked_by_pause":
            blocked_count += 1
            _add_sample(samples, entry)

    return {"blocked_count": blocked_count, "samples": samples}


def _agg_model_repair(proxy_entries: list[dict]) -> dict:
    event_count = 0
    shadow_event_count = 0
    verdict_breakdown: dict[str, int] = {"ok": 0, "nudge_and_retry": 0, "abort": 0, "give_up": 0}
    issue_counts: dict[str, int] = {}
    samples: list[str] = []

    for entry in proxy_entries:
        # Accept both "repair" events and any entry with a "verdict" field
        event_type = entry.get("event_type") or entry.get("type") or ""
        verdict = entry.get("verdict")

        if entry.get("shadow_mode") is True:
            shadow_event_count += 1

        if verdict is not None:
            event_count += 1
            verdict_str = str(verdict)
            if verdict_str in verdict_breakdown:
                verdict_breakdown[verdict_str] += 1

            # Count issues
            issues = entry.get("issues") or []
            if isinstance(issues, list):
                for issue in issues:
                    if isinstance(issue, str):
                        issue_counts[issue] = issue_counts.get(issue, 0) + 1
                    elif isinstance(issue, dict):
                        ik = issue.get("issue") or issue.get("code") or str(issue)
                        issue_counts[ik] = issue_counts.get(ik, 0) + 1
            elif isinstance(issues, str):
                issue_counts[issues] = issue_counts.get(issues, 0) + 1

            # Sample by trace_id
            for key in ("trace_id", "traceId", "task_id", "taskId"):
                tid = entry.get(key)
                if tid and len(samples) < 5 and tid not in samples:
                    samples.append(str(tid))
                    break

    # Top 5 issues by count descending
    top_issues = sorted(
        [{"issue": k, "count": v} for k, v in issue_counts.items()],
        key=lambda x: x["count"],
        reverse=True,
    )[:5]

    return {
        "event_count": event_count,
        "verdict_breakdown": verdict_breakdown,
        "shadow_event_count": shadow_event_count,
        "top_issues": top_issues,
        "samples": samples,
    }


def _agg_synthetic_respond(proxy_entries: list[dict]) -> dict:
    terminal_without_tool_events = 0
    would_have_helped_estimate = 0

    for entry in proxy_entries:
        issues = entry.get("issues") or []
        has_terminal = False
        if isinstance(issues, list):
            for issue in issues:
                issue_str = (
                    issue if isinstance(issue, str)
                    else (issue.get("issue") or issue.get("code") or "")
                )
                if "terminal_without_tool" in str(issue_str):
                    has_terminal = True
                    break
        elif isinstance(issues, str):
            has_terminal = "terminal_without_tool" in issues

        if not has_terminal:
            event_type = (entry.get("event_type") or entry.get("type") or "").lower()
            if event_type == "terminal_without_tool":
                has_terminal = True

        if has_terminal:
            terminal_without_tool_events += 1
            if entry.get("tool_required") is True:
                would_have_helped_estimate += 1

    return {
        "terminal_without_tool_events": terminal_without_tool_events,
        "would_have_helped_estimate": would_have_helped_estimate,
    }


def _agg_skill_promotion(skill_state_path: pathlib.Path) -> dict:
    candidates_notified = 0
    approved = 0
    rejected = 0
    pending = 0

    # Delegate file-not-found and JSON-decode handling to load_state, which
    # returns an empty state dict for any missing or unreadable file.
    data = _load_promotion_state(skill_state_path)
    entries = data.get("entries", [])
    for entry in entries:
        candidates_notified += 1
        status = entry.get("status", "notified")
        if status == "approved":
            approved += 1
        elif status == "rejected":
            rejected += 1
        else:  # notified or other
            pending += 1

    return {
        "candidates_notified": candidates_notified,
        "approved": approved,
        "rejected": rejected,
        "pending": pending,
    }


def _agg_max_sessions(ledger_entries: list[dict]) -> dict:
    count = 0
    by_profile: dict[str, int] = {}

    for entry in ledger_entries:
        status = entry.get("status", "")
        reason = str(entry.get("reason") or entry.get("block_reason") or "").lower()
        if status == "browser_blocked" and "max_sessions" in reason:
            count += 1
            profile = entry.get("profile") or entry.get("execution_profile") or "unknown"
            by_profile[profile] = by_profile.get(profile, 0) + 1

    return {"count": count, "by_profile": by_profile}


def _agg_cleanup_evidence(ledger_entries: list[dict]) -> dict:
    required_count = 0
    missing_cleanup_count = 0
    exit_28_count = 0

    # Build a set of task_ids that later have a cleanup_status entry
    task_ids_with_cleanup: set[str] = set()
    for entry in ledger_entries:
        if entry.get("cleanup_status") or entry.get("status") == "cleanup_complete":
            for key in ("task_id", "taskId", "id"):
                tid = entry.get(key)
                if tid:
                    task_ids_with_cleanup.add(str(tid))
                    break

    # Count EXIT_CLEANUP_REQUIRED indicators
    for entry in ledger_entries:
        status = entry.get("status", "")
        if status in ("exit_cleanup_required", "EXIT_CLEANUP_REQUIRED"):
            exit_28_count += 1

        decisions = entry.get("decisions", entry)
        if decisions.get("require_cleanup_evidence") is True:
            required_count += 1
            tid = _safe_task_id(entry)
            if tid and tid not in task_ids_with_cleanup:
                missing_cleanup_count += 1

    return {
        "required_count": required_count,
        "missing_cleanup_count": missing_cleanup_count,
        "exit_28_count": exit_28_count,
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_report(
    *,
    ledger_path: pathlib.Path | None = None,
    proxy_events_path: pathlib.Path | None = None,
    skill_state_path: pathlib.Path | None = None,
    since_days: int = 14,
    feature_filter: list[str] | None = None,
    tail_lines: int = 5000,
) -> dict:
    """Aggregate shadow-mode signals across all feature-flagged surfaces.

    Parameters
    ----------
    ledger_path:
        Path to the task ledger JSONL file.  Defaults to
        ``~/.openclaw/workspace/.openclaw/task-ledger.jsonl``.
    proxy_events_path:
        Path to the proxy events JSONL file.  Defaults to
        ``~/.openclaw/workspace/.openclaw/traces/proxy-events.jsonl``.
    skill_state_path:
        Path to the skill-promotion state JSON file.  Defaults to
        ``~/.openclaw/workspace/.openclaw/skill-promotion-state.json``.
    since_days:
        Reporting window in days (default 14).  Entries older than this
        threshold are excluded.  Entries whose timestamp cannot be parsed
        are included but flagged in ``data_freshness``.
    feature_filter:
        Optional list of feature names to include.  If ``None`` or
        ``["all"]``, all features are included.  Recognised values:
        ``browser_verifier``, ``pause_gate``, ``model_repair``,
        ``synthetic_respond_inferred``, ``skill_promotion``,
        ``max_sessions_hits``, ``cleanup_evidence_gate``, ``all``.
    tail_lines:
        Maximum number of JSONL lines to read from the end of each file.
        Uses ``scripts.jsonl_io.read_jsonl(path, tail=N)`` which performs
        a backwards byte-chunk scan.  Default 5000.

        ``tail_lines`` caps the per-file sample size.  At default 5000 and a
        ledger turnover above ~360/day, the effective window is shorter than
        ``since_days``.  Increase tail_lines for high-throughput deployments.
        If ``tail_lines`` is smaller than the number of entries written during
        ``since_days``, the report will include a ``data_freshness.window_truncated``
        flag set to ``True``.

        Peak memory: ~10 KB × tail_lines × number_of_files (ledger +
        proxy_events); at defaults ~100 MB worst-case.  On memory-constrained
        hosts reduce tail_lines.

    Returns
    -------
    dict
        Nested report dict.  See module docstring for schema.
    """
    ledger_path = ledger_path or _DEFAULT_LEDGER
    proxy_events_path = proxy_events_path or _DEFAULT_PROXY_EVENTS
    skill_state_path = skill_state_path or _DEFAULT_SKILL_STATE

    now = utc_now()
    since = now - timedelta(days=since_days)

    # Normalise feature filter
    if feature_filter is None or (len(feature_filter) == 1 and feature_filter[0] == "all"):
        active_features = set(_ALL_FEATURES)
    else:
        active_features = set(feature_filter) - {"all"}
        # Add back "all" if explicitly included
        if "all" in (feature_filter or []):
            active_features = set(_ALL_FEATURES)

    # Tail-read JSONL sources
    raw_ledger = read_jsonl(pathlib.Path(ledger_path), tail=tail_lines)
    raw_proxy = read_jsonl(pathlib.Path(proxy_events_path), tail=tail_lines)

    # Filter by window and count unparseable timestamps
    ledger_unparseable = 0
    proxy_unparseable = 0
    ledger_entries: list[dict] = []
    proxy_entries: list[dict] = []

    for entry in raw_ledger:
        in_win, ts_ok = _in_window(entry, since)
        if not ts_ok:
            ledger_unparseable += 1
        if in_win:
            ledger_entries.append(entry)

    for entry in raw_proxy:
        in_win, ts_ok = _in_window(entry, since)
        if not ts_ok:
            proxy_unparseable += 1
        if in_win:
            proxy_entries.append(entry)

    # Assemble features
    features: dict[str, Any] = {}

    if "browser_verifier" in active_features:
        features["browser_verifier"] = _agg_browser_verifier(ledger_entries)

    if "pause_gate" in active_features:
        features["pause_gate"] = _agg_pause_gate(ledger_entries)

    if "model_repair" in active_features:
        features["model_repair"] = _agg_model_repair(proxy_entries)

    if "synthetic_respond_inferred" in active_features:
        features["synthetic_respond_inferred"] = _agg_synthetic_respond(proxy_entries)

    if "skill_promotion" in active_features:
        features["skill_promotion"] = _agg_skill_promotion(pathlib.Path(skill_state_path))

    if "max_sessions_hits" in active_features:
        features["max_sessions_hits"] = _agg_max_sessions(ledger_entries)

    if "cleanup_evidence_gate" in active_features:
        features["cleanup_evidence_gate"] = _agg_cleanup_evidence(ledger_entries)

    # window_truncated is True when the ledger (or proxy) reached the tail cap,
    # meaning earlier entries within since_days may not have been scanned.
    window_truncated = (
        len(raw_ledger) >= tail_lines or len(raw_proxy) >= tail_lines
    )

    return {
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%S+00:00"),
        "window": {
            "days": since_days,
            "since": since.strftime("%Y-%m-%dT%H:%M:%S+00:00"),
            "until": now.strftime("%Y-%m-%dT%H:%M:%S+00:00"),
        },
        "data_freshness": {
            "ledger_lines_scanned": len(raw_ledger),
            "proxy_events_lines_scanned": len(raw_proxy),
            "skill_state_present": pathlib.Path(skill_state_path).exists(),
            "ledger_unparseable_timestamp_count": ledger_unparseable,
            "proxy_unparseable_timestamp_count": proxy_unparseable,
            "window_truncated": window_truncated,
        },
        "features": features,
        "raw_filter_applied": feature_filter,
    }


def to_markdown(report: dict) -> str:
    """Render the report dict as a single markdown document.

    The output is Telegram-friendly (uses plain markdown, no HTML).
    Each feature gets its own section with totals, breakdowns, and up to
    5 sample task_ids.  Empty / all-zero reports are handled gracefully.
    """
    lines: list[str] = []
    gen = report.get("generated_at", "unknown")
    window = report.get("window", {})
    freshness = report.get("data_freshness", {})

    lines.append("# Shadow-Mode Report")
    lines.append("")
    lines.append(f"**Generated:** {gen}")
    lines.append(
        f"**Window:** {window.get('days', '?')} days "
        f"({window.get('since', '?')} → {window.get('until', '?')})"
    )
    lines.append("")
    lines.append("## Data Freshness")
    lines.append("")
    lines.append(f"- Ledger lines scanned: {freshness.get('ledger_lines_scanned', 0)}")
    lines.append(f"- Proxy events lines scanned: {freshness.get('proxy_events_lines_scanned', 0)}")
    lines.append(f"- Skill state present: {freshness.get('skill_state_present', False)}")
    if freshness.get("ledger_unparseable_timestamp_count", 0):
        lines.append(
            f"- Ledger entries without parseable timestamp: "
            f"{freshness['ledger_unparseable_timestamp_count']} (included in report)"
        )
    if freshness.get("proxy_unparseable_timestamp_count", 0):
        lines.append(
            f"- Proxy entries without parseable timestamp: "
            f"{freshness['proxy_unparseable_timestamp_count']} (included in report)"
        )
    if freshness.get("window_truncated"):
        lines.append(
            "- **WARNING: window truncated** — the per-file `tail_lines` cap was hit; "
            "the effective window is shorter than `since_days`. Raise `tail_lines` "
            "or investigate ledger turnover."
        )

    features = report.get("features", {})

    # browser_verifier
    if "browser_verifier" in features:
        bv = features["browser_verifier"]
        lines.append("")
        lines.append("## Feature: browser_verifier")
        lines.append("")
        lines.append(f"- Shadow events: {bv.get('shadow_count', 0)}")
        lines.append(f"- Enforced blocks: {bv.get('enforced_block_count', 0)}")
        lines.append(f"- Enforced approvals: {bv.get('enforced_approval_count', 0)}")
        bd = bv.get("decision_breakdown", {})
        if bd:
            lines.append("")
            lines.append("**Decision Breakdown:**")
            for flag, cnt in sorted(bd.items()):
                lines.append(f"  - {flag}: {cnt}")
        samples = bv.get("samples", [])
        if samples:
            lines.append("")
            lines.append(f"**Samples ({len(samples)}):** " + ", ".join(str(s) for s in samples))

    # pause_gate
    if "pause_gate" in features:
        pg = features["pause_gate"]
        lines.append("")
        lines.append("## Feature: pause_gate")
        lines.append("")
        lines.append(f"- Blocked count: {pg.get('blocked_count', 0)}")
        samples = pg.get("samples", [])
        if samples:
            lines.append(f"**Samples ({len(samples)}):** " + ", ".join(str(s) for s in samples))

    # model_repair
    if "model_repair" in features:
        mr = features["model_repair"]
        lines.append("")
        lines.append("## Feature: model_repair")
        lines.append("")
        lines.append(f"- Total events: {mr.get('event_count', 0)}")
        lines.append(f"- Shadow events: {mr.get('shadow_event_count', 0)}")
        vbd = mr.get("verdict_breakdown", {})
        if vbd:
            lines.append("")
            lines.append("**Verdict Breakdown:**")
            for verdict, cnt in sorted(vbd.items()):
                lines.append(f"  - {verdict}: {cnt}")
        top_issues = mr.get("top_issues", [])
        if top_issues:
            lines.append("")
            lines.append("**Top Issues:**")
            for item in top_issues:
                lines.append(f"  - {item['issue']}: {item['count']}")
        samples = mr.get("samples", [])
        if samples:
            lines.append("")
            lines.append(f"**Samples ({len(samples)}):** " + ", ".join(str(s) for s in samples))

    # synthetic_respond_inferred
    if "synthetic_respond_inferred" in features:
        sr = features["synthetic_respond_inferred"]
        lines.append("")
        lines.append("## Feature: synthetic_respond_inferred")
        lines.append("")
        lines.append(f"- Terminal-without-tool events: {sr.get('terminal_without_tool_events', 0)}")
        lines.append(
            f"- Would-have-helped estimate: {sr.get('would_have_helped_estimate', 0)}"
        )

    # skill_promotion
    if "skill_promotion" in features:
        sp = features["skill_promotion"]
        lines.append("")
        lines.append("## Feature: skill_promotion")
        lines.append("")
        lines.append(f"- Candidates notified: {sp.get('candidates_notified', 0)}")
        lines.append(f"- Approved: {sp.get('approved', 0)}")
        lines.append(f"- Rejected: {sp.get('rejected', 0)}")
        lines.append(f"- Pending: {sp.get('pending', 0)}")

    # max_sessions_hits
    if "max_sessions_hits" in features:
        ms = features["max_sessions_hits"]
        lines.append("")
        lines.append("## Feature: max_sessions_hits")
        lines.append("")
        lines.append(f"- Total hits: {ms.get('count', 0)}")
        by_profile = ms.get("by_profile", {})
        if by_profile:
            lines.append("")
            lines.append("**By Profile:**")
            for profile, cnt in sorted(by_profile.items()):
                lines.append(f"  - {profile}: {cnt}")

    # cleanup_evidence_gate
    if "cleanup_evidence_gate" in features:
        ceg = features["cleanup_evidence_gate"]
        lines.append("")
        lines.append("## Feature: cleanup_evidence_gate")
        lines.append("")
        lines.append(f"- Required cleanup count: {ceg.get('required_count', 0)}")
        lines.append(f"- Missing cleanup count: {ceg.get('missing_cleanup_count', 0)}")
        lines.append(f"- Exit-28 count: {ceg.get('exit_28_count', 0)}")

    filt = report.get("raw_filter_applied")
    if filt:
        lines.append("")
        lines.append(f"*Filter applied: {', '.join(filt)}*")

    return "\n".join(lines) + "\n"
