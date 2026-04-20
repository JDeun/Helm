from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


class MemoryOpsTests(unittest.TestCase):
    def create_workspace(self, root: Path) -> None:
        (root / ".helm" / "checkpoints").mkdir(parents=True)
        (root / "references").mkdir()
        (root / "skills").mkdir()
        (root / "skill_drafts").mkdir()
        (root / "memory").mkdir()
        (root / ".helm" / "context_sources.json").write_text('{"sources": []}\n', encoding="utf-8")
        (root / ".helm" / "task-ledger.jsonl").write_text("", encoding="utf-8")
        (root / ".helm" / "command-log.jsonl").write_text("", encoding="utf-8")
        (root / ".helm" / "checkpoints" / "index.json").write_text("[]\n", encoding="utf-8")
        (root / "references" / "execution_profiles.json").write_text(
            json.dumps({"profiles": {"inspect_local": {}, "workspace_edit": {}, "service_ops": {}}}),
            encoding="utf-8",
        )
        (root / "references" / "skill_profile_policies.json").write_text(
            json.dumps({"skills": {}}),
            encoding="utf-8",
        )
        (root / "references" / "skill-capture-template.md").write_text("# Template\n", encoding="utf-8")
        (root / "references" / "skill-contract-template.json").write_text("{}\n", encoding="utf-8")

    def run_cli(self, workspace: Path, *args: str) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["HELM_WORKSPACE"] = str(workspace)
        return subprocess.run(
            [sys.executable, str(REPO_ROOT / "helm.py"), *args],
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )

    def test_memory_op_history_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self.create_workspace(root)

            op = self.run_cli(
                root,
                "memory",
                "op",
                "write",
                "--subject",
                "router policy",
                "--scope",
                "private",
                "--reason",
                "record a write op",
                "--evidence",
                "manual verification",
            )
            self.assertEqual(op.returncode, 0, op.stderr)

            history = self.run_cli(root, "memory", "history", "--json")
            self.assertEqual(history.returncode, 0, history.stderr)
            payload = json.loads(history.stdout)
            self.assertEqual(payload["count"], 1)
            self.assertEqual(payload["items"][0]["operation"], "write")

    def test_memory_crystallize_persists_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self.create_workspace(root)
            ledger_entry = {
                "task_id": "task-1",
                "task_name": "router fix",
                "profile": "workspace_edit",
                "status": "completed",
                "memory_capture": {
                    "claim_state": {"confidence_hint": "high"},
                    "supersession": {"state": "none", "supersedes_task_ids": []},
                    "review_flags": [],
                    "crystallization": {
                        "question": "What changed?",
                        "action": "Edited router",
                        "result": "router updated",
                        "lesson": "keep policy explicit",
                        "affected_entities": ["skill:router"],
                    },
                },
            }
            (root / ".helm" / "task-ledger.jsonl").write_text(json.dumps(ledger_entry) + "\n", encoding="utf-8")

            result = self.run_cli(root, "memory", "crystallize", "--task-id", "task-1")
            self.assertEqual(result.returncode, 0, result.stderr)

            artifact = root / ".helm" / "crystallized-sessions.jsonl"
            self.assertTrue(artifact.exists())
            rows = [json.loads(line) for line in artifact.read_text(encoding="utf-8").splitlines() if line.strip()]
            self.assertEqual(rows[0]["task_id"], "task-1")

    def test_review_queue_surfaces_partial_and_missing_follow_up(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self.create_workspace(root)
            entries = [
                {
                    "task_id": "task-1",
                    "task_name": "refresh router policy",
                    "profile": "workspace_edit",
                    "status": "completed",
                    "memory_capture": {
                        "relevant": True,
                        "finalization_status": "capture_partial",
                        "claim_state": {"confidence_hint": "low"},
                        "review_flags": [{"type": "truth_resolution_review", "severity": "medium"}],
                        "supersession": {"state": "none", "supersedes_task_ids": []},
                    },
                },
                {
                    "task_id": "task-2",
                    "task_name": "rerun router policy refresh",
                    "profile": "workspace_edit",
                    "status": "completed",
                    "memory_capture": {
                        "relevant": True,
                        "finalization_status": "capture_written",
                        "claim_state": {"confidence_hint": "high"},
                        "review_flags": [],
                        "supersession": {"state": "refreshes_prior_state", "supersedes_task_ids": ["task-1"]},
                    },
                },
            ]
            (root / ".helm" / "task-ledger.jsonl").write_text(
                "\n".join(json.dumps(entry) for entry in entries) + "\n",
                encoding="utf-8",
            )

            result = self.run_cli(root, "memory", "review-queue", "--json")
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["count"], 2)
            self.assertEqual(payload["items"][0]["task_id"], "task-2")
            self.assertIn("missing_crystallization", payload["items"][0]["blockers"])
            self.assertIn("missing_supersede_op", payload["items"][0]["blockers"])
            self.assertIn("finalization=capture_partial", payload["items"][1]["blockers"])


if __name__ == "__main__":
    unittest.main()
