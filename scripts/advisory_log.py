"""Observability helper for advisory-channel exception swallow sites.

Three places in the codebase intentionally swallow exceptions from
optional "advisory" evaluations so the authoritative gate keeps
working when an advisory module misbehaves:

* :func:`scripts.command_guard.evaluate` (advisory_action_scope)
* :func:`scripts.command_guard._evaluate_advisory_action_scope`
  (action_scope module import)
* :func:`scripts.run_with_profile._attach_advisory_action_scope`
* :func:`scripts.reply_gate._advisory_phase_modules` (action_scope +
  freshness branches)

Pre-R5 these sites used a bare ``except Exception: pass`` which made
breakage in ``scripts.action_scope`` or ``scripts.freshness_lib``
invisible to operators (R4 review §Issues > Minor > M2). This helper
keeps the silent-on-the-hot-path semantics — failures still degrade
gracefully — but records each failure in a process-local counter and
emits a one-line debug breadcrumb to stderr when
``HELM_ADVISORY_DEBUG`` is set, so the failure mode is no longer
indistinguishable from "advisory not applicable for this entry".

The helper is intentionally dependency-free (stdlib only) and never
re-raises.
"""
from __future__ import annotations

import os
import sys
import threading
from collections import Counter
from typing import Iterator

__all__ = [
    "record_advisory_failure",
    "snapshot_advisory_failures",
    "reset_advisory_failures",
]

_LOCK = threading.Lock()
_FAILURES: Counter[str] = Counter()
_DEBUG_ENV = "HELM_ADVISORY_DEBUG"


def record_advisory_failure(channel: str, exc: BaseException) -> None:
    """Record one advisory-channel failure for later inspection.

    Parameters
    ----------
    channel:
        Stable identifier for the swallow site, e.g.
        ``"command_guard.action_scope"`` or ``"reply_gate.freshness"``.
        Used as the counter key so callers can attribute a spike.
    exc:
        The caught exception. Only its type-name and stringified form
        are read; never re-raised.

    The function is best-effort: any internal failure (e.g. stderr
    closed during interpreter shutdown) is itself swallowed so the
    caller's hot path stays advisory-safe.
    """
    try:
        with _LOCK:
            _FAILURES[channel] += 1
            _FAILURES[f"{channel}:{type(exc).__name__}"] += 1
        if os.environ.get(_DEBUG_ENV):
            # One short line, never multi-line stack — the goal is a
            # breadcrumb, not full forensics. Operators who want the
            # traceback can re-run with the advisory module raising
            # directly.
            print(
                f"[advisory] channel={channel} "
                f"error={type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
    except Exception:
        # Never let the observability helper itself break the
        # advisory-only contract.
        pass


def snapshot_advisory_failures() -> dict[str, int]:
    """Return a thread-safe copy of the counter for diagnostics.

    Currently consumed by tests to confirm an advisory channel has (or has
    not) been raising. A future ``helm doctor`` panel can surface the
    counter directly via this function; the wiring is not yet in place.
    """
    with _LOCK:
        return dict(_FAILURES)


def reset_advisory_failures() -> None:
    """Reset the in-process counter; intended for test isolation."""
    with _LOCK:
        _FAILURES.clear()


def iter_advisory_channels() -> Iterator[str]:
    """Iterate channel names recorded so far."""
    with _LOCK:
        return iter(list(_FAILURES.keys()))
