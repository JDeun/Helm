from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest
from unittest.mock import patch

from scripts.memory_capture import (
    _crystallization,
    _retention_profile,
    _review_flags,
    _supersession,
    build_memory_capture_plan,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_task(
    *,
    task_id: str = "task-001",
    task_name: str = "deploy service",
    status: str = "completed",
    profile: str = "service_ops",
    skill: str | None = "my-skill",
    runtime_target: str | None = "prod-server",
    command_preview: str = "helm run deploy",
    finished_at: str = "2024-01-01T12:00:00Z",
    meta: dict | None = None,
    checkpoint_paths: list | None = None,
) -> dict:
    return {
        "task_id": task_id,
        "task_name": task_name,
        "status": status,
        "profile": profile,
        "skill": skill,
        "runtime_target": runtime_target,
        "command_preview": command_preview,
        "finished_at": finished_at,
        "meta": meta or {},
        "checkpoint_paths": checkpoint_paths or [],
    }


# ---------------------------------------------------------------------------
# build_memory_capture_plan — basic completed task
# ---------------------------------------------------------------------------

def test_build_memory_capture_plan_basic_completed_task() -> None:
    task = _make_task(status="completed", profile="service_ops")

    # Patch _recent_final_tasks to return empty so _supersession does nothing
    with patch("scripts.memory_capture._recent_final_tasks", return_value=[]):
        plan = build_memory_capture_plan(task)

    assert plan["relevant"] is True
    assert plan["priority"] == "required"
    assert "operational_state" in plan["event_types"]
    assert "daily_memory" in plan["recommended_layers"]
    assert isinstance(plan["reasons"], list)
    assert len(plan["reasons"]) > 0
    assert plan["finalization_status"] == "capture_planned"


def test_build_memory_capture_plan_irrelevant_task() -> None:
    task = _make_task(
        status="completed",
        profile="inspect_local",
        task_name="check logs",
        command_preview="cat logs.txt",
        skill=None,
        runtime_target=None,
    )

    with patch("scripts.memory_capture._recent_final_tasks", return_value=[]):
        plan = build_memory_capture_plan(task)

    assert plan["relevant"] is False
    assert plan["priority"] == "none"
    assert plan["recommended_layers"] == []
    assert plan["finalization_status"] == "no_capture_needed"


def test_build_memory_capture_plan_high_impact_failure_is_relevant() -> None:
    task = _make_task(status="failed", profile="service_ops")

    with patch("scripts.memory_capture._recent_final_tasks", return_value=[]):
        plan = build_memory_capture_plan(task)

    assert plan["relevant"] is True
    assert any("failure" in r for r in plan["reasons"])


# ---------------------------------------------------------------------------
# build_memory_capture_plan — browser evidence in meta
# ---------------------------------------------------------------------------

def test_build_memory_capture_plan_with_browser_evidence() -> None:
    task = _make_task(
        status="completed",
        profile="inspect_local",
        task_name="check endpoint",
        command_preview="browser snapshot",
        meta={
            "harness": {
                "browser_evidence": {
                    "reason": "needed live DOM",
                    "evidence": "snapshot taken",
                    "api_reusable": False,
                    "next_action": "stop",
                }
            }
        },
    )

    with patch("scripts.memory_capture._recent_final_tasks", return_value=[]):
        plan = build_memory_capture_plan(task)

    assert plan["relevant"] is True
    assert "operational_state" in plan["event_types"]


def test_build_memory_capture_plan_api_reusable_browser_evidence_adds_project_state() -> None:
    task = _make_task(
        status="completed",
        profile="inspect_local",
        task_name="discover api",
        command_preview="browser network trace",
        meta={
            "harness": {
                "browser_evidence": {
                    "reason": "found network endpoint",
                    "evidence": "endpoint /api/v2/data",
                    "api_reusable": True,
                    "next_action": "promote to retrieval route",
                }
            }
        },
    )

    with patch("scripts.memory_capture._recent_final_tasks", return_value=[]):
        plan = build_memory_capture_plan(task)

    assert "project_state" in plan["event_types"]
    assert "operational_state" in plan["event_types"]


def test_build_memory_capture_plan_has_crystallization_and_retention() -> None:
    task = _make_task(status="completed", profile="service_ops")

    with patch("scripts.memory_capture._recent_final_tasks", return_value=[]):
        plan = build_memory_capture_plan(task)

    assert "crystallization" in plan
    assert "question" in plan["crystallization"]
    assert "action" in plan["crystallization"]
    assert "result" in plan["crystallization"]
    assert "lesson" in plan["crystallization"]

    assert "retention" in plan
    assert "tier" in plan["retention"]
    assert "decay_hint" in plan["retention"]


# ---------------------------------------------------------------------------
# _supersession
# ---------------------------------------------------------------------------

def test_supersession_matching_task_name_gets_high_score() -> None:
    task = _make_task(task_name="deploy service", status="completed")
    prior = _make_task(
        task_id="prior-001",
        task_name="deploy service",
        status="completed",
        finished_at="2024-01-01T10:00:00Z",
    )

    with patch("scripts.memory_capture._recent_final_tasks", return_value=[prior]):
        result = _supersession(task)

    assert result["state"] != "not_applicable"
    assert result["state"] != "none"
    assert len(result["supersedes_task_ids"]) >= 1
    # The matching prior entry should have score >= 4 (task name match alone = 4)
    assert result["supersedes_task_ids"][0] == "prior-001"


def test_supersession_non_final_task_returns_not_applicable() -> None:
    task = _make_task(status="in_progress")

    result = _supersession(task)

    assert result["state"] == "not_applicable"
    assert result["supersedes_task_ids"] == []


def test_supersession_no_matching_prior_tasks() -> None:
    task = _make_task(task_name="unique task xyz", status="completed")
    prior = _make_task(
        task_id="prior-002",
        task_name="completely different task",
        status="completed",
        skill="other-skill",
        runtime_target="other-server",
    )

    with patch("scripts.memory_capture._recent_final_tasks", return_value=[prior]):
        result = _supersession(task)

    assert result["state"] == "none"
    assert result["supersedes_task_ids"] == []


def test_supersession_failed_prior_marks_as_retries() -> None:
    task = _make_task(task_name="deploy service", status="completed")
    prior_failed = _make_task(
        task_id="prior-failed-001",
        task_name="deploy service",
        status="failed",
        finished_at="2024-01-01T09:00:00Z",
    )

    with patch("scripts.memory_capture._recent_final_tasks", return_value=[prior_failed]):
        result = _supersession(task)

    assert result["state"] == "retries_or_replaces_prior_work"


# ---------------------------------------------------------------------------
# _review_flags
# ---------------------------------------------------------------------------

def test_review_flags_high_risk_score_task() -> None:
    task = _make_task(task_name="fix and update deployment", status="completed")
    claim_state = {"confidence_hint": "low"}

    # Patch _supersession to return empty so we can isolate flag logic
    with patch("scripts.memory_capture._supersession", return_value={"supersedes_task_ids": []}):
        flags = _review_flags(task, claim_state)

    flag_types = {f["type"] for f in flags}
    assert "low_confidence_review" in flag_types


def test_review_flags_contradiction_keywords_trigger_review() -> None:
    task = _make_task(task_name="rewrite authentication flow", status="completed")
    task["touched_paths"] = ["README.md"]
    claim_state = {"confidence_hint": "medium"}

    with patch("scripts.memory_capture._supersession", return_value={"supersedes_task_ids": []}):
        flags = _review_flags(task, claim_state)

    flag_types = {f["type"] for f in flags}
    assert "contradiction_review" in flag_types


def test_review_flags_supersession_adds_supersession_review() -> None:
    task = _make_task(task_name="deploy service", status="completed")
    claim_state = {"confidence_hint": "high"}

    with patch("scripts.memory_capture._supersession", return_value={"supersedes_task_ids": ["prior-001"]}):
        flags = _review_flags(task, claim_state)

    flag_types = {f["type"] for f in flags}
    assert "supersession_review" in flag_types


def test_review_flags_clean_task_has_no_flags() -> None:
    task = _make_task(task_name="read config file", status="completed")
    claim_state = {"confidence_hint": "high"}

    with patch("scripts.memory_capture._supersession", return_value={"supersedes_task_ids": []}):
        flags = _review_flags(task, claim_state)

    assert flags == []


# ---------------------------------------------------------------------------
# _crystallization
# ---------------------------------------------------------------------------

def test_crystallization_output_structure() -> None:
    task = _make_task(
        task_name="deploy service",
        command_preview="helm run deploy",
        skill="deploy-skill",
        runtime_target="prod-server",
        status="completed",
    )
    event_types = ["operational_state", "project_state"]
    reasons = ["service operation may change live systems", "project docs changed"]

    result = _crystallization(task, event_types, reasons)

    assert result["question"] == "deploy service"
    assert result["action"] == "helm run deploy"
    assert "completed" in result["result"]
    assert result["lesson"] == reasons[0]
    affected = result["affected_entities"]
    assert "skill:deploy-skill" in affected
    assert "runtime:prod-server" in affected
    assert "event:operational_state" in affected
    assert "event:project_state" in affected


def test_crystallization_no_task_name_uses_default_question() -> None:
    task = {**_make_task(), "task_name": None, "command_preview": None}
    result = _crystallization(task, [], [])

    assert result["question"] == "What changed in this run?"
    assert result["lesson"] == "No durable lesson inferred."


def test_crystallization_affected_entities_capped_at_eight() -> None:
    task = _make_task(
        skill="s",
        runtime_target="r",
        status="completed",
    )
    event_types = ["e1", "e2", "e3", "e4", "e5", "e6", "e7", "e8", "e9"]
    result = _crystallization(task, event_types, ["some reason"])

    assert len(result["affected_entities"]) <= 8


# ---------------------------------------------------------------------------
# _retention_profile
# ---------------------------------------------------------------------------

def test_retention_profile_empty_event_types_is_ephemeral() -> None:
    result = _retention_profile([])

    assert result["tier"] == "ephemeral"
    assert result["decay_hint"] == "drop_if_unreferenced"


def test_retention_profile_knowledge_state_is_durable_knowledge() -> None:
    result = _retention_profile(["knowledge_state"])

    assert result["tier"] == "durable_knowledge"
    assert result["decay_hint"] == "keep_until_superseded"


def test_retention_profile_project_state_is_durable_operational() -> None:
    result = _retention_profile(["project_state"])

    assert result["tier"] == "durable_operational"
    assert result["decay_hint"] == "keep_until_workflow_changes"


def test_retention_profile_operational_state_is_durable_operational() -> None:
    result = _retention_profile(["operational_state"])

    assert result["tier"] == "durable_operational"
    assert result["decay_hint"] == "keep_until_workflow_changes"


def test_retention_profile_other_event_types_are_working_memory() -> None:
    result = _retention_profile(["some_other_event"])

    assert result["tier"] == "working_memory"
    assert result["decay_hint"] == "downrank_if_stale"


def test_retention_profile_knowledge_state_wins_over_operational() -> None:
    # knowledge_state takes priority in the if-elif chain
    result = _retention_profile(["operational_state", "knowledge_state"])

    assert result["tier"] == "durable_knowledge"
