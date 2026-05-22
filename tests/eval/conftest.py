# tests/eval/conftest.py
"""Shared fixtures for the reliability eval suite.

Provides only fixtures that are actually wired into a scenario today:

- ``in_memory_state``: a fresh task-state dict (from
  :func:`helm_state_model.new_task_state`). Used by scenario 4.
- ``ledger_path``: a tmp-path-scoped JSONL ledger file path (not
  pre-created). Used by scenario 2.

A ``fake_clock`` fixture was considered but removed: monkeypatching the
state-model's internal timestamp helper is not a behavioral assertion at
the eval/scenario level, so the fixture would be dead weight. Add one
back when a scenario actually needs deterministic timestamps.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from helm_state_model import new_task_state


@pytest.fixture
def in_memory_state() -> dict:
    """Return a fresh, isolated task-state dict."""
    return new_task_state()


@pytest.fixture
def ledger_path(tmp_path: Path) -> Path:
    """Return a tmp-scoped path for a JSONL ledger (parent dir exists; file not pre-created)."""
    return tmp_path / "eval-ledger.jsonl"
