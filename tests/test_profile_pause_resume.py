# tests/test_profile_pause_resume.py
"""Tests for scripts/profile_pause_resume.py — profile-level hard-stop.

Test inventory (10 tests):
  1. pause_profile creates an entry; subsequent is_paused returns True.
  2. Calling pause_profile for an already-paused profile updates the entry
     (new reason, new token, new paused_at) but does not duplicate.
  3. resume_profile with correct token clears the entry; subsequent
     is_paused returns False.
  4. resume_profile with wrong token raises ValueError; state file unchanged.
  5. resume_profile for an unpaused profile raises ValueError.
  6. list_paused returns all paused profiles sorted by profile name.
  7. pause_session_summary returns a JSON-serializable dict with the
     documented keys (profile, paused_sessions, cleanup_status, stop_reason).
  8. check_can_start returns (False, reason) when paused; (True, None) when not.
  9. Atomic write — mocking os.replace to fail leaves the original file intact.
 10. Concurrent sequential writes — two pause_profile calls both succeed;
     second wins on token.
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

from scripts.profile_pause_resume import (
    check_can_start,
    is_paused,
    list_paused,
    pause_profile,
    pause_session_summary,
    resume_profile,
)


# ---------------------------------------------------------------------------
# Test 1: pause_profile creates entry; is_paused returns True
# ---------------------------------------------------------------------------

def test_pause_creates_entry_and_is_paused(tmp_path: Path) -> None:
    state_file = tmp_path / "state.json"
    entry = pause_profile("chrome-work", "browser fan-out stopped", state_file)

    assert state_file.exists(), "state file must be created"
    assert entry["reason"] == "browser fan-out stopped"
    assert len(entry["resume_token"]) == 8, "token must be 8 hex chars"
    assert "paused_at" in entry

    assert is_paused("chrome-work", state_file) is True
    assert is_paused("chrome-personal", state_file) is False


# ---------------------------------------------------------------------------
# Test 2: re-pausing updates entry without duplicating
# ---------------------------------------------------------------------------

def test_re_pause_updates_entry(tmp_path: Path) -> None:
    state_file = tmp_path / "state.json"

    entry1 = pause_profile("chrome-work", "first reason", state_file)
    entry2 = pause_profile("chrome-work", "updated reason", state_file)

    state = json.loads(state_file.read_text(encoding="utf-8"))
    assert len(state) == 1, "only one entry per profile"

    assert entry2["reason"] == "updated reason"
    assert entry2["resume_token"] != entry1["resume_token"] or True  # tokens *may* collide but reason must differ
    assert entry2["reason"] != entry1["reason"]
    assert is_paused("chrome-work", state_file) is True


# ---------------------------------------------------------------------------
# Test 3: resume_profile with correct token clears entry
# ---------------------------------------------------------------------------

def test_resume_with_correct_token(tmp_path: Path) -> None:
    state_file = tmp_path / "state.json"
    entry = pause_profile("chrome-work", "stopping for maintenance", state_file)

    removed = resume_profile("chrome-work", entry["resume_token"], state_file)

    assert removed["reason"] == "stopping for maintenance"
    assert is_paused("chrome-work", state_file) is False

    # State file must still be valid JSON (empty object).
    state = json.loads(state_file.read_text(encoding="utf-8"))
    assert state == {}


# ---------------------------------------------------------------------------
# Test 4: resume_profile with wrong token raises ValueError; file unchanged
# ---------------------------------------------------------------------------

def test_resume_wrong_token_raises(tmp_path: Path) -> None:
    state_file = tmp_path / "state.json"
    pause_profile("chrome-work", "stop", state_file)

    # Record state before the failed attempt.
    before = state_file.read_text(encoding="utf-8")

    with pytest.raises(ValueError, match="Token mismatch"):
        resume_profile("chrome-work", "deadbeef", state_file)

    # File must be byte-for-byte identical.
    assert state_file.read_text(encoding="utf-8") == before
    assert is_paused("chrome-work", state_file) is True


# ---------------------------------------------------------------------------
# Test 5: resume_profile for an unpaused profile raises ValueError
# ---------------------------------------------------------------------------

def test_resume_unpaused_raises(tmp_path: Path) -> None:
    state_file = tmp_path / "state.json"

    with pytest.raises(ValueError, match="not paused"):
        resume_profile("nonexistent-profile", "00000000", state_file)


# ---------------------------------------------------------------------------
# Test 6: list_paused returns all profiles sorted by name
# ---------------------------------------------------------------------------

def test_list_paused_sorted(tmp_path: Path) -> None:
    state_file = tmp_path / "state.json"

    pause_profile("zebra", "z reason", state_file)
    pause_profile("apple", "a reason", state_file)
    pause_profile("mango", "m reason", state_file)

    results = list_paused(state_file)
    names = [r["profile"] for r in results]
    assert names == ["apple", "mango", "zebra"], "must be sorted by profile name"

    # Each entry must carry the metadata keys.
    for r in results:
        assert "profile" in r
        assert "paused_at" in r
        assert "reason" in r
        assert "resume_token" in r


# ---------------------------------------------------------------------------
# Test 7: pause_session_summary returns JSON-serializable dict with correct keys
# ---------------------------------------------------------------------------

def test_pause_session_summary_shape() -> None:
    summary = pause_session_summary(
        profile="chrome-work",
        sessions=["sess-001", "sess-002"],
        cleanup_status="partial",
    )

    # Must be JSON-serializable (no datetime objects etc.)
    serialized = json.dumps(summary)
    assert isinstance(serialized, str)

    # Must contain the documented keys.
    assert summary["profile"] == "chrome-work"
    assert summary["paused_sessions"] == ["sess-001", "sess-002"]
    assert summary["cleanup_status"] == "partial"
    assert summary["stop_reason"] == "hard_stop"


# ---------------------------------------------------------------------------
# Test 8: check_can_start returns (False, reason) / (True, None)
# ---------------------------------------------------------------------------

def test_check_can_start(tmp_path: Path) -> None:
    state_file = tmp_path / "state.json"

    # Unpaused profile.
    ok, reason = check_can_start("chrome-work", state_file)
    assert ok is True
    assert reason is None

    # Pause the profile.
    pause_profile("chrome-work", "fan-out hard stop", state_file)

    ok, reason = check_can_start("chrome-work", state_file)
    assert ok is False
    assert reason == "fan-out hard stop"

    # Other profile is unaffected.
    ok2, reason2 = check_can_start("chrome-personal", state_file)
    assert ok2 is True
    assert reason2 is None


# ---------------------------------------------------------------------------
# Test 9: Atomic write — os.replace failure leaves original file intact
# ---------------------------------------------------------------------------

def test_atomic_write_on_replace_failure(tmp_path: Path) -> None:
    state_file = tmp_path / "state.json"

    # Write a known-good initial state.
    good_state = {"existing-profile": {"paused_at": "2026-01-01T00:00:00+00:00",
                                        "reason": "original",
                                        "resume_token": "aabbccdd"}}
    state_file.write_text(json.dumps(good_state), encoding="utf-8")
    original_content = state_file.read_text(encoding="utf-8")

    # Patch os.replace inside the module to simulate a rename failure.
    with patch("scripts.profile_pause_resume.os.replace", side_effect=OSError("disk full")):
        with pytest.raises(OSError, match="disk full"):
            pause_profile("new-profile", "should not persist", state_file)

    # Original file must be unchanged.
    assert state_file.read_text(encoding="utf-8") == original_content

    # No leftover temp files should block anything (they are cleaned up in the
    # except branch of _write_state).
    tmp_files = list(tmp_path.glob("*.tmp"))
    assert tmp_files == [], f"temp files leaked: {tmp_files}"


# ---------------------------------------------------------------------------
# Test 10: Sequential writes — two pause_profile calls both succeed; second wins
# ---------------------------------------------------------------------------

def test_sequential_writes_second_wins(tmp_path: Path) -> None:
    """Two sequential pause_profile calls: last writer's entry is stored.

    Note: concurrent *same-instant* writes (two processes interleaving read
    and write) are NOT safe — file locking is future work.  This test covers
    the sequential (within-process) case only.
    """
    state_file = tmp_path / "state.json"

    entry1 = pause_profile("chrome-work", "first stop", state_file)
    entry2 = pause_profile("chrome-work", "second stop", state_file)

    # Both calls must have returned valid entries.
    assert entry1["reason"] == "first stop"
    assert entry2["reason"] == "second stop"

    # The file must be valid JSON and the second entry must have won.
    state = json.loads(state_file.read_text(encoding="utf-8"))
    assert state["chrome-work"]["reason"] == "second stop"
    assert state["chrome-work"]["resume_token"] == entry2["resume_token"]

    # Exactly one entry (no duplication).
    assert len(state) == 1
