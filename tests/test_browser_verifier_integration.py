# tests/test_browser_verifier_integration.py
"""Integration tests for the OPENCLAW_BROWSER_GATE feature flag wired in
scripts/run_with_profile.py (Wave 3a).

Test inventory:

  1. --browser-action absent: existing behavior unchanged; verify NOT called.
  2. --browser-action=read, gate OFF, profile=inspect_local: shadow mode —
     verifier called, decision logged with status="browser_recon_shadow",
     runner exits 0.
  3. --browser-action=read, gate ON, profile=inspect_local: allow_single_session=True
     → proceeds, ledger has browser_recon field.
  4. --browser-action=submit, gate ON, profile=inspect_local: block_mutation=True /
     allow_single_session=False → EXIT_BROWSER_BLOCKED (27), stderr message,
     ledger browser_blocked row.
  5a. --browser-action=submit, gate ON, profile=service_ops, logged-in, no --approve-risk
      → exit 24 + browser_requires_approval ledger row.
  5b. --browser-action=submit, gate ON, profile=service_ops, logged-in, --approve-risk
      → proceeds + browser_approved_with_risk ledger row.
  6. --browser-action=read without --browser-url-pattern: argparse choices still
     accepted (url_pattern defaults to ""), verifier returns require_confirmation
     in shadow mode (gate OFF) — shadow still proceeds exit 0.
  7. Truthy/falsy detection on OPENCLAW_BROWSER_GATE re-uses env_flag (no
     duplicate helper).
  8. Pause-gate and browser-gate are independent: paused profile + browser-action
     → pause gate wins (exits EXIT_PAUSED=26), verifier NOT called.
"""
from __future__ import annotations

import json
import subprocess as _sp
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_FAKE_PROFILES = {
    "inspect_local": {
        "description": "Read-only local inspection.",
        "backend": "local",
        "runtime_backend": "local-shell",
        "runtime_target_kind": "workspace",
        "isolation": "shared-session",
        "handoff_required": False,
        "writes_allowed": False,
        "network_allowed": False,
        "checkpoint": "never",
    },
    "service_ops": {
        "description": "Service operations.",
        "backend": "local",
        "runtime_backend": "local-shell",
        "runtime_target_kind": "workspace",
        "isolation": "shared-session",
        "handoff_required": False,
        "writes_allowed": True,
        "network_allowed": True,
        "checkpoint": "never",
    },
}

# A verifier decision for inspect_local + read: allow_single_session=True
_DECISION_READ_ALLOWED = {
    "allow_single_session": True,
    "allow_parallel": False,
    "require_user_login": False,
    "require_confirmation": False,
    "block_mutation": False,
    "pause_profile": False,
    "reason": "ok",
    "checks": {"profile_policy": "present", "action_class": "read"},
}

# A verifier decision that blocks mutation (inspect_local + submit)
_DECISION_SUBMIT_BLOCKED = {
    "allow_single_session": False,
    "allow_parallel": False,
    "require_user_login": False,
    "require_confirmation": False,
    "block_mutation": True,
    "pause_profile": False,
    "reason": "profile.allow_mutation=false",
    "checks": {"profile_policy": "present", "action_class": "mutation", "mutation_mode": "block"},
}

# A verifier decision for service_ops + submit (gated = require_confirmation)
_DECISION_SUBMIT_GATED = {
    "allow_single_session": True,
    "allow_parallel": False,
    "require_user_login": True,
    "require_confirmation": True,
    "block_mutation": False,
    "pause_profile": False,
    "reason": "profile.allow_mutation=gated (OQ-1: site-note-aware confirm)",
    "checks": {"profile_policy": "present", "action_class": "mutation", "mutation_mode": "gated"},
}


def _make_args(
    profile: str = "inspect_local",
    browser_action: str | None = None,
    browser_url_pattern: str | None = "https://example.com/*",
    browser_logged_in: bool = False,
    browser_parallel: bool = False,
    browser_site_note: str | None = None,
    approve_risk: bool = False,
):
    args = MagicMock()
    args.profile = profile
    args.guard_mode = "off"
    args.guard_json = False
    args.approve_risk = approve_risk
    args.command = ["echo", "hello"]
    args.runtime_target = None
    args.task_name = "test-browser-gate"
    args.task_goal = None
    args.checkpoint = None
    args.skill = None
    args.backend = None
    args.meta_json = None
    args.task_id = None
    args.label = None
    args.path = None
    args.runtime_note = None
    args.delivery_mode = "inline"
    args.timeout = 1800
    # Browser-gate flags
    args.browser_action = browser_action
    args.browser_url_pattern = browser_url_pattern
    args.browser_logged_in = browser_logged_in
    args.browser_parallel = browser_parallel
    args.browser_site_note = browser_site_note
    return args


def _write_pause_state(state_path: Path, profile: str, reason: str = "rate-limit exceeded") -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps({profile: {"paused_at": "2026-05-22T00:00:00+00:00", "reason": reason, "resume_token": "abcd1234"}}),
        encoding="utf-8",
    )


def _common_patches(*, ledger_calls: list[dict] | None = None, subprocess_rc: int = 0):
    """Return a context-manager-compatible patch stack for the common mocks."""
    def capture_ledger(entry: dict) -> None:
        if ledger_calls is not None:
            ledger_calls.append(dict(entry))

    return [
        patch("scripts.run_with_profile.load_profiles", return_value=_FAKE_PROFILES),
        patch("scripts.run_with_profile.validate_skill_profile"),
        patch(
            "scripts.run_with_profile.append_ledger",
            side_effect=(capture_ledger if ledger_calls is not None else None),
        ),
        patch("scripts.run_with_profile._best_effort_index"),
        patch("scripts.run_with_profile.run_checkpoint", return_value=None),
        patch("scripts.run_with_profile.evaluate_command_guard", return_value=None),
        patch("scripts.run_with_profile.finalize_task"),
        patch("scripts.run_with_profile.latest_snapshot_path", return_value=None),
        patch(
            "scripts.run_with_profile.subprocess.run",
            return_value=_sp.CompletedProcess(args=[], returncode=subprocess_rc),
        ),
    ]


