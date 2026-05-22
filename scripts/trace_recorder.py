"""Trace recorder for Helm harness task runs.

Records a structured trace of a single task execution so that failed
runs can be analysed and (in a future task) re-played.  The schema
mirrors the SmallCode Phase 5 trace format with additions for Helm's
profile / skill routing.

Actual re-execution of tool calls is **out of scope** for this module.
:mod:`scripts.trace_replay` handles printing a human-readable replay
plan; automated re-execution is a future deliverable.

Public API
----------
* :func:`start_trace`           — create an empty trace object.
* :func:`record_tool_call`      — append a tool-call entry.
* :func:`record_changed_file`   — append a changed-file path (deduplicating).
* :func:`record_validation_gate`— append a validation-gate result.
* :func:`set_failure_signature` — attach a structured failure signature.
* :func:`set_outcome`           — set outcome, replay hint, skill candidate.
* :func:`save_trace`            — atomically write the trace to disk.
* :func:`load_trace`            — load a previously saved trace.

Schema (all keys always present on a saved trace):
::

    {
      "taskId":         "<str>",
      "startedAt":      "<iso8601>",
      "profile":        "<str>",
      "skill":          "<str | null>",
      "inputSummary":   "<str>",
      "toolSequence":   [
          {
            "name":          "<str>",
            "purpose":       "<str>",
            "args":          {},
            "durationMs":    0,
            "status":        "success|failure",
            "resultSummary": null
          }
      ],
      "changedFiles":    [],
      "validationGates": [{"name": "<str>", "status": "<str>"}],
      "failureSignature": null,
      "outcome":         "completed|failed|aborted|null",
      "replayHint":      null,
      "skillCandidate":  false
    }

Canonical traces directory
--------------------------
``~/.openclaw/workspace/.openclaw/traces/``

Override by setting the ``OPENCLAW_TRACES_DIR`` environment variable.
Tests should use the ``tmp_path`` pytest fixture instead.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts.time_helpers import utc_now_iso  # noqa: E402

__all__ = [
    "default_traces_dir",
    "start_trace",
    "record_tool_call",
    "record_changed_file",
    "record_validation_gate",
    "set_failure_signature",
    "set_outcome",
    "save_trace",
    "load_trace",
]

# ---------------------------------------------------------------------------
# Canonical traces directory
# ---------------------------------------------------------------------------

def default_traces_dir() -> Path:
    """Return the canonical traces directory, honouring ``OPENCLAW_TRACES_DIR``.

    The env value is expanded with :py:meth:`Path.expanduser` so values like
    ``~/my/traces`` resolve relative to the current user's home directory
    rather than creating a literal ``~`` directory.
    """
    env = os.environ.get("OPENCLAW_TRACES_DIR")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".openclaw" / "workspace" / ".openclaw" / "traces"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def start_trace(
    task_id: str,
    profile: str,
    skill: str | None,
    input_summary: str,
) -> dict:
    """Create and return a new, empty trace object.

    Parameters
    ----------
    task_id:       Stable identifier for the task (UUID or slug).
    profile:       Execution profile name (e.g. ``"service_ops"``).
    skill:         Skill name driving the task, or ``None``.
    input_summary: Short human-readable description of the task input.

    Returns
    -------
    dict
        A fully initialised trace with empty sequences and ``outcome`` set
        to ``None``.
    """
    return {
        "taskId": task_id,
        "startedAt": utc_now_iso(),
        "profile": profile,
        "skill": skill,
        "inputSummary": input_summary,
        "toolSequence": [],
        "changedFiles": [],
        "validationGates": [],
        "failureSignature": None,
        "outcome": None,
        "replayHint": None,
        "skillCandidate": False,
    }


def record_tool_call(
    trace: dict,
    name: str,
    purpose: str,
    args: dict,
    duration_ms: int,
    status: str,
    result_summary: str | None = None,
) -> None:
    """Append a tool-call entry to *trace["toolSequence"]*.

    Parameters
    ----------
    trace:          A trace object returned by :func:`start_trace`.
    name:           Tool name (e.g. ``"Bash"``).
    purpose:        Human-readable description of why the tool was called.
    args:           Arguments dict passed to the tool.
    duration_ms:    Wall-clock duration of the call in milliseconds.
    status:         ``"success"`` or ``"failure"``.
    result_summary: Optional short description of the result.
    """
    trace["toolSequence"].append(
        {
            "name": name,
            "purpose": purpose,
            "args": args,
            "durationMs": duration_ms,
            "status": status,
            "resultSummary": result_summary,
        }
    )


def record_changed_file(trace: dict, path: str) -> None:
    """Append *path* to *trace["changedFiles"]*, deduplicating.

    Parameters
    ----------
    trace: A trace object returned by :func:`start_trace`.
    path:  Absolute or relative path of the file that was changed.
    """
    if path not in trace["changedFiles"]:
        trace["changedFiles"].append(path)


def record_validation_gate(trace: dict, name: str, status: str) -> None:
    """Append a validation-gate result to *trace["validationGates"]*.

    Parameters
    ----------
    trace:  A trace object returned by :func:`start_trace`.
    name:   Gate name (e.g. ``"pytest"``, ``"type-check"``).
    status: Gate status string (e.g. ``"passed"``, ``"failed"``, ``"skipped"``).
    """
    trace["validationGates"].append({"name": name, "status": status})


def set_failure_signature(trace: dict, sig: dict) -> None:
    """Attach a structured failure signature to *trace*.

    Parameters
    ----------
    trace: A trace object returned by :func:`start_trace`.
    sig:   Failure signature dict (e.g. from :func:`scripts.failure_signature.signature`).
    """
    trace["failureSignature"] = sig


def set_outcome(
    trace: dict,
    outcome: str,
    replay_hint: str | None = None,
    skill_candidate: bool = False,
) -> None:
    """Set the final outcome fields on *trace*.

    Parameters
    ----------
    trace:           A trace object returned by :func:`start_trace`.
    outcome:         One of ``"completed"``, ``"failed"``, ``"aborted"``.
    replay_hint:     Optional human-readable suggestion for how to replay.
    skill_candidate: Whether this run is a candidate for skill extraction.
    """
    trace["outcome"] = outcome
    trace["replayHint"] = replay_hint
    trace["skillCandidate"] = skill_candidate


def save_trace(trace: dict, traces_dir: Path) -> Path:
    """Atomically write *trace* as JSON to *traces_dir/<taskId>.json*.

    The write is atomic: the JSON is first written to a temporary file in
    the same directory, then renamed into place with :func:`os.replace`.

    Parameters
    ----------
    trace:      A trace object returned by :func:`start_trace`.
    traces_dir: Directory in which to store the trace file.  Created if
                it does not exist.

    Returns
    -------
    pathlib.Path
        The final path of the written trace file.
    """
    traces_dir = Path(traces_dir)
    traces_dir.mkdir(parents=True, exist_ok=True)

    dest = traces_dir / f"{trace['taskId']}.json"
    payload = json.dumps(trace, indent=2, ensure_ascii=False)

    # Atomic write: tmp file in the same directory → rename.
    fd, tmp_path = tempfile.mkstemp(dir=traces_dir, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
        os.replace(tmp_path, dest)
    except Exception:
        # Clean up the temp file if anything goes wrong before the rename.
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

    return dest


def load_trace(traces_dir: Path, task_id: str) -> dict:
    """Load and return the trace for *task_id* from *traces_dir*.

    Parameters
    ----------
    traces_dir: Directory that contains the trace files.
    task_id:    Task identifier (must match the ``taskId`` in the saved file).

    Returns
    -------
    dict
        The parsed trace object.

    Raises
    ------
    FileNotFoundError
        If no trace file exists for *task_id*.
    json.JSONDecodeError
        If the file exists but is not valid JSON.
    """
    path = Path(traces_dir) / f"{task_id}.json"
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)
