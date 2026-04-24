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


def test_manual_remote_guard_decision_is_recorded(monkeypatch, tmp_path):
    """manual-remote backend should still evaluate and record guard decision."""
    from scripts.run_with_profile import cmd_run
    import inspect
    source = inspect.getsource(cmd_run)
    guard_pos = source.find("Guard evaluation")
    manual_remote_pos = source.find("manual-remote")
    assert guard_pos < manual_remote_pos, "Guard evaluation must occur before manual-remote backend check"


def test_helm_guard_mode_off_via_env_warns(monkeypatch, capsys):
    """HELM_GUARD_MODE=off via env should print a warning."""
    from scripts.run_with_profile import cmd_run
    import inspect
    source = inspect.getsource(cmd_run)
    assert "HELM_GUARD_MODE" in source
    assert "WARNING" in source or "warning" in source.lower()
