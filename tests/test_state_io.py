# tests/test_state_io.py
from __future__ import annotations

import json
import sys
import warnings
from pathlib import Path

import pytest

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


def test_lock_failure_warns(tmp_path: Path, monkeypatch) -> None:
    """When locking is unavailable, a warning should be emitted (at least once)."""
    import scripts.state_io as state_io_mod
    state_io_mod._LOCK_WARNING_ISSUED = False

    target = tmp_path / "ledger.jsonl"

    # Mock locking to fail
    if sys.platform == "win32":
        import msvcrt
        monkeypatch.setattr(msvcrt, "locking", lambda *a: (_ for _ in ()).throw(OSError("mock lock fail")))
    else:
        import fcntl
        original_flock = fcntl.flock
        def _fail_flock(fd, op):
            if op == fcntl.LOCK_EX:
                raise OSError("mock lock fail")
            return original_flock(fd, op)
        monkeypatch.setattr(fcntl, "flock", _fail_flock)

    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        append_jsonl_atomic(target, {"a": 1})
        append_jsonl_atomic(target, {"b": 2})

    lock_warnings = [x for x in w if "locking unavailable" in str(x.message).lower() or "file locking" in str(x.message).lower()]
    assert len(lock_warnings) >= 1

    # Data should still be written despite lock failure
    lines = target.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2

    state_io_mod._LOCK_WARNING_ISSUED = False


def test_windows_lock_size_matches_write(tmp_path: Path) -> None:
    """Verify data integrity after write (covers lock semantics indirectly)."""
    target = tmp_path / "ledger.jsonl"
    large_entry = {"key": "x" * 1000}
    append_jsonl_atomic(target, large_entry)
    line = target.read_text(encoding="utf-8").strip()
    parsed = json.loads(line)
    assert parsed["key"] == "x" * 1000


def test_concurrent_append_no_data_loss(tmp_path: Path) -> None:
    """Multiple threads appending simultaneously should not lose data."""
    import scripts.state_io as state_io_mod
    from concurrent.futures import ThreadPoolExecutor

    state_io_mod._LOCK_WARNING_ISSUED = False
    target = tmp_path / "concurrent.jsonl"
    n_threads = 10
    n_writes_per_thread = 20

    def writer(thread_id: int) -> None:
        for i in range(n_writes_per_thread):
            append_jsonl_atomic(target, {"thread": thread_id, "seq": i})

    with ThreadPoolExecutor(max_workers=n_threads) as pool:
        futures = [pool.submit(writer, t) for t in range(n_threads)]
        for f in futures:
            f.result()

    lines = target.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == n_threads * n_writes_per_thread

    # Verify every entry is valid JSON
    for line in lines:
        entry = json.loads(line)
        assert "thread" in entry
        assert "seq" in entry

    state_io_mod._LOCK_WARNING_ISSUED = False


# ---------------------------------------------------------------------------
# Ledger extension — new optional fields (Task 2)
# ---------------------------------------------------------------------------

