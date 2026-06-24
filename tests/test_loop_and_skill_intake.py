from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(REPO_ROOT / "helm.py"), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def test_loop_examples_validate_through_library() -> None:
    from scripts.loop_lib import load_loop_file, validate_loop

    for rel in ("examples/loops/completion-evidence.yaml", "examples/loops/docs-sweep.yaml"):
        payload = load_loop_file(REPO_ROOT / rel)
        result = validate_loop(payload)
        assert result["ok"], result["issues"]
        assert payload["id"]
        assert payload["required_evidence"]
        assert payload["stop_conditions"]


def test_loop_cli_validates_and_inspects_examples() -> None:
    validate = run_cli("loops", "validate", "examples/loops/completion-evidence.yaml", "--json")
    assert validate.returncode == 0, validate.stderr
    payload = json.loads(validate.stdout)
    assert payload["ok"] is True
    assert payload["id"] == "completion-evidence"

    inspect = run_cli("loops", "inspect", "completion-evidence", "--json")
    assert inspect.returncode == 0, inspect.stderr
    inspected = json.loads(inspect.stdout)
    assert inspected["loop"]["id"] == "completion-evidence"

    missing = run_cli("loops", "inspect", "missing-loop", "--json")
    assert missing.returncode == 1
    missing_payload = json.loads(missing.stdout)
    assert missing_payload["ok"] is False


def test_loop_validator_rejects_missing_evidence() -> None:
    from scripts.loop_lib import validate_loop

    result = validate_loop({"id": "bad", "title": "Bad", "steps": []})
    assert not result["ok"]
    assert "required_evidence" in " ".join(result["issues"])


def test_skill_intake_classifier_is_conservative() -> None:
    from scripts.skill_intake_lib import classify_candidate, validate_candidate

    safe = classify_candidate("tool poisoning audit", "read-only MCP server review")
    unsafe = classify_candidate("phishing credential collection", "stealth and credential theft workflow")
    sensitive = classify_candidate("cloud credential store checklist", "production key rotation review")
    assert safe["risk_class"] in {"D1", "D2"}
    assert unsafe["risk_class"] == "X"
    assert sensitive["risk_class"] != "D0"
    assert validate_candidate(safe)["ok"] is True
    assert validate_candidate({"name": "bad", "risk_class": "D9"})["ok"] is False


def test_skill_intake_cli_classifies_json() -> None:
    result = run_cli("skill-intake", "classify", "prompt injection audit", "--description", "read-only checks", "--json")
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["risk_class"] in {"D1", "D2"}
    assert payload["default_action"] in {"draft", "draft_with_contract"}


def test_coding_task_pipeline_manifest_is_checkpointed() -> None:
    path = REPO_ROOT / "references" / "pipelines" / "coding-task-finalization-pipeline.yaml"
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert payload["pipeline_id"] == "coding-task-finalization-pipeline"
    assert len(payload["stages"]) >= 3
    assert payload["checkpoint_policy"]["enabled"] is True
    assert "completion_evidence" in payload["final_evidence_contract"]
    for stage in payload["stages"]:
        assert stage["director_skill"]
        assert stage["produces"]
