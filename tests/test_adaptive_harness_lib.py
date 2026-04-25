from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from scripts import adaptive_harness_lib
from scripts.adaptive_harness_lib import (
    _deep_merge,
    build_hydration_commands,
    infer_file_intake_evidence,
    preflight_payload,
    resolve_skill_contract,
)


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


# ---------------------------------------------------------------------------
# C5: sys.executable in hydration commands
# ---------------------------------------------------------------------------

def test_build_hydration_commands_uses_sys_executable() -> None:
    """build_hydration_commands must use sys.executable, not a hardcoded 'python3'."""
    commands = build_hydration_commands(
        {
            "context": {
                "required": True,
                "query": "test query",
                "include": [],
                "limit": 3,
            }
        }
    )

    assert len(commands) >= 1
    assert commands[0][0] == sys.executable
    assert commands[0][0] != "python3"


def test_build_hydration_commands_failed_include_uses_sys_executable() -> None:
    """The failed-include command must also use sys.executable."""
    commands = build_hydration_commands(
        {
            "context": {
                "required": True,
                "query": "test query",
                "include": [],
                "limit": 3,
                "failed_include": ["network"],
                "failed_limit": 4,
            }
        }
    )

    assert len(commands) == 2
    assert commands[1][0] == sys.executable
    assert commands[1][0] != "python3"


# ---------------------------------------------------------------------------
# H11: _deep_merge helper
# ---------------------------------------------------------------------------

def test_deep_merge_scalar_overlay_wins() -> None:
    base = {"a": 1, "b": 2}
    overlay = {"b": 99, "c": 3}
    result = _deep_merge(base, overlay)
    assert result == {"a": 1, "b": 99, "c": 3}


def test_deep_merge_nested_dict_merges_recursively() -> None:
    base = {"browser_work": {"required": False, "required_fields": ["reason", "evidence"]}}
    overlay = {"browser_work": {"required": True}}
    result = _deep_merge(base, overlay)
    # overlay sets required=True; base's required_fields must be preserved
    assert result["browser_work"]["required"] is True
    assert result["browser_work"]["required_fields"] == ["reason", "evidence"]


def test_deep_merge_does_not_mutate_base() -> None:
    base = {"x": {"y": 1}}
    overlay = {"x": {"z": 2}}
    result = _deep_merge(base, overlay)
    assert "z" not in base["x"]
    assert result["x"] == {"y": 1, "z": 2}


def test_deep_merge_list_is_replaced_not_extended() -> None:
    base = {"allowed_profiles": ["inspect_local"]}
    overlay = {"allowed_profiles": ["workspace_edit"]}
    result = _deep_merge(base, overlay)
    assert result["allowed_profiles"] == ["workspace_edit"]


def test_resolve_skill_contract_preserves_base_required_fields_on_partial_override() -> None:
    """H11 regression: a contract that overrides browser_work.required without
    specifying required_fields must still inherit the base default required_fields."""
    fake_contracts = {
        "my-skill": {
            "browser_work": {"required": True},
        }
    }
    with patch.object(adaptive_harness_lib, "load_skill_contract_manifests", return_value=fake_contracts):
        contract = resolve_skill_contract("my-skill")

    assert contract["browser_work"]["required"] is True
    # The base provides ["reason", "evidence", "api_reusable", "next_action"]
    assert "reason" in contract["browser_work"]["required_fields"]
    assert "evidence" in contract["browser_work"]["required_fields"]