# ---------------------------------------------------------------------------
# Test 1: --browser-action absent → verify NOT called
# ---------------------------------------------------------------------------

def test_no_browser_action_verify_not_called(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When --browser-action is absent, verify() must never be invoked."""
    monkeypatch.delenv("OPENCLAW_BROWSER_GATE", raising=False)

    with patch("scripts.run_with_profile.load_profiles", return_value=_FAKE_PROFILES), \
         patch("scripts.run_with_profile.validate_skill_profile"), \
         patch("scripts.run_with_profile.append_ledger"), \
         patch("scripts.run_with_profile._best_effort_index"), \
         patch("scripts.run_with_profile.run_checkpoint", return_value=None), \
         patch("scripts.run_with_profile.evaluate_command_guard", return_value=None), \
         patch("scripts.run_with_profile.finalize_task"), \
         patch("scripts.run_with_profile.latest_snapshot_path", return_value=None), \
         patch("scripts.run_with_profile.subprocess.run",
               return_value=_sp.CompletedProcess(args=[], returncode=0)), \
         patch("scripts.browser_work_verifier.verify") as mock_verify:

        from scripts.run_with_profile import cmd_run
        rc = cmd_run(_make_args(browser_action=None))

    assert mock_verify.call_count == 0, (
        f"verify() must NOT be called when --browser-action is absent; "
        f"call_count={mock_verify.call_count}"
    )
    assert rc == 0


# ---------------------------------------------------------------------------
# Test 2: gate OFF + --browser-action=read → shadow mode
# ---------------------------------------------------------------------------

def test_shadow_mode_gate_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Gate OFF: verifier called, decision logged as browser_recon_shadow, runner exits 0."""
    monkeypatch.delenv("OPENCLAW_BROWSER_GATE", raising=False)

    ledger_calls: list[dict] = []

    with patch("scripts.run_with_profile.load_profiles", return_value=_FAKE_PROFILES), \
         patch("scripts.run_with_profile.validate_skill_profile"), \
         patch("scripts.run_with_profile.append_ledger",
               side_effect=lambda e: ledger_calls.append(dict(e))), \
         patch("scripts.run_with_profile._best_effort_index"), \
         patch("scripts.run_with_profile.run_checkpoint", return_value=None), \
         patch("scripts.run_with_profile.evaluate_command_guard", return_value=None), \
         patch("scripts.run_with_profile.finalize_task",
               side_effect=lambda t: ledger_calls.append(dict(t))), \
         patch("scripts.run_with_profile.latest_snapshot_path", return_value=None), \
         patch("scripts.run_with_profile.subprocess.run",
               return_value=_sp.CompletedProcess(args=[], returncode=0)), \
         patch("scripts.browser_work_verifier.verify",
               return_value=_DECISION_READ_ALLOWED) as mock_verify:

        from scripts.run_with_profile import cmd_run
        rc = cmd_run(_make_args(browser_action="read", profile="inspect_local"))

    # Verifier was called
    assert mock_verify.call_count == 1, f"verify() should have been called once; got {mock_verify.call_count}"

    # Runner proceeds
    assert rc == 0, f"Expected exit 0 in shadow mode, got {rc}"

    # Ledger has a browser_recon_shadow entry
    shadow_entries = [e for e in ledger_calls if e.get("status") == "browser_recon_shadow"]
    assert shadow_entries, (
        f"Expected a browser_recon_shadow ledger entry; entries={[e.get('status') for e in ledger_calls]}"
    )
    se = shadow_entries[0]
    assert "browser_recon" in se, f"Expected browser_recon field in shadow entry; got {se}"


# ---------------------------------------------------------------------------
# Test 3: gate ON + --browser-action=read + allow_single_session=True → proceeds
# ---------------------------------------------------------------------------

def test_enforce_mode_read_allowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Gate ON, read action allowed: runner proceeds, ledger has browser_recon field."""
    monkeypatch.setenv("OPENCLAW_BROWSER_GATE", "1")

    ledger_calls: list[dict] = []

    with patch("scripts.run_with_profile.load_profiles", return_value=_FAKE_PROFILES), \
         patch("scripts.run_with_profile.validate_skill_profile"), \
         patch("scripts.run_with_profile.append_ledger",
               side_effect=lambda e: ledger_calls.append(dict(e))), \
         patch("scripts.run_with_profile._best_effort_index"), \
         patch("scripts.run_with_profile.run_checkpoint", return_value=None), \
         patch("scripts.run_with_profile.evaluate_command_guard", return_value=None), \
         patch("scripts.run_with_profile.finalize_task",
               side_effect=lambda t: ledger_calls.append(dict(t))), \
         patch("scripts.run_with_profile.latest_snapshot_path", return_value=None), \
         patch("scripts.run_with_profile.subprocess.run",
               return_value=_sp.CompletedProcess(args=[], returncode=0)), \
         patch("scripts.browser_work_verifier.verify",
               return_value=_DECISION_READ_ALLOWED):

        from scripts.run_with_profile import cmd_run
        rc = cmd_run(_make_args(browser_action="read", profile="inspect_local"))

    assert rc == 0, f"Expected exit 0 when read is allowed; got {rc}"

    # At least one ledger entry should have browser_recon set (the finalize_task call
    # passes the task dict which has browser_recon embedded by _evaluate_browser_gate)
    recon_entries = [e for e in ledger_calls if "browser_recon" in e]
    assert recon_entries, (
        f"Expected at least one ledger entry with browser_recon field; "
        f"entries={[list(e.keys()) for e in ledger_calls]}"
    )


# ---------------------------------------------------------------------------
# Test 4: gate ON + --browser-action=submit + inspect_local → EXIT_BROWSER_BLOCKED
# ---------------------------------------------------------------------------

def test_enforce_mode_submit_blocked(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
) -> None:
    """Gate ON, submit on inspect_local: block_mutation=True → EXIT_BROWSER_BLOCKED (27)."""
    monkeypatch.setenv("OPENCLAW_BROWSER_GATE", "1")

    ledger_calls: list[dict] = []

    with patch("scripts.run_with_profile.load_profiles", return_value=_FAKE_PROFILES), \
         patch("scripts.run_with_profile.validate_skill_profile"), \
         patch("scripts.run_with_profile.append_ledger",
               side_effect=lambda e: ledger_calls.append(dict(e))), \
         patch("scripts.run_with_profile._best_effort_index"), \
         patch("scripts.run_with_profile.run_checkpoint", return_value=None), \
         patch("scripts.run_with_profile.subprocess.run") as mock_subprocess, \
         patch("scripts.browser_work_verifier.verify",
               return_value=_DECISION_SUBMIT_BLOCKED):

        from scripts.run_with_profile import cmd_run, EXIT_BROWSER_BLOCKED
        rc = cmd_run(_make_args(browser_action="submit", profile="inspect_local"))

    assert rc == EXIT_BROWSER_BLOCKED == 27, (
        f"Expected EXIT_BROWSER_BLOCKED (27); got {rc}"
    )

    # subprocess.run must NOT have been called
    mock_subprocess.assert_not_called()

    # stderr message
    captured = capsys.readouterr()
    assert "BROWSER GATE BLOCKED" in captured.err, (
        f"Expected 'BROWSER GATE BLOCKED' in stderr; got: {captured.err!r}"
    )

    # Ledger has browser_blocked entry
    blocked_entries = [e for e in ledger_calls if e.get("status") == "browser_blocked"]
    assert blocked_entries, (
        f"Expected browser_blocked ledger entry; statuses={[e.get('status') for e in ledger_calls]}"
    )
    be = blocked_entries[0]
    assert "browser_recon" in be, f"Expected browser_recon in browser_blocked entry; got {be}"


# ---------------------------------------------------------------------------
# Test 5a: service_ops + submit + logged-in, gate ON, NO --approve-risk → exit 24
# ---------------------------------------------------------------------------

def test_enforce_mode_submit_gated_no_approval(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
) -> None:
    """Gate ON, gated mutation, no --approve-risk → EXIT_GUARD_REQUIRE_APPROVAL (24)."""
    monkeypatch.setenv("OPENCLAW_BROWSER_GATE", "1")

    ledger_calls: list[dict] = []

    with patch("scripts.run_with_profile.load_profiles", return_value=_FAKE_PROFILES), \
         patch("scripts.run_with_profile.validate_skill_profile"), \
         patch("scripts.run_with_profile.append_ledger",
               side_effect=lambda e: ledger_calls.append(dict(e))), \
         patch("scripts.run_with_profile._best_effort_index"), \
         patch("scripts.run_with_profile.run_checkpoint", return_value=None), \
         patch("scripts.run_with_profile.subprocess.run") as mock_subprocess, \
         patch("scripts.browser_work_verifier.verify",
               return_value=_DECISION_SUBMIT_GATED):

        from scripts.run_with_profile import cmd_run, EXIT_GUARD_REQUIRE_APPROVAL
        rc = cmd_run(
            _make_args(
                browser_action="submit",
                profile="service_ops",
                browser_logged_in=True,
                approve_risk=False,
            )
        )

    assert rc == EXIT_GUARD_REQUIRE_APPROVAL == 24, (
        f"Expected EXIT_GUARD_REQUIRE_APPROVAL (24); got {rc}"
    )
    mock_subprocess.assert_not_called()

    captured = capsys.readouterr()
    assert "APPROVAL" in captured.err.upper(), (
        f"Expected approval message in stderr; got: {captured.err!r}"
    )

    requires_entries = [e for e in ledger_calls if e.get("status") == "browser_requires_approval"]
    assert requires_entries, (
        f"Expected browser_requires_approval ledger entry; "
        f"statuses={[e.get('status') for e in ledger_calls]}"
    )
    re = requires_entries[0]
    assert "browser_recon" in re, f"Expected browser_recon in requires_approval entry; got {re}"


# ---------------------------------------------------------------------------
# Test 5b: service_ops + submit + logged-in, gate ON, WITH --approve-risk → proceeds
# ---------------------------------------------------------------------------

def test_enforce_mode_submit_gated_with_approval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Gate ON, gated mutation, --approve-risk passed → proceeds, browser_approved_with_risk logged."""
    monkeypatch.setenv("OPENCLAW_BROWSER_GATE", "1")

    ledger_calls: list[dict] = []

    with patch("scripts.run_with_profile.load_profiles", return_value=_FAKE_PROFILES), \
         patch("scripts.run_with_profile.validate_skill_profile"), \
         patch("scripts.run_with_profile.append_ledger",
               side_effect=lambda e: ledger_calls.append(dict(e))), \
         patch("scripts.run_with_profile._best_effort_index"), \
         patch("scripts.run_with_profile.run_checkpoint", return_value=None), \
         patch("scripts.run_with_profile.evaluate_command_guard", return_value=None), \
         patch("scripts.run_with_profile.finalize_task",
               side_effect=lambda t: ledger_calls.append(dict(t))), \
         patch("scripts.run_with_profile.latest_snapshot_path", return_value=None), \
         patch("scripts.run_with_profile.subprocess.run",
               return_value=_sp.CompletedProcess(args=[], returncode=0)), \
         patch("scripts.browser_work_verifier.verify",
               return_value=_DECISION_SUBMIT_GATED):

        from scripts.run_with_profile import cmd_run
        rc = cmd_run(
            _make_args(
                browser_action="submit",
                profile="service_ops",
                browser_logged_in=True,
                approve_risk=True,
            )
        )

    assert rc == 0, f"Expected exit 0 when --approve-risk passed; got {rc}"

    approved_entries = [e for e in ledger_calls if e.get("status") == "browser_approved_with_risk"]
    assert approved_entries, (
        f"Expected browser_approved_with_risk ledger entry; "
        f"statuses={[e.get('status') for e in ledger_calls]}"
    )
    ae = approved_entries[0]
    assert "browser_recon" in ae, f"Expected browser_recon in approved entry; got {ae}"


# ---------------------------------------------------------------------------
# Test 6: --browser-action=read without --browser-url-pattern → shadow mode, exit 0
# ---------------------------------------------------------------------------

def test_browser_action_without_url_pattern_shadow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Gate OFF: missing url_pattern → verifier gets empty string, shadow proceeds exit 0."""
    monkeypatch.delenv("OPENCLAW_BROWSER_GATE", raising=False)

    ledger_calls: list[dict] = []

    with patch("scripts.run_with_profile.load_profiles", return_value=_FAKE_PROFILES), \
         patch("scripts.run_with_profile.validate_skill_profile"), \
         patch("scripts.run_with_profile.append_ledger",
               side_effect=lambda e: ledger_calls.append(dict(e))), \
         patch("scripts.run_with_profile._best_effort_index"), \
         patch("scripts.run_with_profile.run_checkpoint", return_value=None), \
         patch("scripts.run_with_profile.evaluate_command_guard", return_value=None), \
         patch("scripts.run_with_profile.finalize_task",
               side_effect=lambda t: ledger_calls.append(dict(t))), \
         patch("scripts.run_with_profile.latest_snapshot_path", return_value=None), \
         patch("scripts.run_with_profile.subprocess.run",
               return_value=_sp.CompletedProcess(args=[], returncode=0)):

        from scripts.run_with_profile import cmd_run
        # url_pattern=None → falls back to "" inside _evaluate_browser_gate
        rc = cmd_run(_make_args(browser_action="read", browser_url_pattern=None))

    # Shadow mode always proceeds
    assert rc == 0, f"Expected exit 0 in shadow mode even without url_pattern; got {rc}"

    shadow_entries = [e for e in ledger_calls if e.get("status") == "browser_recon_shadow"]
    assert shadow_entries, (
        f"Expected browser_recon_shadow entry even without url_pattern; "
        f"statuses={[e.get('status') for e in ledger_calls]}"
    )


# ---------------------------------------------------------------------------
# Test 7: Truthy/falsy detection on OPENCLAW_BROWSER_GATE uses env_flag
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("env_value,expected_enabled", [
    ("1", True),
    ("true", True),
    ("yes", True),
    ("YES", True),
    ("True", True),
    ("", False),
    ("0", False),
    ("false", False),
    ("no", False),
])
def test_browser_gate_enabled_detection(
    monkeypatch: pytest.MonkeyPatch,
    env_value: str,
    expected_enabled: bool,
) -> None:
    """_browser_gate_enabled must match env_flag semantics exactly."""
    monkeypatch.setenv("OPENCLAW_BROWSER_GATE", env_value)
    from scripts.run_with_profile import _browser_gate_enabled
    result = _browser_gate_enabled()
    assert result == expected_enabled, (
        f"For OPENCLAW_BROWSER_GATE={env_value!r}: expected {expected_enabled}, got {result}"
    )


def test_browser_gate_disabled_when_env_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """_browser_gate_enabled must return False when OPENCLAW_BROWSER_GATE is unset."""
    monkeypatch.delenv("OPENCLAW_BROWSER_GATE", raising=False)
    from scripts.run_with_profile import _browser_gate_enabled
    assert _browser_gate_enabled() is False


def test_browser_gate_uses_env_flag_not_duplicate_helper() -> None:
    """_browser_gate_enabled delegates to env_flag — the module-level import is shared."""
    import scripts.run_with_profile as rwp
    import scripts.env_flags as ef
    # Both should map the same values the same way (spot-check)
    import os
    old = os.environ.get("OPENCLAW_BROWSER_GATE")
    try:
        os.environ["OPENCLAW_BROWSER_GATE"] = "yes"
        assert rwp._browser_gate_enabled() == ef.env_flag("OPENCLAW_BROWSER_GATE")
        os.environ["OPENCLAW_BROWSER_GATE"] = "0"
        assert rwp._browser_gate_enabled() == ef.env_flag("OPENCLAW_BROWSER_GATE")
    finally:
        if old is None:
            os.environ.pop("OPENCLAW_BROWSER_GATE", None)
        else:
            os.environ["OPENCLAW_BROWSER_GATE"] = old


# ---------------------------------------------------------------------------
# Test 8: Pause gate wins over browser gate
# ---------------------------------------------------------------------------

def test_pause_gate_wins_before_browser_gate(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture,
) -> None:
    """Paused profile + --browser-action → EXIT_PAUSED (26); verifier NOT called."""
    monkeypatch.setenv("OPENCLAW_PAUSE_GATE", "1")
    monkeypatch.setenv("OPENCLAW_BROWSER_GATE", "1")

    pause_state = tmp_path / "pause-state.json"
    _write_pause_state(pause_state, "inspect_local", reason="over-rate-limit")
    monkeypatch.setenv("OPENCLAW_PAUSE_STATE", str(pause_state))

    ledger_calls: list[dict] = []

    with patch("scripts.run_with_profile.load_profiles", return_value=_FAKE_PROFILES), \
         patch("scripts.run_with_profile.validate_skill_profile"), \
         patch("scripts.run_with_profile.append_ledger",
               side_effect=lambda e: ledger_calls.append(dict(e))), \
         patch("scripts.run_with_profile._best_effort_index"), \
         patch("scripts.run_with_profile.run_checkpoint", return_value=None), \
         patch("scripts.run_with_profile.subprocess.run") as mock_subprocess, \
         patch("scripts.browser_work_verifier.verify") as mock_verify:

        from scripts.run_with_profile import cmd_run, EXIT_PAUSED
        rc = cmd_run(_make_args(browser_action="read", profile="inspect_local"))

    assert rc == EXIT_PAUSED == 26, f"Expected EXIT_PAUSED (26); got {rc}"

    # verify() must NOT have been called — pause gate fired first
    assert mock_verify.call_count == 0, (
        f"verify() must NOT be called when pause gate fires first; "
        f"call_count={mock_verify.call_count}"
    )

    mock_subprocess.assert_not_called()

    # Stderr should mention the pause
    captured = capsys.readouterr()
    assert "paused" in captured.err.lower(), f"Expected 'paused' in stderr; got: {captured.err!r}"

    # Ledger has blocked_by_pause, NOT browser_recon_shadow
    blocked = [e for e in ledger_calls if e.get("status") == "blocked_by_pause"]
    assert blocked, f"Expected blocked_by_pause ledger entry; statuses={[e.get('status') for e in ledger_calls]}"
    shadow = [e for e in ledger_calls if e.get("status") == "browser_recon_shadow"]
    assert not shadow, f"No browser_recon_shadow entry expected when pause wins; found: {shadow}"


# ---------------------------------------------------------------------------
# Wave 3b new tests
# ---------------------------------------------------------------------------

# A gated decision WITH a site note present in checks (simulates OQ-5 auto-resolve).
_DECISION_SUBMIT_GATED_WITH_SITE_NOTE = {
    "allow_single_session": True,
    "allow_parallel": False,
    "require_user_login": True,
    "require_confirmation": True,
    "block_mutation": False,
    "pause_profile": False,
    "require_cleanup_evidence": False,
    "reason": "profile.allow_mutation=gated (OQ-1 resolved: require_confirmation=True; satisfied by --approve-risk OR existing site note)",
    "checks": {
        "profile_policy": "present",
        "action_class": "mutation",
        "mutation_mode": "gated",
        "existing_site_note": "present",
    },
}

# A risky_edit read decision (require_cleanup_evidence=True).
_DECISION_RISKY_EDIT_READ = {
    "allow_single_session": True,
    "allow_parallel": False,
    "require_user_login": False,
    "require_confirmation": False,
    "block_mutation": False,
    "pause_profile": False,
    "require_cleanup_evidence": True,
    "reason": "ok",
    "checks": {
        "profile_policy": "present",
        "action_class": "read",
        "require_cleanup_evidence": True,
        "existing_site_note": "absent",
    },
}


# ---------------------------------------------------------------------------
# OQ-1: site-note satisfies gated confirmation gate (no --approve-risk needed)
# ---------------------------------------------------------------------------

def test_oq1_site_note_satisfies_gated_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OQ-1: service_ops submit with site note present → proceeds without --approve-risk.

    Verifier emits require_confirmation=True AND existing_site_note=present.
    Runner must treat this as satisfied and log browser_approved_by_site_note.
    """
    monkeypatch.setenv("OPENCLAW_BROWSER_GATE", "1")

    ledger_calls: list[dict] = []

    with patch("scripts.run_with_profile.load_profiles", return_value=_FAKE_PROFILES), \
         patch("scripts.run_with_profile.validate_skill_profile"), \
         patch("scripts.run_with_profile.append_ledger",
               side_effect=lambda e: ledger_calls.append(dict(e))), \
         patch("scripts.run_with_profile._best_effort_index"), \
         patch("scripts.run_with_profile.run_checkpoint", return_value=None), \
         patch("scripts.run_with_profile.evaluate_command_guard", return_value=None), \
         patch("scripts.run_with_profile.finalize_task",
               side_effect=lambda t: ledger_calls.append(dict(t))), \
         patch("scripts.run_with_profile.latest_snapshot_path", return_value=None), \
         patch("scripts.run_with_profile.subprocess.run",
               return_value=_sp.CompletedProcess(args=[], returncode=0)), \
         patch("scripts.browser_work_verifier.verify",
               return_value=_DECISION_SUBMIT_GATED_WITH_SITE_NOTE):

        from scripts.run_with_profile import cmd_run
        rc = cmd_run(
            _make_args(
                browser_action="submit",
                profile="service_ops",
                browser_logged_in=True,
                approve_risk=False,  # NOT passing --approve-risk
                browser_site_note="/some/note.md",  # site note present
            )
        )

    assert rc == 0, f"Expected exit 0 when site note satisfies gate; got {rc}"

    approved_entries = [e for e in ledger_calls if e.get("status") == "browser_approved_by_site_note"]
    assert approved_entries, (
        f"Expected browser_approved_by_site_note ledger entry; "
        f"statuses={[e.get('status') for e in ledger_calls]}"
    )


def test_oq1_neither_approval_nor_site_note_blocks(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
) -> None:
    """OQ-1: gated gate with no site note and no --approve-risk → exit 24."""
    monkeypatch.setenv("OPENCLAW_BROWSER_GATE", "1")

    ledger_calls: list[dict] = []

    # Use the gated decision WITHOUT site note
    with patch("scripts.run_with_profile.load_profiles", return_value=_FAKE_PROFILES), \
         patch("scripts.run_with_profile.validate_skill_profile"), \
         patch("scripts.run_with_profile.append_ledger",
               side_effect=lambda e: ledger_calls.append(dict(e))), \
         patch("scripts.run_with_profile._best_effort_index"), \
         patch("scripts.run_with_profile.run_checkpoint", return_value=None), \
         patch("scripts.run_with_profile.subprocess.run") as mock_subprocess, \
         patch("scripts.browser_work_verifier.verify",
               return_value=_DECISION_SUBMIT_GATED):

        from scripts.run_with_profile import cmd_run, EXIT_GUARD_REQUIRE_APPROVAL
        rc = cmd_run(
            _make_args(
                browser_action="submit",
                profile="service_ops",
                browser_logged_in=True,
                approve_risk=False,
                browser_site_note=None,
            )
        )

    assert rc == EXIT_GUARD_REQUIRE_APPROVAL == 24, (
        f"Expected EXIT_GUARD_REQUIRE_APPROVAL (24); got {rc}"
    )
    mock_subprocess.assert_not_called()

    requires_entries = [e for e in ledger_calls if e.get("status") == "browser_requires_approval"]
    assert requires_entries, (
        f"Expected browser_requires_approval ledger entry; "
        f"statuses={[e.get('status') for e in ledger_calls]}"
    )


# ---------------------------------------------------------------------------
# OQ-3: max_sessions counter blocks 6th inspect_local browser session
# ---------------------------------------------------------------------------

def test_oq3_max_sessions_blocks_when_at_cap(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture,
) -> None:
    """OQ-3: runner blocks when active session count >= max_sessions.

    Populate a synthetic ledger with 5 open inspect_local browser sessions
    (max_sessions=5 for inspect_local).  The 6th attempt → EXIT_BROWSER_BLOCKED.
    """
    monkeypatch.setenv("OPENCLAW_BROWSER_GATE", "1")

    import datetime as _dt
    import uuid as _uuid

    # Build a synthetic ledger with 5 open sessions within the 10-minute window.
    ledger_file = tmp_path / "state" / "task-ledger.jsonl"
    ledger_file.parent.mkdir(parents=True)

    now_dt = _dt.datetime.now(_dt.timezone.utc)

    open_entries = []
    for i in range(5):
        entry = {
            "task_id": str(_uuid.uuid4()),
            "profile": "inspect_local",
            "status": "running",
            "started_at": (now_dt - _dt.timedelta(minutes=2 + i)).isoformat(),
            "browser_recon": {"allow_single_session": True, "require_cleanup_evidence": False},
        }
        open_entries.append(json.dumps(entry))

    ledger_file.write_text("\n".join(open_entries) + "\n", encoding="utf-8")

    ledger_calls: list[dict] = []

    with patch("scripts.run_with_profile.TASK_LEDGER", ledger_file), \
         patch("scripts.run_with_profile.load_profiles", return_value=_FAKE_PROFILES), \
         patch("scripts.run_with_profile.validate_skill_profile"), \
         patch("scripts.run_with_profile.append_ledger",
               side_effect=lambda e: ledger_calls.append(dict(e))), \
         patch("scripts.run_with_profile._best_effort_index"), \
         patch("scripts.run_with_profile.run_checkpoint", return_value=None), \
         patch("scripts.run_with_profile.subprocess.run") as mock_subprocess, \
         patch("scripts.browser_work_verifier.verify",
               return_value=_DECISION_READ_ALLOWED):

        from scripts.run_with_profile import cmd_run, EXIT_BROWSER_BLOCKED
        rc = cmd_run(_make_args(browser_action="read", profile="inspect_local"))

    assert rc == EXIT_BROWSER_BLOCKED == 27, (
        f"Expected EXIT_BROWSER_BLOCKED (27) when max_sessions reached; got {rc}"
    )
    mock_subprocess.assert_not_called()

    captured = capsys.readouterr()
    assert "max_sessions" in captured.err.lower(), (
        f"Expected 'max_sessions' in stderr; got: {captured.err!r}"
    )

    blocked_entries = [e for e in ledger_calls if e.get("status") == "browser_blocked"]
    assert blocked_entries, (
        f"Expected browser_blocked ledger entry; statuses={[e.get('status') for e in ledger_calls]}"
    )
    assert blocked_entries[0].get("browser_block_reason") == "max_sessions_reached"


def test_oq3_max_sessions_allows_when_under_cap(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """OQ-3: runner proceeds when active session count < max_sessions."""
    monkeypatch.setenv("OPENCLAW_BROWSER_GATE", "1")

    import datetime as _dt
    import uuid as _uuid

    # Only 2 open sessions — under cap of 5 for inspect_local.
    ledger_file = tmp_path / "state" / "task-ledger.jsonl"
    ledger_file.parent.mkdir(parents=True)

    now_dt = _dt.datetime.now(_dt.timezone.utc)

    open_entries = []
    for i in range(2):
        entry = {
            "task_id": str(_uuid.uuid4()),
            "profile": "inspect_local",
            "status": "running",
            "started_at": (now_dt - _dt.timedelta(minutes=2 + i)).isoformat(),
            "browser_recon": {"allow_single_session": True, "require_cleanup_evidence": False},
        }
        open_entries.append(json.dumps(entry))

    ledger_file.write_text("\n".join(open_entries) + "\n", encoding="utf-8")

    ledger_calls: list[dict] = []

    with patch("scripts.run_with_profile.TASK_LEDGER", ledger_file), \
         patch("scripts.run_with_profile.load_profiles", return_value=_FAKE_PROFILES), \
         patch("scripts.run_with_profile.validate_skill_profile"), \
         patch("scripts.run_with_profile.append_ledger",
               side_effect=lambda e: ledger_calls.append(dict(e))), \
         patch("scripts.run_with_profile._best_effort_index"), \
         patch("scripts.run_with_profile.run_checkpoint", return_value=None), \
         patch("scripts.run_with_profile.evaluate_command_guard", return_value=None), \
         patch("scripts.run_with_profile.finalize_task",
               side_effect=lambda t: ledger_calls.append(dict(t))), \
         patch("scripts.run_with_profile.latest_snapshot_path", return_value=None), \
         patch("scripts.run_with_profile.subprocess.run",
               return_value=_sp.CompletedProcess(args=[], returncode=0)), \
         patch("scripts.browser_work_verifier.verify",
               return_value=_DECISION_READ_ALLOWED):

        from scripts.run_with_profile import cmd_run
        rc = cmd_run(_make_args(browser_action="read", profile="inspect_local"))

    assert rc == 0, f"Expected exit 0 when under max_sessions cap; got {rc}"


def test_oq3_max_sessions_ignores_sessions_with_cleanup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """OQ-3: sessions that have cleanup_status don't count toward the cap."""
    monkeypatch.setenv("OPENCLAW_BROWSER_GATE", "1")

    import datetime as _dt
    import uuid as _uuid

    ledger_file = tmp_path / "state" / "task-ledger.jsonl"
    ledger_file.parent.mkdir(parents=True)

    now_dt = _dt.datetime.now(_dt.timezone.utc)

    # 5 sessions, but all with cleanup_status = "confirmed"
    lines = []
    for i in range(5):
        tid = str(_uuid.uuid4())
        open_entry = {
            "task_id": tid,
            "profile": "inspect_local",
            "status": "running",
            "started_at": (now_dt - _dt.timedelta(minutes=2 + i)).isoformat(),
            "browser_recon": {"allow_single_session": True, "require_cleanup_evidence": False},
        }
        cleanup_entry = {
            "task_id": tid,
            "profile": "inspect_local",
            "status": "completed",
            "cleanup_status": "confirmed",
            "started_at": (now_dt - _dt.timedelta(minutes=1 + i)).isoformat(),
        }
        lines.append(json.dumps(open_entry))
        lines.append(json.dumps(cleanup_entry))

    ledger_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    ledger_calls: list[dict] = []

    with patch("scripts.run_with_profile.TASK_LEDGER", ledger_file), \
         patch("scripts.run_with_profile.load_profiles", return_value=_FAKE_PROFILES), \
         patch("scripts.run_with_profile.validate_skill_profile"), \
         patch("scripts.run_with_profile.append_ledger",
               side_effect=lambda e: ledger_calls.append(dict(e))), \
         patch("scripts.run_with_profile._best_effort_index"), \
         patch("scripts.run_with_profile.run_checkpoint", return_value=None), \
         patch("scripts.run_with_profile.evaluate_command_guard", return_value=None), \
         patch("scripts.run_with_profile.finalize_task",
               side_effect=lambda t: ledger_calls.append(dict(t))), \
         patch("scripts.run_with_profile.latest_snapshot_path", return_value=None), \
         patch("scripts.run_with_profile.subprocess.run",
               return_value=_sp.CompletedProcess(args=[], returncode=0)), \
         patch("scripts.browser_work_verifier.verify",
               return_value=_DECISION_READ_ALLOWED):

        from scripts.run_with_profile import cmd_run
        rc = cmd_run(_make_args(browser_action="read", profile="inspect_local"))

    assert rc == 0, (
        f"Expected exit 0 when all sessions have cleanup_status; got {rc}"
    )


# ---------------------------------------------------------------------------
# OQ-7: finalization gate — task with require_cleanup_evidence and no cleanup_status
# ---------------------------------------------------------------------------

def test_oq7_finalization_gate_blocks_when_cleanup_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture,
) -> None:
    """OQ-7: task with prior browser_recon.require_cleanup_evidence=True and no
    cleanup_status → finalization refuses with EXIT_CLEANUP_REQUIRED (28).
    """
    monkeypatch.setenv("OPENCLAW_BROWSER_GATE", "1")

    import uuid as _uuid
    import datetime as _dt

    task_id = str(_uuid.uuid4())

    # Pre-populate ledger with a browser_recon entry for this task that
    # had require_cleanup_evidence=True but no cleanup_status.
    ledger_file = tmp_path / "state" / "task-ledger.jsonl"
    ledger_file.parent.mkdir(parents=True)
    prior_entry = {
        "task_id": task_id,
        "profile": "risky_edit",
        "status": "browser_recon",
        "started_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "browser_recon": {
            "allow_single_session": True,
            "require_cleanup_evidence": True,
        },
    }
    ledger_file.write_text(json.dumps(prior_entry) + "\n", encoding="utf-8")

    ledger_calls: list[dict] = []

    # Use a fake risky_edit profile that allows writes.
    fake_profiles_with_risky = {
        **_FAKE_PROFILES,
        "risky_edit": {
            "description": "Risky editing profile.",
            "backend": "local",
            "runtime_backend": "local-shell",
            "runtime_target_kind": "workspace",
            "isolation": "shared-session",
            "handoff_required": False,
            "writes_allowed": True,
            "network_allowed": True,
            "checkpoint": "never",
        },
    }

    with patch("scripts.run_with_profile.TASK_LEDGER", ledger_file), \
         patch("scripts.run_with_profile.load_profiles", return_value=fake_profiles_with_risky), \
         patch("scripts.run_with_profile.validate_skill_profile"), \
         patch("scripts.run_with_profile.append_ledger",
               side_effect=lambda e: ledger_calls.append(dict(e))), \
         patch("scripts.run_with_profile._best_effort_index"), \
         patch("scripts.run_with_profile.run_checkpoint", return_value=None), \
         patch("scripts.run_with_profile.evaluate_command_guard", return_value=None), \
         patch("scripts.run_with_profile.latest_snapshot_path", return_value=None), \
         patch("scripts.run_with_profile.subprocess.run",
               return_value=_sp.CompletedProcess(args=[], returncode=0)), \
         patch("scripts.browser_work_verifier.verify",
               return_value=_DECISION_RISKY_EDIT_READ):

        from scripts.run_with_profile import cmd_run, EXIT_CLEANUP_REQUIRED

        # Override the task_id so that _check_cleanup_required_satisfied
        # finds the pre-populated ledger entry.
        args = _make_args(browser_action="read", profile="risky_edit")
        args.task_id = task_id

        rc = cmd_run(args)

    assert rc == EXIT_CLEANUP_REQUIRED == 28, (
        f"Expected EXIT_CLEANUP_REQUIRED (28); got {rc}"
    )

    captured = capsys.readouterr()
    assert "cleanup" in captured.err.lower(), (
        f"Expected 'cleanup' in stderr; got: {captured.err!r}"
    )

    cleanup_blocked = [e for e in ledger_calls if e.get("status") == "browser_cleanup_required"]
    assert cleanup_blocked, (
        f"Expected browser_cleanup_required ledger entry; "
        f"statuses={[e.get('status') for e in ledger_calls]}"
    )


