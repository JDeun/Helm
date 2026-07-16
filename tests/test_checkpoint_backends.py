"""Tests for scripts/checkpoint_backends.py — non-file runtime-state capture.

Helm's workspace checkpoint snapshots files only; a runtime/dependency bump has
no before-state to compare or restore against. These pluggable backends capture
non-file state (e.g. the installed-distribution fingerprint) so a risky runtime
op has a concrete before-state and a diff.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import checkpoint_backends as cb  # noqa: E402


def test_pip_freeze_fingerprint_is_name_version_map():
    fp = cb.pip_freeze_fingerprint()
    assert isinstance(fp, dict)
    # pytest is installed in the test env, so it must appear
    assert any(k.lower() == "pytest" for k in fp)
    assert all(isinstance(v, str) for v in fp.values())


def test_diff_fingerprints_detects_add_remove_change():
    before = {"a": "1.0", "b": "2.0"}
    after = {"a": "1.1", "c": "3.0"}
    d = cb.diff_fingerprints(before, after)
    assert d["added"] == {"c": "3.0"}
    assert d["removed"] == {"b": "2.0"}
    assert d["changed"] == {"a": {"before": "1.0", "after": "1.1"}}
    assert d["changed_any"] is True


def test_diff_identical_is_no_change():
    fp = {"a": "1.0"}
    d = cb.diff_fingerprints(fp, dict(fp))
    assert d["changed_any"] is False


def test_capture_runtime_state_writes_fingerprint(tmp_path):
    result = cb.capture_runtime_state(tmp_path / "cp", backends=("pip_freeze",))
    assert result["ok"] is True
    assert result["backends"]["pip_freeze"]["ok"] is True
    written = Path(result["backends"]["pip_freeze"]["path"])
    assert written.exists()


def test_capture_unknown_backend_is_reported_not_fatal(tmp_path):
    result = cb.capture_runtime_state(tmp_path / "cp", backends=("does_not_exist",))
    assert result["ok"] is False
    assert result["backends"]["does_not_exist"]["ok"] is False
    assert result["backends"]["does_not_exist"]["error"] == "unknown_backend"
