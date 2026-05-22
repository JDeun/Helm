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
        assert payload[0]["metadata"]["ranking"]["field_scores"]["excerpt"] > 0
        assert payload[0]["metadata"]["ranking"]["source_priority"] > 0


def _ranking_args(query: str | None = None, **overrides) -> argparse.Namespace:
    """Build a minimal argparse.Namespace that ``collect_results`` accepts.

    Centralised so multiple ranking tests stay in lock-step with the
    argparse surface defined in ``ops_memory_query.build_parser``.
    """
    base = dict(
        query=query,
        mode=None,
        include=list(ops_memory_query.SOURCE_CHOICES),
        adapter=None,
        limit=20,
        skill=None,
        task_id=None,
        entity=None,
        since=None,
        failed_only=False,
        latest_tasks=False,
        ascending=False,
        explain_ranking=True,
        summary=False,
        json=False,
    )
    base.update(overrides)
    return argparse.Namespace(**base)


def test_collect_results_ranking_query_match_outranks_unmatched(tmp_path: Path) -> None:
    """Items whose excerpt contains the query MUST sort above unrelated items.

    Regression guard for ``collect_results`` field-scoring: removing the
    query_score branch (e.g. by returning ``0`` unconditionally) would
    cause this assertion to fail because both items would be tied on
    source_priority alone.
    """
    workspace = tmp_path / "ws"
    (workspace / ".helm").mkdir(parents=True)
    (workspace / ".helm" / "context_sources.json").write_text('{"sources": []}\n', encoding="utf-8")
    memory = workspace / "memory"
    memory.mkdir()
    (memory / "decision.md").write_text("decision: pick the new ranking implementation\n", encoding="utf-8")
    (memory / "unrelated.md").write_text("breakfast plan\n", encoding="utf-8")

    env = os.environ.copy()
    env["HELM_WORKSPACE"] = str(workspace)
    env["PYTHONPATH"] = str(REPO_ROOT)
    result = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "ops_memory_query.py"),
            "decision",
            "--include",
            "memory",
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
    assert payload, "expected at least one result"
    # The matching record must be first; query_score must dominate the
    # tie-breaker order (total_score is the primary sort key).
    top = payload[0]
    assert "decision" in top["excerpt"].lower()
    assert top["metadata"]["ranking"]["query_score"] > 0


def test_collect_results_ranking_source_priority_breaks_ties(tmp_path: Path) -> None:
    """When two items have equal query_score, the higher source_priority wins.

    ``notes`` (priority 60) outranks ``memory`` (priority 50). This pins
    the priority table so a future refactor cannot accidentally invert
    the ordering.
    """
    workspace = tmp_path / "ws"
    (workspace / ".helm").mkdir(parents=True)
    (workspace / ".helm" / "context_sources.json").write_text('{"sources": []}\n', encoding="utf-8")
    notes_root = workspace / "notes"
    memory_root = workspace / "memory"
    notes_root.mkdir()
    memory_root.mkdir()
    # Both files contain the exact query phrase exactly once on a short line,
    # so their query_score components match. Distinguishing factor is source.
    (notes_root / "topic.md").write_text("alpha\n", encoding="utf-8")
    (memory_root / "topic.md").write_text("alpha\n", encoding="utf-8")

    env = os.environ.copy()
    env["HELM_WORKSPACE"] = str(workspace)
    env["PYTHONPATH"] = str(REPO_ROOT)
    result = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "ops_memory_query.py"),
            "alpha",
            "--include",
            "notes",
            "memory",
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
    sources_in_order = [item["source"] for item in payload]
    # Notes (priority 60) must precede memory (priority 50) when query_score ties.
    assert sources_in_order.index("notes") < sources_in_order.index("memory")


def test_collect_results_limit_truncates(tmp_path: Path) -> None:
    """The ``--limit`` arg must truncate to N items even when many match."""
    workspace = tmp_path / "ws"
    (workspace / ".helm").mkdir(parents=True)
    (workspace / ".helm" / "context_sources.json").write_text('{"sources": []}\n', encoding="utf-8")
    memory = workspace / "memory"
    memory.mkdir()
    for i in range(7):
        (memory / f"hit-{i}.md").write_text("needle present\n", encoding="utf-8")

    env = os.environ.copy()
    env["HELM_WORKSPACE"] = str(workspace)
    env["PYTHONPATH"] = str(REPO_ROOT)
    result = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "ops_memory_query.py"),
            "needle",
            "--include",
            "memory",
            "--limit",
            "3",
            "--json",
        ],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert len(payload) == 3


def test_collect_results_explain_ranking_omitted_by_default(tmp_path: Path) -> None:
    """Without ``--explain-ranking``, ``ranking`` metadata must NOT leak.

    Defends the privacy/UX contract that scoring internals are an opt-in
    surface.
    """
    workspace = tmp_path / "ws"
    (workspace / ".helm").mkdir(parents=True)
    (workspace / ".helm" / "context_sources.json").write_text('{"sources": []}\n', encoding="utf-8")
    memory = workspace / "memory"
    memory.mkdir()
    (memory / "note.md").write_text("token\n", encoding="utf-8")

    env = os.environ.copy()
    env["HELM_WORKSPACE"] = str(workspace)
    env["PYTHONPATH"] = str(REPO_ROOT)
    result = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "ops_memory_query.py"),
            "token",
            "--include",
            "memory",
            "--json",
        ],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload
    for item in payload:
        assert "ranking" not in item["metadata"]


def test_entity_mode_expands_one_hop_ontology_neighbors() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        workspace = Path(tmpdir)
        ontology = workspace / "memory" / "ontology"
        ontology.mkdir(parents=True)
        (workspace / ".helm").mkdir()
        (workspace / ".helm" / "context_sources.json").write_text('{"sources": []}\n', encoding="utf-8")
        (ontology / "entities.jsonl").write_text(
            "\n".join(
                [
                    json.dumps({"id": "project_helm", "type": "project", "properties": {"name": "Helm"}}),
                    json.dumps({"id": "concept_memory", "type": "concept", "properties": {"name": "Memory"}}),
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        (ontology / "relations.jsonl").write_text(
            json.dumps(
                {
                    "from": "project_helm",
                    "to": "concept_memory",
                    "relation_type": "uses",
                    "properties": {"notes": "Helm uses memory context."},
                }
            )
            + "\n",
            encoding="utf-8",
        )
        env = os.environ.copy()
        env["HELM_WORKSPACE"] = str(workspace)
        env["PYTHONPATH"] = str(REPO_ROOT)

        result = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "scripts" / "ops_memory_query.py"),
                "--mode",
                "entity",
                "--entity",
                "project_helm",
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
        kinds = {item["kind"] for item in payload}
        assert "graph-relation" in kinds
        assert "graph-neighbor" in kinds
        graph_items = [item for item in payload if item["metadata"].get("graph_expansion")]
        assert graph_items
        assert all(item["metadata"]["ranking"]["graph_boost"] > 0 for item in graph_items)
