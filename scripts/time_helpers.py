#!/usr/bin/env python3
"""Centralized UTC ISO-8601 timestamp helpers.

Background
----------
Prior to this module the codebase carried ~10 independent ``utc_now_iso``
definitions (and one ``_utcnow_iso``) — see the 2026-05-21 Helm full
review §Duplication Findings. Two stylistic variants existed:

* default :py:meth:`datetime.isoformat` output (microsecond precision,
  ``+00:00`` suffix); used by most scripts/commands.
* the explicit ``"%Y-%m-%dT%H:%M:%SZ"`` (``Z`` suffix, second precision)
  used by ``memory_tree/tree.py``.

Mixing them in the same JSONL stream causes string-comparison "newest
first" sort orders to behave inconsistently because microsecond-precision
timestamps sort *after* second-precision ones for the same instant.

Public API
----------
* :func:`utc_now` — the timezone-aware ``datetime`` (UTC) used by every
  caller that needs both an object and a string.
* :func:`utc_now_iso` — the canonical ISO-8601 string. Currently this
  emits ``datetime.now(timezone.utc).isoformat()`` (with offset) so it
  matches every legacy ``utc_now_iso`` shim from the scripts directory
  bit-for-bit, preventing on-disk format drift.
* :func:`utc_now_iso_seconds` — the explicit ``"%Y-%m-%dT%H:%M:%SZ"``
  form used by memory_tree.

Both functions take an optional ``now`` parameter so tests can inject a
fixed clock without monkey-patching :func:`datetime.now`.

Design notes
------------
This module deliberately mirrors the legacy formats verbatim so that
existing JSONL files (task-ledger.jsonl, etc.) can be migrated one
module at a time without re-rendering historical timestamps. A future
sweep may unify on a single format (likely the ``Z``-suffix second
precision variant since it is shorter and unambiguous), but that is a
breaking change for any external tooling that parses Helm state files.
"""

from __future__ import annotations

from datetime import datetime, timezone

__all__ = [
    "utc_now",
    "utc_now_iso",
    "utc_now_iso_seconds",
]


def utc_now(now: datetime | None = None) -> datetime:
    """Return a timezone-aware UTC :class:`datetime`.

    If ``now`` is provided it is normalized to UTC; otherwise the
    current wall-clock time is used. Tests should pass ``now`` rather
    than monkey-patching :py:meth:`datetime.now`.
    """
    if now is None:
        return datetime.now(timezone.utc)
    if now.tzinfo is None:
        return now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc)


def utc_now_iso(now: datetime | None = None) -> str:
    """Return the canonical ISO-8601 timestamp string.

    Format matches the legacy ``utc_now_iso`` implementations across
    ``scripts/`` (e.g. ``2026-05-21T08:00:00.123456+00:00``), preserving
    on-disk JSONL compatibility.
    """
    return utc_now(now).isoformat()


def utc_now_iso_seconds(now: datetime | None = None) -> str:
    """Return the ``Z``-suffix, second-precision ISO-8601 string.

    Format: ``2026-05-21T08:00:00Z``. Matches the legacy ``_utcnow_iso``
    used by ``memory_tree/tree.py``. Prefer this for human-readable
    summaries (frontmatter, summary cards) where microsecond precision
    is noise.
    """
    return utc_now(now).strftime("%Y-%m-%dT%H:%M:%SZ")