def test_oq7_finalization_gate_passes_when_cleanup_recorded(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """OQ-7: task with require_cleanup_evidence=True AND cleanup_status recorded → proceeds."""
    monkeypatch.setenv("OPENCLAW_BROWSER_GATE", "1")

    import uuid as _uuid
    import datetime as _dt

    task_id = str(_uuid.uuid4())

    # Pre-populate ledger with both a browser_recon entry AND a cleanup entry.
    ledger_file = tmp_path / "state" / "task-ledger.jsonl"
    ledger_file.parent.mkdir(parents=True)
    now = _dt.datetime.now(_dt.timezone.utc).isoformat()
    prior_entry = {
        "task_id": task_id,
        "profile": "risky_edit",
        "status": "browser_recon",
        "started_at": now,
        "browser_recon": {
            "allow_single_session": True,
            "require_cleanup_evidence": True,
        },
    }
    cleanup_entry = {
        "task_id": task_id,
        "profile": "risky_edit",
        "status": "browser_cleanup_recorded",
        "cleanup_status": "confirmed",
        "started_at": now,
    }
    ledger_file.write_text(
        json.dumps(prior_entry) + "\n" + json.dumps(cleanup_entry) + "\n",
        encoding="utf-8",
    )

    ledger_calls: list[dict] = []

    fake_profiles_with_risky = {
        **_FAKE_PROFILES,
        "risky_edit": {
            "description": "Risky editing profile.",
            "backend": "local",
            "runtime_backend": "local-shell",
            "runtime_target_kind": "workspace",
            "isolation": "shared-session",
            "handoff_required": False,
            "writes_allowed": True,
            "network_allowed": True,
            "checkpoint": "never",
        },
    }

    with patch("scripts.run_with_profile.TASK_LEDGER", ledger_file), \
         patch("scripts.run_with_profile.load_profiles", return_value=fake_profiles_with_risky), \
         patch("scripts.run_with_profile.validate_skill_profile"), \
         patch("scripts.run_with_profile.append_ledger",
               side_effect=lambda e: ledger_calls.append(dict(e))), \
         patch("scripts.run_with_profile._best_effort_index"), \
         patch("scripts.run_with_profile.run_checkpoint", return_value=None), \
         patch("scripts.run_with_profile.evaluate_command_guard", return_value=None), \
         patch("scripts.run_with_profile.finalize_task",
               side_effect=lambda t: ledger_calls.append(dict(t))), \
         patch("scripts.run_with_profile.latest_snapshot_path", return_value=None), \
         patch("scripts.run_with_profile.subprocess.run",
               return_value=_sp.CompletedProcess(args=[], returncode=0)), \
         patch("scripts.browser_work_verifier.verify",
               return_value=_DECISION_RISKY_EDIT_READ):

        from scripts.run_with_profile import cmd_run

        args = _make_args(browser_action="read", profile="risky_edit")
        args.task_id = task_id

        rc = cmd_run(args)

    assert rc == 0, f"Expected exit 0 when cleanup_status recorded; got {rc}"


def test_oq7_finalization_gate_off_does_not_enforce(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """OQ-7: when OPENCLAW_BROWSER_GATE is off, finalization gate does not enforce."""
    monkeypatch.delenv("OPENCLAW_BROWSER_GATE", raising=False)

    import uuid as _uuid
    import datetime as _dt

    task_id = str(_uuid.uuid4())

    ledger_file = tmp_path / "state" / "task-ledger.jsonl"
    ledger_file.parent.mkdir(parents=True)
    now = _dt.datetime.now(_dt.timezone.utc).isoformat()
    prior_entry = {
        "task_id": task_id,
        "profile": "risky_edit",
        "status": "browser_recon",
        "started_at": now,
        "browser_recon": {
            "allow_single_session": True,
            "require_cleanup_evidence": True,
        },
    }
    ledger_file.write_text(json.dumps(prior_entry) + "\n", encoding="utf-8")

    ledger_calls: list[dict] = []

    fake_profiles_with_risky = {
        **_FAKE_PROFILES,
        "risky_edit": {
            "description": "Risky editing profile.",
            "backend": "local",
            "runtime_backend": "local-shell",
            "runtime_target_kind": "workspace",
            "isolation": "shared-session",
            "handoff_required": False,
            "writes_allowed": True,
            "network_allowed": True,
            "checkpoint": "never",
        },
    }

    with patch("scripts.run_with_profile.TASK_LEDGER", ledger_file), \
         patch("scripts.run_with_profile.load_profiles", return_value=fake_profiles_with_risky), \
         patch("scripts.run_with_profile.validate_skill_profile"), \
         patch("scripts.run_with_profile.append_ledger",
               side_effect=lambda e: ledger_calls.append(dict(e))), \
         patch("scripts.run_with_profile._best_effort_index"), \
         patch("scripts.run_with_profile.run_checkpoint", return_value=None), \
         patch("scripts.run_with_profile.evaluate_command_guard", return_value=None), \
         patch("scripts.run_with_profile.finalize_task",
               side_effect=lambda t: ledger_calls.append(dict(t))), \
         patch("scripts.run_with_profile.latest_snapshot_path", return_value=None), \
         patch("scripts.run_with_profile.subprocess.run",
               return_value=_sp.CompletedProcess(args=[], returncode=0)), \
         patch("scripts.browser_work_verifier.verify",
               return_value=_DECISION_RISKY_EDIT_READ):

        from scripts.run_with_profile import cmd_run

        args = _make_args(browser_action="read", profile="risky_edit")
        args.task_id = task_id

        rc = cmd_run(args)

    # Gate off → shadow mode; cleanup gate not enforced; completes exit 0.
    assert rc == 0, (
        f"Expected exit 0 when browser gate is off (shadow mode); got {rc}"
    )
