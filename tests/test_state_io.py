# tests/test_state_io.py
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.state_io import append_jsonl_atomic


def test_append_creates_parent_dirs(tmp_path: Path) -> None:
    target = tmp_path / "sub" / "deep" / "ledger.jsonl"
    append_jsonl_atomic(target, {"key": "value"})
    assert target.exists()
    lines = target.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0]) == {"key": "value"}


def test_append_multiple_entries(tmp_path: Path) -> None:
    target = tmp_path / "ledger.jsonl"
    append_jsonl_atomic(target, {"a": 1})
    append_jsonl_atomic(target, {"b": 2})
    lines = target.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0]) == {"a": 1}
    assert json.loads(lines[1]) == {"b": 2}


def test_append_uses_sorted_keys(tmp_path: Path) -> None:
    target = tmp_path / "ledger.jsonl"
    append_jsonl_atomic(target, {"z": 1, "a": 2})
    line = target.read_text(encoding="utf-8").strip()
    assert line == '{"a": 2, "z": 1}'


def test_append_preserves_unicode(tmp_path: Path) -> None:
    target = tmp_path / "ledger.jsonl"
    append_jsonl_atomic(target, {"name": "한글 테스트"})
    line = target.read_text(encoding="utf-8").strip()
    assert "한글 테스트" in line


def test_append_one_json_per_line(tmp_path: Path) -> None:
    target = tmp_path / "ledger.jsonl"
    append_jsonl_atomic(target, {"nested": {"deep": "value"}})
    lines = target.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["nested"]["deep"] == "value"
