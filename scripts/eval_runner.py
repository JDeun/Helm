#!/usr/bin/env python3
"""Eval runner for the Helm/OpenClaw agent reliability eval suite (Forge後補 D).

Runs one or all reliability eval scenarios and produces a structured
PASS/FAIL result per scenario.

Usage
-----
    python3 scripts/eval_runner.py --scenario 1
    python3 scripts/eval_runner.py --scenario all
    python3 scripts/eval_runner.py --all

Output (JSON to stdout, one object per run):
    {
      "scenario_id": "1",
      "name": "inspect_only_no_file_creation",
      "expected": "guard denies write under inspect_local; no artifact created",
      "actual": "PASS" | "<exception class>: <message>",
      "status": "PASS" | "FAIL",
      "evidence_path": "<tmp evidence directory or null>"
    }

The runner delegates to pytest for actual test execution and captures the
result; it does NOT re-implement the test logic.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# ---------------------------------------------------------------------------
# Scenario registry
# ---------------------------------------------------------------------------

_SCENARIOS: dict[str, dict[str, str]] = {
    "1": {
        "name": "inspect_only_no_file_creation",
        "description": (
            "guard denies write under inspect_local; no artifact created"
        ),
        "test_path": "tests/eval/test_scenario_1_inspect_only_no_file_creation.py",
    },
    "2": {
        "name": "save_request_persists_artifact",
        "description": (
            "workspace_edit save action writes artifact to disk; "
            "is_finalized=True after all steps complete"
        ),
        "test_path": "tests/eval/test_scenario_2_save_request_persists_artifact.py",
    },
    "3": {
        "name": "recovered_context_survives_compaction",
        "description": (
            "active_unhandled recovered message survives context compaction; "
            "unhandled_recovered_messages still returns the entry"
        ),
        "test_path": "tests/eval/test_scenario_3_recovered_context_survives_compaction.py",
    },
    "4": {
        "name": "external_side_effect_requires_approval",
        "description": (
            "send without record_approval raises; "
            "after record_approval the call proceeds and is logged"
        ),
        "test_path": "tests/eval/test_scenario_4_external_side_effect_requires_approval.py",
    },
    "5": {
        "name": "compaction_no_false_complete",
        "description": (
            "is_finalized=False after compaction when a required step is missing"
        ),
        "test_path": "tests/eval/test_scenario_5_compaction_no_false_complete.py",
    },
    "6": {
        "name": "partial_completion_not_reported_as_complete",
        "description": (
            "task with 2/3 steps done + raise: ledger outcome!='completed', "
            "completed_steps has 2 entries, is_finalized=False"
        ),
        "test_path": "tests/eval/test_scenario_6_partial_completion_not_reported_as_complete.py",
    },
}


# ---------------------------------------------------------------------------
# Runner helpers
# ---------------------------------------------------------------------------


def _run_scenario(scenario_id: str) -> dict:
    """Run a single scenario via pytest and return a structured result dict."""
    meta = _SCENARIOS[scenario_id]
    test_path = ROOT / meta["test_path"]

    evidence_dir = tempfile.mkdtemp(prefix=f"eval-scenario-{scenario_id}-")

    # Run pytest in a subprocess so failures don't abort the runner.
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        str(test_path),
        "-v",
        "--tb=short",
        "--no-header",
        f"--basetemp={evidence_dir}",
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=str(ROOT),
        )
        passed = proc.returncode == 0
        actual = "PASS" if passed else f"FAIL: {proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else 'see evidence'}"
    except Exception as exc:  # noqa: BLE001
        passed = False
        actual = f"{type(exc).__name__}: {exc}"

    return {
        "scenario_id": scenario_id,
        "name": meta["name"],
        "expected": meta["description"],
        "actual": "PASS" if passed else actual,
        "status": "PASS" if passed else "FAIL",
        "evidence_path": evidence_dir,
    }


def _run_all() -> list[dict]:
    results = []
    for sid in sorted(_SCENARIOS, key=lambda k: int(k)):
        results.append(_run_scenario(sid))
    return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Run Helm reliability eval scenarios and emit structured PASS/FAIL results.",
    )
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--scenario",
        metavar="ID",
        help=(
            "Scenario ID to run (1-6), or 'all' to run every scenario. "
            "Example: --scenario 3"
        ),
    )
    group.add_argument(
        "--all",
        action="store_true",
        default=False,
        help="Run all scenarios (equivalent to --scenario all).",
    )
    p.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Emit output as JSON (default: human-readable summary).",
    )
    return p


def _human_line(result: dict) -> str:
    status_icon = "PASS" if result["status"] == "PASS" else "FAIL"
    return (
        f"[{status_icon}] scenario {result['scenario_id']}: "
        f"{result['name']} — {result['actual']}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.all or (args.scenario and args.scenario.lower() == "all"):
        results = _run_all()
    else:
        sid = args.scenario
        if sid not in _SCENARIOS:
            print(
                f"Unknown scenario ID {sid!r}. Valid IDs: {sorted(_SCENARIOS)}",
                file=sys.stderr,
            )
            return 2
        results = [_run_scenario(sid)]

    if args.json:
        print(json.dumps(results, indent=2, ensure_ascii=False))
    else:
        any_fail = False
        for r in results:
            print(_human_line(r))
            if r["status"] != "PASS":
                any_fail = True
                print(f"   evidence: {r['evidence_path']}")
        total = len(results)
        passed = sum(1 for r in results if r["status"] == "PASS")
        print(f"\n{passed}/{total} scenarios passed.")
        if any_fail:
            return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
