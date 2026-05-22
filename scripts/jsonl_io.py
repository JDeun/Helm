#!/usr/bin/env python3
"""Centralized JSONL reader helpers.

Background
----------
Prior to this module the codebase carried at least four independent
``read_jsonl`` implementations plus a generator ``iter_jsonl`` — see
the 2026-05-21 Helm full review §Duplication Findings. They differed
in *how* malformed lines were handled:

* ``commands/__init__.read_jsonl`` and ``scripts.memory_ops._read_jsonl``
  emit warnings to stderr via a ``_warn_parse_failure`` helper.
* ``scripts.skill_capture.read_jsonl`` and
  ``scripts.ops_memory_query.iter_jsonl`` emit warnings inline.
* ``scripts.ops_daily_report.read_jsonl`` and
  ``scripts.hitl_decision_patterns.read_jsonl`` swallow malformed lines
  silently.

The inconsistency causes some scripts to silently lose entries while
others warn loudly on the same input. This module exposes a single
generator-based reader plus a list-materializing wrapper so callers may
opt into a uniform warning policy.

Public API
----------
* :func:`iter_jsonl` — generator that yields one dict per valid line,
  warning to stderr on malformed lines and non-object payloads.
* :func:`read_jsonl` — materializing wrapper (``list(iter_jsonl(...))``)
  for callers that prefer the legacy slurp signature.
* :func:`iter_jsonl_silent` — same as :func:`iter_jsonl` but silent on
  malformed lines (for ranking/scoring loops where warnings would be
  noise).

All variants treat a missing path as an empty stream, never raising.
"""

from __future__ import annotations

import io
import json
import os
import sys
from pathlib import Path
from typing import Iterable, Iterator

__all__ = [
    "iter_jsonl",
    "iter_jsonl_silent",
    "read_jsonl",
    "tail_jsonl",
]


def _warn(path: Path, detail: str) -> None:
    print(
        f"warning: ignoring malformed state file {path}: {detail}",
        file=sys.stderr,
    )


def iter_jsonl(path: Path) -> Iterator[dict]:
    """Yield one ``dict`` per non-empty, well-formed JSONL line.

    Malformed lines and non-object payloads emit a single-line warning
    to stderr (matching :func:`commands.read_jsonl`'s legacy behavior)
    and are skipped. A missing path yields no entries.
    """
    if not path.exists():
        return
    try:
        handle = open(path, "r", encoding="utf-8")
    except OSError as exc:
        _warn(path, str(exc))
        return
    with handle:
        for lineno, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                _warn(path, f"line {lineno}: {exc}")
                continue
            if not isinstance(payload, dict):
                _warn(path, f"line {lineno}: expected JSON object")
                continue
            yield payload


def iter_jsonl_silent(path: Path) -> Iterator[dict]:
    """Same as :func:`iter_jsonl` but silently skips malformed lines.

    Use this only where warnings would be noise (e.g. inner ranking
    loops in ops_memory_query). For state-file readers prefer
    :func:`iter_jsonl` so corruption surfaces early.
    """
    if not path.exists():
        return
    try:
        handle = open(path, "r", encoding="utf-8")
    except OSError:
        return
    with handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                yield payload


def read_jsonl(path: Path, tail: int | None = None) -> list[dict]:
    """Materializing wrapper around :func:`iter_jsonl`.

    When ``tail`` is ``None`` (default) the entire file is parsed and the
    behavior matches the legacy ``list(iter_jsonl(path))`` form. When
    ``tail`` is a positive integer the function returns only the last
    ``tail`` well-formed JSON object lines, parsed via a backwards
    byte-chunk scan that avoids slurping the whole file. Callers that
    only need a trailing window (e.g. ``helm status``,
    ``helm dashboard``) should pass ``tail`` to keep memory bounded on
    multi-MB ledgers.

    Order in the returned list is chronological (oldest of the tail
    first), matching ``entries[-tail:]`` semantics.
    """
    if tail is None:
        return list(iter_jsonl(path))
    if tail <= 0:
        return []
    return tail_jsonl(path, tail)


def tail_jsonl(path: Path, n: int, *, chunk_size: int = 65536) -> list[dict]:
    """Return up to ``n`` trailing well-formed JSONL object lines.

    Reads the file in fixed-size chunks from the end backwards and
    parses lines bottom-up, stopping when ``n`` valid object lines have
    been collected. Malformed lines are skipped silently (same policy
    as :func:`iter_jsonl_silent` since the tail use cases are
    display-oriented).

    The returned list is in chronological order (oldest first), so a
    caller can replace ``entries = list(iter_jsonl(p)); entries[-n:]``
    with ``tail_jsonl(p, n)`` and get the same ordering.
    """
    if not path.exists() or n <= 0:
        return []
    try:
        fh = open(path, "rb")
    except OSError:
        return []
    collected: list[dict] = []
    leftover = b""
    with fh:
        try:
            fh.seek(0, os.SEEK_END)
        except OSError:
            return []
        position = fh.tell()
        while position > 0 and len(collected) < n:
            read_size = min(chunk_size, position)
            position -= read_size
            try:
                fh.seek(position)
                chunk = fh.read(read_size)
            except OSError:
                break
            buffer = chunk + leftover
            # The first segment may be a partial line if we have not yet
            # reached BOF; defer it to the next iteration.
            newline_index = buffer.find(b"\n")
            if position > 0 and newline_index != -1:
                leftover = buffer[: newline_index + 1]
                remaining = buffer[newline_index + 1 :]
            elif position > 0 and newline_index == -1:
                # The entire buffer is the tail of a line whose start is
                # in an earlier (un-read) chunk. Defer the whole buffer
                # as leftover and emit nothing this iteration. This
                # handles single lines larger than ``chunk_size``: the
                # window naturally grows across iterations until a
                # newline (or BOF) is found.
                leftover = buffer
                remaining = b""
            else:
                # position == 0 — BOF reached; whatever sits in buffer
                # is the start of the file and must be emitted now.
                leftover = b""
                remaining = buffer
            # Split into lines and iterate in reverse.
            lines = remaining.split(b"\n")
            for raw in reversed(lines):
                stripped = raw.strip()
                if not stripped:
                    continue
                try:
                    payload = json.loads(stripped.decode("utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
                if not isinstance(payload, dict):
                    continue
                collected.append(payload)
                if len(collected) >= n:
                    break
        # Consume the deferred leftover from the start-of-file boundary.
        if len(collected) < n and leftover:
            stripped = leftover.strip()
            if stripped:
                try:
                    payload = json.loads(stripped.decode("utf-8"))
                    if isinstance(payload, dict):
                        collected.append(payload)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    pass
    collected.reverse()
    return collected
