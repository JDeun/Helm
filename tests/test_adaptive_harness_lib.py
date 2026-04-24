from __future__ import annotations

import json
import tempfile
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from scripts import adaptive_harness_lib
from scripts.adaptive_harness_lib import build_hydration_commands, infer_file_intake_evidence, preflight_payload


def test_build_hydration_commands_omits_empty_include_flags() -> None:
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

    assert len(commands) == 1
    assert "--include" not in commands[0]
    assert commands[0][-2:] == ["--limit", "4"]


def test_latest_task_entry_skips_malformed_jsonl_rows() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "task-ledger.jsonl"
        path.write_text('{"task_id":"task-1","status":"completed"}\nnot-json\n', encoding="utf-8")

        with patch.object(adaptive_harness_lib, "TASK_LEDGER", path):
            entry = adaptive_harness_lib.latest_task_entry("task-1")

        assert entry is not None
        assert entry["task_id"] == "task-1"


def test_infer_file_intake_evidence_resolves_workspace_relative_paths() -> None:
    with TemporaryDirectory() as tmpdir:
        workspace = Path(tmpdir)
        sample = workspace / "docs" / "sample.txt"
        sample.parent.mkdir(parents=True)
        sample.write_text("hello world\n", encoding="utf-8")
        entry = {"command": ["python3", "ingest.py", "docs/sample.txt"]}

        with patch.object(adaptive_harness_lib, "WORKSPACE", workspace):
            payload = infer_file_intake_evidence(entry)

        assert payload is not None
        assert payload["path"] == str(sample)
        assert payload["inferred"]


def test_preflight_records_divergence_and_direct_skill_fallback() -> None:
    payload = preflight_payload(
        skill=None,
        profile="inspect_local",
        model=None,
        model_tier=None,
        task_name="architecture options",
        runtime_target=None,
        user_request="새 Helm 설계 방향을 비교해줘",
        context_confirmed=False,
        command=["true"],
        browser_evidence=None,
        retrieval_evidence=None,
        file_intake_evidence=None,
        route_decision=None,
    )

    assert payload["ok"]
    assert payload["interaction_workflow"]["mode"] == "diverge_then_converge"
    assert payload["skill_relevance"]["verdict"] == "direct"


def test_preflight_blocks_poor_skill_match() -> None:
    payload = preflight_payload(
        skill="travel-ops-ko",
        profile="inspect_local",
        model=None,
        model_tier=None,
        task_name="household ledger",
        runtime_target=None,
        user_request="가계부 항목을 정리해",
        context_confirmed=False,
        command=["true"],
        browser_evidence=None,
        retrieval_evidence=None,
        file_intake_evidence=None,
        route_decision=None,
    )

    assert not payload["ok"]
    assert payload["skill_relevance"]["verdict"] == "poor"
    assert "skill_relevance" in {check["name"] for check in payload["checks"] if not check["ok"]}


def test_skill_relevance_policy_can_relax_poor_match_blocking() -> None:
    policy = {
        "validation": {
            "skill_relevance_min_score": 0,
            "skill_relevance_block_verdicts": [],
            "skill_relevance_warn_verdicts": ["poor"],
        }
    }
    with patch.object(adaptive_harness_lib, "load_harness_policy", return_value=policy):
        payload = preflight_payload(
            skill="travel-ops-ko",
            profile="inspect_local",
            model=None,
            model_tier=None,
            task_name="household ledger",
            runtime_target=None,
            user_request="가계부 항목을 정리해",
            context_confirmed=False,
            command=["true"],
            browser_evidence=None,
            retrieval_evidence=None,
            file_intake_evidence=None,
            route_decision=None,
        )

    failed = {check["name"] for check in payload["checks"] if not check["ok"]}
    assert "skill_relevance" not in failed
    assert payload["skill_relevance"]["policy"]["min_score"] == 0
