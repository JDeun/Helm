# tests/eval/conftest.py
"""Shared fixtures for the reliability eval suite.

Provides:
- in_memory_state: a fresh task-state dict (from new_task_state)
- ledger_path: a tmp_path-scoped JSONL ledger file path (not pre-created)
- fake_clock: a simple callable that returns a fixed UTC ISO8601 timestamp,
  suitable for monkeypatching _utcnow_iso8601 where deterministic timestamps
  are needed.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
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


@pytest.fixture
def fake_clock():
    """Return a callable that yields a fixed UTC ISO8601 timestamp string.

    Use this to monkeypatch helm_state_model._utcnow_iso8601 in tests that need
    deterministic timestamps without freezegun.
    """
    _FIXED_TS = "2026-05-22T00:00:00+00:00"

    def _clock() -> str:
        return _FIXED_TS

    return _clock
