"""Sync test: browser_gate._BROWSER_MAX_SESSIONS must derive from
browser_work_verifier._PROFILE_POLICIES so the two cannot silently drift.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.browser_gate import _BROWSER_MAX_SESSIONS
from scripts.browser_work_verifier import _PROFILE_POLICIES


def test_max_sessions_derived_from_profile_policies():
    """Every profile-policy entry's max_sessions must equal the browser_gate cap."""
    for name, policy in _PROFILE_POLICIES.items():
        expected = policy.get("max_sessions", 1)
        actual = _BROWSER_MAX_SESSIONS.get(name)
        assert actual == expected, (
            f"profile {name!r}: _BROWSER_MAX_SESSIONS={actual!r} != "
            f"_PROFILE_POLICIES[{name!r}].max_sessions={expected!r}"
        )


def test_max_sessions_has_all_policy_profiles():
    """_BROWSER_MAX_SESSIONS must cover exactly the profiles in _PROFILE_POLICIES."""
    assert set(_BROWSER_MAX_SESSIONS.keys()) == set(_PROFILE_POLICIES.keys())
