"""Browser work verifier — minimum viable per Task 13 design.

Pure function `verify(request)` returns a BrowserReconDecision dict with
six bool flags (per docs/harness-engineering/03-browser-work-verifier.md §3)
plus `reason` and `checks`. No callers yet — wiring is Wave 3. Profile
policies are inline constants mirroring 04-browser-profile-policy.md §3;
unresolved open questions (OQ-1..8) default to `require_confirmation`
with the OQ number in the reason. stdlib only, no I/O.
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
    if intended_action in _MUTATION_ACTIONS:
        return "mutation"
    if intended_action in _READ_ACTIONS:
        return "read"
    return "unknown"


def _check_login_compat(
    logged_in_required: bool, policy: dict[str, Any] | None
) -> tuple[bool, str]:
    if not logged_in_required:
        return True, ""
    if policy is None:
        return False, "logged-in required but no browser policy for this profile"
    if not policy.get("allow_logged_in_profile", False):
        return False, "logged-in required but profile.allow_logged_in_profile=false"
    return True, ""


def _check_mutation_allowed(
    action_class: str, policy: dict[str, Any] | None
) -> tuple[str, str]:
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
    if not parallel_requested:
        return False, ""
    if policy is None:
        return False, "no browser policy — parallel default-deny"
    if policy.get("max_sessions", 1) <= 1:
        return False, "profile.max_sessions <= 1"
    if action_class == "mutation":
        return False, "mutation actions are not parallel-safe"
    # Read-only + parallel + max_sessions>1: permissive per design §4 Check 6,
    # whether or not a site note exists (future verifier may inspect the note).
    return True, ""


def verify(request: dict[str, Any]) -> dict[str, Any]:
    """Evaluate a browser task and return a BrowserReconDecision dict.

    Required keys in ``request``: ``url_pattern``, ``intended_action``,
    ``logged_in_account_required``, ``parallel_requested``,
    ``execution_profile``. Optional: ``existing_site_note_path``.

    Returns a dict with the six DECISION_KEYS flags + ``reason`` + ``checks``.
    Defensive: malformed input returns safe-default decision rather than
    raising.
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
