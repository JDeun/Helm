"""Helm CLI commands for the skill-promotion pipeline (Wave 4).

``skill_promotion_state``, ``skill_promotion_digest``, and
``skill_promotion_approval`` form a single pipeline: state tracks candidates,
digest builds and sends the Telegram payload, approval parses replies and
applies state transitions.

Subcommands
-----------
digest      Build and print the daily/weekly digest payload as JSON.
approve     Manually approve a candidate by id.
reject      Manually reject a candidate by id.
pending     List candidates that are notified but not yet approved/rejected.
state-path  Print the state file path currently in use.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from scripts.skill_promotion_approval import handle_reply
from scripts.skill_promotion_digest import build_digest
from scripts.skill_promotion_state import (
    default_state_path,
    load_state,
    mark_approved,
    mark_rejected,
    pending_approvals,
    save_state,
)


def cmd_skill_promotion(args: argparse.Namespace) -> int:
    """Dispatch ``helm skill-promotion <subcommand>``."""
    sub = getattr(args, "skill_promotion_command", None)
    if sub is None or sub == "help":
        _print_help()
        return 0

    dispatch = {
        "digest": _cmd_digest,
        "approve": _cmd_approve,
        "reject": _cmd_reject,
        "pending": _cmd_pending,
        "state-path": _cmd_state_path,
    }
    handler = dispatch.get(sub)
    if handler is None:
        print(f"Unknown skill-promotion subcommand: {sub!r}", file=sys.stderr)
        return 2
    return handler(args)


# ---------------------------------------------------------------------------
# Subcommand handlers
# ---------------------------------------------------------------------------

def _cmd_digest(args: argparse.Namespace) -> int:
    """Build and print the digest payload as pretty-printed JSON."""
    cadence: str = getattr(args, "cadence", "daily") or "daily"
    max_n: int = getattr(args, "max", 5) or 5
    state_path_str: str | None = getattr(args, "state_path", None)
    traces_dir_str: str | None = getattr(args, "traces_dir", None)

    payload = build_digest(
        traces_dir=Path(traces_dir_str) if traces_dir_str else None,
        state_path=Path(state_path_str) if state_path_str else None,
        max_candidates=max_n,
        cadence=cadence,
    )
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


def _cmd_approve(args: argparse.Namespace) -> int:
    """Manually approve a candidate by id."""
    cid: str = args.candidate_id
    state_path_str: str | None = getattr(args, "state_path", None)
    sp = Path(state_path_str) if state_path_str else None

    outcome = handle_reply(
        f"approve {cid}",
        state_path=sp,
    )
    oc = outcome["outcome"]
    if oc == "ok":
        print(f"approved {cid}")
        return 0
    elif oc == "unknown_id":
        print(f"error: unknown candidate_id {cid!r}", file=sys.stderr)
        return 1
    elif oc == "already_processed":
        print(f"error: {cid!r} already processed", file=sys.stderr)
        return 1
    else:
        print(f"error: unexpected outcome {oc!r}", file=sys.stderr)
        return 1


def _cmd_reject(args: argparse.Namespace) -> int:
    """Manually reject a candidate by id."""
    cid: str = args.candidate_id
    reason: str | None = getattr(args, "reason", None)
    state_path_str: str | None = getattr(args, "state_path", None)
    sp = Path(state_path_str) if state_path_str else None

    msg = f"reject {cid}"
    if reason:
        msg = f"reject {cid} {reason}"

    outcome = handle_reply(msg, state_path=sp)
    oc = outcome["outcome"]
    if oc == "ok":
        print(f"rejected {cid}" + (f" reason={reason!r}" if reason else ""))
        return 0
    elif oc == "unknown_id":
        print(f"error: unknown candidate_id {cid!r}", file=sys.stderr)
        return 1
    elif oc == "already_processed":
        print(f"error: {cid!r} already processed", file=sys.stderr)
        return 1
    else:
        print(f"error: unexpected outcome {oc!r}", file=sys.stderr)
        return 1


def _cmd_pending(args: argparse.Namespace) -> int:
    """List pending (notified but not processed) candidates."""
    state_path_str: str | None = getattr(args, "state_path", None)
    sp = Path(state_path_str) if state_path_str else None
    use_json: bool = getattr(args, "json", False)

    state = load_state(sp)
    items = pending_approvals(state)

    if use_json:
        print(json.dumps(items, indent=2, ensure_ascii=False))
        return 0

    if not items:
        print("No pending candidates.")
        return 0

    for entry in items:
        cid = entry.get("candidate_id", "?")
        fp = entry.get("fingerprint") or {}
        skill = fp.get("skill") or "(no skill)"
        task = fp.get("task_name") or "?"
        notified_at = entry.get("notified_at", "?")
        print(f"[{cid}] {skill} / {task}  notified_at={notified_at}")
    return 0


def _cmd_state_path(args: argparse.Namespace) -> int:
    """Print the state file path being used."""
    state_path_str: str | None = getattr(args, "state_path", None)
    sp = Path(state_path_str) if state_path_str else default_state_path()
    print(str(sp))
    return 0


# ---------------------------------------------------------------------------
# Help
# ---------------------------------------------------------------------------

def _print_help() -> None:
    print(
        "usage: helm skill-promotion <subcommand> [options]\n\n"
        "Subcommands:\n"
        "  digest     [--cadence daily|weekly] [--max N] [--state-path P] [--traces-dir D]\n"
        "  approve    <id>  [--state-path P]\n"
        "  reject     <id>  [--reason TEXT] [--state-path P]\n"
        "  pending    [--json] [--state-path P]\n"
        "  state-path [--state-path P]\n"
    )