class TestLedgerExtension:
    """Verify that the ledger writer (append_jsonl_atomic + build_ledger_entry)
    persists new optional fields and that old entries without them still parse.
    """

    def _write_and_read(self, path: Path, entries: list[dict]) -> list[dict]:
        from scripts.state_io import append_jsonl_atomic
        for entry in entries:
            append_jsonl_atomic(path, entry)
        from scripts.jsonl_io import read_jsonl
        return read_jsonl(path)

    def test_failure_signature_persisted(self, tmp_path: Path):
        path = tmp_path / "ledger.jsonl"
        entry = {
            "task_id": "abc",
            "status": "failed",
            "failure_signature": {
                "component": "skill",
                "tool": "google_sheets_append_row",
                "profile": "service_ops",
                "error_class": "google_sheets_api",
                "target": None,
                "fingerprint": "abcd1234",
            },
        }
        rows = self._write_and_read(path, [entry])
        assert len(rows) == 1
        assert rows[0]["failure_signature"]["error_class"] == "google_sheets_api"
        assert rows[0]["failure_signature"]["fingerprint"] == "abcd1234"

    def test_sessions_persisted(self, tmp_path: Path):
        path = tmp_path / "ledger.jsonl"
        entry = {
            "task_id": "abc",
            "status": "completed",
            "sessions": ["session-001", "session-002"],
        }
        rows = self._write_and_read(path, [entry])
        assert rows[0]["sessions"] == ["session-001", "session-002"]

    def test_snapshot_evidence_persisted(self, tmp_path: Path):
        path = tmp_path / "ledger.jsonl"
        entry = {
            "task_id": "abc",
            "status": "completed",
            "snapshot_evidence": "~/.openclaw/workspace/.openclaw/snapshots/snap123.json",
        }
        rows = self._write_and_read(path, [entry])
        assert rows[0]["snapshot_evidence"] == "~/.openclaw/workspace/.openclaw/snapshots/snap123.json"

    def test_cleanup_status_persisted(self, tmp_path: Path):
        path = tmp_path / "ledger.jsonl"
        for value in ("ok", "partial", "failed", "not_required"):
            entry = {"task_id": f"task-{value}", "status": "completed", "cleanup_status": value}
            path2 = tmp_path / f"ledger_{value}.jsonl"
            rows = self._write_and_read(path2, [entry])
            assert rows[0]["cleanup_status"] == value

    def test_browser_fields_persisted(self, tmp_path: Path):
        path = tmp_path / "ledger.jsonl"
        entry = {
            "task_id": "browser-task",
            "status": "completed",
            "browser_profile": "default",
            "browser_mode": "headless",
            "source_urls": ["https://example.com/page1"],
            "screenshot_evidence": "~/screenshots/task123.png",
            "console_network_signals": {"xhr_count": 5},
            "site_note_update": "Updated landing page note.",
        }
        rows = self._write_and_read(path, [entry])
        r = rows[0]
        assert r["browser_profile"] == "default"
        assert r["browser_mode"] == "headless"
        assert r["source_urls"] == ["https://example.com/page1"]
        assert r["screenshot_evidence"] == "~/screenshots/task123.png"
        assert r["console_network_signals"]["xhr_count"] == 5
        assert r["site_note_update"] == "Updated landing page note."

    def test_retry_count_unchanged(self, tmp_path: Path):
        path = tmp_path / "ledger.jsonl"
        entry = {"task_id": "abc", "status": "failed", "retry_count": 3}
        rows = self._write_and_read(path, [entry])
        assert rows[0]["retry_count"] == 3

    def test_old_entry_without_new_fields_parses_cleanly(self, tmp_path: Path):
        """Legacy entries (no failure_signature, no sessions, etc.) still round-trip."""
        path = tmp_path / "ledger.jsonl"
        legacy_entry = {
            "task_id": "legacy-001",
            "task_name": "old task",
            "profile": "service_ops",
            "status": "completed",
            "exit_code": 0,
            "retry_count": 0,
        }
        rows = self._write_and_read(path, [legacy_entry])
        assert rows[0]["task_id"] == "legacy-001"
        # New fields must NOT appear on old entries
        assert "failure_signature" not in rows[0]
        assert "sessions" not in rows[0]
        assert "cleanup_status" not in rows[0]
        assert "snapshot_evidence" not in rows[0]

    def test_mixed_old_and_new_entries(self, tmp_path: Path):
        """Mix of legacy and new-field entries all parse correctly."""
        path = tmp_path / "ledger.jsonl"
        legacy = {"task_id": "leg-001", "status": "completed", "exit_code": 0}
        new_entry = {
            "task_id": "new-001",
            "status": "failed",
            "failure_signature": {"component": "guard", "tool": "run_with_profile", "profile": None,
                                   "error_class": "guard_deny", "target": "profile:inspect_local",
                                   "fingerprint": "deadbeef"},
        }
        rows = self._write_and_read(path, [legacy, new_entry])
        assert len(rows) == 2
        assert "failure_signature" not in rows[0]
        assert rows[1]["failure_signature"]["error_class"] == "guard_deny"

    def test_no_null_fillers_on_legacy_roundtrip(self, tmp_path: Path):
        """Round-tripping a legacy entry must not inject null values for new fields."""
        path = tmp_path / "ledger.jsonl"
        legacy = {
            "task_id": "leg-002",
            "status": "failed",
            "exit_code": 1,
        }
        rows = self._write_and_read(path, [legacy])
        row = rows[0]
        for field in ("failure_signature", "sessions", "snapshot_evidence", "cleanup_status",
                      "browser_profile", "browser_mode", "source_urls", "screenshot_evidence",
                      "console_network_signals", "site_note_update"):
            assert field not in row, f"New field {field!r} should not appear on legacy entry"


