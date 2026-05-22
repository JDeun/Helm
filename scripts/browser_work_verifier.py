"""Browser work verifier — minimum viable per Task 13 design.

Reads a `BrowserTaskSpec` request and returns a `BrowserReconDecision` dict
with bool flags (allow_single_session, allow_parallel, require_user_login,
require_confirmation, block_mutation, pause_profile) plus a diagnostic
`reason` and a per-check `checks` map.

This module has NO callers yet — wiring into `run_with_profile.py` is a
later Wave (Task 13 → Wave 3). It is a pure function: stdlib only, no
global state, no I/O. Future work will load profile policies from a YAML
file at `~/Helm/references/browser_profile_policy.yaml`; for now the
policies are inline constants derived from `docs/harness-engineering/
04-browser-profile-policy.md` §3.

Open questions from 04-browser-profile-policy.md §6 that are NOT yet
resolved here (default to `require_confirmation` with the OQ number in
the reason string):

- OQ-1: `allow_mutation: gated` — confirmation + site note, or either?
        Current default: require_confirmation when site note absent.
- OQ-2: risky_edit + logged-in escalation path.
        Current default: block (allow_logged_in_profile: false).
- OQ-3: max_sessions enforcement location.
        Current default: verifier emits the cap; runner-side enforcement
        is a Wave 3 task.
- OQ-4..8: see design doc.
"""
from __future__ import annotations

from typing import Any

DECISION_KEYS = frozenset(
    {
        "allow_single_session",
        "allow_parallel",
        "require_user_login",
        "require_confirmation",
        "block_mutation",
        "pause_profile",
    }
)

_MUTATION_ACTIONS = frozenset({"fillform", "interact", "submit"})
_READ_ACTIONS = frozenset(
    {"read", "navigate", "fetch_resource", "screenshot", "crawl_batch"}
)
_KNOWN_ACTIONS = _MUTATION_ACTIONS | _READ_ACTIONS

# Profile policies (per docs/harness-engineering/04-browser-profile-policy.md §3)
# workspace_edit and remote_handoff intentionally have no browser policy.
_PROFILE_POLICIES: dict[str, dict[str, Any]] = {
    "inspect_local": {
        "allowed_modes": ("crawl", "default"),
        "allow_logged_in_profile": False,
        "allow_mutation": False,
        "max_sessions": 5,
    },
    "service_ops": {
        "allowed_modes": ("default",),
        "allow_logged_in_profile": True,
        "allow_mutation": "gated",
        "max_sessions": 3,
    },
    "risky_edit": {
        "allowed_modes": ("default", "crawl"),
        "allow_logged_in_profile": False,
        "allow_mutation": False,
        "require_checkpoint": True,
        "require_pause_resume": True,
        "require_cleanup_evidence": True,
        "max_sessions": 2,
    },
}

_REQUIRED_REQUEST_KEYS = frozenset(
    {
        "url_pattern",
        "intended_action",
        "logged_in_account_required",
        "parallel_requested",
        "execution_profile",
    }
)


def _malformed(reason: str) -> dict[str, Any]:
    """Build a safe-default decision for a request that cannot be reasoned about."""
    return {
        "allow_single_session": False,
        "allow_parallel": False,
        "require_user_login": False,
        "require_confirmation": True,
        "block_mutation": False,
        "pause_profile": False,
        "reason": reason,
        "checks": {},
    }


def _check_action_class(intended_action: str) -> str:
    """Return 'mutation', 'read', or 'unknown' for the action."""
    if intended_action in _MUTATION_ACTIONS:
        return "mutation"
    if intended_action in _READ_ACTIONS:
        return "read"
    return "unknown"


def _check_login_compat(
    logged_in_required: bool, policy: dict[str, Any] | None
) -> tuple[bool, str]:
    """Return (compatible, reason). False means the profile cannot satisfy the login need."""
    if not logged_in_required:
        return True, ""
    if policy is None:
        return False, "logged-in required but no browser policy for this profile"
    if not policy.get("allow_logged_in_profile", False):
        return (
            False,
            "logged-in required but profile.allow_logged_in_profile=false",
        )
    return True, ""


def _check_mutation_allowed(
    action_class: str, policy: dict[str, Any] | None
) -> tuple[str, str]:
    """Return (mode, reason) where mode is 'block', 'gated', 'allow', or 'na'."""
    if action_class != "mutation":
        return "na", ""
    if policy is None:
        return "block", "no browser policy for this profile (mutation default-deny)"
    setting = policy.get("allow_mutation", False)
    if setting is False:
        return "block", "profile.allow_mutation=false"
    if setting == "gated":
        return "gated", "profile.allow_mutation=gated (OQ-1: site-note-aware confirm)"
    if setting is True:
        return "allow", ""
    return "block", f"unknown allow_mutation value {setting!r} — safe-default block"


