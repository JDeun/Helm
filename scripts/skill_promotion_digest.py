"""Telegram-ready daily/weekly digest of skill scaffold candidates (Wave 4).

Reads recent traces via :func:`scripts.trace_to_skill.load_recent_traces` and
:func:`scripts.trace_to_skill.skill_scaffold_candidates`, filters out already-
processed candidates, and returns a structured payload ready for delivery by
the workspace-side Telegram transport.

This module **does not** make any Telegram API calls.  All I/O is local file
reads/writes.

Default paths
-------------
* Traces dir: ``~/.openclaw/workspace/.openclaw/traces``
  (env override ``OPENCLAW_TRACES_DIR``)
* State file: see :mod:`scripts.skill_promotion_state`

Payload shape
-------------
::

    {
      "generated_at": "<iso8601>",
      "cadence": "daily" | "weekly",
      "candidates": [
        {
          "candidate_id": "<8hex>",
          "skill": "<str or null>",
          "task_name": "<str>",
          "count": <int>,
          "sample_trace_ids": ["<id1>", ...],
          "status": "new" | "reminder"
        },
        ...
      ],
      "summary_text": "<plain text, ≤800 chars>",
      "approval_reply_examples": [
        "approve <id>",
        "reject <id> [reason]",
        "details <id>"
      ]
    }
"""

from __future__ import annotations

import os
import pathlib
import sys

__all__ = ["build_digest"]

_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts.skill_promotion_state import (
    candidate_id_for,
    is_processed,
    load_state,
    record_notified,
    save_state,
)
from scripts.time_helpers import utc_now_iso
from scripts.trace_to_skill import load_recent_traces, skill_scaffold_candidates

_SUMMARY_BYTE_BUDGET = 800
_APPROVAL_EXAMPLES = [
    "approve <id>",
    "reject <id> [reason]",
    "details <id>",
]


def _default_traces_dir() -> pathlib.Path:
    env = os.environ.get("OPENCLAW_TRACES_DIR")
    if env:
        return pathlib.Path(env).expanduser()
    return pathlib.Path.home() / ".openclaw" / "workspace" / ".openclaw" / "traces"


def _build_summary(candidates: list[dict], total_available: int) -> str:
    """Build a Telegram-friendly plain-text summary, capped at 800 bytes.

    Format per candidate::

        [a1b2c3d4] skill-name / task name — 5×, sample: trace-id-1

    A "+ N more" line is appended when *total_available* exceeds the number
    of candidates included.
    """
    lines: list[str] = ["Skill Promotion Digest\n"]
    for c in candidates:
        skill_label = c["skill"] if c["skill"] else "(no skill)"
        sample = c["sample_trace_ids"][0] if c["sample_trace_ids"] else "(none)"
        status_tag = " [reminder]" if c["status"] == "reminder" else ""
        line = (
            f"[{c['candidate_id']}] {skill_label} / {c['task_name']}"
            f" — {c['count']}×, sample: {sample}{status_tag}"
        )
        lines.append(line)

    remaining = total_available - len(candidates)
    if remaining > 0:
        lines.append(f"+ {remaining} more")

    text = "\n".join(lines)
    # Truncate to byte budget if needed, preserving valid UTF-8.
    encoded = text.encode("utf-8")
    if len(encoded) > _SUMMARY_BYTE_BUDGET:
        truncated = encoded[: _SUMMARY_BYTE_BUDGET - 3].decode("utf-8", errors="ignore")
        text = truncated + "..."
    return text


def build_digest(
    *,
    traces_dir: pathlib.Path | None = None,
    state_path: pathlib.Path | None = None,
    min_success: int = 3,
    max_candidates: int = 5,
    cadence: str = "daily",
) -> dict:
    """Build and return a Telegram-ready digest payload.

    Parameters
    ----------
    traces_dir:
        Directory containing trace JSON files.  Defaults to
        ``~/.openclaw/workspace/.openclaw/traces`` (or ``OPENCLAW_TRACES_DIR``).
    state_path:
        Path to the promotion state JSON file.  Defaults to the path returned
        by :func:`scripts.skill_promotion_state.default_state_path`.
    min_success:
        Minimum number of successful traces to qualify as a scaffold candidate
        (passed directly to :func:`skill_scaffold_candidates`).
    max_candidates:
        Maximum number of candidates included in the payload.  Extra candidates
        contribute a "+ N more" line to ``summary_text``.
    cadence:
        Informational string — ``"daily"`` or ``"weekly"``.  Not validated;
        passed through verbatim into the payload.

    Returns
    -------
    dict
        See module docstring for the payload shape.

    Side effects
    ------------
    Calls :func:`record_notified` for new (not previously notified) candidates
    and persists state via :func:`save_state`.
    """
    resolved_traces_dir = pathlib.Path(traces_dir) if traces_dir is not None else _default_traces_dir()

    traces = load_recent_traces(resolved_traces_dir)
    all_candidates_raw = skill_scaffold_candidates(traces, min_success=min_success)

    state = load_state(state_path)

    # Classify candidates: skip processed ones, tag remaining as new/reminder.
    classified: list[dict] = []
    for raw in all_candidates_raw:
        cid = candidate_id_for(raw.get("skill"), raw.get("task_name", ""))
        if is_processed(state, cid):
            continue
        # Determine status before record_notified so we can tell new vs reminder.
        already_notified = any(
            e["candidate_id"] == cid for e in state.get("entries", [])
        )
        classified.append(
            {
                "candidate_id": cid,
                "skill": raw.get("skill"),
                "task_name": raw.get("task_name", ""),
                "count": raw.get("count", 0),
                "sample_trace_ids": raw.get("sample_trace_ids", []),
                "status": "reminder" if already_notified else "new",
            }
        )

    total_available = len(classified)
    included = classified[:max_candidates]

    # Record newly-notified candidates (idempotent for reminders).
    state_dirty = False
    for c in included:
        if c["status"] == "new":
            # Build fingerprint from the raw candidate data.
            fingerprint = {
                "skill": c["skill"],
                "task_name": c["task_name"],
                "count": c["count"],
            }
            record_notified(state, c["candidate_id"], fingerprint)
            state_dirty = True

    if state_dirty:
        save_state(state, state_path)

    summary_text = _build_summary(included, total_available)

    return {
        "generated_at": utc_now_iso(),
        "cadence": cadence,
        "candidates": included,
        "summary_text": summary_text,
        "approval_reply_examples": list(_APPROVAL_EXAMPLES),
    }
