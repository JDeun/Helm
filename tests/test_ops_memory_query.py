from __future__ import annotations

import argparse
import tempfile
import unittest
from pathlib import Path

from helm_context import ContextSource
from scripts import ops_memory_query


class OpsMemoryQueryTests(unittest.TestCase):
    def test_read_jsonl_skips_malformed_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "entities.jsonl"
            path.write_text('{"id":"ok-1"}\nnot-json\n', encoding="utf-8")

            rows = ops_memory_query.read_jsonl(path)

            self.assertEqual(rows, [{"id": "ok-1"}])

    def test_load_checkpoint_results_tolerates_invalid_index_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            checkpoint_root = workspace / ".helm" / "checkpoints"
            checkpoint_root.mkdir(parents=True)
            (checkpoint_root / "index.json").write_text("{not-json\n", encoding="utf-8")
            source = ContextSource(
                name="helm-local",
                kind="helm",
                root=workspace,
                state_dir_name=".helm",
            )
            args = argparse.Namespace(query=None, since=None)

            results = list(ops_memory_query.load_checkpoint_results(source, args))

            self.assertEqual(results, [])


if __name__ == "__main__":
    unittest.main()
