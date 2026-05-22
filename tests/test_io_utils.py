# tests/test_io_utils.py
"""Tests for scripts/io_utils.py — atomic_write_json helper.

Test inventory (4 tests):
  1. atomic_write_json writes valid JSON to the destination path.
  2. atomic_write_json creates the parent directory if it does not exist.
  3. On os.replace failure: tempfile is cleaned up; original file is intact.
  4. On os.replace failure when no original exists: tempfile is cleaned up;
     destination file is not created.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.io_utils import atomic_write_json


# ---------------------------------------------------------------------------
# Test 1: basic write round-trip
# ---------------------------------------------------------------------------

def test_atomic_write_json_roundtrip(tmp_path: Path) -> None:
    dest = tmp_path / "out.json"
    data = {"key": "value", "num": 42, "nested": {"a": [1, 2, 3]}}
    atomic_write_json(dest, data)

    assert dest.exists(), "destination file must be created"
    loaded = json.loads(dest.read_text(encoding="utf-8"))
    assert loaded == data, "round-tripped content must match"


# ---------------------------------------------------------------------------
# Test 2: parent directory is created automatically
# ---------------------------------------------------------------------------

def test_atomic_write_json_creates_parent_dirs(tmp_path: Path) -> None:
    dest = tmp_path / "deep" / "nested" / "out.json"
    assert not dest.parent.exists(), "precondition: parent must not exist"

    atomic_write_json(dest, {"x": 1})

    assert dest.exists(), "destination file must be created even with missing parents"
    loaded = json.loads(dest.read_text(encoding="utf-8"))
    assert loaded == {"x": 1}


# ---------------------------------------------------------------------------
# Test 3: os.replace failure — tempfile cleaned up; original intact
# ---------------------------------------------------------------------------

def test_atomic_write_json_replace_failure_leaves_original(tmp_path: Path) -> None:
    dest = tmp_path / "state.json"
    original = {"original": True}
    dest.write_text(json.dumps(original), encoding="utf-8")
    original_content = dest.read_text(encoding="utf-8")

    with patch("scripts.io_utils.os.replace", side_effect=OSError("disk full")):
        with pytest.raises(OSError, match="disk full"):
            atomic_write_json(dest, {"new": True})

    # Original file must be unchanged.
    assert dest.read_text(encoding="utf-8") == original_content

    # No leftover .tmp files.
    tmp_files = list(tmp_path.glob("*.tmp"))
    assert tmp_files == [], f"temp files leaked: {tmp_files}"


# ---------------------------------------------------------------------------
# Test 4: os.replace failure when no original — tempfile cleaned up, no dest
# ---------------------------------------------------------------------------

def test_atomic_write_json_replace_failure_no_original(tmp_path: Path) -> None:
    dest = tmp_path / "new.json"
    assert not dest.exists(), "precondition: destination must not exist"

    with patch("scripts.io_utils.os.replace", side_effect=OSError("no space")):
        with pytest.raises(OSError, match="no space"):
            atomic_write_json(dest, {"data": 123})

    # Destination must not have been created.
    assert not dest.exists(), "destination must not exist after failed write"

    # No leftover .tmp files.
    tmp_files = list(tmp_path.glob("*.tmp"))
    assert tmp_files == [], f"temp files leaked: {tmp_files}"
