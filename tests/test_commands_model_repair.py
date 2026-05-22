"""Tests for commands/model_repair.py — CLI smoke tests.

Coverage (~3 cases):
 1. Exit 0 with flags unset
 2. Exit 0 with flags set
 3. Stdout mentions both flags' detected values
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _run_cmd(monkeypatch, env_repair=None, env_respond=None):
    """Import and run cmd_model_repair_check, capturing stdout."""
    import argparse
    import io

    # Set env
    if env_repair is None:
        monkeypatch.delenv("HELM_MODEL_REPAIR", raising=False)
    else:
        monkeypatch.setenv("HELM_MODEL_REPAIR", env_repair)

    if env_respond is None:
        monkeypatch.delenv("HELM_SYNTHETIC_RESPOND", raising=False)
    else:
        monkeypatch.setenv("HELM_SYNTHETIC_RESPOND", env_respond)

    # Reload to pick up env changes
    import importlib
    for mod_name in list(sys.modules.keys()):
        if mod_name in ("scripts.model_repair", "scripts.respond_tool_wiring",
                        "commands.model_repair"):
            del sys.modules[mod_name]

    from commands import model_repair as cmd_mod

    captured = io.StringIO()
    old_stdout = sys.stdout
    sys.stdout = captured
    try:
        ns = argparse.Namespace()
        exit_code = cmd_mod.cmd_model_repair_check(ns)
    finally:
        sys.stdout = old_stdout

    return exit_code, captured.getvalue()


# ---------------------------------------------------------------------------
# 1. Exit 0 with flags unset
# ---------------------------------------------------------------------------


def test_model_repair_check_exits_0_flags_unset(monkeypatch):
    exit_code, output = _run_cmd(monkeypatch)
    assert exit_code == 0


# ---------------------------------------------------------------------------
# 2. Exit 0 with flags set
# ---------------------------------------------------------------------------


def test_model_repair_check_exits_0_flags_set(monkeypatch):
    exit_code, output = _run_cmd(monkeypatch, env_repair="1", env_respond="1")
    assert exit_code == 0


# ---------------------------------------------------------------------------
# 3. Stdout mentions both flags' detected values
# ---------------------------------------------------------------------------


def test_model_repair_check_output_mentions_both_flags(monkeypatch):
    exit_code, output = _run_cmd(monkeypatch, env_repair="1", env_respond="0")
    # Output should mention both flag names and their detected True/False state
    assert "HELM_MODEL_REPAIR" in output
    assert "HELM_SYNTHETIC_RESPOND" in output
    # With repair=1 → enabled True; respond=0 → enabled False
    assert "True" in output or "true" in output
    assert "False" in output or "false" in output
