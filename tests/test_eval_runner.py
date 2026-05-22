# tests/test_eval_runner.py
"""Tests for ``scripts/eval_runner.py``.

The runner's exit code is the contract CI relies on. Both human-readable
and JSON output modes must return nonzero when any scenario fails;
otherwise automation silently misses regressions.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _write_runner_stub(workdir: Path, scenario_test_relpath: str) -> Path:
    """Write a vendored eval_runner that points at a single forced-fail test.

    We don't mutate the real ``scripts/eval_runner.py``; instead we copy the
    module under a different name with the ``_SCENARIOS`` registry
    rewritten to register the forced-fail test as scenario ``"fail"``.
    This isolates the test from any future addition or rename of real
    scenarios.
    """
    src = (ROOT / "scripts" / "eval_runner.py").read_text(encoding="utf-8")

    # Replace the _SCENARIOS dict literal with a one-entry registry that
    # points at our forced-fail scenario.
    needle = "_SCENARIOS: dict[str, dict[str, str]] = {"
    assert needle in src, "expected _SCENARIOS dict literal in eval_runner.py"
    head, _, tail = src.partition(needle)
    # Drop the original dict body up to and including its closing brace.
    depth = 1
    i = 0
    while i < len(tail) and depth > 0:
        ch = tail[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        i += 1
    rest = tail[i:]  # everything after the closing brace of the original dict

    replacement = (
        "_SCENARIOS: dict[str, dict[str, str]] = {\n"
        '    "fail": {\n'
        '        "name": "forced_fail",\n'
        '        "description": "forced-fail scenario for exit-code test",\n'
        f'        "test_path": "{scenario_test_relpath}",\n'
        "    },\n"
        "}"
    )

    rewritten = head + replacement + rest
    runner_path = workdir / "stub_runner.py"
    runner_path.write_text(rewritten, encoding="utf-8")
    return runner_path


def test_eval_runner_json_exits_nonzero_on_failure(tmp_path: Path) -> None:
    """`eval_runner.py --json --scenario <id>` must exit nonzero when the
    scenario fails. Without this, CI parsing only stdout would miss
    regressions."""
    # Write a forced-fail scenario test file inside tmp_path/tests/.
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    fail_test = tests_dir / "test_forced_fail.py"
    fail_test.write_text(
        textwrap.dedent(
            """\
            def test_forced_fail():
                assert False, "this scenario is intentionally failing"
            """
        ),
        encoding="utf-8",
    )

    # The runner uses `cwd=ROOT` and constructs the test path relative to
    # ROOT, so we need to give it an absolute test path. The simplest path
    # is to symlink/copy the fail test into a known place under ROOT —
    # but that pollutes the repo. Instead, we patch the _SCENARIOS dict
    # to point at an absolute path on disk by writing a stub runner that
    # imports nothing repo-relative.
    runner_path = _write_runner_stub(tmp_path, str(fail_test.resolve()))

    # Run the stub runner in --json mode against the forced-fail scenario.
    proc = subprocess.run(
        [sys.executable, str(runner_path), "--json", "--scenario", "fail"],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )

    # The stub printed JSON to stdout — parse and confirm the FAIL status.
    assert proc.stdout, f"runner produced no stdout; stderr={proc.stderr!r}"
    payload = json.loads(proc.stdout)
    assert isinstance(payload, list) and len(payload) == 1
    assert payload[0]["status"] == "FAIL", (
        f"scenario should have status=FAIL, got {payload[0]!r}"
    )

    # The exit code must be nonzero — this is the regression we are
    # guarding against.
    assert proc.returncode != 0, (
        f"--json mode must exit nonzero on scenario failure, got "
        f"returncode={proc.returncode}; stdout={proc.stdout!r}"
    )


def test_eval_runner_json_exits_zero_when_all_pass(tmp_path: Path) -> None:
    """Sanity counterpart: when every scenario passes, --json must exit 0."""
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    pass_test = tests_dir / "test_forced_pass.py"
    pass_test.write_text(
        textwrap.dedent(
            """\
            def test_forced_pass():
                assert True
            """
        ),
        encoding="utf-8",
    )

    runner_path = _write_runner_stub(tmp_path, str(pass_test.resolve()))

    proc = subprocess.run(
        [sys.executable, str(runner_path), "--json", "--scenario", "fail"],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )

    assert proc.stdout, f"runner produced no stdout; stderr={proc.stderr!r}"
    payload = json.loads(proc.stdout)
    assert payload[0]["status"] == "PASS"
    assert proc.returncode == 0, (
        f"--json mode must exit 0 when all scenarios pass, got "
        f"returncode={proc.returncode}; stderr={proc.stderr!r}"
    )
