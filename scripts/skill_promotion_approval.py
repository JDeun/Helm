"""Incoming Telegram reply parser and approval handler for Wave 4.

Parses free-text messages of the form::

    approve <8hex-id>
    reject <8hex-id> [optional reason text]
    details <8hex-id>

and applies the corresponding state transition via
:mod:`scripts.skill_promotion_state`.

The module **does not** communicate with Telegram — it only parses strings and
mutates local state.  The Telegram transport lives in the workspace-side task.

# NOTE: keep in sync with workspace/scripts/skill_promotion_telegram_handler.py
# parse_message — both modules must agree on the approved/rejected/details
# command vocabulary and the 8-hex candidate_id format.

Public API
----------
* :func:`parse_reply`  — parse a raw message string into an action dict or ``None``.
* :func:`handle_reply` — apply the parsed action, call optional callbacks,
  and return an outcome dict.
"""

from __future__ import annotations

import pathlib
import re
import sys

__all__ = ["parse_reply", "handle_reply"]

_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts.skill_promotion_state import (
    is_processed,
    load_state,
    mark_approved,
    mark_rejected,
    save_state,
)

# Regex: action verb (case-insensitive), one 8-hex candidate_id, optional reason.
_REPLY_RE = re.compile(
    r"^\s*(?P<action>approve|reject|details)\s+(?P<cid>[0-9a-fA-F]{8})(?:\s+(?P<reason>.+))?\s*$",
    re.IGNORECASE,
)


def parse_reply(message: str) -> dict | None:
    """Parse *message* as a skill-promotion approval reply.

    Returns one of::

        {"action": "approve", "candidate_id": "<8hex>"}
        {"action": "reject",  "candidate_id": "<8hex>", "reason": "<str> | None"}
        {"action": "details", "candidate_id": "<8hex>"}

    Returns ``None`` if *message* does not match any recognised pattern.

    Rules
    -----
    * Whitespace is stripped from both ends of *message*.
    * The action verb is case-insensitive (``APPROVE``, ``Approve``, ``approve``
      are all accepted).
    * The candidate_id must be exactly 8 hexadecimal characters (``[0-9a-f]``).
      The returned ``candidate_id`` is lowercased.
    * For ``reject``, any text after the candidate_id is treated as the reason.
    * For ``approve`` and ``details``, trailing text is silently ignored.
    """
    m = _REPLY_RE.match(message.strip())
    if m is None:
        return None
    action = m.group("action").lower()
    cid = m.group("cid").lower()
    reason_raw = m.group("reason")
    reason = reason_raw.strip() if reason_raw else None

    result: dict = {"action": action, "candidate_id": cid}
    if action == "reject":
        result["reason"] = reason
    return result


def handle_reply(
    message: str,
    *,
    state_path: pathlib.Path | None = None,
    traces_dir: pathlib.Path | None = None,
    drafts_dir: pathlib.Path | None = None,
    approve_callback: "callable | None" = None,
    reject_callback: "callable | None" = None,
) -> dict:
    """Apply the parsed action from *message* to the promotion state.

    Parameters
    ----------
    message:
        Raw incoming text (e.g. a Telegram message body).
    state_path:
        Override path for the state file.
    traces_dir:
        Override traces directory (forwarded to *approve_callback* only).
    drafts_dir:
        Override drafts directory (forwarded to *approve_callback* only).
    approve_callback:
        Optional callable invoked on successful approval.  Called as::

            approve_callback(candidate_id: str, sample_trace_id: str | None)

        ``sample_trace_id`` is the first entry in the state fingerprint's
        ``sample_trace_ids`` list, or ``None`` if absent.
    reject_callback:
        Optional callable invoked on successful rejection.  Called as::

            reject_callback(candidate_id: str, reason: str | None)

    Returns
    -------
    dict
        ::

            {
              "action": "<action or 'not_an_approval'>",
              "candidate_id": "<8hex or None>",
              "outcome": "ok"
                        | "not_an_approval"
                        | "unknown_id"
                        | "already_processed"
            }
    """
    parsed = parse_reply(message)
    if parsed is None:
        return {"action": "not_an_approval", "candidate_id": None, "outcome": "not_an_approval"}

    action = parsed["action"]
    cid = parsed["candidate_id"]

    state = load_state(state_path)

    # Check candidate exists.
    all_ids = {e["candidate_id"] for e in state.get("entries", [])}
    if cid not in all_ids:
        return {"action": action, "candidate_id": cid, "outcome": "unknown_id"}

    # Check not already processed.
    if is_processed(state, cid):
        return {"action": action, "candidate_id": cid, "outcome": "already_processed"}

    if action == "approve":
        mark_approved(state, cid)
        save_state(state, state_path)
        # Determine sample trace id for the callback.
        sample_trace_id: str | None = None
        for entry in state.get("entries", []):
            if entry["candidate_id"] == cid:
                fp = entry.get("fingerprint") or {}
                samples = fp.get("sample_trace_ids") or []
                sample_trace_id = samples[0] if samples else None
                break
        if approve_callback is not None:
            approve_callback(cid, sample_trace_id)

    elif action == "reject":
        reason = parsed.get("reason")
        mark_rejected(state, cid, reason=reason)
        save_state(state, state_path)
        if reject_callback is not None:
            reject_callback(cid, reason)

    # "details" requires no state mutation.

    return {"action": action, "candidate_id": cid, "outcome": "ok"}
