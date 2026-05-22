"""Tests for scripts/browser_work_verifier.py (Wave 3b — OQ-1..8 resolved)."""
from __future__ import annotations

import pytest
from pathlib import Path

from scripts.browser_work_verifier import DECISION_KEYS, verify, _resolve_site_note_path


def _req(**overrides):
    base = {
        "url_pattern": "https://example.com/*",
        "intended_action": "read",
        "logged_in_account_required": False,
        "parallel_requested": False,
        "execution_profile": "inspect_local",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# DECISION_KEYS — now 7 keys (require_cleanup_evidence added in Wave 3b)
# ---------------------------------------------------------------------------

def test_decision_keys_exact():
    expected = {
        "allow_single_session",
        "allow_parallel",
        "require_user_login",
        "require_confirmation",
        "block_mutation",
        "pause_profile",
        "require_cleanup_evidence",   # OQ-7
    }
    assert DECISION_KEYS == expected


def test_verify_returns_required_shape():
    d = verify(_req())
    for k in DECISION_KEYS:
        assert k in d, f"missing decision key {k}"
    assert "reason" in d and isinstance(d["reason"], str) and d["reason"]
    assert "checks" in d and isinstance(d["checks"], dict)


def test_verify_decision_flags_are_bool():
    d = verify(_req())
    for k in DECISION_KEYS:
        assert isinstance(d[k], bool), f"{k} should be bool, got {type(d[k]).__name__}"


# ---------------------------------------------------------------------------
# inspect_local baseline
# ---------------------------------------------------------------------------

def test_inspect_local_read_single_session():
    d = verify(_req(intended_action="read"))
    assert d["allow_single_session"] is True
    assert d["allow_parallel"] is False
    assert d["block_mutation"] is False
    assert d["require_user_login"] is False
    assert d["require_cleanup_evidence"] is False


def test_inspect_local_read_parallel():
    d = verify(_req(intended_action="read", parallel_requested=True))
    assert d["allow_single_session"] is True
    assert d["allow_parallel"] is True


def test_inspect_local_crawl_batch_parallel():
    d = verify(_req(intended_action="crawl_batch", parallel_requested=True))
    assert d["allow_single_session"] is True
    assert d["allow_parallel"] is True


def test_inspect_local_fillform_blocked():
    d = verify(_req(intended_action="fillform"))
    assert d["block_mutation"] is True
    assert d["allow_single_session"] is False
    assert "allow_mutation=false" in d["reason"]


def test_inspect_local_submit_blocked():
    d = verify(_req(intended_action="submit"))
    assert d["block_mutation"] is True
    assert d["allow_single_session"] is False


def test_inspect_local_interact_blocked():
    d = verify(_req(intended_action="interact"))
    assert d["block_mutation"] is True


def test_logged_in_required_but_profile_denies():
    d = verify(_req(logged_in_account_required=True))
    # inspect_local has allow_logged_in_profile=false
    assert d["require_user_login"] is True
    assert d["allow_single_session"] is False
    assert "allow_logged_in_profile=false" in d["reason"]


# ---------------------------------------------------------------------------
# service_ops baseline
# ---------------------------------------------------------------------------

def test_service_ops_read_with_login_ok():
    d = verify(
        _req(execution_profile="service_ops", logged_in_account_required=True)
    )
    assert d["allow_single_session"] is True
    assert d["require_user_login"] is True
    assert d["block_mutation"] is False
    assert d["require_cleanup_evidence"] is False


def test_service_ops_submit_is_gated():
    """OQ-1: gated mutation → require_confirmation=True.

    Contract: verifier emits require_confirmation=True; runner enforces
    by requiring --approve-risk OR an existing site note (see
    run_with_profile._evaluate_browser_gate).
    """
    d = verify(
        _req(
            execution_profile="service_ops",
            intended_action="submit",
            logged_in_account_required=True,
        )
    )
    # gated → single-session OK + require_confirmation
    assert d["allow_single_session"] is True
    assert d["require_confirmation"] is True
    assert d["block_mutation"] is False
    assert "gated" in d["reason"]
    # OQ-1: reason string names both satisfaction paths
    assert "OQ-1" in d["reason"]


def test_service_ops_gated_site_note_present_still_emits_require_confirmation(tmp_path):
    """OQ-1: verifier always emits require_confirmation for gated; runner decides.

    Even when a site note is present the verifier still emits
    require_confirmation=True.  The runner is responsible for treating
    the site note as satisfying the gate.
    """
    note_dir = tmp_path / "skills" / "browser-site-notes"
    note_dir.mkdir(parents=True)
    note_file = note_dir / "example.com.md"
    note_file.write_text("# example.com site note\n")

    d = verify(
        _req(
            execution_profile="service_ops",
            intended_action="submit",
            logged_in_account_required=True,
            existing_site_note_path=str(note_file),
        ),
        workspace_root=str(tmp_path),
    )
    assert d["allow_single_session"] is True
    assert d["require_confirmation"] is True   # verifier always emits; runner enforces
    assert d["checks"]["existing_site_note"] == "present"


def test_service_ops_crawl_parallel_within_cap():
    d = verify(
        _req(
            execution_profile="service_ops",
            intended_action="crawl_batch",
            parallel_requested=True,
        )
    )
    # service_ops max_sessions=3 > 1, crawl_batch is read-only
    assert d["allow_single_session"] is True
    assert d["allow_parallel"] is True


# ---------------------------------------------------------------------------
# OQ-2: risky_edit + logged-in → permanent block (not escalation path)
# ---------------------------------------------------------------------------

def test_risky_edit_logged_in_permanent_block():
    """OQ-2: risky_edit + logged_in_required → allow_single_session=False.

    Reason must point to service_ops upgrade path.
    """
    d = verify(
        _req(
            execution_profile="risky_edit",
            intended_action="read",
            logged_in_account_required=True,
        )
    )
    assert d["allow_single_session"] is False
    assert d["require_user_login"] is True
    # Reason should mention service_ops upgrade path (OQ-2 resolution)
    assert "service_ops" in d["reason"]
    assert "allow_logged_in_profile=false" in d["reason"]


def test_risky_edit_submit_blocked_by_policy():
    # risky_edit's allow_mutation is false
    d = verify(
        _req(execution_profile="risky_edit", intended_action="submit")
    )
    assert d["block_mutation"] is True
    assert d["allow_single_session"] is False


# ---------------------------------------------------------------------------
# OQ-7: risky_edit + any browser action → require_cleanup_evidence=True
# ---------------------------------------------------------------------------

def test_risky_edit_read_has_cleanup_evidence_flag():
    """OQ-7: risky_edit + read → require_cleanup_evidence=True in decision."""
    d = verify(
        _req(execution_profile="risky_edit", intended_action="read")
    )
    assert d["allow_single_session"] is True
    assert d["require_cleanup_evidence"] is True
    assert d["checks"].get("require_cleanup_evidence") is True


def test_risky_edit_crawl_batch_has_cleanup_evidence_flag():
    """OQ-7: risky_edit + crawl_batch → require_cleanup_evidence=True."""
    d = verify(
        _req(execution_profile="risky_edit", intended_action="crawl_batch")
    )
    assert d["allow_single_session"] is True
    assert d["require_cleanup_evidence"] is True


def test_inspect_local_read_no_cleanup_evidence_flag():
    """inspect_local does not require cleanup evidence."""
    d = verify(_req(execution_profile="inspect_local", intended_action="read"))
    assert d["require_cleanup_evidence"] is False


def test_service_ops_read_no_cleanup_evidence_flag():
    """service_ops does not require cleanup evidence."""
    d = verify(_req(execution_profile="service_ops", intended_action="read"))
    assert d["require_cleanup_evidence"] is False


# ---------------------------------------------------------------------------
# OQ-4: remote_handoff + browser → hard block (allow_single_session=False)
# NOTE: previously was require_confirmation=True; now hard block.
# ---------------------------------------------------------------------------

def test_remote_handoff_hard_block():
    """OQ-4: remote_handoff + any browser action → hard block (not soft gate)."""
    d = verify(_req(execution_profile="remote_handoff"))
    # Hard block: allow_single_session=False AND require_confirmation=False
    assert d["allow_single_session"] is False
    assert d["require_confirmation"] is False   # CHANGED from Wave 3a: no longer soft gate
    assert d["checks"]["profile_policy"] == "absent"
    assert d["checks"].get("hard_block") is True
    # Reason must name OQ-4
    assert "OQ-4" in d["reason"]


def test_remote_handoff_hard_block_for_mutation():
    """OQ-4: remote_handoff + submit → hard block, not gated."""
    d = verify(_req(execution_profile="remote_handoff", intended_action="submit"))
    assert d["allow_single_session"] is False
    assert d["require_confirmation"] is False
    assert d["block_mutation"] is False   # hard block fires before mutation check


# ---------------------------------------------------------------------------
# OQ-8: workspace_edit + browser → hard block (allow_single_session=False)
# NOTE: previously was require_confirmation=True; now hard block.
# ---------------------------------------------------------------------------

def test_workspace_edit_hard_block():
    """OQ-8: workspace_edit + browser → hard block (not soft confirmation gate)."""
    d = verify(_req(execution_profile="workspace_edit"))
    assert d["allow_single_session"] is False
    assert d["require_confirmation"] is False   # CHANGED from Wave 3a: no longer soft gate
    assert d["checks"]["profile_policy"] == "absent"
    assert d["checks"].get("hard_block") is True
    assert "OQ-8" in d["reason"]


def test_workspace_edit_hard_block_for_read():
    """OQ-8: workspace_edit + read → hard block regardless of action type."""
    d = verify(_req(execution_profile="workspace_edit", intended_action="read"))
    assert d["allow_single_session"] is False
    assert d["require_confirmation"] is False


def test_workspace_edit_hard_block_for_submit():
    """OQ-8: workspace_edit + submit → hard block before mutation check."""
    d = verify(_req(execution_profile="workspace_edit", intended_action="submit"))
    assert d["allow_single_session"] is False
    assert d["require_confirmation"] is False
    assert d["block_mutation"] is False  # hard block fires before mutation check


# ---------------------------------------------------------------------------
# OQ-5: fixed-path site note lookup with workspace_root kwarg
# ---------------------------------------------------------------------------

def test_site_note_auto_resolved_when_absent_from_request(tmp_path: Path):
    """OQ-5: verifier auto-resolves site note from fixed path when not in request."""
    note_dir = tmp_path / "skills" / "browser-site-notes"
    note_dir.mkdir(parents=True)
    note_file = note_dir / "example.com.md"
    note_file.write_text("# example.com site note\n")

    d = verify(
        _req(
            url_pattern="https://example.com/page",
            intended_action="read",
        ),
        workspace_root=str(tmp_path),
    )
    assert d["allow_single_session"] is True
    assert d["checks"]["existing_site_note"] == "present"
    assert d["checks"].get("site_note_auto_resolved") is True


def test_site_note_not_resolved_when_file_missing(tmp_path: Path):
    """OQ-5: when site note file doesn't exist, absent is reported correctly."""
    d = verify(
        _req(url_pattern="https://example.com/page", intended_action="read"),
        workspace_root=str(tmp_path),
    )
    assert d["allow_single_session"] is True
    assert d["checks"]["existing_site_note"] == "absent"
    assert d["checks"].get("site_note_auto_resolved") is None


def test_site_note_caller_supplied_takes_precedence(tmp_path: Path):
    """OQ-5: caller-supplied existing_site_note_path is used as-is."""
    # Create a note in the fixed-path location too, to confirm caller takes
    # precedence without triggering auto-resolve.
    note_dir = tmp_path / "skills" / "browser-site-notes"
    note_dir.mkdir(parents=True)
    (note_dir / "example.com.md").write_text("fixed path note\n")

    caller_note = tmp_path / "my_notes" / "example_notes.md"
    caller_note.parent.mkdir(parents=True)
    caller_note.write_text("caller-supplied note\n")

    d = verify(
        _req(
            url_pattern="https://example.com/page",
            intended_action="read",
            existing_site_note_path=str(caller_note),
        ),
        workspace_root=str(tmp_path),
    )
    assert d["allow_single_session"] is True
    assert d["checks"]["existing_site_note"] == "present"
    # Auto-resolve should NOT have triggered since caller supplied the note
    assert d["checks"].get("site_note_auto_resolved") is None


def test_resolve_site_note_path_helper(tmp_path: Path):
    """Unit test for _resolve_site_note_path helper."""
    note_dir = tmp_path / "skills" / "browser-site-notes"
    note_dir.mkdir(parents=True)
    note_file = note_dir / "api.example.org.md"
    note_file.write_text("note content")

    result = _resolve_site_note_path("https://api.example.org/v1/", workspace_root=str(tmp_path))
    assert result == note_file

    # Non-existent host returns None
    result2 = _resolve_site_note_path("https://other.example.org/", workspace_root=str(tmp_path))
    assert result2 is None


def test_resolve_site_note_path_returns_none_for_empty():
    assert _resolve_site_note_path("") is None
    assert _resolve_site_note_path(None) is None  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# FIX H-4: host trailing dot strip
# ---------------------------------------------------------------------------

def test_resolve_site_note_path_trailing_dot_stripped(tmp_path: Path):
    """FQDN trailing dot (example.com.) must resolve to example.com site note."""
    note_dir = tmp_path / "skills" / "browser-site-notes"
    note_dir.mkdir(parents=True)
    note_file = note_dir / "example.com.md"
    note_file.write_text("note content")

    # Trailing dot in FQDN form
    result = _resolve_site_note_path("example.com.", workspace_root=str(tmp_path))
    assert result == note_file, "Trailing dot should be stripped to find site note"


def test_resolve_site_note_path_wildcard_resolved(tmp_path: Path):
    """Wildcard pattern *.example.com must resolve to example.com site note."""
    note_dir = tmp_path / "skills" / "browser-site-notes"
    note_dir.mkdir(parents=True)
    note_file = note_dir / "example.com.md"
    note_file.write_text("note content")

    result = _resolve_site_note_path("*.example.com", workspace_root=str(tmp_path))
    assert result == note_file, "*.example.com should resolve to example.com site note"


def test_resolve_site_note_path_fqdn_url_trailing_dot(tmp_path: Path):
    """URL with FQDN host 'https://example.com./path' should find site note."""
    note_dir = tmp_path / "skills" / "browser-site-notes"
    note_dir.mkdir(parents=True)
    note_file = note_dir / "example.com.md"
    note_file.write_text("note content")

    # urlparse returns hostname='example.com.' for "https://example.com./path"
    result = _resolve_site_note_path("https://example.com./path", workspace_root=str(tmp_path))
    assert result == note_file, "FQDN URL trailing dot should be stripped"


# ---------------------------------------------------------------------------
# Unknown profile (not in hard-block list, not in policy) — soft gate
# ---------------------------------------------------------------------------

def test_unknown_profile_treated_as_no_policy():
    d = verify(_req(execution_profile="nope_profile"))
    assert d["require_confirmation"] is True
    assert d["allow_single_session"] is False
    # Unknown profiles use soft gate (require_confirmation), not hard block
    assert d["checks"].get("hard_block") is None


# ---------------------------------------------------------------------------
# Unknown intended_action
# ---------------------------------------------------------------------------

def test_unknown_intended_action_requires_confirmation():
    d = verify(_req(intended_action="dance"))
    assert d["require_confirmation"] is True
    assert d["allow_single_session"] is False
    assert "unknown intended_action" in d["reason"]


# ---------------------------------------------------------------------------
# Malformed request
# ---------------------------------------------------------------------------

def test_malformed_request_missing_key():
    d = verify({"url_pattern": "x"})
    assert d["require_confirmation"] is True
    assert d["allow_single_session"] is False
    assert "malformed request" in d["reason"]


def test_complete_request_does_not_trigger_malformed():
    d = verify(_req())
    assert "malformed" not in d["reason"]


# ---------------------------------------------------------------------------
# Site-note presence is informational for inspect_local read
# ---------------------------------------------------------------------------

def test_existing_site_note_does_not_change_inspect_local_read():
    base = verify(_req(intended_action="read"))
    with_note = verify(
        _req(
            intended_action="read",
            existing_site_note_path="/some/notes/example.com.md",
        )
    )
    # Site note presence is informational; both should allow single session
    assert base["allow_single_session"] is True
    assert with_note["allow_single_session"] is True


# ---------------------------------------------------------------------------
# reason string is non-empty for every decision
# ---------------------------------------------------------------------------

def test_reason_string_is_non_empty_for_every_decision():
    for spec in [
        _req(),
        _req(intended_action="submit"),
        _req(execution_profile="workspace_edit"),
        _req(execution_profile="remote_handoff"),
        _req(execution_profile="service_ops", intended_action="submit",
             logged_in_account_required=True),
        _req(execution_profile="risky_edit", intended_action="read"),
    ]:
        d = verify(spec)
        assert d["reason"], f"empty reason for {spec}"
