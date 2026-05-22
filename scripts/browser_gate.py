"""Browser gate helpers extracted from ``scripts.run_with_profile`` (M-1 refactor).

This module owns the browser-gate logic that was previously inlined in
``run_with_profile.py``: the action-set constants, the per-profile session cap
table, the ledger-based session counter, the cleanup-evidence helpers, and the
``_evaluate_browser_gate`` orchestrator.

``run_with_profile`` imports the constants and core helpers from here and
exposes thin wrappers for the ledger-reading functions so that the existing
call sites and test patches continue to work without modification.

Design note on backward-test-compat (strategy: thin wrappers in run_with_profile)
----------------------------------------------------------------------------------
The ledger-reading functions ``_count_active_browser_sessions`` and
``_check_cleanup_required_satisfied`` need access to ``TASK_LEDGER``, a
module-level path defined in ``run_with_profile``.  Tests patch
``scripts.run_with_profile.TASK_LEDGER`` and call the functions via
``rwp._count_active_browser_sessions(...)``.

To honour that pattern without a circular import, the canonical implementations
here accept ``task_ledger`` as an **explicit keyword argument**.
``run_with_profile`` exposes thin wrappers (also named
``_count_active_browser_sessions`` / ``_check_cleanup_required_satisfied``)
that fill in the ``task_ledger`` from its own ``TASK_LEDGER`` at call time.
Patching ``rwp.TASK_LEDGER`` therefore propagates transparently to the
browser-gate logic.

``_evaluate_browser_gate`` likewise receives its dependencies (ledger path,
helper callbacks, exit codes) via keyword arguments to avoid importing
``run_with_profile`` from this module.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    import argparse

__all__ = [
    "_BROWSER_ACTIONS",
    "_BROWSER_SESSION_TAIL_LINES",
    "_BROWSER_MAX_SESSIONS",
    "_count_active_browser_sessions_impl",
    "_require_cleanup_evidence_from_entry",
    "_check_cleanup_required_satisfied_impl",
    "_evaluate_browser_gate_impl",
]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_BROWSER_ACTIONS = frozenset({
    "read", "navigate", "fetch_resource", "screenshot",
    "crawl_batch", "fillform", "interact", "submit",
})

# Tail cap for _count_active_browser_sessions.  2000 lines covers a 10-minute
# window at up to 200 task-starts per minute.  Raise this constant if your
# throughput exceeds that rate.
_BROWSER_SESSION_TAIL_LINES: int = 2000

# Per-profile session cap constants for OQ-3 max_sessions enforcement.
# Mirrors _PROFILE_POLICIES in browser_work_verifier.py.
_BROWSER_MAX_SESSIONS: dict[str, int] = {
    "inspect_local": 5,
    "service_ops": 3,
    "risky_edit": 2,
}


# ---------------------------------------------------------------------------
# Core implementations (ledger path passed explicitly)
# ---------------------------------------------------------------------------

def _count_active_browser_sessions_impl(
    profile: str,
    window_minutes: int = 10,
    *,
    task_ledger: Path,
) -> int:
    """Count open browser sessions for *profile* in the recent ledger window.

    OQ-3: Runner-side max_sessions enforcement via ledger counter.
    Extracted for testability; called only from ``_evaluate_browser_gate_impl``.

    A session is "open" when:
    - A ledger entry has ``browser_recon`` set (verifier was called)
    - The entry's ``status`` is ``browser_recon`` or ``running`` or
      ``browser_approved_with_risk`` or ``browser_approved_by_site_note``
      (i.e. a session was authorised to open)
    - The entry's ``started_at`` is within ``window_minutes`` of now
    - No later entry for the same ``task_id`` has ``cleanup_status`` set

    Returns the count of such open sessions.  Returns 0 on any read/
    parse error (fail-open so that ledger corruption never blocks work).

    Throughput assumption
    ---------------------
    Only the last ``_BROWSER_SESSION_TAIL_LINES`` ledger lines are examined.
    This covers a 10-minute window at up to 200 task-starts per minute.  At
    higher throughput the tail cap may miss older still-open sessions; raise
    ``_BROWSER_SESSION_TAIL_LINES`` accordingly.
    """
    from scripts.time_helpers import utc_now_iso
    try:
        if not task_ledger.exists():
            return 0

        lines = task_ledger.read_text(encoding="utf-8").splitlines()
        # Parse all entries, keep only the last N lines for performance
        # (10-minute window; see _BROWSER_SESSION_TAIL_LINES for the
        # throughput assumption).
        tail = lines[-_BROWSER_SESSION_TAIL_LINES:]

        now_ts = utc_now_iso()
        # Build a simple ISO-comparable window cutoff.
        import datetime as _dt
        now_dt = _dt.datetime.fromisoformat(now_ts.replace("Z", "+00:00"))
        cutoff_dt = now_dt - _dt.timedelta(minutes=window_minutes)

        _OPEN_STATUSES = frozenset({
            "browser_recon",
            "running",
            "browser_approved_with_risk",
            "browser_approved_by_site_note",
            "browser_recon_shadow",
        })

        # First pass: collect all entries.
        all_entries: list[dict] = []
        for line in tail:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(entry, dict):
                all_entries.append(entry)

        # Identify task_ids that have cleanup evidence.
        tasks_with_cleanup: set[str] = set()
        for entry in all_entries:
            task_id = entry.get("task_id")
            if task_id and entry.get("cleanup_status"):
                tasks_with_cleanup.add(task_id)

        # Count open sessions.
        open_count = 0
        for entry in all_entries:
            if entry.get("profile") != profile:
                continue
            if not entry.get("browser_recon"):
                continue
            status = entry.get("status", "")
            if status not in _OPEN_STATUSES:
                continue
            started_at = entry.get("started_at", "")
            if not started_at:
                continue
            try:
                entry_dt = _dt.datetime.fromisoformat(
                    started_at.replace("Z", "+00:00")
                )
            except ValueError:
                continue
            if entry_dt < cutoff_dt:
                continue
            task_id = entry.get("task_id")
            if task_id and task_id in tasks_with_cleanup:
                continue
            open_count += 1

        return open_count
    except Exception:  # noqa: BLE001 — fail-open; never block on ledger errors
        return 0


def _require_cleanup_evidence_from_entry(entry: dict) -> bool:
    """Return True iff *entry* has ``browser_recon.require_cleanup_evidence == True``.

    Defensively handles the case where ``browser_recon`` is None, missing, or
    any non-dict type (boolean, string, int, …) — all resolve to False.
    Only a ``dict`` with ``require_cleanup_evidence`` set to a truthy value
    returns True.
    """
    recon = entry.get("browser_recon")
    if not isinstance(recon, dict):
        return False
    return bool(recon.get("require_cleanup_evidence"))


def _check_cleanup_required_satisfied_impl(
    task_id: str,
    *,
    task_ledger: Path,
) -> tuple[bool, str | None]:
    """Check whether cleanup evidence is recorded for *task_id*.

    OQ-7: Finalization gate.
    Extracted for testability; called only from ``cmd_run``.

    Returns ``(satisfied, reason)`` where:
    - ``satisfied=True`` means cleanup is not required OR evidence exists.
    - ``satisfied=False, reason=str`` means cleanup was required but no
      ``cleanup_status`` row exists for this task.

    Reads the task ledger and examines all rows for this task_id.
    Fail-open: any ledger read error returns ``(True, None)`` so that
    ledger corruption never permanently blocks task completion.
    """
    try:
        if not task_ledger.exists():
            return True, None

        entries_for_task: list[dict] = []
        for line in task_ledger.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(entry, dict) and entry.get("task_id") == task_id:
                entries_for_task.append(entry)

        if not entries_for_task:
            return True, None

        # Check if any entry required cleanup evidence.
        cleanup_required = any(
            _require_cleanup_evidence_from_entry(entry)
            for entry in entries_for_task
        )
        if not cleanup_required:
            return True, None

        # Check if any entry has cleanup_status recorded.
        has_cleanup = any(
            entry.get("cleanup_status")
            for entry in entries_for_task
        )
        if has_cleanup:
            return True, None

        return False, (
            f"task {task_id} required browser cleanup evidence "
            "(require_cleanup_evidence=True) but no cleanup_status is recorded; "
            "record cleanup_status before marking complete"
        )
    except Exception:  # noqa: BLE001 — fail-open
        return True, None


def _evaluate_browser_gate_impl(
    args: "argparse.Namespace",
    task: dict,
    *,
    task_ledger: Path,
    append_ledger_fn: Callable[[dict], None],
    browser_gate_enabled_fn: Callable[[], bool],
    utc_now_iso_fn: Callable[[], str],
    exit_browser_blocked: int,
    exit_guard_require_approval: int,
) -> int | None:
    """Evaluate the browser verifier gate and mutate *task* with the result.

    Returns:
        None   — caller should proceed normally.
        int    — exit code; caller should exit immediately with that code.

    This function is called ONLY when ``--browser-action`` is present.
    It never raises: any exception from the verifier is caught and treated
    as a ``require_confirmation`` shadow-mode fallback.

    Behavior matrix
    ---------------
    gate OFF  → shadow mode: decision logged as ``browser_recon_shadow``,
                runner proceeds (returns None).
    gate ON   → enforce mode: decision honored per spec.
    """
    from scripts.browser_work_verifier import verify as _bv_verify

    request = {
        "url_pattern": getattr(args, "browser_url_pattern", None) or "",
        "intended_action": args.browser_action,
        "logged_in_account_required": bool(getattr(args, "browser_logged_in", False)),
        "parallel_requested": bool(getattr(args, "browser_parallel", False)),
        "execution_profile": args.profile,
    }
    site_note = getattr(args, "browser_site_note", None)
    if site_note:
        request["existing_site_note_path"] = site_note

    try:
        decision = _bv_verify(request)
    except Exception as exc:  # noqa: BLE001 — verifier errors degrade safely
        decision = {
            "allow_single_session": False,
            "allow_parallel": False,
            "require_user_login": False,
            "require_confirmation": True,
            "block_mutation": False,
            "pause_profile": False,
            "require_cleanup_evidence": False,
            "reason": f"verifier exception: {exc}",
            "checks": {},
        }

    enforce = browser_gate_enabled_fn()

    if not enforce:
        # Shadow mode: log and proceed.
        task["status"] = "browser_recon_shadow"
        task["browser_recon"] = decision
        task["finished_at"] = utc_now_iso_fn()
        append_ledger_fn(dict(task))
        # Reset status so the calling code can continue.
        task.pop("status", None)
        task.pop("browser_recon", None)
        task.pop("finished_at", None)
        return None

    # Enforce mode — honor the decision.

    # OQ-3: max_sessions check — BEFORE session open.
    # Only enforce when gate is on; shadow mode always proceeds.
    _profile = args.profile
    _max_sessions = _BROWSER_MAX_SESSIONS.get(_profile)
    if _max_sessions is not None:
        _active = _count_active_browser_sessions_impl(_profile, task_ledger=task_ledger)
        if _active >= _max_sessions:
            task["status"] = "browser_blocked"
            task["finished_at"] = utc_now_iso_fn()
            task["browser_recon"] = decision
            task["browser_block_reason"] = "max_sessions_reached"
            append_ledger_fn(dict(task))
            print(
                f"BROWSER GATE BLOCKED: max_sessions reached for profile "
                f"{_profile!r} (active={_active}, cap={_max_sessions})",
                file=sys.stderr,
            )
            return exit_browser_blocked

    block = (
        decision.get("block_mutation") is True
        or decision.get("allow_single_session") is False
    )
    if block:
        task["status"] = "browser_blocked"
        task["finished_at"] = utc_now_iso_fn()
        task["browser_recon"] = decision
        append_ledger_fn(dict(task))
        print(
            f"BROWSER GATE BLOCKED: {decision.get('reason', 'blocked by verifier')}",
            file=sys.stderr,
        )
        return exit_browser_blocked

    if decision.get("require_confirmation"):
        # OQ-1: gated mutation gate is satisfied by EITHER --approve-risk OR an
        # existing site note (caller-supplied or auto-resolved by verifier).
        _site_note_in_decision = decision.get("checks", {}).get("existing_site_note")
        _site_note_present = (
            _site_note_in_decision == "present"
            or bool(request.get("existing_site_note_path"))
            or bool(getattr(args, "browser_site_note", None))
        )

        if _site_note_present:
            # Site note satisfies the gate (OQ-1).
            task["browser_approved_by_site_note"] = True
            task["status_browser"] = "browser_approved_by_site_note"
            _browser_recon_entry = dict(task)
            _browser_recon_entry["status"] = "browser_approved_by_site_note"
            _browser_recon_entry["browser_recon"] = decision
            append_ledger_fn(_browser_recon_entry)
            # task continues; do NOT set status yet — normal flow takes over.
        elif getattr(args, "approve_risk", False):
            # --approve-risk satisfies the gate.
            task["browser_approved_with_risk"] = True
            task["status_browser"] = "browser_approved_with_risk"
            _browser_recon_entry = dict(task)
            _browser_recon_entry["status"] = "browser_approved_with_risk"
            _browser_recon_entry["browser_recon"] = decision
            append_ledger_fn(_browser_recon_entry)
            # task continues; do NOT set status yet — normal flow takes over.
        else:
            # Neither approval nor site note — require human approval.
            task["status"] = "browser_requires_approval"
            task["finished_at"] = utc_now_iso_fn()
            task["browser_recon"] = decision
            append_ledger_fn(dict(task))
            print(
                f"BROWSER GATE REQUIRES APPROVAL: {decision.get('reason', '')}. "
                "Pass --approve-risk or provide a site note to proceed.",
                file=sys.stderr,
            )
            return exit_guard_require_approval
        return None

    # All clear: embed recon into the task dict for the normal finalize path.
    task["browser_recon"] = decision
    return None
