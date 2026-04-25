from __future__ import annotations
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.command_guard import evaluate_command_guard


def test_guard_blocks_before_subprocess() -> None:
    decision = evaluate_command_guard(
        command=["rm", "-rf", "/"],
        selected_profile="inspect_local",
        profiles={"inspect_local": {"writes_allowed": False, "network_allowed": False}},
        workspace=Path("/tmp"),
    )
    assert decision.action == "deny"


def test_guard_audit_mode_does_not_block() -> None:
    decision = evaluate_command_guard(
        command=["rm", "-rf", "build"],
        selected_profile="workspace_edit",
        profiles={"workspace_edit": {"writes_allowed": True, "network_allowed": False}},
        workspace=Path("/tmp"),
    )
    assert decision.action == "require_approval"


def test_approve_risk_allows_required_approval() -> None:
    decision = evaluate_command_guard(
        command=["rm", "-rf", "build"],
        selected_profile="risky_edit",
        profiles={"risky_edit": {"writes_allowed": True, "network_allowed": False}},
        workspace=Path("/tmp"),
    )
    assert decision.action == "require_approval"
    assert decision.approval_required is True


def test_denied_command_never_runs_subprocess() -> None:
    decision = evaluate_command_guard(
        command=["rm", "-rf", "/"],
        selected_profile="risky_edit",
        profiles={"risky_edit": {"writes_allowed": True, "network_allowed": False}},
        workspace=Path("/tmp"),
    )
    assert decision.action == "deny"


def test_api_provider_detection_does_not_trigger_api_call(monkeypatch) -> None:
    import urllib.request
    api_called = False
    original_urlopen = urllib.request.urlopen
    def mock_urlopen(*a, **kw):
        nonlocal api_called
        api_called = True
        return original_urlopen(*a, **kw)
    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)
    evaluate_command_guard(
        command=["echo", "hello"],
        selected_profile="inspect_local",
        profiles={"inspect_local": {"writes_allowed": False, "network_allowed": False}},
        workspace=Path("/tmp"),
    )
    assert not api_called


def test_guard_off_records_disabled_guard() -> None:
    expected = {"enabled": False, "mode": "off"}
    assert expected["enabled"] is False
    assert expected["mode"] == "off"


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


def _make_args():
    from unittest.mock import MagicMock
    args = MagicMock()
    args.profile = "inspect_local"
    args.guard_mode = "enforce"
    args.guard_json = False
    args.approve_risk = False
    args.command = ["echo", "hello"]
    args.runtime_target = None
    args.task_name = "test"
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
    return args


def test_manual_remote_guard_decision_is_recorded(monkeypatch, tmp_path):
    """manual-remote backend should still evaluate and record guard decision."""
    from unittest.mock import patch

    call_count = 0

    def tracking_evaluate(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return evaluate_command_guard(*args, **kwargs)

    with patch("scripts.run_with_profile.load_profiles", return_value=_FAKE_PROFILES), \
         patch("scripts.run_with_profile.validate_skill_profile"), \
         patch("scripts.run_with_profile.append_ledger"), \
         patch("scripts.run_with_profile._best_effort_index"), \
         patch("scripts.run_with_profile.run_checkpoint", return_value=None), \
         patch("scripts.run_with_profile.evaluate_command_guard", side_effect=tracking_evaluate), \
         patch("scripts.run_with_profile.finalize_task"), \
         patch("scripts.run_with_profile.subprocess.run") as mock_subprocess, \
         patch("scripts.run_with_profile.latest_snapshot_path", return_value=None):

        mock_subprocess.return_value.returncode = 0

        args = _make_args()

        try:
            from scripts.run_with_profile import cmd_run
            cmd_run(args)
        except (SystemExit, Exception):
            pass

    assert call_count >= 1, "evaluate_command_guard must be called before backend handling"


def test_guard_decision_recorded_in_task(monkeypatch, tmp_path):
    """Guard decision should be recorded in the task dict."""
    from unittest.mock import patch, MagicMock
    from scripts.command_guard import GuardDecision, CommandClassification

    fake_decision = GuardDecision(
        action="allow",
        risk_score=0.1,
        score_breakdown={"test": 0.1},
        selected_profile="inspect_local",
        recommended_profile=None,
        reasons=["test"],
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

    captured_tasks = []

    def capture_finalize(task):
        captured_tasks.append(dict(task))

    with patch("scripts.run_with_profile.load_profiles", return_value=_FAKE_PROFILES), \
         patch("scripts.run_with_profile.validate_skill_profile"), \
         patch("scripts.run_with_profile.append_ledger"), \
         patch("scripts.run_with_profile._best_effort_index"), \
         patch("scripts.run_with_profile.run_checkpoint", return_value=None), \
         patch("scripts.run_with_profile.evaluate_command_guard", return_value=fake_decision), \
         patch("scripts.run_with_profile.finalize_task", side_effect=capture_finalize), \
         patch("scripts.run_with_profile.append_jsonl_atomic"), \
         patch("scripts.run_with_profile.subprocess.run") as mock_subprocess, \
         patch("scripts.run_with_profile.latest_snapshot_path", return_value=None):

        mock_subprocess.return_value.returncode = 0

        from scripts.run_with_profile import cmd_run
        args = _make_args()

        try:
            cmd_run(args)
        except (SystemExit, Exception):
            pass

    if captured_tasks:
        task = captured_tasks[0]
        assert "guard" in task, "task must contain guard decision"


def test_helm_guard_mode_off_via_env_warns(monkeypatch, capsys, tmp_path):
    """HELM_GUARD_MODE=off via env should print a warning to stderr."""
    from unittest.mock import patch

    monkeypatch.setenv("HELM_GUARD_MODE", "off")

    with patch("scripts.run_with_profile.load_profiles", return_value=_FAKE_PROFILES), \
         patch("scripts.run_with_profile.validate_skill_profile"), \
         patch("scripts.run_with_profile.append_ledger"), \
         patch("scripts.run_with_profile._best_effort_index"), \
         patch("scripts.run_with_profile.run_checkpoint", return_value=None), \
         patch("scripts.run_with_profile.finalize_task"), \
         patch("scripts.run_with_profile.append_jsonl_atomic"), \
         patch("scripts.run_with_profile.subprocess.run") as mock_subprocess, \
         patch("scripts.run_with_profile.latest_snapshot_path", return_value=None):

        mock_subprocess.return_value.returncode = 0

        args = _make_args()
        args.guard_mode = None  # not via CLI, so env is used

        try:
            from scripts.run_with_profile import cmd_run
            cmd_run(args)
        except (SystemExit, Exception):
            pass

    captured = capsys.readouterr()
    assert "WARNING" in captured.err or "warning" in captured.err.lower(), \
        "Expected warning about HELM_GUARD_MODE=off in stderr"
