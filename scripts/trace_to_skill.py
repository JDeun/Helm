"""Trace-to-skill candidate loop for Helm harness.

Analyses a directory of trace JSON files (written by
:mod:`scripts.trace_recorder`) and surfaces:

* **Skill scaffold candidates** — tasks that succeeded ≥N times and may
  warrant a formal skill.
* **Skill repair candidates** — tasks that failed with the same fingerprint
  ≥M times and likely need a skill fix.
* **Compound runner candidates** — tasks where the same tool sequence repeats
  ≥K times and could be codified into a compound runner.

Public API
----------
* :func:`load_recent_traces`         — load JSON files, newest-first.
* :func:`skill_scaffold_candidates`  — find scaffold opportunities.
* :func:`skill_repair_candidates`    — find repair opportunities.
* :func:`compound_runner_candidates` — find repeated tool sequences.

CLI
---
::

    python3 scripts/trace_to_skill.py \\
        [--traces-dir <path>] \\
        [--min-success N] \\
        [--min-failures M] \\
        [--min-rep K]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

__all__ = [
    "load_recent_traces",
    "skill_scaffold_candidates",
    "skill_repair_candidates",
    "compound_runner_candidates",
]

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalize_task_name(name: str) -> str:
    """Casefold and collapse whitespace for grouping purposes."""
    return " ".join(name.casefold().split())


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_recent_traces(traces_dir: Path, limit: int = 200) -> list[dict]:
    """Load trace JSON files from *traces_dir*, newest first by ``startedAt``.

    Parameters
    ----------
    traces_dir:
        Directory containing ``*.json`` trace files written by
        :func:`scripts.trace_recorder.save_trace`.
    limit:
        Maximum number of traces to return (default 200).

    Returns
    -------
    list[dict]
        Parsed trace objects sorted by ``startedAt`` descending.  Returns an
        empty list if *traces_dir* does not exist.
    """
    traces_dir = Path(traces_dir)
    if not traces_dir.exists():
        return []

    traces: list[dict] = []
    for path in traces_dir.glob("*.json"):
        try:
            with path.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                traces.append(data)
        except (json.JSONDecodeError, OSError):
            # Skip malformed or unreadable files silently.
            continue

    traces.sort(key=lambda t: t.get("startedAt") or "", reverse=True)
    return traces[:limit]


def skill_scaffold_candidates(
    traces: list[dict],
    min_success: int = 3,
) -> list[dict]:
    """Find (skill, task_name) pairs with ≥ *min_success* successful traces.

    Groups traces by ``(skill, task_name_normalized)`` and returns only groups
    whose success count meets or exceeds *min_success*.

    Parameters
    ----------
    traces:
        List of trace objects (as returned by :func:`load_recent_traces`).
    min_success:
        Minimum number of successful completions required (default 3).

    Returns
    -------
    list[dict]
        Each entry has keys:

        ``skill``
            The skill name (may be ``None``).
        ``task_name``
            Representative (non-normalised) task name from the first match.
        ``count``
            Number of successful traces in the group.
        ``sample_trace_ids``
            Up to three most-recent ``taskId`` values from the group.
    """
    # Groups: key → {"task_name": str, "trace_ids": [str], "count": int}
    groups: dict[tuple, dict] = {}

    for trace in traces:
        if trace.get("outcome") != "completed":
            continue
        skill = trace.get("skill")
        raw_name = trace.get("inputSummary") or ""
        norm = _normalize_task_name(raw_name)
        key = (skill, norm)
        if key not in groups:
            groups[key] = {"task_name": raw_name, "trace_ids": [], "count": 0}
        groups[key]["count"] += 1
        groups[key]["trace_ids"].append(trace.get("taskId") or "")

    results: list[dict] = []
    for (skill, _norm), info in groups.items():
        if info["count"] >= min_success:
            sample = info["trace_ids"][-3:]
            results.append(
                {
                    "skill": skill,
                    "task_name": info["task_name"],
                    "count": info["count"],
                    "sample_trace_ids": sample,
                }
            )

    results.sort(key=lambda r: r["count"], reverse=True)
    return results


def skill_repair_candidates(
    traces: list[dict],
    min_failures: int = 2,
) -> list[dict]:
    """Find repeated failure fingerprints with ≥ *min_failures* traces.

    Groups traces by ``failureSignature.fingerprint`` and returns groups
    whose failure count meets or exceeds *min_failures*.

    Parameters
    ----------
    traces:
        List of trace objects (as returned by :func:`load_recent_traces`).
    min_failures:
        Minimum number of failures with the same fingerprint (default 2).

    Returns
    -------
    list[dict]
        Each entry has keys:

        ``fingerprint``
            The failure fingerprint string.
        ``count``
            Number of traces sharing this fingerprint.
        ``example_skill``
            Skill name from the most-recent matching trace (may be ``None``).
        ``sample_trace_ids``
            Up to three ``taskId`` values from the group.
    """
    groups: dict[str, dict] = {}

    for trace in traces:
        sig = trace.get("failureSignature")
        if not isinstance(sig, dict):
            continue
        fingerprint = sig.get("fingerprint")
        if not fingerprint:
            continue
        if fingerprint not in groups:
            groups[fingerprint] = {
                "example_skill": trace.get("skill"),
                "trace_ids": [],
                "count": 0,
            }
        groups[fingerprint]["count"] += 1
        groups[fingerprint]["trace_ids"].append(trace.get("taskId") or "")

    results: list[dict] = []
    for fingerprint, info in groups.items():
        if info["count"] >= min_failures:
            sample = info["trace_ids"][-3:]
            results.append(
                {
                    "fingerprint": fingerprint,
                    "count": info["count"],
                    "example_skill": info["example_skill"],
                    "sample_trace_ids": sample,
                }
            )

    results.sort(key=lambda r: r["count"], reverse=True)
    return results


def compound_runner_candidates(
    traces: list[dict],
    min_repetitions: int = 3,
) -> list[dict]:
    """Find (skill, tool-sequence) pairs repeated ≥ *min_repetitions* times.

    Sequences of length ≤ 1 are excluded because a single-tool sequence does
    not warrant a compound runner.

    Parameters
    ----------
    traces:
        List of trace objects (as returned by :func:`load_recent_traces`).
    min_repetitions:
        Minimum number of times a sequence must repeat (default 3).

    Returns
    -------
    list[dict]
        Each entry has keys:

        ``skill``
            The skill name (may be ``None``).
        ``sequence``
            Tuple of tool name strings.
        ``count``
            Number of traces sharing this (skill, sequence).
        ``sample_trace_ids``
            Up to three ``taskId`` values from the group.
    """
    groups: dict[tuple, dict] = {}

    for trace in traces:
        skill = trace.get("skill")
        tool_seq = trace.get("toolSequence") or []
        names = tuple(entry.get("name") or "" for entry in tool_seq if isinstance(entry, dict))
        if len(names) <= 1:
            continue
        key = (skill, names)
        if key not in groups:
            groups[key] = {"trace_ids": [], "count": 0}
        groups[key]["count"] += 1
        groups[key]["trace_ids"].append(trace.get("taskId") or "")

    results: list[dict] = []
    for (skill, names), info in groups.items():
        if info["count"] >= min_repetitions:
            sample = info["trace_ids"][-3:]
            results.append(
                {
                    "skill": skill,
                    "sequence": list(names),
                    "count": info["count"],
                    "sample_trace_ids": sample,
                }
            )

    results.sort(key=lambda r: r["count"], reverse=True)
    return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Analyse trace history to surface skill candidates."
    )
    parser.add_argument(
        "--traces-dir",
        default=None,
        help="Path to the traces directory (default: ~/.openclaw/workspace/.openclaw/traces).",
    )
    parser.add_argument(
        "--min-success",
        type=int,
        default=3,
        metavar="N",
        help="Minimum successes for scaffold candidates (default: 3).",
    )
    parser.add_argument(
        "--min-failures",
        type=int,
        default=2,
        metavar="M",
        help="Minimum failures for repair candidates (default: 2).",
    )
    parser.add_argument(
        "--min-rep",
        type=int,
        default=3,
        metavar="K",
        help="Minimum repetitions for compound runner candidates (default: 3).",
    )
    return parser


def _default_traces_dir() -> Path:
    import os

    env = os.environ.get("OPENCLAW_TRACES_DIR")
    if env:
        return Path(env)
    return Path.home() / ".openclaw" / "workspace" / ".openclaw" / "traces"


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    traces_dir = Path(args.traces_dir) if args.traces_dir else _default_traces_dir()
    traces = load_recent_traces(traces_dir)

    print(f"=== Trace-to-Skill Candidates ({len(traces)} traces from {traces_dir}) ===\n")

    # --- Scaffold candidates ---
    print("--- Skill Scaffold Candidates (min_success={}) ---".format(args.min_success))
    scaffolds = skill_scaffold_candidates(traces, min_success=args.min_success)
    if scaffolds:
        for item in scaffolds:
            print(
                f"  skill={item['skill']!r}  task={item['task_name']!r}"
                f"  count={item['count']}"
                f"  samples={item['sample_trace_ids']}"
            )
    else:
        print("  (none)")

    # --- Repair candidates ---
    print()
    print("--- Skill Repair Candidates (min_failures={}) ---".format(args.min_failures))
    repairs = skill_repair_candidates(traces, min_failures=args.min_failures)
    if repairs:
        for item in repairs:
            print(
                f"  fingerprint={item['fingerprint']!r}"
                f"  count={item['count']}"
                f"  example_skill={item['example_skill']!r}"
                f"  samples={item['sample_trace_ids']}"
            )
    else:
        print("  (none)")

    # --- Compound runner candidates ---
    print()
    print("--- Compound Runner Candidates (min_rep={}) ---".format(args.min_rep))
    compounds = compound_runner_candidates(traces, min_repetitions=args.min_rep)
    if compounds:
        for item in compounds:
            print(
                f"  skill={item['skill']!r}"
                f"  sequence={item['sequence']}"
                f"  count={item['count']}"
                f"  samples={item['sample_trace_ids']}"
            )
    else:
        print("  (none)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
