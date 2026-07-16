# tests/test_skill_router.py
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.skill_router import load_installed_manifests, route_skill


def test_single_clear_match_routes_direct() -> None:
    manifests = {
        "email-ops": {"route_decision": {"task_type": "email"}},
        "travel-ops-ko": {"route_decision": {"task_type": "travel"}},
    }

    result = route_skill("send an email to the client", manifests)

    assert result == {"decision": "direct", "skill": "email-ops", "score": 60.0}


def test_no_matches_returns_none() -> None:
    manifests = {
        "email-ops": {"route_decision": {"task_type": "email"}},
        "travel-ops-ko": {"route_decision": {"task_type": "travel"}},
    }

    result = route_skill("completely unrelated household chore list", manifests)

    assert result == {"decision": "none", "candidates": []}


def test_tie_at_threshold_is_deterministic_candidates() -> None:
    # Both skills score exactly 35 (skill-name token overlap only), which is
    # exactly at the threshold. Ties must not be broken arbitrarily in favor
    # of a "direct" decision -- both clear, so the result is "candidates",
    # sorted by score desc then skill name asc.
    manifests = {
        "beta-ops": {},
        "alpha-ops": {},
    }

    result = route_skill("alpha ops beta ops task", manifests, min_score=35)

    assert result == {
        "decision": "candidates",
        "candidates": [
            {"skill": "alpha-ops", "score": 35.0},
            {"skill": "beta-ops", "score": 35.0},
        ],
    }


def test_multiple_clear_matches_return_bounded_sorted_candidates() -> None:
    # Nine skills clear a low threshold with distinct (mostly) scores built
    # from independent scoring components (name overlap=35, task_type=25,
    # context=20, allowed_profiles=10). Only the top 8 (default limit) must
    # be returned, sorted score desc then skill name asc for ties.
    request = "widget gizmo sprocket demo"

    def manifest(*, task_type: bool = False, context: bool = False, profile: bool = False) -> dict:
        contract: dict = {"route_decision": {"task_type": "gizmo" if task_type else "generic"}}
        if context:
            contract["context"] = {"query": "sprocket"}
        if profile:
            contract["allowed_profiles"] = ["inspect_local"]
        return contract

    manifests = {
        "widget-a": manifest(),  # overlap only -> 35
        "svc-b": manifest(task_type=True),  # 25
        "widget-c": manifest(task_type=True),  # overlap+task_type -> 60
        "svc-d": manifest(context=True),  # 20
        "widget-e": manifest(context=True),  # overlap+context -> 55
        "svc-f": manifest(task_type=True, context=True),  # 45
        "widget-g": manifest(task_type=True, context=True),  # overlap+task_type+context -> 80
        "svc-h": manifest(profile=True),  # 10
        "widget-i": manifest(profile=True),  # overlap+profile -> 45
    }

    result = route_skill(request, manifests, min_score=10)

    assert result["decision"] == "candidates"
    assert result["candidates"] == [
        {"skill": "widget-g", "score": 80.0},
        {"skill": "widget-c", "score": 60.0},
        {"skill": "widget-e", "score": 55.0},
        {"skill": "svc-f", "score": 45.0},
        {"skill": "widget-i", "score": 45.0},
        {"skill": "widget-a", "score": 35.0},
        {"skill": "svc-b", "score": 25.0},
        {"skill": "svc-d", "score": 20.0},
    ]
    assert len(result["candidates"]) == 8
    assert "svc-h" not in [item["skill"] for item in result["candidates"]]


def test_load_installed_manifests_reads_skills_and_drafts(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills" / "demo-skill"
    skills_dir.mkdir(parents=True)
    (skills_dir / "contract.json").write_text(
        json.dumps({"route_decision": {"task_type": "demo"}}), encoding="utf-8"
    )

    drafts_dir = tmp_path / "skill_drafts" / "draft-skill"
    drafts_dir.mkdir(parents=True)
    (drafts_dir / "contract.json").write_text(
        json.dumps({"route_decision": {"task_type": "draft"}}), encoding="utf-8"
    )

    manifests = load_installed_manifests(tmp_path)

    assert manifests == {
        "demo-skill": {"route_decision": {"task_type": "demo"}},
        "draft-skill": {"route_decision": {"task_type": "draft"}},
    }
