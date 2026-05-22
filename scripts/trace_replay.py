"""Trace replay CLI for Helm harness task runs.

Loads a previously saved trace and prints a human-readable replay plan
so that a failed run can be analysed and manually (or semi-automatically)
re-attempted.

**Actual re-execution of tool calls is out of scope for this module.**
The tool calls are printed for inspection only; automated replay is a
future deliverable.

Usage
-----
::

    python3 scripts/trace_replay.py --task-id <id> [--traces-dir <path>] [--dry-run]

Options
-------
``--task-id``    (required) The task ID to replay (must match a saved trace file).
``--traces-dir`` (optional) Override the traces directory.
                 Defaults to ``OPENCLAW_TRACES_DIR`` env var or
                 ``~/.openclaw/workspace/.openclaw/traces/``.
``--dry-run``    Print the replay plan without executing anything (default).
``--help``       Show this help message and exit.

The ``--dry-run`` flag is the **only** supported mode in this version.
Actual re-execution will be added in a later task.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts.trace_recorder import default_traces_dir, load_trace  # noqa: E402


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _divider(char: str = "-", width: int = 60) -> str:
    return char * width


def _section(title: str) -> str:
    return f"\n{_divider()}\n{title}\n{_divider()}"


def _format_tool_call(index: int, entry: dict) -> str:
    lines = [
        f"  [{index + 1}] {entry.get('name', '?')} — {entry.get('purpose', '')}",
        f"      status   : {entry.get('status', '?')}",
        f"      duration : {entry.get('durationMs', 0)} ms",
    ]
    args = entry.get("args")
    if args:
        try:
            args_str = json.dumps(args, ensure_ascii=False)
        except (TypeError, ValueError):
            args_str = str(args)
        # Truncate very long args for readability.
        if len(args_str) > 200:
            args_str = args_str[:197] + "..."
        lines.append(f"      args     : {args_str}")
    result_summary = entry.get("resultSummary")
    if result_summary:
        lines.append(f"      result   : {result_summary}")
    return "\n".join(lines)


def _format_gate(entry: dict) -> str:
    return f"  {entry.get('name', '?')} : {entry.get('status', '?')}"


def print_replay_plan(trace: dict) -> None:
    """Print a human-readable replay plan for *trace* to stdout."""
    print(_section("TRACE REPLAY PLAN"))
    print(f"  task id      : {trace.get('taskId', '?')}")
    print(f"  started at   : {trace.get('startedAt', '?')}")
    print(f"  profile      : {trace.get('profile', '?')}")
    print(f"  skill        : {trace.get('skill') or '(none)'}")
    print(f"  input        : {trace.get('inputSummary', '?')}")

    # Tool sequence
    tool_seq = trace.get("toolSequence") or []
    print(_section(f"TOOL SEQUENCE  ({len(tool_seq)} call(s))"))
    if tool_seq:
        for i, entry in enumerate(tool_seq):
            print(_format_tool_call(i, entry))
    else:
        print("  (no tool calls recorded)")

    # Changed files
    changed = trace.get("changedFiles") or []
    print(_section(f"CHANGED FILES  ({len(changed)} file(s))"))
    if changed:
        for path in changed:
            print(f"  {path}")
    else:
        print("  (no file changes recorded)")

    # Validation gates
    gates = trace.get("validationGates") or []
    print(_section(f"VALIDATION GATES  ({len(gates)} gate(s))"))
    if gates:
        for gate in gates:
            print(_format_gate(gate))
    else:
        print("  (no validation gates recorded)")

    # Failure signature
    sig = trace.get("failureSignature")
    if sig:
        print(_section("FAILURE SIGNATURE"))
        try:
            print(json.dumps(sig, indent=4, ensure_ascii=False))
        except (TypeError, ValueError):
            print(f"  {sig}")

    # Outcome
    print(_section("OUTCOME"))
    print(f"  outcome          : {trace.get('outcome') or '(unset)'}")
    hint = trace.get("replayHint")
    if hint:
        print(f"  replay hint      : {hint}")
    print(f"  skill candidate  : {trace.get('skillCandidate', False)}")

    print(f"\n{_divider()}")
    print("NOTE: Actual re-execution is not implemented in this version.")
    print("      Review the plan above and re-run the task manually.")
    print(_divider())


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="trace_replay.py",
        description=(
            "Print a human-readable replay plan for a saved Helm harness trace.\n\n"
            "Actual re-execution of tool calls is out of scope for this version; "
            "--dry-run (the default) prints only."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python3 scripts/trace_replay.py --task-id abc-123\n"
            "  python3 scripts/trace_replay.py --task-id abc-123 --traces-dir /tmp/traces\n"
            "  python3 scripts/trace_replay.py --task-id abc-123 --dry-run\n"
        ),
    )
    parser.add_argument(
        "--task-id",
        required=True,
        metavar="ID",
        help="Task ID to replay (must match a saved trace file).",
    )
    parser.add_argument(
        "--traces-dir",
        default=None,
        metavar="PATH",
        help=(
            "Directory containing trace files.  "
            "Defaults to OPENCLAW_TRACES_DIR env var or "
            "~/.openclaw/workspace/.openclaw/traces/."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Print the replay plan without executing anything (default: True).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.  Returns an exit code (0 = success, non-zero = error)."""
    parser = build_parser()
    args = parser.parse_args(argv)

    # Expand `~` in the CLI arg so users can pass ``--traces-dir ~/x`` without
    # creating a literal `~` directory.
    traces_dir = Path(args.traces_dir).expanduser() if args.traces_dir else default_traces_dir()

    try:
        trace = load_trace(traces_dir, args.task_id)
    except FileNotFoundError:
        print(
            f"Error: no trace found for task '{args.task_id}' in {traces_dir}",
            file=sys.stderr,
        )
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"Error loading trace: {exc}", file=sys.stderr)
        return 1

    print_replay_plan(trace)
    return 0


if __name__ == "__main__":
    sys.exit(main())
