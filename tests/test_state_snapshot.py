from __future__ import annotations

import tempfile
import json
from pathlib import Path
from unittest.mock import patch

import helm
from scripts import run_with_profile
from scripts.state_snapshot import latest_snapshot_path, write_state_snapshot


def test_write_state_snapshot_creates_markdown_artifact() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        workspace = Path(tmpdir)
        state_root = workspace / ".helm"
        task = {
            "task_id": "task-1",
            "task_name": "demo task",
            "profile": "risky_edit",
            "runtime_backend": "local",
            "command_preview": "python3 demo.py",
            "status": "completed",
            "finished_at": "2026-04-22T12:00:00+00:00",
            "memory_capture": {"finalization_status": "capture_planned", "recommended_layers": ["notes"]},
            "meta": {
                "harness": {
                    "interaction_workflow": {"mode": "converge"},
                    "skill_relevance": {"verdict": "strong", "score": 60},
                }
            },
        }

        meta = write_state_snapshot(task, workspace=workspace, state_root=state_root)

        snapshot_path = workspace / meta["path"]
        assert snapshot_path.exists()
        content = snapshot_path.read_text(encoding="utf-8")
        assert "[STATE_SNAPSHOT]" in content
        assert "- task_id: task-1" in content
        assert "- objective: demo task" in content
        assert "harness=" in content
        assert "skill_relevance" in content


def test_finalize_task_links_state_snapshot_in_ledger_entry() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        workspace = Path(tmpdir)
        state_root = workspace / ".helm"
        ledger = state_root / "task-ledger.jsonl"
        task = {
            "task_id": "task-2",
            "task_name": "finalize snapshot",
            "profile": "local",
            "runtime_backend": "local",
            "command_preview": "true",
            "status": "completed",
            "finished_at": "2026-04-22T12:00:00+00:00",
            "meta": {},
        }

        with patch.object(run_with_profile, "WORKSPACE", workspace), patch.object(
            run_with_profile, "STATE_ROOT", state_root
        ), patch.object(run_with_profile, "TASK_LEDGER", ledger):
            run_with_profile.finalize_task(task)

        content = ledger.read_text(encoding="utf-8")
        assert '"state_snapshot"' in content
        latest = latest_snapshot_path(state_root)
        assert latest is not None
        assert latest.name.startswith("20260422T120000Z-task-2")


def test_helm_context_payload_reads_latest_snapshot() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        workspace = Path(tmpdir)
        state_root = workspace / ".helm"
        meta = write_state_snapshot(
            {
                "task_id": "task-3",
                "task_name": "context snapshot",
                "profile": "local",
                "status": "completed",
                "command_preview": "true",
                "finished_at": "2026-04-22T12:00:00+00:00",
            },
            workspace=workspace,
            state_root=state_root,
        )
        (state_root / "task-ledger.jsonl").write_text(
            '{"task_id":"task-3","state_snapshot":'
            + json.dumps(meta)
            + "}\n",
            encoding="utf-8",
        )

        payload = helm.build_state_snapshot_payload(workspace)

        assert "[STATE_SNAPSHOT]" in payload["content"]
        assert payload["snapshot"]["path"] == meta["path"]
