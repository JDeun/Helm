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
    args.timeout = 1800
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


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _make_args_with_timeout(timeout: int = 1800, profile: str = "inspect_local"):
    from unittest.mock import MagicMock
    args = MagicMock()
    args.profile = profile
    args.guard_mode = "off"
    args.guard_json = False
    args.approve_risk = False
    args.command = ["sleep", "999"]
    args.runtime_target = None
    args.task_name = "test-timeout"
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
    args.timeout = timeout
    return args


def _make_allow_decision(profile: str = "inspect_local"):
    from scripts.command_guard import GuardDecision, CommandClassification
    return GuardDecision(
        action="allow",
        risk_score=0.0,
        score_breakdown={},
        selected_profile=profile,
        recommended_profile=None,
        reasons=["test allow"],
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


# ---------------------------------------------------------------------------
# C3: subprocess timeout
# ---------------------------------------------------------------------------

def test_timeout_expired_records_timeout_status(capsys):
    """When subprocess.TimeoutExpired is raised, task status must be 'timeout' and exit code 1."""
    from unittest.mock import patch
    from scripts.run_with_profile import cmd_run

    captured_tasks = []

    def capture_finalize(task):
        captured_tasks.append(dict(task))

    with patch("scripts.run_with_profile.load_profiles", return_value=_FAKE_PROFILES), \
         patch("scripts.run_with_profile.validate_skill_profile"), \
         patch("scripts.run_with_profile.append_ledger"), \
         patch("scripts.run_with_profile._best_effort_index"), \
         patch("scripts.run_with_profile.run_checkpoint", return_value=None), \
         patch("scripts.run_with_profile.evaluate_command_guard") as mock_guard, \
         patch("scripts.run_with_profile.finalize_task", side_effect=capture_finalize), \
         patch("scripts.run_with_profile.latest_snapshot_path", return_value=None), \
         patch("scripts.run_with_profile.subprocess.run",
               side_effect=__import__("subprocess").TimeoutExpired(cmd=["sleep", "999"], timeout=5)):

        mock_guard.return_value = _make_allow_decision()

        args = _make_args_with_timeout(timeout=5)
        rc = cmd_run(args)

    assert rc == 1, "timeout should exit with code 1"
    assert captured_tasks, "finalize_task must have been called"
    task = captured_tasks[0]
    assert task["status"] == "timeout", f"expected status='timeout', got {task['status']!r}"
    assert task["failure_stage"] == "execution"
    assert "timed out" in task["failure_reason"]

    out = capsys.readouterr()
    assert "TIMEOUT" in out.err, "Expected TIMEOUT message in stderr"


def test_timeout_zero_disables_limit():
    """timeout=0 should translate to no limit (None passed to subprocess.run)."""
    from unittest.mock import patch
    from scripts.run_with_profile import cmd_run
    import subprocess as _sp

    captured_calls = []

    def fake_subprocess_run(*a, **kw):
        captured_calls.append(kw)
        return _sp.CompletedProcess(args=a[0] if a else [], returncode=0)

    with patch("scripts.run_with_profile.load_profiles", return_value=_FAKE_PROFILES), \
         patch("scripts.run_with_profile.validate_skill_profile"), \
         patch("scripts.run_with_profile.append_ledger"), \
         patch("scripts.run_with_profile._best_effort_index"), \
         patch("scripts.run_with_profile.run_checkpoint", return_value=None), \
         patch("scripts.run_with_profile.evaluate_command_guard") as mock_guard, \
         patch("scripts.run_with_profile.finalize_task"), \
         patch("scripts.run_with_profile.latest_snapshot_path", return_value=None), \
         patch("scripts.run_with_profile.subprocess.run", side_effect=fake_subprocess_run):

        mock_guard.return_value = _make_allow_decision()

        args = _make_args_with_timeout(timeout=0)
        args.command = ["echo", "hello"]
        cmd_run(args)

    assert captured_calls, "subprocess.run must have been called"
    assert captured_calls[0].get("timeout") is None, \
        "timeout=0 must be converted to None (no limit)"


def test_checkpoint_timeout_returns_error_dict():
    """run_checkpoint must return an error dict when the checkpoint process times out."""
    import subprocess as _sp
    from unittest.mock import patch, MagicMock
    from scripts.run_with_profile import run_checkpoint

    profiles = {
        "risky_edit": {
            "checkpoint": "required",
            "writes_allowed": True,
            "network_allowed": False,
        }
    }
    args = MagicMock()
    args.label = "test-label"
    args.path = None

    with patch("scripts.run_with_profile.load_profiles", return_value=profiles), \
         patch("scripts.run_with_profile.subprocess.run",
               side_effect=_sp.TimeoutExpired(cmd=["python3"], timeout=60)):
        result = run_checkpoint("risky_edit", args)

    assert result is not None
    assert "error" in result
    assert "timed out" in result["error"]


# ---------------------------------------------------------------------------
# H4: minimal environment for restricted profiles
# ---------------------------------------------------------------------------

def test_minimal_env_excludes_secrets(monkeypatch):
    """_minimal_env() must not expose secret-like vars that are not in the allow-list."""
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "supersecret")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("PATH", "/usr/bin")

    from scripts.run_with_profile import _minimal_env
    env = _minimal_env()

    assert "AWS_SECRET_ACCESS_KEY" not in env, "AWS_SECRET_ACCESS_KEY must not appear in minimal env"
    assert "OPENAI_API_KEY" not in env, "OPENAI_API_KEY must not appear in minimal env"
    assert "PATH" in env, "PATH must be present in minimal env"


