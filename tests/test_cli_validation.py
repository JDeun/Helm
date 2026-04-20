from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DEMO_WORKSPACE = REPO_ROOT / "examples" / "demo-workspace"


class CliValidationTests(unittest.TestCase):
    def run_cli(self, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(REPO_ROOT / "helm.py"), *args],
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )

    def create_minimal_workspace(self, root: Path) -> None:
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
        (root / "references" / "skill_profile_policies.json").write_text(
            json.dumps({"skills": {}}),
            encoding="utf-8",
        )
        (root / "references" / "skill-capture-template.md").write_text("# Template\n", encoding="utf-8")
        (root / "references" / "skill-contract-template.json").write_text("{}\n", encoding="utf-8")

    def create_minimal_openclaw_workspace(self, root: Path) -> None:
        (root / ".openclaw" / "checkpoints").mkdir(parents=True)
        (root / "references").mkdir()
        (root / "skills").mkdir()
        (root / "skill_drafts").mkdir()
        (root / "memory").mkdir()
        (root / ".openclaw" / "context_sources.json").write_text('{"sources": []}\n', encoding="utf-8")
        (root / ".openclaw" / "task-ledger.jsonl").write_text("", encoding="utf-8")
        (root / ".openclaw" / "command-log.jsonl").write_text("", encoding="utf-8")
        (root / ".openclaw" / "checkpoints" / "index.json").write_text("[]\n", encoding="utf-8")

    def test_helm_validate_reports_missing_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self.create_minimal_workspace(root)
            (root / "skills" / "demo-skill").mkdir(parents=True)
            (root / "skills" / "demo-skill" / "SKILL.md").write_text("# Demo\n", encoding="utf-8")

            result = self.run_cli("validate", "--path", str(root), "--json")

            self.assertEqual(result.returncode, 1)
            payload = json.loads(result.stdout)
            self.assertFalse(payload["ok"])
            self.assertIn("skill `demo-skill` is missing contract.json", payload["issues"])

    def test_status_surfaces_memory_operation_and_crystallized_counts(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self.create_minimal_workspace(root)
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

            result = self.run_cli("status", "--path", str(root), "--verbose")

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("recent_memory_operations=1", result.stdout)
            self.assertIn("recent_crystallized_sessions=1", result.stdout)
            self.assertIn("memory_review_queue_count=1", result.stdout)

    def test_status_uses_openclaw_state_root_when_layout_is_openclaw(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self.create_minimal_openclaw_workspace(root)
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

            result = self.run_cli("status", "--path", str(root), "--verbose")

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("layout=openclaw", result.stdout)
            self.assertIn("recent_memory_operations=1", result.stdout)
            self.assertIn("recent_crystallized_sessions=1", result.stdout)

    def test_context_uses_openclaw_local_source_for_task_hydration(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self.create_minimal_openclaw_workspace(root)
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
            result = self.run_cli("context", "--path", str(root), "--include", "tasks", "--json")

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(len(payload), 1)
            self.assertEqual(payload[0]["metadata"]["task_id"], "task-openclaw")
            self.assertEqual(payload[0]["adapter_kind"], "openclaw")

    def test_context_tolerates_corrupted_context_sources_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self.create_minimal_workspace(root)
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

            result = self.run_cli("context", "--path", str(root), "--include", "tasks", "--json")

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(len(payload), 1)
            self.assertEqual(payload[0]["metadata"]["task_id"], "task-local")
            self.assertEqual(payload[0]["adapter"], "helm-local")

    def test_doctor_reports_invalid_reference_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self.create_minimal_workspace(root)
            (root / "references" / "execution_profiles.json").write_text("{not-json\n", encoding="utf-8")

            result = self.run_cli("doctor", "--path", str(root), "--json")

            self.assertEqual(result.returncode, 1)
            payload = json.loads(result.stdout)
            check = next(item for item in payload["checks"] if item["name"] == "references/execution_profiles.json")
            self.assertFalse(check["ok"])
            self.assertIn("invalid json", check["detail"])

    def test_status_resolves_nested_openclaw_workspace_from_parent_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            home_root = Path(tmpdir)
            workspace_root = home_root / ".openclaw" / "workspace"
            self.create_minimal_openclaw_workspace(workspace_root)
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

            result = self.run_cli("status", "--path", str(home_root), "--json")

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["workspace"], str(workspace_root.resolve()))
            self.assertEqual(payload["layout"], "openclaw")
            self.assertEqual(payload["recent_tasks"][0]["task_id"], "task-nested")

    def test_detect_prefers_nested_openclaw_workspace_over_generic_parent_markers(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            home_root = Path(tmpdir)
            (home_root / "references").mkdir()
            (home_root / "docs").mkdir()
            workspace_root = home_root / ".openclaw" / "workspace"
            self.create_minimal_openclaw_workspace(workspace_root)

            result = self.run_cli("detect", "--path", str(home_root), "--json")

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["workspace"], str(workspace_root.resolve()))
            self.assertEqual(payload["layout"], "openclaw")

    def test_checkpoint_list_uses_openclaw_state_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self.create_minimal_openclaw_workspace(root)
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

            result = self.run_cli("checkpoint", "list", "--path", str(root), "--json")

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload[0]["checkpoint_id"], "openclaw-demo-1")

    def test_survey_on_openclaw_workspace_does_not_recommend_self_adoption(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self.create_minimal_openclaw_workspace(root)

            result = self.run_cli("survey", "--path", str(root), "--json")

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["layout"], "openclaw")
            self.assertNotIn(str(root.resolve()), payload["detected_candidates"].get("openclaw", []))

    def test_status_review_queue_count_matches_missing_follow_up_logic(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self.create_minimal_workspace(root)
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

            result = self.run_cli("status", "--path", str(root), "--verbose")

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("memory_review_queue_count=1", result.stdout)

    def test_run_with_profile_honors_workspace_env_for_demo_contracts(self) -> None:
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
            self.assertEqual(validate_result.returncode, 0)
            self.assertTrue(json.loads(validate_result.stdout)["ok"])

            audit_result = subprocess.run(
                [sys.executable, str(REPO_ROOT / "scripts" / "run_with_profile.py"), "audit-manifest-quality", "--json"],
                capture_output=True,
                text=True,
                check=False,
                env=env,
            )
            self.assertEqual(audit_result.returncode, 0)
            self.assertTrue(json.loads(audit_result.stdout)["ok"])

    def test_survey_does_not_initialize_workspace_when_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "fresh-workspace"

            result = self.run_cli("survey", "--path", str(root), "--json")

            self.assertEqual(result.returncode, 0)
            payload = json.loads(result.stdout[result.stdout.index("{") :])
            self.assertEqual(payload["workspace"], str(root.resolve()))
            self.assertFalse((root / ".helm").exists())
            self.assertFalse((root / "references").exists())

    def test_doctor_reports_healthy_for_minimal_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self.create_minimal_workspace(root)

            result = self.run_cli("doctor", "--path", str(root))

            self.assertEqual(result.returncode, 0)
            self.assertIn("healthy=yes", result.stdout)
            self.assertIn("references/skill-contract-template.json: present", result.stdout)

    def test_run_contract_and_capability_diff_report_recent_task_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self.create_minimal_workspace(root)
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

            contract_result = self.run_cli("run-contract", "--path", str(root), "--json")
            self.assertEqual(contract_result.returncode, 0)
            contract_payload = json.loads(contract_result.stdout)
            self.assertEqual(contract_payload["task"]["task_id"], "task-new")
            self.assertTrue(contract_payload["task"]["file_intake_evidence_present"])

            diff_result = self.run_cli("capability-diff", "--path", str(root), "--json")
            self.assertEqual(diff_result.returncode, 0)
            diff_payload = json.loads(diff_result.stdout)
            changed_fields = {item["field"] for item in diff_payload["changed"]}
            self.assertIn("profile", changed_fields)
            self.assertIn("model_tier", changed_fields)
            self.assertIn("file_intake_evidence_present", changed_fields)

    def test_checkpoint_recommend_reports_demo_checkpoint(self) -> None:
        result = self.run_cli("checkpoint-recommend", "--path", str(DEMO_WORKSPACE))

        self.assertEqual(result.returncode, 0)
        self.assertIn("task_id=demo-task-002", result.stdout)
        self.assertIn("checkpoint_id=20260413T090959Z-demo-router-edit", result.stdout)
        self.assertIn("restore_hint=helm checkpoint --path", result.stdout)

    def test_report_markdown_mentions_failed_demo_task(self) -> None:
        result = self.run_cli("report", "--path", str(DEMO_WORKSPACE), "--format", "markdown")

        self.assertEqual(result.returncode, 0)
        self.assertIn("# Helm Report", result.stdout)
        self.assertIn("demo-task-002", result.stdout)
        self.assertIn("Recent Checkpoints", result.stdout)

    def test_skill_reject_writes_rejection_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self.create_minimal_workspace(root)
            (root / "skill_drafts" / "demo-skill").mkdir(parents=True, exist_ok=True)

            result = self.run_cli(
                "skill-reject",
                "--path",
                str(root),
                "--name",
                "demo-skill",
                "--reason",
                "needs narrower contract",
                "--json",
            )

            self.assertEqual(result.returncode, 0)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["status"], "rejected")
            self.assertEqual(payload["reason"], "needs narrower contract")
            stored = json.loads((root / "skill_drafts" / "demo-skill" / "meta" / "rejection.json").read_text(encoding="utf-8"))
            self.assertEqual(stored["reason"], "needs narrower contract")

    def test_harness_postflight_requires_browser_and_retrieval_evidence_when_contract_demands_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self.create_minimal_workspace(root)
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
            self.assertEqual(missing.returncode, 2)
            payload = json.loads(missing.stdout)
            failed_checks = {item["name"]: item for item in payload["checks"]}
            self.assertFalse(failed_checks["browser_evidence"]["ok"])
            self.assertFalse(failed_checks["retrieval_evidence"]["ok"])
            self.assertFalse(failed_checks["file_intake_evidence"]["ok"])

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
            self.assertEqual(recorded.returncode, 0)
            recorded_payload = json.loads(recorded.stdout)
            self.assertTrue(recorded_payload["postflight"]["ok"])

    def test_harness_postflight_can_infer_missing_evidence_from_task_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self.create_minimal_workspace(root)
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
            self.assertEqual(inferred.returncode, 0)
            payload = json.loads(inferred.stdout)
            self.assertTrue(payload["ok"])
            harness_meta = ((payload["entry"].get("meta") or {}).get("harness") or {})
            self.assertTrue(harness_meta["browser_evidence"]["inferred"])
            self.assertTrue(harness_meta["retrieval_evidence"]["inferred"])

    def test_harness_postflight_conditionally_requires_browser_and_retrieval_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self.create_minimal_workspace(root)
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
            self.assertEqual(local_only.returncode, 0)
            local_payload = json.loads(local_only.stdout)
            local_checks = {item["name"]: item for item in local_payload["checks"]}
            self.assertTrue(local_checks["browser_evidence"]["ok"])
            self.assertTrue(local_checks["retrieval_evidence"]["ok"])

            blocked = subprocess.run(
                [sys.executable, str(REPO_ROOT / "scripts" / "adaptive_harness.py"), "postflight", "--task-id", "task-mixed-2"],
                capture_output=True,
                text=True,
                check=False,
                env=env,
            )
            self.assertEqual(blocked.returncode, 0)
            blocked_payload = json.loads(blocked.stdout)
            self.assertTrue(blocked_payload["ok"])
            harness_meta = ((blocked_payload["entry"].get("meta") or {}).get("harness") or {})
            self.assertTrue(harness_meta["browser_evidence"]["inferred"])
            self.assertEqual(harness_meta["retrieval_evidence"]["exit_classification"], "api_reusable")

    def test_task_ledger_report_shows_evidence_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self.create_minimal_workspace(root)
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
            self.assertEqual(result.returncode, 0)
            self.assertIn("Browser evidence counts:", result.stdout)
            self.assertIn("Retrieval evidence counts:", result.stdout)
            self.assertIn("File intake evidence counts:", result.stdout)
            self.assertIn("Retrieval exit classifications:", result.stdout)
            self.assertIn("Retrieval next-attempt stages:", result.stdout)
            self.assertIn("browser=B retrieval=api_reusable file=-", result.stdout)

    def test_harness_backfill_evidence_updates_prior_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self.create_minimal_workspace(root)
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
            self.assertEqual(result.returncode, 0)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["updated"], 1)
            self.assertEqual(payload["browser_backfilled"], 1)
            self.assertEqual(payload["retrieval_backfilled"], 1)

    def test_manifest_quality_flags_broad_when_any_triggers(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self.create_minimal_workspace(root)
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
            self.assertEqual(result.returncode, 2)
            payload = json.loads(result.stdout)
            item = next(item for item in payload["items"] if item["skill"] == "broad-skill")
            joined = "\n".join(item["warnings"])
            self.assertIn("redundant", joined)
            self.assertIn("overly generic", joined)


if __name__ == "__main__":
    unittest.main()
