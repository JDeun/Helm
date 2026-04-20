from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import adaptive_harness_lib
from scripts.adaptive_harness_lib import build_hydration_commands


class AdaptiveHarnessLibTests(unittest.TestCase):
    def test_build_hydration_commands_omits_empty_include_flags(self) -> None:
        commands = build_hydration_commands(
            {
                "context": {
                    "required": True,
                    "query": "router",
                    "include": [],
                    "limit": 4,
                    "failed_include": [],
                }
            }
        )

        self.assertEqual(len(commands), 1)
        self.assertNotIn("--include", commands[0])
        self.assertEqual(commands[0][-2:], ["--limit", "4"])

    def test_latest_task_entry_skips_malformed_jsonl_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "task-ledger.jsonl"
            path.write_text('{"task_id":"task-1","status":"completed"}\nnot-json\n', encoding="utf-8")

            with patch.object(adaptive_harness_lib, "TASK_LEDGER", path):
                entry = adaptive_harness_lib.latest_task_entry("task-1")

            assert entry is not None
            self.assertEqual(entry["task_id"], "task-1")


if __name__ == "__main__":
    unittest.main()