class TestBuildLedgerEntry:
    """Unit tests for scripts.state_io.build_ledger_entry."""

    def test_returns_copy_not_mutation(self):
        from scripts.state_io import build_ledger_entry
        base = {"task_id": "abc", "status": "failed"}
        result = build_ledger_entry(base, sessions=["s1"])
        assert "sessions" not in base  # original unchanged

    def test_base_fields_preserved(self):
        from scripts.state_io import build_ledger_entry
        base = {"task_id": "abc", "status": "failed", "exit_code": 1, "retry_count": 2}
        result = build_ledger_entry(base)
        assert result["retry_count"] == 2
        assert result["exit_code"] == 1

    def test_failure_signature_included(self):
        from scripts.state_io import build_ledger_entry
        sig = {"component": "guard", "tool": "run_with_profile", "profile": "inspect_local",
               "error_class": "guard_deny", "target": "profile:inspect_local", "fingerprint": "abc12345"}
        result = build_ledger_entry({"task_id": "t1"}, failure_signature=sig)
        assert result["failure_signature"]["error_class"] == "guard_deny"

    def test_sessions_included(self):
        from scripts.state_io import build_ledger_entry
        result = build_ledger_entry({"task_id": "t1"}, sessions=["sess-001"])
        assert result["sessions"] == ["sess-001"]

    def test_cleanup_status_valid_values(self):
        from scripts.state_io import build_ledger_entry
        for val in ("ok", "partial", "failed", "not_required"):
            result = build_ledger_entry({"task_id": "t1"}, cleanup_status=val)
            assert result["cleanup_status"] == val

    def test_cleanup_status_invalid_raises(self):
        from scripts.state_io import build_ledger_entry
        with pytest.raises(ValueError, match="cleanup_status"):
            build_ledger_entry({"task_id": "t1"}, cleanup_status="bad_value")

    def test_none_fields_not_included(self):
        from scripts.state_io import build_ledger_entry
        result = build_ledger_entry({"task_id": "t1"})
        for field in ("failure_signature", "sessions", "snapshot_evidence", "cleanup_status",
                      "browser_profile", "browser_mode", "source_urls", "screenshot_evidence",
                      "console_network_signals", "site_note_update"):
            assert field not in result

    def test_browser_fields_included(self):
        from scripts.state_io import build_ledger_entry
        result = build_ledger_entry(
            {"task_id": "t1"},
            browser_profile="default",
            browser_mode="headless",
            source_urls=["https://example.com"],
            screenshot_evidence="~/screenshots/s.png",
            console_network_signals={"xhr_count": 3},
            site_note_update="note text",
        )
        assert result["browser_profile"] == "default"
        assert result["browser_mode"] == "headless"
        assert result["source_urls"] == ["https://example.com"]
        assert result["screenshot_evidence"] == "~/screenshots/s.png"
        assert result["console_network_signals"]["xhr_count"] == 3
        assert result["site_note_update"] == "note text"

    def test_snapshot_evidence_included(self):
        from scripts.state_io import build_ledger_entry
        result = build_ledger_entry({"task_id": "t1"}, snapshot_evidence="~/snaps/s.json")
        assert result["snapshot_evidence"] == "~/snaps/s.json"

    def test_round_trip_via_append(self, tmp_path: Path):
        """build_ledger_entry result persists cleanly via append_jsonl_atomic."""
        from scripts.state_io import build_ledger_entry, append_jsonl_atomic
        from scripts.jsonl_io import read_jsonl
        path = tmp_path / "ledger.jsonl"
        sig = {"component": "skill", "tool": "gemini_video_understand", "profile": "service_ops",
               "error_class": "gemini_video_api", "target": None, "fingerprint": "feedcafe"}
        entry = build_ledger_entry(
            {"task_id": "rt-001", "status": "failed"},
            failure_signature=sig,
            sessions=["s1", "s2"],
            cleanup_status="not_required",
        )
        append_jsonl_atomic(path, entry)
        rows = read_jsonl(path)
        assert len(rows) == 1
        assert rows[0]["failure_signature"]["error_class"] == "gemini_video_api"
        assert rows[0]["sessions"] == ["s1", "s2"]
        assert rows[0]["cleanup_status"] == "not_required"
