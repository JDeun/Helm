#!/usr/bin/env python3
"""Patch-first edit policy helper.

Provides utilities for enforcing the patch-first editing strategy:
- Loading and caching the edit policy configuration.
- Deciding whether a checkpoint is required before editing a target.
- Tracking per-file patch failure counts.
- Determining the next action after repeated patch failures.

No external dependencies; uses only stdlib + json.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Policy location
# ---------------------------------------------------------------------------

_POLICY_PATH = Path(__file__).parent.parent / "references" / "edit_policy.json"

# Module-level cache; None means "not yet loaded".
_cached_policy: dict[str, Any] | None = None

# State key used to store per-file patch failure counters.
_FAILURES_KEY = "__patch_failures__"

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_edit_policy(*, reload: bool = False) -> dict[str, Any]:
    """Load and cache the edit policy from *references/edit_policy.json*.

    Parameters
    ----------
    reload:
        When *True* the in-process cache is invalidated and the file is
        re-read from disk.

    Raises
    ------
    FileNotFoundError
        If the policy file is missing; re-raised with a clear diagnostic
        message rather than being silently swallowed.
    """
    global _cached_policy
    if _cached_policy is None or reload:
        if not _POLICY_PATH.exists():
            raise FileNotFoundError(
                f"Edit policy file not found: {_POLICY_PATH}. "
                "Ensure references/edit_policy.json exists in the repository root."
            )
        with _POLICY_PATH.open("r", encoding="utf-8") as fh:
            _cached_policy = json.load(fh)
    return copy.deepcopy(_cached_policy)


def should_create_checkpoint(target_kind: str) -> bool:
    """Return *True* if a checkpoint is required before editing *target_kind*.

    Parameters
    ----------
    target_kind:
        A string label identifying the kind of target being edited (e.g.
        ``"skill_router"``, ``"shared_workflow"``).  Matched case-sensitively
        against the ``requires_checkpoint_for`` list in the policy.
    """
    policy = load_edit_policy()
    required: list[str] = policy.get("requires_checkpoint_for", [])
    return target_kind in required


def read_before_write_required() -> bool:
    """Return True when write attempts require fresh read evidence.

    The policy originally documented read-before-write as an advisory note.
    This helper gives runners a deterministic contract surface: any edit
    operation can ask this module whether missing or stale read evidence should
    block the write.
    """
    policy = load_edit_policy()
    read_policy = policy.get("read_before_write", {})
    if not isinstance(read_policy, dict):
        return False
    return read_policy.get("mode") == "required"


def validate_read_evidence(
    *,
    target_path: str,
    evidence: dict[str, Any] | None,
    current_mtime_ns: int | None = None,
    current_size: int | None = None,
) -> tuple[bool, str | None]:
    """Validate read evidence for a pending write.

    Required evidence shape:
    ``{"path": str, "mtime_ns": int|None, "size": int|None, "read_at": str}``.
    ``current_mtime_ns`` and ``current_size`` are optional because callers may
    validate planned writes for not-yet-existing files; when supplied they must
    match the evidence.
    """
    if not read_before_write_required():
        return True, None
    if evidence is None:
        return False, "missing_read_evidence"
    if evidence.get("path") != target_path:
        return False, "read_evidence_path_mismatch"
    if not evidence.get("read_at"):
        return False, "read_evidence_missing_read_at"
    if current_mtime_ns is not None and evidence.get("mtime_ns") != current_mtime_ns:
        return False, "read_evidence_stale_mtime"
    if current_size is not None and evidence.get("size") != current_size:
        return False, "read_evidence_stale_size"
    return True, None


def whole_file_rewrite_allowed(reason: str) -> bool:
    """Return True if ``reason`` is on the whole-file rewrite allowlist."""
    policy = load_edit_policy()
    allowed = policy.get("whole_file_rewrite_allowed_for", [])
    return reason in allowed


def validate_edit_request(
    *,
    target_path: str,
    operation: str,
    read_evidence: dict[str, Any] | None = None,
    current_mtime_ns: int | None = None,
    current_size: int | None = None,
    rewrite_reason: str | None = None,
) -> tuple[bool, str | None]:
    """Validate a planned edit against the SmallCode-inspired policy.

    ``operation`` is a small stable string such as ``"apply_patch"`` or
    ``"whole_file_rewrite"``. The helper is intentionally side-effect free so
    runners can call it before performing file mutations.
    """
    ok, reason = validate_read_evidence(
        target_path=target_path,
        evidence=read_evidence,
        current_mtime_ns=current_mtime_ns,
        current_size=current_size,
    )
    if not ok:
        return ok, reason
    if operation == "whole_file_rewrite":
        if not rewrite_reason:
            return False, "missing_whole_file_rewrite_reason"
        if not whole_file_rewrite_allowed(rewrite_reason):
            return False, "whole_file_rewrite_not_allowed"
    return True, None


def record_patch_failure(state: dict[str, Any], path: str) -> int:
    """Increment the patch-failure counter for *path* inside *state*.

    The counter is stored at ``state["__patch_failures__"][path]``.  This
    function is idempotent in the sense that each call increments by exactly 1
    regardless of the current value; callers are responsible for the number of
    invocations.

    Parameters
    ----------
    state:
        A mutable dict representing the current harness state.  Modified
        in-place.
    path:
        The file path whose failure counter should be incremented.

    Returns
    -------
    int
        The new (post-increment) failure count for *path*.
    """
    if _FAILURES_KEY not in state:
        state[_FAILURES_KEY] = {}
    failures: dict[str, int] = state[_FAILURES_KEY]
    failures[path] = failures.get(path, 0) + 1
    return failures[path]


def next_action_for_path(state: dict[str, Any], path: str) -> str:
    """Return the recommended action for *path* given its current failure count.

    Parameters
    ----------
    state:
        Harness state dict that may contain a ``__patch_failures__`` sub-dict.
    path:
        The file path to evaluate.

    Returns
    -------
    str
        ``"retry"`` when the failure count is strictly below the configured
        ``max_patch_failures_per_file``; otherwise the value of
        ``on_repeated_patch_failure`` from the policy (defaults to
        ``"reload_context_then_decompose"``).
    """
    policy = load_edit_policy()
    max_failures: int = policy.get("max_patch_failures_per_file", 2)
    escalation: str = policy.get("on_repeated_patch_failure", "reload_context_then_decompose")

    failures: dict[str, int] = state.get(_FAILURES_KEY, {})
    count = failures.get(path, 0)

    if count < max_failures:
        return "retry"
    return escalation
