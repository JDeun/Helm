# Mirror of ~/.openclaw/workspace/.worktrees/harness-eng/scripts/tool_groups.py — keep in sync.
"""tool_groups — tool-group grant declarations per execution profile.

Data layout choice: a dedicated ``references/tool_groups.json`` (keyed by
profile name) was chosen over extending ``references/execution_profiles.json``
because it:

1. Keeps the execution-profile schema stable — no changes to consumers of
   ``load_profiles()`` in ``run_with_profile.py``.
2. Allows the tool-group matrix to be loaded independently (e.g. in tests,
   audit scripts, or a future CLI subcommand) without pulling in the full
   profile object graph.
3. Makes the mirror contract explicit: ``tool_groups.json`` is a single,
   self-contained artefact that can be byte-compared between the Helm
   package worktree and the workspace worktree.

The eight tool-group names are treated as plain string constants
(no enum class per task constraints):

    read_file, apply_patch, focused_test, git_diff,
    broad_shell, external_network, secrets_read, destructive_git

Public API:

    load_tool_groups(profile: str) -> dict
        Return {'allow': [...], 'ask': [...], 'deny': [...]} for a profile.
        Raises ValueError on unknown profile.

    classify_tool(profile: str, tool: str) -> str
        Return 'allow' | 'ask' | 'deny' for a (profile, tool) pair.
        Unknown tool defaults to 'ask' (conservative).
        Raises ValueError on unknown profile.

    compute_grant(profile: str, requested_tools: list[str]) -> dict
        Return {'granted': [...], 'requires_approval': [...], 'denied': [...]}.
        The three lists are disjoint and cover requested_tools exactly.
        Order within each bucket matches the input order.
"""
from __future__ import annotations

import json
from pathlib import Path

# Resolve the data file relative to this module's location.
_DATA_FILE = Path(__file__).resolve().parents[1] / "references" / "tool_groups.json"

_CACHE: dict | None = None


def _load_data() -> dict:
    """Load and cache the tool_groups.json data."""
    global _CACHE  # noqa: PLW0603
    if _CACHE is not None:
        return _CACHE
    try:
        raw = _DATA_FILE.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise FileNotFoundError(f"tool_groups data file not found: {_DATA_FILE}") from None
    data = json.loads(raw)
    _CACHE = data
    return data


def _get_profiles() -> dict[str, dict]:
    """Return the profiles dict from the data file."""
    data = _load_data()
    profiles = data.get("profiles")
    if not isinstance(profiles, dict):
        raise ValueError("tool_groups.json is missing a 'profiles' object")
    return profiles


def load_tool_groups(profile: str) -> dict:
    """Return {'allow': [...], 'ask': [...], 'deny': [...]} for a profile.

    Raises ValueError on unknown profile.
    """
    profiles = _get_profiles()
    if profile not in profiles:
        known = ", ".join(sorted(profiles.keys()))
        raise ValueError(f"Unknown profile {profile!r}. Known profiles: {known}")
    entry = profiles[profile]
    return {
        "allow": list(entry.get("allow", [])),
        "ask": list(entry.get("ask", [])),
        "deny": list(entry.get("deny", [])),
    }


def classify_tool(profile: str, tool: str) -> str:
    """Return 'allow' | 'ask' | 'deny' for a (profile, tool) pair.

    Unknown tool defaults to 'ask' (conservative).
    Raises ValueError on unknown profile.
    """
    groups = load_tool_groups(profile)
    if tool in groups["allow"]:
        return "allow"
    if tool in groups["deny"]:
        return "deny"
    # Both known-ask tools and unknown tools resolve to 'ask' (conservative default).
    return "ask"


def compute_grant(profile: str, requested_tools: list[str]) -> dict:
    """Return {'granted': [...], 'requires_approval': [...], 'denied': [...]}.

    The three lists are disjoint and cover requested_tools exactly.
    Order within each bucket matches the input request order.
    Raises ValueError on unknown profile.
    """
    # Validate the profile eagerly (load_tool_groups raises if unknown).
    load_tool_groups(profile)

    granted: list[str] = []
    requires_approval: list[str] = []
    denied: list[str] = []

    for tool in requested_tools:
        decision = classify_tool(profile, tool)
        if decision == "allow":
            granted.append(tool)
        elif decision == "deny":
            denied.append(tool)
        else:
            requires_approval.append(tool)

    return {
        "granted": granted,
        "requires_approval": requires_approval,
        "denied": denied,
    }
