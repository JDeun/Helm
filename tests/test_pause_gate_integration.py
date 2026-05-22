# tests/test_pause_gate_integration.py
"""Tests for the OPENCLAW_PAUSE_GATE feature flag in scripts/run_with_profile.py.

Test inventory (5+ tests):
  1. Gate disabled by default: env unset, profile paused → runner proceeds,
     check_can_start NOT called (call_count == 0).
  2. Gate enabled blocks paused profile: env="1", profile in pause state file →
     non-zero exit, stderr message, ledger has blocked_by_pause entry.
  3. Gate enabled allows unpaused profile: env="1", profile not paused →
     runner proceeds normally.
  4. Truthy/falsy env detection: parametrize over "1"/"true"/"yes"/"YES" enabled;
     ""/"0"/"false"/"no"/unset disabled.
  5. Ledger NOT polluted when disabled: env unset, paused profile → no
     blocked_by_pause row in the ledger.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, call

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
    }
}


def _make_args(profile: str = "inspect_local"):
    args = MagicMock()
    args.profile = profile
    args.guard_mode = "off"
    args.guard_json = False
    args.approve_risk = False
    args.command = ["echo", "hello"]
    args.runtime_target = None
    args.task_name = "test-pause-gate"
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
    return args


def _write_pause_state(state_path: Path, profile: str, reason: str = "rate-limit exceeded") -> None:
    """Write a minimal pause state file with one paused profile."""
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps({profile: {"paused_at": "2026-05-22T00:00:00+00:00", "reason": reason, "resume_token": "abcd1234"}}),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Test 1: Gate disabled by default — check_can_start must NOT be called
# ---------------------------------------------------------------------------

def test_gate_disabled_by_default_does_not_call_check_can_start(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """When OPENCLAW_PAUSE_GATE is unset, check_can_start must never be invoked."""
    monkeypatch.delenv("OPENCLAW_PAUSE_GATE", raising=False)

    pause_state = tmp_path / "pause-state.json"
    _write_pause_state(pause_state, "inspect_local")
    monkeypatch.setenv("OPENCLAW_PAUSE_STATE", str(pause_state))

    import subprocess as _sp

    with patch("scripts.run_with_profile.load_profiles", return_value=_FAKE_PROFILES), \
         patch("scripts.run_with_profile.validate_skill_profile"), \
         patch("scripts.run_with_profile.append_ledger"), \
         patch("scripts.run_with_profile._best_effort_index"), \
         patch("scripts.run_with_profile.run_checkpoint", return_value=None), \
         patch("scripts.run_with_profile.evaluate_command_guard") as mock_guard, \
         patch("scripts.run_with_profile.finalize_task"), \
         patch("scripts.run_with_profile.latest_snapshot_path", return_value=None), \
         patch("scripts.run_with_profile.subprocess.run",
               return_value=_sp.CompletedProcess(args=[], returncode=0)), \
         patch("scripts.profile_pause_resume.check_can_start") as mock_check:

        from scripts.command_guard import GuardDecision, CommandClassification
        mock_guard.return_value = GuardDecision(
            action="allow",
            risk_score=0.0,
            score_breakdown={},
            selected_profile="inspect_local",
            recommended_profile=None,
            reasons=["allowed"],
            matched_rules=[],
            classification=CommandClassification(
                normalized_command="echo hello",
                argv=["echo", "hello"],
                shell_wrapped=False,
                shell_inner_command=None,
                categories=["read"],
                matched_rules=[],
                writes_detected=False,
                network_detected=False,
                destructive_detected=False,
                privilege_detected=False,
                remote_detected=False,
            ),
            approval_required=False,
            approval_hint=None,
        )

        from scripts.run_with_profile import cmd_run
        rc = cmd_run(_make_args())

    assert mock_check.call_count == 0, (
        f"check_can_start must NOT be called when gate is disabled; call_count={mock_check.call_count}"
    )
    assert rc == 0, f"Expected exit code 0 when gate disabled, got {rc}"


# ---------------------------------------------------------------------------
# Test 2: Gate enabled blocks paused profile
# ---------------------------------------------------------------------------

def test_gate_enabled_blocks_paused_profile(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture,
) -> None:
    """env=OPENCLAW_PAUSE_GATE=1, profile paused → non-zero exit + stderr + ledger entry."""
    monkeypatch.setenv("OPENCLAW_PAUSE_GATE", "1")

    pause_state = tmp_path / "pause-state.json"
    _write_pause_state(pause_state, "inspect_local", reason="rate-limit exceeded")
    monkeypatch.setenv("OPENCLAW_PAUSE_STATE", str(pause_state))

    ledger_calls: list[dict] = []

    def capture_ledger(entry: dict) -> None:
        ledger_calls.append(dict(entry))

    with patch("scripts.run_with_profile.load_profiles", return_value=_FAKE_PROFILES), \
         patch("scripts.run_with_profile.validate_skill_profile"), \
         patch("scripts.run_with_profile.append_ledger", side_effect=capture_ledger), \
         patch("scripts.run_with_profile._best_effort_index"), \
         patch("scripts.run_with_profile.run_checkpoint", return_value=None), \
         patch("scripts.run_with_profile.subprocess.run") as mock_subprocess:

        mock_subprocess.return_value.returncode = 0

        from scripts.run_with_profile import cmd_run
        rc = cmd_run(_make_args())

    assert rc != 0, f"Expected non-zero exit code when profile is paused; got {rc}"

    captured = capsys.readouterr()
    assert "inspect_local" in captured.err, "Expected profile name in stderr"
    assert "paused" in captured.err.lower(), "Expected 'paused' in stderr message"
    assert "rate-limit exceeded" in captured.err, "Expected pause reason in stderr"

    blocked_entries = [e for e in ledger_calls if e.get("status") == "blocked_by_pause"]
    assert blocked_entries, f"Expected at least one blocked_by_pause ledger entry; got {ledger_calls}"
    entry = blocked_entries[0]
    assert entry.get("profile") == "inspect_local", f"Expected profile='inspect_local' in entry; got {entry}"
    assert "rate-limit exceeded" in (entry.get("reason") or ""), (
        f"Expected reason in ledger entry; got {entry}"
    )
    assert "updated_at" in entry, f"Expected updated_at in ledger entry; got {entry}"

    mock_subprocess.assert_not_called(), "subprocess.run must NOT be called when profile is paused"


# ---------------------------------------------------------------------------
# Test 3: Gate enabled, profile NOT paused → runs normally
# ---------------------------------------------------------------------------

def test_gate_enabled_allows_unpaused_profile(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """env=OPENCLAW_PAUSE_GATE=1, profile not paused → runner proceeds normally."""
    monkeypatch.setenv("OPENCLAW_PAUSE_GATE", "1")

    # Write pause state for a DIFFERENT profile so inspect_local is not paused
    pause_state = tmp_path / "pause-state.json"
    _write_pause_state(pause_state, "other_profile", reason="irrelevant")
    monkeypatch.setenv("OPENCLAW_PAUSE_STATE", str(pause_state))

    import subprocess as _sp

    with patch("scripts.run_with_profile.load_profiles", return_value=_FAKE_PROFILES), \
         patch("scripts.run_with_profile.validate_skill_profile"), \
         patch("scripts.run_with_profile.append_ledger"), \
         patch("scripts.run_with_profile._best_effort_index"), \
         patch("scripts.run_with_profile.run_checkpoint", return_value=None), \
         patch("scripts.run_with_profile.evaluate_command_guard") as mock_guard, \
         patch("scripts.run_with_profile.finalize_task"), \
         patch("scripts.run_with_profile.latest_snapshot_path", return_value=None), \
         patch("scripts.run_with_profile.subprocess.run",
               return_value=_sp.CompletedProcess(args=[], returncode=0)) as mock_subprocess:

        from scripts.command_guard import GuardDecision, CommandClassification
        mock_guard.return_value = GuardDecision(
            action="allow",
            risk_score=0.0,
            score_breakdown={},
            selected_profile="inspect_local",
            recommended_profile=None,
            reasons=["allowed"],
            matched_rules=[],
            classification=CommandClassification(
                normalized_command="echo hello",
                argv=["echo", "hello"],
                shell_wrapped=False,
                shell_inner_command=None,
                categories=["read"],
                matched_rules=[],
                writes_detected=False,
                network_detected=False,
                destructive_detected=False,
                privilege_detected=False,
                remote_detected=False,
            ),
            approval_required=False,
            approval_hint=None,
        )

        from scripts.run_with_profile import cmd_run
        rc = cmd_run(_make_args())

    assert rc == 0, f"Expected exit code 0 when profile is not paused; got {rc}"
    mock_subprocess.assert_called_once(), "subprocess.run should have been called for unpaused profile"


# ---------------------------------------------------------------------------
# Test 4: Truthy/falsy env detection (parametrized)
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
def test_pause_gate_enabled_detection(
    monkeypatch: pytest.MonkeyPatch,
    env_value: str,
    expected_enabled: bool,
) -> None:
    """_pause_gate_enabled must return True only for truthy values."""
    monkeypatch.setenv("OPENCLAW_PAUSE_GATE", env_value)

    # Re-import to pick up current env
    from scripts.run_with_profile import _pause_gate_enabled
    result = _pause_gate_enabled()
    assert result == expected_enabled, (
        f"For OPENCLAW_PAUSE_GATE={env_value!r}: expected {expected_enabled}, got {result}"
    )


def test_pause_gate_disabled_when_env_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """_pause_gate_enabled must return False when env var is not set at all."""
    monkeypatch.delenv("OPENCLAW_PAUSE_GATE", raising=False)

    from scripts.run_with_profile import _pause_gate_enabled
    assert _pause_gate_enabled() is False, "Expected False when OPENCLAW_PAUSE_GATE is unset"


# ---------------------------------------------------------------------------
# Test 5: Ledger NOT polluted when gate is disabled
# ---------------------------------------------------------------------------

def test_ledger_not_polluted_when_gate_disabled(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """When gate is disabled, no blocked_by_pause entry must appear in the ledger."""
    monkeypatch.delenv("OPENCLAW_PAUSE_GATE", raising=False)

    pause_state = tmp_path / "pause-state.json"
    _write_pause_state(pause_state, "inspect_local", reason="should-not-appear")
    monkeypatch.setenv("OPENCLAW_PAUSE_STATE", str(pause_state))

    import subprocess as _sp

    ledger_calls: list[dict] = []

    def capture_ledger(entry: dict) -> None:
        ledger_calls.append(dict(entry))

    with patch("scripts.run_with_profile.load_profiles", return_value=_FAKE_PROFILES), \
         patch("scripts.run_with_profile.validate_skill_profile"), \
         patch("scripts.run_with_profile.append_ledger", side_effect=capture_ledger), \
         patch("scripts.run_with_profile._best_effort_index"), \
         patch("scripts.run_with_profile.run_checkpoint", return_value=None), \
         patch("scripts.run_with_profile.evaluate_command_guard") as mock_guard, \
         patch("scripts.run_with_profile.finalize_task", side_effect=capture_ledger), \
         patch("scripts.run_with_profile.latest_snapshot_path", return_value=None), \
         patch("scripts.run_with_profile.subprocess.run",
               return_value=_sp.CompletedProcess(args=[], returncode=0)):

        from scripts.command_guard import GuardDecision, CommandClassification
        mock_guard.return_value = GuardDecision(
            action="allow",
            risk_score=0.0,
            score_breakdown={},
            selected_profile="inspect_local",
            recommended_profile=None,
            reasons=["allowed"],
            matched_rules=[],
            classification=CommandClassification(
                normalized_command="echo hello",
                argv=["echo", "hello"],
                shell_wrapped=False,
                shell_inner_command=None,
                categories=["read"],
                matched_rules=[],
                writes_detected=False,
                network_detected=False,
                destructive_detected=False,
                privilege_detected=False,
                remote_detected=False,
            ),
            approval_required=False,
            approval_hint=None,
        )

        from scripts.run_with_profile import cmd_run
        rc = cmd_run(_make_args())

    blocked = [e for e in ledger_calls if e.get("status") == "blocked_by_pause"]
    assert not blocked, (
        f"No blocked_by_pause entries should exist when gate is disabled; found: {blocked}"
    )