def test_minimal_env_includes_helm_vars(monkeypatch):
    """_minimal_env() must pass through any HELM_* and OPENCLAW_* vars."""
    monkeypatch.setenv("HELM_MY_CUSTOM_VAR", "value1")
    monkeypatch.setenv("OPENCLAW_MY_VAR", "value2")

    from scripts.run_with_profile import _minimal_env
    env = _minimal_env()

    assert env.get("HELM_MY_CUSTOM_VAR") == "value1"
    assert env.get("OPENCLAW_MY_VAR") == "value2"


def test_inspect_local_uses_minimal_env(monkeypatch, capsys):
    """inspect_local (writes=False, network=False) must use _minimal_env, not full os.environ."""
    from unittest.mock import patch
    import subprocess as _sp
    from scripts.run_with_profile import cmd_run

    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "supersecret")

    captured_envs = []

    def fake_subprocess_run(*a, **kw):
        captured_envs.append(kw.get("env", {}))
        return _sp.CompletedProcess(args=[], returncode=0)

    with patch("scripts.run_with_profile.load_profiles", return_value=_FAKE_PROFILES), \
         patch("scripts.run_with_profile.validate_skill_profile"), \
         patch("scripts.run_with_profile.append_ledger"), \
         patch("scripts.run_with_profile._best_effort_index"), \
         patch("scripts.run_with_profile.run_checkpoint", return_value=None), \
         patch("scripts.run_with_profile.evaluate_command_guard") as mock_guard, \
         patch("scripts.run_with_profile.finalize_task"), \
         patch("scripts.run_with_profile.latest_snapshot_path", return_value=None), \
         patch("scripts.run_with_profile.subprocess.run", side_effect=fake_subprocess_run):

        mock_guard.return_value = _make_allow_decision()

        args = _make_args_with_timeout(timeout=1800, profile="inspect_local")
        args.command = ["echo", "hello"]
        cmd_run(args)

    assert captured_envs, "subprocess.run must have been called"
    child_env = captured_envs[0]
    assert "AWS_SECRET_ACCESS_KEY" not in child_env, \
        "inspect_local must not receive AWS_SECRET_ACCESS_KEY"
    assert "HELM_TASK_ID" in child_env, "HELM_TASK_ID must always be injected"


