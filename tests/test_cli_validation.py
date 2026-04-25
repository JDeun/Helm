from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DEMO_WORKSPACE = REPO_ROOT / "examples" / "demo-workspace"


def run_cli(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(REPO_ROOT / "helm.py"), *args],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def create_minimal_workspace(root: Path) -> None:
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
        json.dumps({"profiles": {"inspect_local": {}, "workspace_edit": {}}}),
        encoding="utf-8",
    )
    (root / "references" / "model_recovery_policy.json").write_text(
        json.dumps({"version": 1, "state_path": ".helm/model-health-state.json", "models": []}),
        encoding="utf-8",
    )
    (root / "references" / "skill_profile_policies.json").write_text(
        json.dumps({"skills": {}}),
        encoding="utf-8",
    )
    (root / "references" / "skill-capture-template.md").write_text("# Template\n", encoding="utf-8")
    (root / "references" / "skill-contract-template.json").write_text("{}\n", encoding="utf-8")


def create_minimal_openclaw_workspace(root: Path) -> None:
    (root / ".openclaw" / "checkpoints").mkdir(parents=True)
    (root / "references").mkdir()
    (root / "skills").mkdir()
    (root / "skill_drafts").mkdir()
    (root / "memory").mkdir()
    (root / ".openclaw" / "context_sources.json").write_text('{"sources": []}\n', encoding="utf-8")
    (root / ".openclaw" / "task-ledger.jsonl").write_text("", encoding="utf-8")
    (root / ".openclaw" / "command-log.jsonl").write_text("", encoding="utf-8")
    (root / ".openclaw" / "checkpoints" / "index.json").write_text("[]\n", encoding="utf-8")


def test_helm_validate_reports_missing_contract() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        create_minimal_workspace(root)
        (root / "skills" / "demo-skill").mkdir(parents=True)
        (root / "skills" / "demo-skill" / "SKILL.md").write_text("# Demo\n", encoding="utf-8")

        result = run_cli("validate", "--path", str(root), "--json")

        assert result.returncode == 1
        payload = json.loads(result.stdout)
        assert not payload["ok"]
        assert "skill `demo-skill` is missing contract.json" in payload["issues"]


