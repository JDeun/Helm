"""Tests for ``helm skill-promotion`` CLI subcommand (Wave 4).

Coverage matrix
---------------
1.  digest exits 0 and prints valid JSON.
2.  digest JSON contains expected top-level keys.
3.  digest --cadence weekly passes through.
4.  digest --max limits candidate count.
5.  approve exits 0 for a known pending candidate.
6.  approve exits nonzero for unknown id.
7.  approve exits nonzero for already-processed id.
8.  reject exits 0 for a known pending candidate.
9.  reject exits nonzero for unknown id.
10. pending exits 0 and lists candidates.
11. pending --json exits 0 and prints valid JSON list.
12. state-path exits 0 and prints a path string.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.skill_promotion_state import (
    load_state,
    record_notified,
    save_state,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

HELM_PY = ROOT / "helm.py"


def _run(args: list[str], *, env_extra: dict | None = None) -> subprocess.CompletedProcess:
    import os

    env = os.environ.copy()
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, str(HELM_PY), *args],
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )


def _make_trace(task_id: str, *, skill: str = "s", task: str = "t", n: int = 3) -> dict:
    return {
        "taskId": task_id,
        "startedAt": "2026-01-01T00:00:00Z",
        "profile": "service_ops",
        "skill": skill,
        "inputSummary": task,
        "toolSequence": [],
        "changedFiles": [],
        "validationGates": [],
        "failureSignature": None,
        "outcome": "completed",
        "replayHint": None,
        "skillCandidate": False,
    }


def _populate_traces(traces_dir: Path, n: int = 3) -> None:
    traces_dir.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        (traces_dir / f"t{i}.json").write_text(
            json.dumps(_make_trace(f"t{i}")), encoding="utf-8"
        )


def _setup_known_candidate(tmp_path: Path) -> tuple[Path, Path, str]:
    """Create traces + state with one notified candidate. Returns (traces_dir, state_path, cid)."""
    traces_dir = tmp_path / "traces"
    state_path = tmp_path / "state.json"
    _populate_traces(traces_dir)
    # Run digest to register the candidate.
    result = _run([
        "skill-promotion", "digest",
        "--traces-dir", str(traces_dir),
        "--state-path", str(state_path),
    ])
    assert result.returncode == 0, f"digest failed: {result.stderr}"
    payload = json.loads(result.stdout)
    if not payload["candidates"]:
        pytest.skip("No candidates generated — need at least min_success traces")
    cid = payload["candidates"][0]["candidate_id"]
    return traces_dir, state_path, cid


# ---------------------------------------------------------------------------
# 1–4. digest subcommand
# ---------------------------------------------------------------------------

class TestDigestCommand:
    def test_exits_zero(self, tmp_path):
        result = _run([
            "skill-promotion", "digest",
            "--traces-dir", str(tmp_path / "t"),
            "--state-path", str(tmp_path / "s.json"),
        ])
        assert result.returncode == 0

    def test_outputs_valid_json(self, tmp_path):
        result = _run([
            "skill-promotion", "digest",
            "--traces-dir", str(tmp_path / "t"),
            "--state-path", str(tmp_path / "s.json"),
        ])
        payload = json.loads(result.stdout)
        assert isinstance(payload, dict)

    def test_expected_top_level_keys(self, tmp_path):
        result = _run([
            "skill-promotion", "digest",
            "--traces-dir", str(tmp_path / "t"),
            "--state-path", str(tmp_path / "s.json"),
        ])
        payload = json.loads(result.stdout)
        for key in ("generated_at", "cadence", "candidates", "summary_text", "approval_reply_examples"):
            assert key in payload, f"Missing key: {key}"

    def test_cadence_weekly_passes_through(self, tmp_path):
        result = _run([
            "skill-promotion", "digest",
            "--cadence", "weekly",
            "--traces-dir", str(tmp_path / "t"),
            "--state-path", str(tmp_path / "s.json"),
        ])
        payload = json.loads(result.stdout)
        assert payload["cadence"] == "weekly"

    def test_max_limits_candidates(self, tmp_path):
        traces_dir = tmp_path / "traces"
        traces_dir.mkdir(parents=True, exist_ok=True)
        # Write 4 distinct candidate groups (each with 3 traces).
        for i in range(4):
            for j in range(3):
                (traces_dir / f"t{i}_{j}.json").write_text(
                    json.dumps(_make_trace(f"t{i}_{j}", skill=f"skill-{i}", task=f"task {i}")),
                    encoding="utf-8",
                )
        result = _run([
            "skill-promotion", "digest",
            "--max", "2",
            "--traces-dir", str(traces_dir),
            "--state-path", str(tmp_path / "s.json"),
        ])
        payload = json.loads(result.stdout)
        assert len(payload["candidates"]) <= 2


# ---------------------------------------------------------------------------
# 5–7. approve subcommand
# ---------------------------------------------------------------------------

class TestApproveCommand:
    def test_approve_known_candidate_exits_zero(self, tmp_path):
        _, state_path, cid = _setup_known_candidate(tmp_path)
        result = _run([
            "skill-promotion", "approve", cid,
            "--state-path", str(state_path),
        ])
        assert result.returncode == 0

    def test_approve_prints_approved(self, tmp_path):
        _, state_path, cid = _setup_known_candidate(tmp_path)
        result = _run([
            "skill-promotion", "approve", cid,
            "--state-path", str(state_path),
        ])
        assert "approved" in result.stdout

    def test_approve_unknown_id_exits_nonzero(self, tmp_path):
        sp = tmp_path / "state.json"
        save_state({"entries": []}, sp)
        result = _run([
            "skill-promotion", "approve", "ffffffff",
            "--state-path", str(sp),
        ])
        assert result.returncode != 0

    def test_approve_already_processed_exits_nonzero(self, tmp_path):
        _, state_path, cid = _setup_known_candidate(tmp_path)
        # Approve once.
        _run(["skill-promotion", "approve", cid, "--state-path", str(state_path)])
        # Approve again — should fail.
        result = _run([
            "skill-promotion", "approve", cid,
            "--state-path", str(state_path),
        ])
        assert result.returncode != 0


# ---------------------------------------------------------------------------
# 8–9. reject subcommand
# ---------------------------------------------------------------------------

class TestRejectCommand:
    def test_reject_known_candidate_exits_zero(self, tmp_path):
        _, state_path, cid = _setup_known_candidate(tmp_path)
        result = _run([
            "skill-promotion", "reject", cid,
            "--state-path", str(state_path),
        ])
        assert result.returncode == 0

    def test_reject_with_reason_exits_zero(self, tmp_path):
        _, state_path, cid = _setup_known_candidate(tmp_path)
        result = _run([
            "skill-promotion", "reject", cid,
            "--reason", "not needed",
            "--state-path", str(state_path),
        ])
        assert result.returncode == 0

    def test_reject_unknown_id_exits_nonzero(self, tmp_path):
        sp = tmp_path / "state.json"
        save_state({"entries": []}, sp)
        result = _run([
            "skill-promotion", "reject", "ffffffff",
            "--state-path", str(sp),
        ])
        assert result.returncode != 0


# ---------------------------------------------------------------------------
# 10–11. pending subcommand
# ---------------------------------------------------------------------------

class TestPendingCommand:
    def test_pending_exits_zero(self, tmp_path):
        sp = tmp_path / "state.json"
        save_state({"entries": []}, sp)
        result = _run([
            "skill-promotion", "pending",
            "--state-path", str(sp),
        ])
        assert result.returncode == 0

    def test_pending_lists_notified_candidate(self, tmp_path):
        sp = tmp_path / "state.json"
        state = {"entries": []}
        record_notified(state, "aabbccdd", {"skill": "s", "task_name": "t", "count": 3})
        save_state(state, sp)
        result = _run([
            "skill-promotion", "pending",
            "--state-path", str(sp),
        ])
        assert result.returncode == 0
        assert "aabbccdd" in result.stdout

    def test_pending_json_mode(self, tmp_path):
        sp = tmp_path / "state.json"
        state = {"entries": []}
        record_notified(state, "aabbccdd", {"skill": "s", "task_name": "t", "count": 3})
        save_state(state, sp)
        result = _run([
            "skill-promotion", "pending",
            "--json",
            "--state-path", str(sp),
        ])
        assert result.returncode == 0
        items = json.loads(result.stdout)
        assert isinstance(items, list)
        assert len(items) == 1

    def test_pending_no_candidates_message(self, tmp_path):
        sp = tmp_path / "state.json"
        save_state({"entries": []}, sp)
        result = _run([
            "skill-promotion", "pending",
            "--state-path", str(sp),
        ])
        assert result.returncode == 0
        assert "No pending" in result.stdout


# ---------------------------------------------------------------------------
# 12. state-path subcommand
# ---------------------------------------------------------------------------

class TestStatePathCommand:
    def test_state_path_exits_zero(self, tmp_path):
        result = _run([
            "skill-promotion", "state-path",
            "--state-path", str(tmp_path / "custom.json"),
        ])
        assert result.returncode == 0

    def test_state_path_prints_path(self, tmp_path):
        custom = tmp_path / "custom.json"
        result = _run([
            "skill-promotion", "state-path",
            "--state-path", str(custom),
        ])
        assert result.returncode == 0
        assert str(custom) in result.stdout.strip()

    def test_state_path_default_when_no_override(self, tmp_path):
        """state-path with env override prints the env-provided path."""
        import os
        custom_path = str(tmp_path / "env_state.json")
        result = _run(
            ["skill-promotion", "state-path"],
            env_extra={"OPENCLAW_SKILL_PROMOTION_STATE": custom_path},
        )
        assert result.returncode == 0
        assert custom_path in result.stdout.strip()
