#!/usr/bin/env python3
"""Per-file-type validation gate runner.

Provides utilities for determining and executing the appropriate validation
commands for a given file, based on its extension and the gate policy defined
in *references/gate_policy.json*.

Design constraints:
- No external dependencies beyond stdlib + subprocess.
- Gate commands are split into argv lists (no shell=True) to avoid shell
  injection vulnerabilities.
- The `runner` parameter in `run_gates` allows test-time injection without
  actual subprocess spawning.
"""
from __future__ import annotations

import copy
import json
import subprocess
from pathlib import Path
from typing import Any, Callable

# ---------------------------------------------------------------------------
# Policy location
# ---------------------------------------------------------------------------

_POLICY_PATH = Path(__file__).parent.parent / "references" / "gate_policy.json"

# Module-level cache.
_cached_gate_policy: dict[str, list[str]] | None = None

# Map file extensions (lowercase, without leading dot) to gate_policy.json keys.
_EXT_TO_LANG: dict[str, str] = {
    "py": "python",
    "js": "javascript",
    "mjs": "javascript",
    "ts": "typescript",
    "tsx": "typescript",
    "json": "json",
    "md": "markdown",
}

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _load_gate_policy(*, reload: bool = False) -> dict[str, list[str]]:
    """Load and cache the gate policy from *references/gate_policy.json*.

    Raises
    ------
    FileNotFoundError
        If the policy file is missing; re-raised with a clear diagnostic
        message rather than being silently swallowed.
    """
    global _cached_gate_policy
    if _cached_gate_policy is None or reload:
        if not _POLICY_PATH.exists():
            raise FileNotFoundError(
                f"Gate policy file not found: {_POLICY_PATH}. "
                "Ensure references/gate_policy.json exists in the repository root."
            )
        with _POLICY_PATH.open("r", encoding="utf-8") as fh:
            _cached_gate_policy = json.load(fh)
    return copy.deepcopy(_cached_gate_policy)


def _ext_for_path(path: str) -> str:
    """Return the lowercase extension (without leading dot) for *path*."""
    suffix = Path(path).suffix
    if not suffix:
        return ""
    return suffix.lstrip(".").lower()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def gates_for_path(path: str) -> list[str]:
    """Return the list of validation command strings for *path*.

    Extension detection is case-insensitive.  Unknown extensions produce an
    empty list (no gate).  Markdown produces an empty list intentionally (gate
    placeholder with no automatic check).

    Each returned string has the ``{path}`` placeholder substituted with the
    actual *path* argument.

    Parameters
    ----------
    path:
        Absolute or relative file path whose extension drives language
        detection.

    Returns
    -------
    list[str]
        Zero or more command strings, each ready for further splitting into
        an argv list via :func:`shlex.split` or equivalent.
    """
    ext = _ext_for_path(path)
    lang = _EXT_TO_LANG.get(ext)
    if lang is None:
        return []

    policy = _load_gate_policy()
    templates: list[str] = policy.get(lang, [])
    return [tmpl.replace("{path}", path) for tmpl in templates]


def run_gates(
    path: str,
    runner: Callable[..., Any] = subprocess.run,
) -> list[dict[str, Any]]:
    """Run all validation gates for *path* and return structured results.

    Each gate command is split into an argv list (no shell expansion) and
    passed to *runner*.  This keeps execution safe from injection and makes
    unit testing straightforward via the *runner* injection point.

    Parameters
    ----------
    path:
        File path to validate.
    runner:
        Callable with the same signature as :func:`subprocess.run`.  Defaults
        to the real ``subprocess.run``; pass a mock in tests to avoid actual
        subprocess creation.

    Returns
    -------
    list[dict]
        One entry per gate, each with keys:
        ``cmd`` (list[str]), ``returncode`` (int), ``stdout`` (str),
        ``stderr`` (str).
    """
    results: list[dict[str, Any]] = []

    for cmd_str in gates_for_path(path):
        argv = cmd_str.split()
        completed = runner(
            argv,
            capture_output=True,
            text=True,
        )
        results.append(
            {
                "cmd": argv,
                "returncode": completed.returncode,
                "stdout": completed.stdout,
                "stderr": completed.stderr,
            }
        )

    return results
