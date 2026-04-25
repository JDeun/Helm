# tests/test_state_io.py
from __future__ import annotations

import json
import sys
import warnings
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
