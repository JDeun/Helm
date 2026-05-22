"""Tests for scripts/browser_work_verifier.py."""
from __future__ import annotations

import pytest

from scripts.browser_work_verifier import DECISION_KEYS, verify


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


def test_decision_keys_exact():
    expected = {
        "allow_single_session",
        "allow_parallel",
        "require_user_login",
        "require_confirmation",
        "block_mutation",
        "pause_profile",
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


def test_inspect_local_read_single_session():
    d = verify(_req(intended_action="read"))
    assert d["allow_single_session"] is True
    assert d["allow_parallel"] is False
    assert d["block_mutation"] is False
    assert d["require_user_login"] is False


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


def test_service_ops_read_with_login_ok():
    d = verify(
        _req(execution_profile="service_ops", logged_in_account_required=True)
    )
    assert d["allow_single_session"] is True
    assert d["require_user_login"] is True
    assert d["block_mutation"] is False


def test_service_ops_submit_is_gated():
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


def test_risky_edit_submit_blocked_by_default():
    # risky_edit's allow_mutation is false in this stub (OQ-2 unresolved)
    d = verify(
        _req(execution_profile="risky_edit", intended_action="submit")
    )
    assert d["block_mutation"] is True
    assert d["allow_single_session"] is False


def test_risky_edit_read_single_session():
    d = verify(
        _req(execution_profile="risky_edit", intended_action="read")
    )
    assert d["allow_single_session"] is True


def test_workspace_edit_no_browser_policy_requires_confirmation():
    d = verify(_req(execution_profile="workspace_edit"))
    assert d["require_confirmation"] is True
    assert d["allow_single_session"] is False
    assert "no browser policy" in d["reason"]
    assert d["checks"]["profile_policy"] == "absent"


def test_remote_handoff_no_browser_policy():
    d = verify(_req(execution_profile="remote_handoff"))
    assert d["require_confirmation"] is True
    assert d["allow_single_session"] is False
    assert "OQ-4" in d["reason"]


def test_unknown_profile_treated_as_no_policy():
    d = verify(_req(execution_profile="nope_profile"))
    assert d["require_confirmation"] is True
    assert d["allow_single_session"] is False


def test_unknown_intended_action_requires_confirmation():
    d = verify(_req(intended_action="dance"))
    assert d["require_confirmation"] is True
    assert d["allow_single_session"] is False
    assert "unknown intended_action" in d["reason"]


def test_malformed_request_missing_key():
    d = verify({"url_pattern": "x"})
    assert d["require_confirmation"] is True
    assert d["allow_single_session"] is False
    assert "malformed request" in d["reason"]


def test_complete_request_does_not_trigger_malformed():
    d = verify(_req())
    assert "malformed" not in d["reason"]


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


def test_reason_string_is_non_empty_for_every_decision():
    for spec in [
        _req(),
        _req(intended_action="submit"),
        _req(execution_profile="workspace_edit"),
        _req(execution_profile="service_ops", intended_action="submit",
             logged_in_account_required=True),
    ]:
        d = verify(spec)
        assert d["reason"], f"empty reason for {spec}"
