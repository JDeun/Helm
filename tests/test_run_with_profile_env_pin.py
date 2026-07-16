"""Tests for per-task interpreter fingerprint + env-match (P11/P12) and the
run_checkpoint interpreter-resolution fix.

A daemon whose PATH lacks the running interpreter's directory would fail a
hardcoded ``python3`` subprocess (the exact class of bug OpenClaw hit under
launchd). The real checkpoint subprocess must use ``sys.executable``; the task
ledger must record which interpreter ran the task so a later env-match check
can catch a runtime drift.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_with_profile as rwp  # noqa: E402


def _stub_args(**over):
    base = dict(
        meta_json=None,
        task_id="t1",
        parent_task_id=None,
        task_name=None,
        task_goal=None,
        skill=None,
        runtime_target=None,
        runtime_note=None,
        label=None,
        path=None,
        delivery_mode="inline",
        verified_execution=False,
        verified_attempt=False,
    )
    base.update(over)
    return argparse.Namespace(**base)


def test_interpreter_fingerprint_reports_current():
    fp = rwp.interpreter_fingerprint()
    assert fp["python_executable"] == sys.executable
    assert fp["python_version"] == ".".join(str(p) for p in sys.version_info[:3])


def test_check_interpreter_match_detects_drift():
    fp = rwp.interpreter_fingerprint()
    assert rwp.check_interpreter_match(fp)["match"] is True
    drift = {"python_executable": "/somewhere/else/python", "python_version": "3.0.0"}
    result = rwp.check_interpreter_match(drift)
    assert result["match"] is False
    assert result["actual"]["python_version"] == fp["python_version"]


def test_task_stub_records_runtime_env(monkeypatch):
    monkeypatch.setattr(rwp, "load_profiles", lambda: {"p": {"backend": "b", "checkpoint": "never"}})
    stub = rwp.task_stub("p", _stub_args(), ["echo", "hi"])
    assert stub["runtime_env"]["python_executable"] == sys.executable
    assert stub["runtime_env"]["python_version"] == ".".join(str(p) for p in sys.version_info[:3])


def test_run_checkpoint_uses_current_interpreter(monkeypatch):
    monkeypatch.setattr(rwp, "load_profiles", lambda: {"p": {"backend": "b", "checkpoint": "required"}})
    captured = {}

    class _Result:
        returncode = 0
        stdout = "{}"
        stderr = ""

    def _fake_run(cmd, **kw):
        captured["cmd"] = cmd
        return _Result()

    monkeypatch.setattr(rwp.subprocess, "run", _fake_run)
    rwp.run_checkpoint("p", _stub_args(label="lbl", path=["scripts"]))
    # The real subprocess must invoke the current interpreter, not a bare "python3"
    assert captured["cmd"][0] == sys.executable
    assert captured["cmd"][0] != "python3"


class _Classification:
    def __init__(self, destructive):
        self.destructive_detected = destructive


def test_should_force_checkpoint_on_destructive():
    assert rwp.should_force_checkpoint(_Classification(True)) is True
    assert rwp.should_force_checkpoint(_Classification(False)) is False
    assert rwp.should_force_checkpoint(None) is False


def test_run_checkpoint_forced_on_non_required_profile(monkeypatch):
    # profile does NOT require a checkpoint, but a destructive op forces one
    monkeypatch.setattr(rwp, "load_profiles", lambda: {"p": {"backend": "b", "checkpoint": "never"}})
    calls = {"n": 0}

    class _Result:
        returncode = 0
        stdout = "{}"
        stderr = ""

    def _fake_run(cmd, **kw):
        calls["n"] += 1
        return _Result()

    monkeypatch.setattr(rwp.subprocess, "run", _fake_run)
    # without force → skipped (no subprocess)
    assert rwp.run_checkpoint("p", _stub_args(label="l", path=["scripts"]), force=False) is None
    assert calls["n"] == 0
    # with force → checkpoint runs even though profile is "never"
    rwp.run_checkpoint("p", _stub_args(label="l", path=["scripts"]), force=True)
    assert calls["n"] == 1
