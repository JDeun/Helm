"""Unit tests for :mod:`scripts.jsonl_io`.

The R1 sweep added a shared JSONL reader to replace four bespoke
implementations. R2 I5 extended :func:`read_jsonl` with an opt-in
``tail`` argument and added :func:`tail_jsonl` for callers that only
need a trailing window. These tests pin the contract.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.jsonl_io import iter_jsonl, read_jsonl, tail_jsonl


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")


def test_read_jsonl_without_tail_matches_iter_jsonl(tmp_path: Path) -> None:
    path = tmp_path / "log.jsonl"
    rows = [{"i": i} for i in range(50)]
    _write_jsonl(path, rows)
    via_read = read_jsonl(path)
    via_iter = list(iter_jsonl(path))
    assert via_read == via_iter
    assert via_read == rows


def test_read_jsonl_tail_returns_last_n_in_chronological_order(tmp_path: Path) -> None:
    path = tmp_path / "log.jsonl"
    rows = [{"i": i} for i in range(200)]
    _write_jsonl(path, rows)
    tail = read_jsonl(path, tail=10)
    assert len(tail) == 10
    assert tail[0]["i"] == 190
    assert tail[-1]["i"] == 199


def test_read_jsonl_tail_larger_than_file_returns_all(tmp_path: Path) -> None:
    path = tmp_path / "log.jsonl"
    rows = [{"i": i} for i in range(5)]
    _write_jsonl(path, rows)
    assert read_jsonl(path, tail=1000) == rows


def test_read_jsonl_tail_zero_returns_empty(tmp_path: Path) -> None:
    path = tmp_path / "log.jsonl"
    _write_jsonl(path, [{"i": 1}])
    assert read_jsonl(path, tail=0) == []


def test_read_jsonl_tail_negative_returns_empty(tmp_path: Path) -> None:
    path = tmp_path / "log.jsonl"
    _write_jsonl(path, [{"i": 1}])
    assert read_jsonl(path, tail=-3) == []


def test_read_jsonl_tail_missing_file_returns_empty(tmp_path: Path) -> None:
    assert read_jsonl(tmp_path / "no_such.jsonl", tail=5) == []


def test_tail_jsonl_handles_multi_chunk_files(tmp_path: Path) -> None:
    """Force the chunk loop to iterate by using a small chunk_size."""
    path = tmp_path / "log.jsonl"
    rows = [{"i": i, "pad": "x" * 32} for i in range(200)]
    _write_jsonl(path, rows)
    # chunk_size=128 forces many iterations.
    tail = tail_jsonl(path, 50, chunk_size=128)
    assert len(tail) == 50
    assert tail[0]["i"] == 150
    assert tail[-1]["i"] == 199


def test_tail_jsonl_first_line_when_n_equals_file_length(tmp_path: Path) -> None:
    """Edge case: when n == row count the BOF-boundary leftover must be returned."""
    path = tmp_path / "log.jsonl"
    rows = [{"i": i} for i in range(10)]
    _write_jsonl(path, rows)
    tail = tail_jsonl(path, 10, chunk_size=8)
    assert len(tail) == 10
    assert tail[0]["i"] == 0
    assert tail[-1]["i"] == 9


def test_tail_jsonl_skips_malformed_lines_silently(tmp_path: Path) -> None:
    path = tmp_path / "log.jsonl"
    with path.open("w", encoding="utf-8") as fh:
        fh.write('{"i": 1}\n')
        fh.write("not json\n")
        fh.write('{"i": 2}\n')
        fh.write("\n")  # blank line
        fh.write('{"i": 3}\n')
    tail = read_jsonl(path, tail=10)
    assert [row["i"] for row in tail] == [1, 2, 3]


def test_tail_jsonl_handles_trailing_newline(tmp_path: Path) -> None:
    path = tmp_path / "log.jsonl"
    with path.open("w", encoding="utf-8") as fh:
        fh.write('{"i": 1}\n')
        fh.write('{"i": 2}\n')
        fh.write("\n")  # blank trailing line
    tail = read_jsonl(path, tail=5)
    assert [row["i"] for row in tail] == [1, 2]


def test_tail_jsonl_empty_file_returns_empty(tmp_path: Path) -> None:
    path = tmp_path / "empty.jsonl"
    path.write_text("", encoding="utf-8")
    assert tail_jsonl(path, 5) == []


def test_tail_jsonl_single_line_exceeds_chunk_size(tmp_path: Path) -> None:
    """A JSONL row larger than ``chunk_size`` must still be returned.

    Regression for R4 M1: previously the backwards-scan dropped any
    line whose byte length exceeded ``chunk_size`` because no newline
    appeared in the current chunk and the partial bytes were
    discarded instead of accumulated for the next iteration.
    """
    path = tmp_path / "log.jsonl"
    # Write three rows; the middle and last rows each exceed the chunk
    # window so the backwards scan must accumulate across at least two
    # iterations before seeing a newline.
    payloads = [
        {"i": 1, "blob": "a" * 32},
        {"i": 2, "blob": "b" * 4096},
        {"i": 3, "blob": "c" * 4096},
    ]
    with path.open("w", encoding="utf-8") as fh:
        for entry in payloads:
            fh.write(json.dumps(entry, sort_keys=True) + "\n")

    # chunk_size deliberately smaller than the larger lines so they
    # cross multiple iterations of the backwards scan.
    tail = tail_jsonl(path, 3, chunk_size=512)
    assert [row["i"] for row in tail] == [1, 2, 3]
    assert tail[1]["blob"] == "b" * 4096
    assert tail[2]["blob"] == "c" * 4096

    # And the trailing window (n=2) must also include the over-sized
    # last row — historically the bug surfaced as a missing tail entry.
    tail2 = tail_jsonl(path, 2, chunk_size=512)
    assert [row["i"] for row in tail2] == [2, 3]


def test_tail_jsonl_single_giant_line(tmp_path: Path) -> None:
    """One row much larger than chunk_size still parses (BOF edge)."""
    path = tmp_path / "log.jsonl"
    huge = {"i": 1, "blob": "x" * 20000}
    with path.open("w", encoding="utf-8") as fh:
        fh.write(json.dumps(huge, sort_keys=True) + "\n")
    tail = tail_jsonl(path, 5, chunk_size=256)
    assert len(tail) == 1
    assert tail[0]["i"] == 1
    assert tail[0]["blob"] == "x" * 20000
