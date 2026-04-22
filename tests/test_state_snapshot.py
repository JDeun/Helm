from __future__ import annotations

import tempfile
import unittest
import json
from pathlib import Path
from unittest.mock import patch

import helm
from scripts import run_with_profile
from scripts.state_snapshot import latest_snapshot_path, write_state_snapshot


class StateSnapshotTests(unittest.TestCase):
    def test_write_state_snapshot_creates_markdown_artifact(self) -> None:
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
            self.assertTrue(snapshot_path.exists())
            content = snapshot_path.read_text(encoding="utf-8")
            self.assertIn("[STATE_SNAPSHOT]", content)
            self.assertIn("- task_id: task-1", content)
            self.assertIn("- objective: demo task", content)
            self.assertIn("harness=", content)
            self.assertIn("skill_relevance", content)

    def test_finalize_task_links_state_snapshot_in_ledger_entry(self) -> None:
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
            self.assertIn('"state_snapshot"', content)
            latest = latest_snapshot_path(state_root)
            self.assertIsNotNone(latest)
            self.assertTrue(latest.name.startswith("20260422T120000Z-task-2"))

    def test_helm_context_payload_reads_latest_snapshot(self) -> None:
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

            self.assertIn("[STATE_SNAPSHOT]", payload["content"])
            self.assertEqual(payload["snapshot"]["path"], meta["path"])


if __name__ == "__main__":
    unittest.main()
