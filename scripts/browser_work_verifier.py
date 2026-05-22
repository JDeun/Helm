"""Browser work verifier — Wave 3b resolved design.

Pure function `verify(request, workspace_root=None)` returns a
BrowserReconDecision dict with seven bool flags (per
docs/harness-engineering/03-browser-work-verifier.md §3) plus `reason`
and `checks`.  No callers yet — wiring is in run_with_profile.py.
Profile policies are inline constants mirroring
04-browser-profile-policy.md §3.  stdlib only, no I/O except for
optional site-note file lookup.

Open-question resolution status (Wave 3b — 2026-05-22):
  OQ-1 RESOLVED: `gated` mutation → require_confirmation=True. Runner
      side honors --approve-risk OR presence of existing_site_note_path
      to satisfy the gate.  Verifier emits; runner enforces.
  OQ-2 RESOLVED: risky_edit + logged-in → permanent block
      (allow_logged_in_profile=false).  Reason string points to
      service_ops upgrade path.
  OQ-3 RESOLVED: max_sessions enforced runner-side via ledger counter
      (see run_with_profile._count_active_browser_sessions).
  OQ-4 RESOLVED: remote_handoff + any browser action → hard block
      (allow_single_session=False, NOT require_confirmation).
  OQ-5 RESOLVED: site note lookup uses fixed path
      <workspace>/skills/browser-site-notes/<host>.md.  Verifier
      auto-resolves when existing_site_note_path is None.
  OQ-6 RESOLVED (Wave 3a): browser_recon is a top-level sibling key
      alongside guard in the ledger row.
  OQ-7 RESOLVED: require_cleanup_evidence=True emitted for risky_edit
      when any browser action is requested.  Runner enforces at
      finalization (EXIT_CLEANUP_REQUIRED=28).
  OQ-8 RESOLVED: workspace_edit + any browser action → hard block
      (allow_single_session=False, NOT require_confirmation).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

DECISION_KEYS = frozenset(
    {
        "allow_single_session",
        "allow_parallel",
        "require_user_login",
        "require_confirmation",
        "block_mutation",
        "pause_profile",
        "require_cleanup_evidence",
    }
)

_MUTATION_ACTIONS = frozenset({"fillform", "interact", "submit"})
_READ_ACTIONS = frozenset(
    {"read", "navigate", "fetch_resource", "screenshot", "crawl_batch"}
)
_KNOWN_ACTIONS = _MUTATION_ACTIONS | _READ_ACTIONS

# Profile policies (per docs/harness-engineering/04-browser-profile-policy.md §3)
# workspace_edit and remote_handoff intentionally have no browser policy:
#   workspace_edit → hard block (OQ-8 resolved)
#   remote_handoff → hard block (OQ-4 resolved)
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

# Profiles explicitly excluded from browser work — any browser action on
# these profiles is a hard block (not a soft confirmation gate).
# OQ-4: remote_handoff; OQ-8: workspace_edit.
_HARD_BLOCK_PROFILES: dict[str, str] = {
    "workspace_edit": (
        "workspace_edit has no browser policy (OQ-8 resolved): browser work "
        "under workspace_edit is not a recognised workflow — reclassify to "
        "inspect_local, service_ops, or risky_edit"
    ),
    "remote_handoff": (
        "remote_handoff has no browser policy (OQ-4 resolved): Chrome profile "
        "identity and session cap cannot be determined for remote targets — "
        "reclassify or use a local profile for browser work"
    ),
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

# Default workspace root resolution order:
#   1. caller-supplied workspace_root kwarg
#   2. OPENCLAW_WORKSPACE env var
#   3. ~/.openclaw/workspace
_DEFAULT_WORKSPACE_FALLBACK = Path.home() / ".openclaw" / "workspace"


def _resolve_workspace_root(workspace_root: str | Path | None) -> Path:
    """Return the effective workspace root Path.

    Resolution order:
    1. ``workspace_root`` argument (if non-None).
    2. ``OPENCLAW_WORKSPACE`` environment variable.
    3. ``~/.openclaw/workspace`` as hard fallback.
    """
    if workspace_root is not None:
        return Path(workspace_root)
    env_val = os.environ.get("OPENCLAW_WORKSPACE")
    if env_val:
        return Path(env_val)
    return _DEFAULT_WORKSPACE_FALLBACK


def _resolve_site_note_path(
    host_or_url_pattern: str,
    workspace_root: str | Path | None = None,
) -> Path | None:
    """Return the fixed-path site note for *host_or_url_pattern* if it exists.

    Fixed path convention (OQ-5 resolved):
        <workspace_root>/skills/browser-site-notes/<host>.md

    Where ``<host>`` is extracted from the URL/pattern by stripping the
    scheme and any leading ``*./`` characters, then taking the hostname
    component.  Returns ``None`` when the file does not exist.

    Examples::

        _resolve_site_note_path("https://example.com/path")
        # → <workspace>/skills/browser-site-notes/example.com.md  (if exists)

        _resolve_site_note_path("https://*.example.com/*")
        # → <workspace>/skills/browser-site-notes/example.com.md

        _resolve_site_note_path("example.com")
        # → <workspace>/skills/browser-site-notes/example.com.md
    """
    if not host_or_url_pattern:
        return None

    # Attempt to parse as URL first; fall back to treating the whole string
    # as the host.
    try:
        parsed = urlparse(host_or_url_pattern)
        host = parsed.hostname or ""
    except Exception:  # noqa: BLE001
        host = ""

    if not host:
        # Pattern like "example.com/*" or bare "example.com"
        raw = host_or_url_pattern.lstrip("*./")
        # Take the first path-like segment before a slash
        host = raw.split("/")[0].split("*")[-1].lstrip(".")
        if not host:
            return None

    # Sanitise: strip trailing DNS dot (FQDN form "example.com.") and any
    # leading wildcard segments.  We keep the full hostname because site notes
    # are per-host, not per-TLD; "*.sub.example.com" → "sub.example.com".
    host = host.strip(".").lstrip("*")

    if not host:
        return None

    workspace = _resolve_workspace_root(workspace_root)
    note_path = workspace / "skills" / "browser-site-notes" / f"{host}.md"
    return note_path if note_path.exists() else None


def _malformed(reason: str) -> dict[str, Any]:
    """Build a safe-default decision for a request that cannot be reasoned about."""
    return {
        "allow_single_session": False,
        "allow_parallel": False,
        "require_user_login": False,
        "require_confirmation": True,
        "block_mutation": False,
        "pause_profile": False,
        "require_cleanup_evidence": False,
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
        return (
            False,
            "logged-in required but profile.allow_logged_in_profile=false "
            "(OQ-2 resolved: risky_edit + logged-in is a permanent block; "
            "use service_ops for logged-in browser operations)",
        )
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
        return "gated", (
            "profile.allow_mutation=gated (OQ-1 resolved: require_confirmation=True; "
            "satisfied by --approve-risk OR existing site note)"
        )
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


def verify(
    request: dict[str, Any],
    workspace_root: str | Path | None = None,
) -> dict[str, Any]:
    """Evaluate a browser task and return a BrowserReconDecision dict.

    Required keys in ``request``: ``url_pattern``, ``intended_action``,
    ``logged_in_account_required``, ``parallel_requested``,
    ``execution_profile``. Optional: ``existing_site_note_path``.

    Optional kwarg ``workspace_root`` overrides the workspace root used
    for site-note lookup (OQ-5).  Defaults to OPENCLAW_WORKSPACE env
    then ``~/.openclaw/workspace``.

    Returns a dict with the seven DECISION_KEYS flags + ``reason`` +
    ``checks``.  Defensive: malformed input returns safe-default decision
    rather than raising.
    """
    missing = _REQUIRED_REQUEST_KEYS - set(request.keys())
    if missing:
        return _malformed(f"malformed request: missing keys {sorted(missing)!r}")

    intended_action = request["intended_action"]
    profile_name = request["execution_profile"]
    logged_in_required = bool(request["logged_in_account_required"])
    parallel_requested = bool(request["parallel_requested"])
    existing_site_note_path: str | None = request.get("existing_site_note_path")
    url_pattern: str = request.get("url_pattern") or ""

    # Decision flags start at safe defaults; checks turn them on selectively.
    decision: dict[str, Any] = {
        "allow_single_session": False,
        "allow_parallel": False,
        "require_user_login": False,
        "require_confirmation": False,
        "block_mutation": False,
        "pause_profile": False,
        "require_cleanup_evidence": False,
    }
    reasons: list[str] = []
    checks: dict[str, Any] = {}

    # OQ-4 / OQ-8: Profiles with no browser policy are HARD BLOCKed (not soft
    # confirmation).  workspace_edit and remote_handoff fall here.
    if profile_name in _HARD_BLOCK_PROFILES:
        block_reason = _HARD_BLOCK_PROFILES[profile_name]
        reasons.append(block_reason)
        checks["profile_policy"] = "absent"
        checks["hard_block"] = True
        # allow_single_session stays False; require_confirmation stays False
        # (this is a hard block, not a "ask the user" gate).
        return _finalize(decision, reasons, checks)

    policy = _PROFILE_POLICIES.get(profile_name)

    # Unknown profile (not in hard-block list, not in policy table) → safe
    # default: no policy present, require_confirmation.
    if policy is None:
        reasons.append(
            f"profile {profile_name!r} has no browser policy"
        )
        decision["require_confirmation"] = True
        checks["profile_policy"] = "absent"
        return _finalize(decision, reasons, checks)

    checks["profile_policy"] = "present"

    # OQ-5: Auto-resolve site note path when caller did not supply one.
    if existing_site_note_path is None and url_pattern:
        resolved = _resolve_site_note_path(url_pattern, workspace_root=workspace_root)
        if resolved is not None:
            existing_site_note_path = str(resolved)
            checks["site_note_auto_resolved"] = True

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

    # OQ-7: risky_edit always requires cleanup evidence for any browser action.
    if policy.get("require_cleanup_evidence", False):
        decision["require_cleanup_evidence"] = True
        checks["require_cleanup_evidence"] = True

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
    decision: dict[str, Any],
    reasons: list[str],
    checks: dict[str, Any],
) -> dict[str, Any]:
    """Attach reason + checks to the decision and return it."""
    result: dict[str, Any] = dict(decision)
    result["reason"] = "; ".join(r for r in reasons if r) or "ok"
    result["checks"] = dict(checks)
    return result
