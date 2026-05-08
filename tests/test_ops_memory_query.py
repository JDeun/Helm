from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from helm_context import ContextSource
from scripts import ops_memory_query


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_read_jsonl_skips_malformed_rows() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "entities.jsonl"
        path.write_text('{"id":"ok-1"}\nnot-json\n', encoding="utf-8")

        rows = ops_memory_query.read_jsonl(path)

        assert rows == [{"id": "ok-1"}]


def test_load_checkpoint_results_tolerates_invalid_index_json() -> None:
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

        assert results == []


def test_cli_explain_ranking_includes_score_breakdown() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        workspace = Path(tmpdir)
        (workspace / ".helm").mkdir()
        (workspace / ".helm" / "context_sources.json").write_text('{"sources": []}\n', encoding="utf-8")
        (workspace / "MEMORY.md").write_text("- decision: keep memory policy explicit\n", encoding="utf-8")
        env = os.environ.copy()
        env["HELM_WORKSPACE"] = str(workspace)
        env["PYTHONPATH"] = str(REPO_ROOT)

        result = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "scripts" / "ops_memory_query.py"),
                "decision",
                "--explain-ranking",
                "--json",
            ],
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )

        assert result.returncode == 0, result.stderr
        payload = json.loads(result.stdout)
        assert payload[0]["metadata"]["ranking"]["query_score"] > 0
        assert payload[0]["metadata"]["ranking"]["source_priority"] > 0