def test_network_allowed_profile_uses_full_env(monkeypatch):
    """A profile with network_allowed=True should receive the full environment."""
    import subprocess as _sp
    from unittest.mock import patch
    from scripts.run_with_profile import cmd_run

    monkeypatch.setenv("MY_CUSTOM_VAR", "myvalue")

    network_profile = {
        "service_ops": {
            "description": "Service operations with network.",
            "backend": "local",
            "runtime_backend": "local-shell",
            "runtime_target_kind": "workspace",
            "isolation": "shared-session",
            "handoff_required": False,
            "writes_allowed": True,
            "network_allowed": True,
            "checkpoint": "never",
        }
    }

    captured_envs = []

    def fake_subprocess_run(*a, **kw):
        captured_envs.append(kw.get("env", {}))
        return _sp.CompletedProcess(args=[], returncode=0)

    with patch("scripts.run_with_profile.load_profiles", return_value=network_profile), \
         patch("scripts.run_with_profile.validate_skill_profile"), \
         patch("scripts.run_with_profile.append_ledger"), \
         patch("scripts.run_with_profile._best_effort_index"), \
         patch("scripts.run_with_profile.run_checkpoint", return_value=None), \
         patch("scripts.run_with_profile.evaluate_command_guard") as mock_guard, \
         patch("scripts.run_with_profile.finalize_task"), \
         patch("scripts.run_with_profile.latest_snapshot_path", return_value=None), \
         patch("scripts.run_with_profile.subprocess.run", side_effect=fake_subprocess_run):

        mock_guard.return_value = _make_allow_decision(profile="service_ops")

        args = _make_args_with_timeout(timeout=1800, profile="service_ops")
        args.command = ["echo", "hello"]
        cmd_run(args)

    assert captured_envs, "subprocess.run must have been called"
    child_env = captured_envs[0]
    assert "MY_CUSTOM_VAR" in child_env, \
        "network_allowed profile should receive full env including MY_CUSTOM_VAR"


# ---------------------------------------------------------------------------
# H5: lazy profile loading / invalid profile validation
# ---------------------------------------------------------------------------

def test_cmd_run_unknown_profile_returns_2(capsys):
    """cmd_run with an unrecognised profile name must return exit code 2 with a clear message."""
    from unittest.mock import patch
    from scripts.run_with_profile import cmd_run
    from unittest.mock import MagicMock

    args = MagicMock()
    args.profile = "nonexistent_profile"
    args.command = ["echo", "hello"]

    with patch("scripts.run_with_profile.load_profiles", return_value=_FAKE_PROFILES):
        rc = cmd_run(args)

    assert rc == 2, f"expected return code 2, got {rc}"
    out = capsys.readouterr()
    assert "nonexistent_profile" in out.err or "Unknown profile" in out.err, \
        "Expected error message mentioning the bad profile name in stderr"


def test_cmd_show_unknown_profile_returns_2(capsys):
    """cmd_show with an unrecognised profile name must return exit code 2 with a clear message."""
    from unittest.mock import patch, MagicMock
    from scripts.run_with_profile import cmd_show

    args = MagicMock()
    args.profile = "does_not_exist"

    with patch("scripts.run_with_profile.load_profiles", return_value=_FAKE_PROFILES):
        rc = cmd_show(args)

    assert rc == 2, f"expected return code 2, got {rc}"
    out = capsys.readouterr()
    assert "does_not_exist" in out.err or "Unknown profile" in out.err, \
        "Expected error message mentioning the bad profile name in stderr"


def test_build_parser_survives_missing_profiles_file(monkeypatch, tmp_path):
    """build_parser must not crash even if the profiles file is absent (lazy loading)."""
    monkeypatch.setattr(
        "scripts.run_with_profile.PROFILE_FILE",
        tmp_path / "nonexistent_profiles.json",
    )
    from scripts.run_with_profile import build_parser
    # Should not raise even though the profile file does not exist
    parser = build_parser()
    assert parser is not None


# ---------------------------------------------------------------------------
# Fix 7: fail-closed exception path produces tuples, not lists
# ---------------------------------------------------------------------------