def _check_parallel_safe(
    parallel_requested: bool,
    action_class: str,
    existing_site_note_path: str | None,
    policy: dict[str, Any] | None,
) -> tuple[bool, str]:
    """Return (parallel_ok, reason)."""
    if not parallel_requested:
        return False, ""
    if policy is None:
        return False, "no browser policy — parallel default-deny"
    if policy.get("max_sessions", 1) <= 1:
        return False, "profile.max_sessions <= 1"
    if action_class == "mutation":
        return False, "mutation actions are not parallel-safe"
    if existing_site_note_path is None:
        # No site note + read-only is the documented permissive case
        # (design doc §4 Check 6): allow.
        return True, ""
    # Site note present — we cannot read it from this stub, so default to
    # the permissive read-only case. Future verifier will inspect the note.
    return True, ""


def verify(request: dict[str, Any]) -> dict[str, Any]:
    """Evaluate a browser task request and return a `BrowserReconDecision` dict.

    Required keys in `request`:
      - url_pattern (str)
      - intended_action (str — one of the documented vocabulary in design doc §2)
      - logged_in_account_required (bool)
      - parallel_requested (bool)
      - execution_profile (str — one of the 5 OpenClaw profiles)

    Optional:
      - existing_site_note_path (str | None)

    Returns a dict with all six decision flags, `reason` (str), and `checks`
    (dict) with per-check outcomes. `decision_keys() == DECISION_KEYS`.

    Defensive: on a malformed request (missing key, wrong type), returns a
    safe-default decision (`allow_single_session=False, require_confirmation=True`)
    rather than raising. `reason` documents why.
    """
    missing = _REQUIRED_REQUEST_KEYS - set(request.keys())
    if missing:
        return _malformed(f"malformed request: missing keys {sorted(missing)!r}")

    intended_action = request["intended_action"]
    profile_name = request["execution_profile"]
    logged_in_required = bool(request["logged_in_account_required"])
    parallel_requested = bool(request["parallel_requested"])
    existing_site_note_path = request.get("existing_site_note_path")

    policy = _PROFILE_POLICIES.get(profile_name)

    # Decision flags start at safe defaults; checks turn them on selectively.
    decision = {
        "allow_single_session": False,
        "allow_parallel": False,
        "require_user_login": False,
        "require_confirmation": False,
        "block_mutation": False,
        "pause_profile": False,
    }
    reasons: list[str] = []
    checks: dict[str, Any] = {}

    # No browser policy for this profile (workspace_edit, remote_handoff,
    # or unknown) → require confirmation; do not authorize sessions.
    if policy is None:
        reasons.append(
            f"profile {profile_name!r} has no browser policy (OQ-4: remote_handoff "
            "and workspace_edit treatment)"
        )
        decision["require_confirmation"] = True
        checks["profile_policy"] = "absent"
        return _finalize(decision, reasons, checks)

    checks["profile_policy"] = "present"

    # Check 3: action classification (mutation vs read).
    action_class = _check_action_class(intended_action)
    checks["action_class"] = action_class
    if action_class == "unknown":
        reasons.append(
            f"unknown intended_action {intended_action!r}; require_confirmation"
        )
        decision["require_confirmation"] = True
        return _finalize(decision, reasons, checks)

    # Check 1+2: login compatibility.
    login_ok, login_reason = _check_login_compat(logged_in_required, policy)
    checks["login_ok"] = login_ok
    if logged_in_required:
        decision["require_user_login"] = True
    if not login_ok:
        reasons.append(login_reason)
        return _finalize(decision, reasons, checks)

    # Check 3+4: mutation handling.
    mut_mode, mut_reason = _check_mutation_allowed(action_class, policy)
    checks["mutation_mode"] = mut_mode
    if mut_mode == "block":
        decision["block_mutation"] = True
        reasons.append(mut_reason)
        return _finalize(decision, reasons, checks)
    if mut_mode == "gated":
        decision["require_confirmation"] = True
        reasons.append(mut_reason)
        # gated still allows the session — fall through to single-session grant
    if mut_mode == "allow":
        reasons.append("mutation explicitly allowed by profile")

    # At this point a single session is permissible.
    decision["allow_single_session"] = True

    # Check 6: parallel safety.
    parallel_ok, parallel_reason = _check_parallel_safe(
        parallel_requested, action_class, existing_site_note_path, policy
    )
    checks["parallel_ok"] = parallel_ok
    if parallel_ok:
        decision["allow_parallel"] = True
    elif parallel_requested and parallel_reason:
        reasons.append(parallel_reason)

    # Check 7: site note absence + mutation already handled via gated path.
    # Site note presence does not by itself authorize anything (per design §4).
    checks["existing_site_note"] = (
        "present" if existing_site_note_path else "absent"
    )

    return _finalize(decision, reasons, checks)


def _finalize(
    decision: dict[str, bool],
    reasons: list[str],
    checks: dict[str, Any],
) -> dict[str, Any]:
    """Attach reason + checks to the decision and return it."""
    result: dict[str, Any] = dict(decision)
    result["reason"] = "; ".join(r for r in reasons if r) or "ok"
    result["checks"] = dict(checks)
    return result