def test_status_surfaces_memory_operation_and_crystallized_counts() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        create_minimal_workspace(root)
        (root / ".helm" / "task-ledger.jsonl").write_text(
            json.dumps(
                {
                    "task_id": "task-review",
                    "task_name": "replace router memory",
                    "status": "completed",
                    "memory_capture": {
                        "finalization_status": "capture_partial",
                        "review_flags": [{"type": "truth_resolution_review"}],
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )
        (root / ".helm" / "memory-operations.jsonl").write_text(
            json.dumps(
                {
                    "id": "memop-1",
                    "timestamp": "2026-04-20T00:00:00+00:00",
                    "operation": "write",
                    "subject": "router policy",
                    "scope": "private",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        (root / ".helm" / "crystallized-sessions.jsonl").write_text(
            json.dumps(
                {
                    "id": "crystal-1",
                    "task_id": "task-1",
                    "crystallization": {"question": "What changed?", "result": "updated"},
                }
            )
            + "\n",
            encoding="utf-8",
        )

        result = run_cli("status", "--path", str(root), "--verbose")

        assert result.returncode == 0, result.stderr
        assert "recent_memory_operations=1" in result.stdout
        assert "recent_crystallized_sessions=1" in result.stdout
        assert "memory_review_queue_count=1" in result.stdout


def test_status_uses_openclaw_state_root_when_layout_is_openclaw() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        create_minimal_openclaw_workspace(root)
        (root / ".openclaw" / "memory-operations.jsonl").write_text(
            json.dumps(
                {
                    "id": "memop-1",
                    "timestamp": "2026-04-20T00:00:00+00:00",
                    "operation": "supersede",
                    "subject": "resolved retry",
                    "scope": "private",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        (root / ".openclaw" / "crystallized-sessions.jsonl").write_text(
            json.dumps(
                {
                    "id": "crystal-1",
                    "task_id": "task-1",
                    "crystallization": {"question": "What changed?", "result": "updated"},
                }
            )
            + "\n",
            encoding="utf-8",
        )

        result = run_cli("status", "--path", str(root), "--verbose")

        assert result.returncode == 0, result.stderr
        assert "layout=openclaw" in result.stdout
        assert "recent_memory_operations=1" in result.stdout
        assert "recent_crystallized_sessions=1" in result.stdout


def test_context_uses_openclaw_local_source_for_task_hydration() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        create_minimal_openclaw_workspace(root)
        (root / ".openclaw" / "task-ledger.jsonl").write_text(
            json.dumps(
                {
                    "task_id": "task-openclaw",
                    "task_name": "openclaw local task",
                    "status": "completed",
                    "profile": "inspect_local",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        result = run_cli("context", "--path", str(root), "--include", "tasks", "--json")

        assert result.returncode == 0, result.stderr
        payload = json.loads(result.stdout)
        assert len(payload) == 1
        assert payload[0]["metadata"]["task_id"] == "task-openclaw"
        assert payload[0]["adapter_kind"] == "openclaw"


def test_context_tolerates_corrupted_context_sources_file() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        create_minimal_workspace(root)
        (root / ".helm" / "context_sources.json").write_text("{not-json\n", encoding="utf-8")
        (root / ".helm" / "task-ledger.jsonl").write_text(
            json.dumps(
                {
                    "task_id": "task-local",
                    "task_name": "local task",
                    "status": "completed",
                    "profile": "inspect_local",
                }
            )
            + "\n",
            encoding="utf-8",
        )

        result = run_cli("context", "--path", str(root), "--include", "tasks", "--json")

        assert result.returncode == 0, result.stderr
        payload = json.loads(result.stdout)
        assert len(payload) == 1
        assert payload[0]["metadata"]["task_id"] == "task-local"
        assert payload[0]["adapter"] == "helm-local"


def test_doctor_reports_invalid_reference_json() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        create_minimal_workspace(root)
        (root / "references" / "execution_profiles.json").write_text("{not-json\n", encoding="utf-8")

        result = run_cli("doctor", "--path", str(root), "--json")

        assert result.returncode == 1
        payload = json.loads(result.stdout)
        check = next(item for item in payload["checks"] if item["name"] == "references/execution_profiles.json")
        assert not check["ok"]
        assert "invalid json" in check["detail"]


def test_status_resolves_nested_openclaw_workspace_from_parent_path() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        home_root = Path(tmpdir)
        workspace_root = home_root / ".openclaw" / "workspace"
        create_minimal_openclaw_workspace(workspace_root)
        (workspace_root / ".openclaw" / "task-ledger.jsonl").write_text(
            json.dumps(
                {
                    "task_id": "task-nested",
                    "task_name": "nested openclaw task",
                    "status": "completed",
                    "profile": "inspect_local",
                }
            )
            + "\n",
            encoding="utf-8",
        )

        result = run_cli("status", "--path", str(home_root), "--json")

        assert result.returncode == 0, result.stderr
        payload = json.loads(result.stdout)
        assert payload["workspace"] == str(workspace_root.resolve())
        assert payload["layout"] == "openclaw"
        assert payload["recent_tasks"][0]["task_id"] == "task-nested"


def test_detect_prefers_nested_openclaw_workspace_over_generic_parent_markers() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        home_root = Path(tmpdir)
        (home_root / "references").mkdir()
        (home_root / "docs").mkdir()
        workspace_root = home_root / ".openclaw" / "workspace"
        create_minimal_openclaw_workspace(workspace_root)

        result = run_cli("detect", "--path", str(home_root), "--json")

        assert result.returncode == 0, result.stderr
        payload = json.loads(result.stdout)
        assert payload["workspace"] == str(workspace_root.resolve())
        assert payload["layout"] == "openclaw"


def test_checkpoint_list_uses_openclaw_state_root() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        create_minimal_openclaw_workspace(root)
        checkpoints = [
            {
                "checkpoint_id": "openclaw-demo-1",
                "label": "demo snapshot",
                "paths": ["memory/router.md"],
                "archive": "checkpoints/openclaw-demo-1.tar.gz",
            }
        ]
        (root / ".openclaw" / "checkpoints" / "index.json").write_text(
            json.dumps(checkpoints) + "\n",
            encoding="utf-8",
        )

        result = run_cli("checkpoint", "list", "--path", str(root), "--json")

        assert result.returncode == 0, result.stderr
        payload = json.loads(result.stdout)
        assert payload[0]["checkpoint_id"] == "openclaw-demo-1"


def test_survey_on_openclaw_workspace_does_not_recommend_self_adoption() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        create_minimal_openclaw_workspace(root)

        result = run_cli("survey", "--path", str(root), "--json")

        assert result.returncode == 0, result.stderr
        payload = json.loads(result.stdout)
        assert payload["layout"] == "openclaw"
        assert str(root.resolve()) not in payload["detected_candidates"].get("openclaw", [])


def test_status_review_queue_count_matches_missing_follow_up_logic() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        create_minimal_workspace(root)
        (root / ".helm" / "task-ledger.jsonl").write_text(
            json.dumps(
                {
                    "task_id": "task-1",
                    "task_name": "router refresh",
                    "status": "completed",
                    "memory_capture": {
                        "relevant": True,
                        "finalization_status": "capture_written",
                        "claim_state": {"confidence_hint": "high"},
                        "review_flags": [],
                        "supersession": {"state": "refreshes_prior_state", "supersedes_task_ids": ["task-old"]},
                        "crystallization": {"question": "What changed?", "result": "updated"},
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )

        result = run_cli("status", "--path", str(root), "--verbose")

        assert result.returncode == 0, result.stderr
        assert "memory_review_queue_count=1" in result.stdout


def test_run_with_profile_honors_workspace_env_for_demo_contracts() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        (root / "references").mkdir()
        (root / "skill_drafts" / "demo-skill").mkdir(parents=True)
        (root / "references" / "execution_profiles.json").write_text(
            json.dumps({"profiles": {"inspect_local": {}, "workspace_edit": {}, "risky_edit": {}}}),
            encoding="utf-8",
        )
        (root / "skill_drafts" / "demo-skill" / "contract.json").write_text(
            json.dumps(
                {
                    "skill": "demo-skill",
                    "allowed_profiles": ["inspect_local", "risky_edit"],
                    "default_profile": "inspect_local",
                    "context": {
                        "required": True,
                        "query": "router",
                        "include": ["tasks", "commands"],
                        "limit": 4,
                        "failed_include": ["tasks", "commands"],
                        "failed_limit": 4,
                    },
                    "runner": {"entrypoint": "demo_runner.py", "strict_required": True},
                }
            ),
            encoding="utf-8",
        )
        (root / "skill_drafts" / "demo-skill" / "SKILL.md").write_text(
            """# Demo Skill

## Core rule

Inspect first and mutate only through the strict runner.

## Input contract

- Required inputs: router target
- Optional inputs: prior task context
- Ask first when missing: ask for the target file
- If the request is broad or ambiguous, how it must be narrowed: one router surface at a time

## Decision contract

- State the decision order explicitly: inspect, decide, then mutate
- Route outward when another workflow owns the task
- Ask for approval before risky edits
- Red flags: missing target or missing prior failure context

## Execution contract

- State the real commands, tools, or APIs to use: `rg`, `sed`, and `helm harness preflight`
- Use saved context and recent failures before mutation
- Use the strict runner path through the declared entrypoint
- If the workflow has risk, mention the execution profile or checkpoint rule

## Output contract

- Default output format: short summary
- Always include: target, decision, next step
- Length rule: concise
- Do not say or Do not imply: success before validation

## Failure contract

- Failure types: missing input or command failure
- Fallback behavior: stop at inspection
- User-facing failure language: explain the blocked dependency plainly
""",
            encoding="utf-8",
        )

        env = os.environ.copy()
        env["HELM_WORKSPACE"] = str(root)

        validate_result = subprocess.run(
            [sys.executable, str(REPO_ROOT / "scripts" / "run_with_profile.py"), "validate-manifests", "--json"],
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )
        assert validate_result.returncode == 0
        assert json.loads(validate_result.stdout)["ok"]

        audit_result = subprocess.run(
            [sys.executable, str(REPO_ROOT / "scripts" / "run_with_profile.py"), "audit-manifest-quality", "--json"],
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )
        assert audit_result.returncode == 0
        assert json.loads(audit_result.stdout)["ok"]


def test_survey_does_not_initialize_workspace_when_missing() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir) / "fresh-workspace"

        result = run_cli("survey", "--path", str(root), "--json")

        assert result.returncode == 0
        payload = json.loads(result.stdout[result.stdout.index("{"):])
        assert payload["workspace"] == str(root.resolve())
        assert not (root / ".helm").exists()
        assert not (root / "references").exists()


def test_doctor_reports_healthy_for_minimal_workspace() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        create_minimal_workspace(root)

        result = run_cli("doctor", "--path", str(root))

        assert result.returncode == 0
        assert "healthy=yes" in result.stdout
        assert "references/skill-contract-template.json: present" in result.stdout
        assert "model-health:" in result.stdout


def test_health_select_uses_fresh_state() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        create_minimal_workspace(root)
        (root / "references" / "model_recovery_policy.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "state_path": ".helm/model-health-state.json",
                    "fresh_after_seconds": 300,
                    "models": [
                        {"ref": "openai/gpt-4.1-mini", "provider": "openai", "priority": 10, "probe": {"kind": "openai_chat_completion", "model": "gpt-4.1-mini"}}
                    ],
                }
            ),
            encoding="utf-8",
        )
        (root / ".helm" / "model-health-state.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "models": {
                        "openai/gpt-4.1-mini": {
                            "status": "healthy",
                            "checked_at": "2099-01-01T00:00:00+00:00",
                            "last_ok_at": "2099-01-01T00:00:00+00:00",
                        }
                    },
                }
            ),
            encoding="utf-8",
        )

        result = run_cli("health", "--path", str(root), "select", "--json")

        assert result.returncode == 0, result.stderr
        payload = json.loads(result.stdout)
        assert payload["model"] == "openai/gpt-4.1-mini"
        assert payload["source"] == "model-health-state"


def test_memory_capture_chat_writes_ledger_entries() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        create_minimal_workspace(root)

        result = run_cli(
            "memory",
            "--path",
            str(root),
            "capture-chat",
            "--task-name",
            "document release state",
            "--path",
            "README.md",
            "--path",
            "CHANGELOG.md",
            "--json",
        )

        assert result.returncode == 0, result.stderr
        payload = json.loads(result.stdout)
        assert payload["memory_capture"]["touched_paths"] == ["README.md", "CHANGELOG.md"]
        lines = (root / ".helm" / "task-ledger.jsonl").read_text(encoding="utf-8").splitlines()
        assert len(lines) == 3
        final = json.loads(lines[-1])
        assert final["status"] == "completed"
        assert final["memory_capture"]["finalization_status"] == "capture_planned"


def test_run_contract_and_capability_diff_report_recent_task_state() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        create_minimal_workspace(root)
        (root / "skills" / "demo-skill").mkdir(parents=True)
        (root / "skills" / "demo-skill" / "contract.json").write_text(
            json.dumps(
                {
                    "skill": "demo-skill",
                    "allowed_profiles": ["inspect_local", "workspace_edit"],
                    "default_profile": "inspect_local",
                    "file_intake": {"required": True},
                }
            ),
            encoding="utf-8",
        )
        (root / ".helm" / "task-ledger.jsonl").write_text(
            "\n".join(
                [
                    json.dumps(
                        {
                            "task_id": "task-old",
                            "task_name": "inspect draft",
                            "skill": "demo-skill",
                            "profile": "inspect_local",
                            "status": "completed",
                            "memory_capture": {"finalization_status": "capture_written"},
                            "meta": {"harness": {"model_tier": "frontier", "enforcement_level": "light"}},
                        }
                    ),
                    json.dumps(
                        {
                            "task_id": "task-new",
                            "task_name": "edit draft",
                            "skill": "demo-skill",
                            "profile": "workspace_edit",
                            "status": "completed",
                            "delivery_mode": "inline",
                            "memory_capture": {"finalization_status": "capture_written"},
                            "meta": {
                                "harness": {
                                    "model_tier": "constrained",
                                    "enforcement_level": "strict",
                                    "file_intake_evidence": {
                                        "path": "/tmp/demo.pdf",
                                        "claimed_type": "application/pdf",
                                        "detected_type": "application/pdf",
                                        "detector": "magika",
                                        "route_decision": "pdf_document",
                                    },
                                }
                            },
                        }
                    ),
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        contract_result = run_cli("run-contract", "--path", str(root), "--json")
        assert contract_result.returncode == 0
        contract_payload = json.loads(contract_result.stdout)
        assert contract_payload["task"]["task_id"] == "task-new"
        assert contract_payload["task"]["file_intake_evidence_present"]

        diff_result = run_cli("capability-diff", "--path", str(root), "--json")
        assert diff_result.returncode == 0
        diff_payload = json.loads(diff_result.stdout)
        changed_fields = {item["field"] for item in diff_payload["changed"]}
        assert "profile" in changed_fields
        assert "model_tier" in changed_fields
        assert "file_intake_evidence_present" in changed_fields


def test_checkpoint_recommend_reports_demo_checkpoint() -> None:
    result = run_cli("checkpoint-recommend", "--path", str(DEMO_WORKSPACE))

    assert result.returncode == 0
    assert "task_id=demo-task-002" in result.stdout
    assert "checkpoint_id=20260413T090959Z-demo-router-edit" in result.stdout
    assert "restore_hint=helm checkpoint --path" in result.stdout


def test_report_markdown_mentions_failed_demo_task() -> None:
    result = run_cli("report", "--path", str(DEMO_WORKSPACE), "--format", "markdown")

    assert result.returncode == 0
    assert "# Helm Report" in result.stdout
    assert "demo-task-002" in result.stdout
    assert "Recent Checkpoints" in result.stdout


def test_skill_reject_writes_rejection_payload() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        create_minimal_workspace(root)
        (root / "skill_drafts" / "demo-skill").mkdir(parents=True, exist_ok=True)

        result = run_cli(
            "skill-reject",
            "--path",
            str(root),
            "--name",
            "demo-skill",
            "--reason",
            "needs narrower contract",
            "--json",
        )

        assert result.returncode == 0
        payload = json.loads(result.stdout)
        assert payload["status"] == "rejected"
        assert payload["reason"] == "needs narrower contract"
        stored = json.loads((root / "skill_drafts" / "demo-skill" / "meta" / "rejection.json").read_text(encoding="utf-8"))
        assert stored["reason"] == "needs narrower contract"


def test_harness_postflight_requires_browser_and_retrieval_evidence_when_contract_demands_it() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        create_minimal_workspace(root)
        (root / "skills" / "browser-skill").mkdir(parents=True)
        (root / "skills" / "browser-skill" / "contract.json").write_text(
            json.dumps(
                {
                    "skill": "browser-skill",
                    "allowed_profiles": ["inspect_local"],
                    "default_profile": "inspect_local",
                    "browser_work": {
                        "required": True,
                        "required_fields": ["reason", "evidence", "api_reusable", "next_action"],
                    },
                    "retrieval_policy": {
                        "required": True,
                        "required_fields": ["attempt_stage", "exit_classification", "recovery_artifact"],
                    },
                    "file_intake": {
                        "required": True,
                        "required_fields": ["path", "claimed_type", "detected_type", "detector", "route_decision"],
                    },
                }
            ),
            encoding="utf-8",
        )
        (root / ".helm" / "task-ledger.jsonl").write_text(
            json.dumps(
                {
                    "task_id": "task-browser-1",
                    "task_name": "generic inspection task",
                    "skill": "browser-skill",
                    "status": "completed",
                    "command_preview": "python3 scripts/do_work.py",
                    "memory_capture": {"finalization_status": "capture_written"},
                    "meta": {"harness": {"enforcement_level": "strict"}},
                }
            )
            + "\n",
            encoding="utf-8",
        )

        env = os.environ.copy()
        env["HELM_WORKSPACE"] = str(root)

        missing = subprocess.run(
            [sys.executable, str(REPO_ROOT / "scripts" / "adaptive_harness.py"), "postflight", "--task-id", "task-browser-1"],
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )
        assert missing.returncode == 2
        payload = json.loads(missing.stdout)
        failed_checks = {item["name"]: item for item in payload["checks"]}
        assert not failed_checks["browser_evidence"]["ok"]
        assert not failed_checks["retrieval_evidence"]["ok"]
        assert not failed_checks["file_intake_evidence"]["ok"]

        recorded = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "scripts" / "adaptive_harness.py"),
                "record-evidence",
                "--task-id",
                "task-browser-1",
                "--browser-evidence-json",
                json.dumps(
                    {
                        "reason": "JS-only page required live browser inspection",
                        "evidence": "Captured snapshot plus blocking selector state",
                        "api_reusable": True,
                        "next_action": "Promote the discovered endpoint into a cheaper fetch path",
                    }
                ),
                "--retrieval-evidence-json",
                json.dumps(
                    {
                        "attempt_stage": "browser_network",
                        "exit_classification": "api_reusable",
                        "recovery_artifact": "/tmp/browser-network.json",
                    }
                ),
                "--file-intake-evidence-json",
                json.dumps(
                    {
                        "path": "/tmp/example.pdf",
                        "claimed_type": "application/pdf",
                        "detected_type": "application/pdf",
                        "detector": "magika",
                        "route_decision": "pdf_document",
                    }
                ),
            ],
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )
        assert recorded.returncode == 0
        recorded_payload = json.loads(recorded.stdout)
        assert recorded_payload["postflight"]["ok"]


def test_harness_postflight_can_infer_missing_evidence_from_task_metadata() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        create_minimal_workspace(root)
        (root / "skills" / "browser-skill").mkdir(parents=True)
        (root / "skills" / "browser-skill" / "contract.json").write_text(
            json.dumps(
                {
                    "skill": "browser-skill",
                    "allowed_profiles": ["inspect_local"],
                    "default_profile": "inspect_local",
                    "browser_work": {"required": True},
                    "retrieval_policy": {"required": True},
                }
            ),
            encoding="utf-8",
        )
        (root / ".helm" / "task-ledger.jsonl").write_text(
            json.dumps(
                {
                    "task_id": "task-browser-2",
                    "task_name": "blocked browser retrieval",
                    "skill": "browser-skill",
                    "status": "completed",
                    "command_preview": "playwright inspect blocked page and inspect network endpoint",
                    "runtime_note": "network endpoint looked reusable after browser inspection",
                    "memory_capture": {"finalization_status": "capture_written"},
                    "meta": {"harness": {"enforcement_level": "strict", "user_request": "inspect blocked site and reuse the API if possible"}},
                }
            )
            + "\n",
            encoding="utf-8",
        )

        env = os.environ.copy()
        env["HELM_WORKSPACE"] = str(root)

        inferred = subprocess.run(
            [sys.executable, str(REPO_ROOT / "scripts" / "adaptive_harness.py"), "postflight", "--task-id", "task-browser-2"],
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )
        assert inferred.returncode == 0
        payload = json.loads(inferred.stdout)
        assert payload["ok"]
        harness_meta = ((payload["entry"].get("meta") or {}).get("harness") or {})
        assert harness_meta["browser_evidence"]["inferred"]
        assert harness_meta["retrieval_evidence"]["inferred"]


def test_harness_postflight_conditionally_requires_browser_and_retrieval_evidence() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        create_minimal_workspace(root)
        (root / "skills" / "mixed-skill").mkdir(parents=True)
        (root / "skills" / "mixed-skill" / "contract.json").write_text(
            json.dumps(
                {
                    "skill": "mixed-skill",
                    "allowed_profiles": ["inspect_local"],
                    "default_profile": "inspect_local",
                    "browser_work": {"when_any": ["browser", "playwright", "selector"]},
                    "retrieval_policy": {"when_any": ["blocked", "403", "network", "endpoint"]},
                }
            ),
            encoding="utf-8",
        )
        (root / ".helm" / "task-ledger.jsonl").write_text(
            "\n".join(
                [
                    json.dumps(
                        {
                            "task_id": "task-mixed-1",
                            "task_name": "summarize local notes",
                            "skill": "mixed-skill",
                            "status": "completed",
                            "command_preview": "python3 scripts/summarize_notes.py",
                            "memory_capture": {"finalization_status": "capture_written"},
                            "meta": {"harness": {"enforcement_level": "strict"}},
                        }
                    ),
                    json.dumps(
                        {
                            "task_id": "task-mixed-2",
                            "task_name": "browser blocked lookup",
                            "skill": "mixed-skill",
                            "status": "completed",
                            "command_preview": "playwright browser lookup with blocked 403 response and reusable network endpoint",
                            "memory_capture": {"finalization_status": "capture_written"},
                            "meta": {"harness": {"enforcement_level": "strict"}},
                        }
                    ),
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        env = os.environ.copy()
        env["HELM_WORKSPACE"] = str(root)

        local_only = subprocess.run(
            [sys.executable, str(REPO_ROOT / "scripts" / "adaptive_harness.py"), "postflight", "--task-id", "task-mixed-1"],
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )
        assert local_only.returncode == 0
        local_payload = json.loads(local_only.stdout)
        local_checks = {item["name"]: item for item in local_payload["checks"]}
        assert local_checks["browser_evidence"]["ok"]
        assert local_checks["retrieval_evidence"]["ok"]

        blocked = subprocess.run(
            [sys.executable, str(REPO_ROOT / "scripts" / "adaptive_harness.py"), "postflight", "--task-id", "task-mixed-2"],
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )
        assert blocked.returncode == 0
        blocked_payload = json.loads(blocked.stdout)
        assert blocked_payload["ok"]
        harness_meta = ((blocked_payload["entry"].get("meta") or {}).get("harness") or {})
        assert harness_meta["browser_evidence"]["inferred"]
        assert harness_meta["retrieval_evidence"]["exit_classification"] == "api_reusable"


def test_task_ledger_report_shows_evidence_summary() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        create_minimal_workspace(root)
        (root / ".helm" / "task-ledger.jsonl").write_text(
            json.dumps(
                {
                    "task_id": "task-report-1",
                    "task_name": "browser retrieval",
                    "status": "completed",
                    "profile": "inspect_local",
                    "memory_capture": {"finalization_status": "capture_written"},
                    "meta": {
                        "harness": {
                            "enforcement_level": "strict",
                            "model_tier": "constrained",
                            "browser_evidence": {"reason": "browser used"},
                            "retrieval_evidence": {"exit_classification": "api_reusable"},
                        }
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )
        env = os.environ.copy()
        env["HELM_WORKSPACE"] = str(root)

        result = subprocess.run(
            [sys.executable, str(REPO_ROOT / "scripts" / "task_ledger_report.py"), "--summary", "--limit", "1"],
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )
        assert result.returncode == 0
        assert "Browser evidence counts:" in result.stdout
        assert "Retrieval evidence counts:" in result.stdout
        assert "File intake evidence counts:" in result.stdout
        assert "Retrieval exit classifications:" in result.stdout
        assert "Retrieval next-attempt stages:" in result.stdout
        assert "browser=B retrieval=api_reusable file=-" in result.stdout


def test_harness_backfill_evidence_updates_prior_tasks() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        create_minimal_workspace(root)
        (root / "skills" / "mixed-skill").mkdir(parents=True)
        (root / "skills" / "mixed-skill" / "contract.json").write_text(
            json.dumps(
                {
                    "skill": "mixed-skill",
                    "allowed_profiles": ["inspect_local"],
                    "default_profile": "inspect_local",
                    "browser_work": {"when_any": ["browser", "playwright"]},
                    "retrieval_policy": {"when_any": ["blocked", "network", "endpoint"]},
                }
            ),
            encoding="utf-8",
        )
        (root / ".helm" / "task-ledger.jsonl").write_text(
            json.dumps(
                {
                    "task_id": "task-backfill-1",
                    "task_name": "browser blocked lookup",
                    "skill": "mixed-skill",
                    "status": "completed",
                    "command_preview": "playwright browser lookup with blocked network endpoint",
                    "runtime_note": "network endpoint looked reusable after browser inspection",
                    "memory_capture": {"finalization_status": "capture_written"},
                    "meta": {"harness": {"enforcement_level": "strict"}},
                }
            )
            + "\n",
            encoding="utf-8",
        )
        env = os.environ.copy()
        env["HELM_WORKSPACE"] = str(root)

        result = subprocess.run(
            [sys.executable, str(REPO_ROOT / "scripts" / "adaptive_harness.py"), "backfill-evidence", "--skill", "mixed-skill"],
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )
        assert result.returncode == 0
        payload = json.loads(result.stdout)
        assert payload["updated"] == 1
        assert payload["browser_backfilled"] == 1
        assert payload["retrieval_backfilled"] == 1


def test_manifest_quality_flags_broad_when_any_triggers() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        create_minimal_workspace(root)
        (root / "skills" / "broad-skill").mkdir(parents=True)
        (root / "skills" / "broad-skill" / "contract.json").write_text(
            json.dumps(
                {
                    "skill": "broad-skill",
                    "allowed_profiles": ["inspect_local"],
                    "default_profile": "inspect_local",
                    "browser_work": {"required": True, "when_any": ["web", "site", "task"]},
                }
            ),
            encoding="utf-8",
        )

        env = os.environ.copy()
        env["HELM_WORKSPACE"] = str(root)
        result = subprocess.run(
            [sys.executable, str(REPO_ROOT / "scripts" / "run_with_profile.py"), "audit-manifest-quality", "--json"],
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )
        assert result.returncode == 2
        payload = json.loads(result.stdout)
        item = next(item for item in payload["items"] if item["skill"] == "broad-skill")
        joined = "\n".join(item["warnings"])
        assert "redundant" in joined
        assert "overly generic" in joined