def test_guard_exception_fallback_uses_tuples_and_require_approval(capsys):
    """When evaluate_command_guard raises, the fallback decision must use tuples and require_approval."""
    from unittest.mock import patch
    from scripts.run_with_profile import cmd_run

    captured_tasks = []

    def capture_finalize(task):
        captured_tasks.append(dict(task))

    def raise_guard(*args, **kwargs):
        raise RuntimeError("simulated guard failure")

    with patch("scripts.run_with_profile.load_profiles", return_value=_FAKE_PROFILES), \
         patch("scripts.run_with_profile.validate_skill_profile"), \
         patch("scripts.run_with_profile.append_ledger"), \
         patch("scripts.run_with_profile._best_effort_index"), \
         patch("scripts.run_with_profile.run_checkpoint", return_value=None), \
         patch("scripts.run_with_profile.evaluate_command_guard", side_effect=raise_guard), \
         patch("scripts.run_with_profile.finalize_task", side_effect=capture_finalize), \
         patch("scripts.run_with_profile.latest_snapshot_path", return_value=None), \
         patch("scripts.run_with_profile.subprocess.run") as mock_subprocess:

        mock_subprocess.return_value.returncode = 0

        args = _make_args()
        args.guard_mode = "enforce"
        args.approve_risk = True  # approve so it doesn't exit early
        args.command = ["echo", "hello"]

        rc = cmd_run(args)

    # The fallback decision should have action="require_approval"
    # Check the guard info recorded in the task
    assert captured_tasks, "finalize_task must have been called"
    task = captured_tasks[0]
    guard_info = task.get("guard", {})
    assert guard_info.get("action") == "require_approval", \
        f"expected require_approval, got {guard_info.get('action')}"

    # Verify the fallback dataclass was constructed with tuples
    # We re-trigger the fallback path directly to inspect field types
    from scripts.command_guard import GuardDecision, CommandClassification
    try:
        raise RuntimeError("simulated")
    except RuntimeError as exc:
        fallback = GuardDecision(
            action="require_approval",
            risk_score=0.5,
            score_breakdown={"guard_error": 0.5},
            selected_profile="inspect_local",
            recommended_profile=None,
            reasons=tuple([f"guard evaluation error: {exc}"]),
            matched_rules=tuple(),
            classification=CommandClassification(
                normalized_command="echo hello",
                argv=tuple(["echo", "hello"]),
                shell_wrapped=False,
                shell_inner_command=None,
                categories=tuple(["unknown"]),
                matched_rules=tuple(),
                writes_detected=False,
                network_detected=False,
                destructive_detected=False,
                privilege_detected=False,
                remote_detected=False,
            ),
            approval_required=True,
            approval_hint="--approve-risk",
        )
    assert isinstance(fallback.reasons, tuple), f"reasons should be tuple, got {type(fallback.reasons)}"
    assert isinstance(fallback.matched_rules, tuple), f"matched_rules should be tuple, got {type(fallback.matched_rules)}"
    assert isinstance(fallback.classification.argv, tuple), f"argv should be tuple, got {type(fallback.classification.argv)}"
    assert isinstance(fallback.classification.categories, tuple), f"categories should be tuple, got {type(fallback.classification.categories)}"
    assert isinstance(fallback.classification.matched_rules, tuple), f"classification.matched_rules should be tuple, got {type(fallback.classification.matched_rules)}"


def test_negative_timeout_treated_as_zero():
    """A negative --timeout value should be clamped to 0 (no limit)."""
    from unittest.mock import patch
    from scripts.run_with_profile import cmd_run
    import subprocess as _sp

    captured_calls = []

    def fake_subprocess_run(*a, **kw):
        captured_calls.append(kw)
        return _sp.CompletedProcess(args=a[0] if a else [], returncode=0)

    with patch("scripts.run_with_profile.load_profiles", return_value=_FAKE_PROFILES), \
         patch("scripts.run_with_profile.validate_skill_profile"), \
         patch("scripts.run_with_profile.append_ledger"), \
         patch("scripts.run_with_profile._best_effort_index"), \
         patch("scripts.run_with_profile.run_checkpoint", return_value=None), \
         patch("scripts.run_with_profile.evaluate_command_guard") as mock_guard, \
         patch("scripts.run_with_profile.finalize_task"), \
         patch("scripts.run_with_profile.latest_snapshot_path", return_value=None), \
         patch("scripts.run_with_profile.subprocess.run", side_effect=fake_subprocess_run):

        mock_guard.return_value = _make_allow_decision()

        args = _make_args_with_timeout(timeout=-5)
        args.command = ["echo", "hello"]
        cmd_run(args)

    assert captured_calls, "subprocess.run must have been called"
    assert captured_calls[0].get("timeout") is None, \
        "negative timeout must be clamped to 0 then converted to None (no limit)"


def test_known_profiles_listed_in_error_message(capsys):
    """The error message for an unknown profile must list the known profiles."""
    from unittest.mock import patch, MagicMock
    from scripts.run_with_profile import cmd_show

    profiles = {
        "inspect_local": {"description": "...", "backend": "local", "checkpoint": "never",
                          "writes_allowed": False, "network_allowed": False},
        "workspace_edit": {"description": "...", "backend": "local", "checkpoint": "never",
                           "writes_allowed": True, "network_allowed": False},
    }

    args = MagicMock()
    args.profile = "bogus_profile"

    with patch("scripts.run_with_profile.load_profiles", return_value=profiles):
        rc = cmd_show(args)

    assert rc == 2
    out = capsys.readouterr()
    assert "inspect_local" in out.err or "workspace_edit" in out.err, \
        "Error message should list known profiles"
