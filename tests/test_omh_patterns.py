from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from consensus_plan_gate import build_plan, evaluate_scope, review_plan, run_consensus
from evidence_gatherer import gather_evidence, load_config, read_file_evidence, run_command, validate_command
from role_catalog import expand_role_markers, resolve_role
from task_state_bundle import write_task_state_bundle


class RoleCatalogTests(unittest.TestCase):
    def test_known_role_expands_from_central_catalog(self) -> None:
        payload = expand_role_markers("[role:verifier] check evidence")
        self.assertEqual(payload["role"]["role_id"], "verifier")
        self.assertIn("Independently evaluate", payload["expanded"])

    def test_unknown_and_multiple_roles_fail_fast(self) -> None:
        with self.assertRaisesRegex(ValueError, "unknown role"):
            resolve_role("wizard")
        with self.assertRaisesRegex(ValueError, "exactly one"):
            expand_role_markers("[role:planner] [role:critic]")

    def test_consensus_review_expands_catalog_prompt_into_live_review_input(self) -> None:
        plan = build_plan({"task_id": "role-plan", "task_name": "role plan", "profile": "risky_edit"}, scope=["scripts"])
        review = review_plan(plan, "critic", round_number=1)
        self.assertIn("Actively search", review["role_prompt"])
        self.assertIn(review["role_prompt"], review["expanded_role_input"])


class EvidenceGathererTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_config(ROOT / "references" / "evidence_commands.json")

    def test_allowlist_matches_tokens_not_strings(self) -> None:
        allowed, _, _ = validate_command([sys.executable, "-m", "unittest", "--help"], config=self.config, cwd=ROOT)
        self.assertTrue(allowed)
        confused, _, _ = validate_command(["npm", "testing"], config=self.config, cwd=ROOT)
        self.assertFalse(confused)

    def test_shell_chaining_and_redirection_are_rejected(self) -> None:
        for command in (
            ["python3", "-m", "pytest", "tests;rm"],
            ["npm", "test", "&&", "curl"],
            ["go", "test", ">", "out"],
        ):
            allowed, reason, _ = validate_command(command, config=self.config, cwd=ROOT)
            self.assertFalse(allowed)
            self.assertIn("metacharacters", reason)

    def test_rejected_command_is_never_spawned(self) -> None:
        with patch("evidence_gatherer.subprocess.run") as spawn:
            row = run_command(["bash", "-c", "echo unsafe"], cwd=ROOT, config=self.config)
        self.assertEqual(row["status"], "rejected")
        spawn.assert_not_called()

    def test_max_count_fails_closed_and_records_only_limit(self) -> None:
        config = json.loads(json.dumps(self.config))
        config["limits"]["max_commands"] = 1
        with patch(
            "evidence_gatherer.run_command",
            return_value={"ok": True, "status": "passed", "exit_code": 0},
        ) as runner:
            payload = gather_evidence(
                [["npm", "test"], ["go", "test"]],
                cwd=ROOT,
                config=config,
            )
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["reason"], "max command count exceeded")
        self.assertEqual(runner.call_count, 1)

    def test_file_readback_rejects_workspace_escape(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            row = read_file_evidence("../secret", cwd=Path(tmpdir))
        self.assertFalse(row["ok"])
        self.assertIn("escapes", row["reason"])

    def test_output_redacts_secret_environment_values(self) -> None:
        completed = subprocess.CompletedProcess(["python3"], 0, stdout="token=super-secret-value", stderr="")
        with patch.dict("os.environ", {"API_TOKEN": "super-secret-value"}, clear=False), patch(
            "evidence_gatherer.subprocess.run", return_value=completed
        ):
            row = run_command([sys.executable, "-m", "unittest", "--help"], cwd=ROOT, config=self.config)
        self.assertTrue(row["ok"])
        self.assertNotIn("super-secret-value", row["stdout"])
        self.assertIn("[REDACTED]", row["stdout"])

    def test_caller_asserted_service_evidence_is_not_trusted(self) -> None:
        payload = gather_evidence(
            [],
            cwd=ROOT,
            config=self.config,
            service_evidence=[{"kind": "forged", "verified": True, "source": "calendar/event-1"}],
        )
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["service_results"][0]["kind"], "service_readback")
        self.assertEqual(payload["service_results"][0]["provenance"], "caller_supplied")

    def test_service_readback_requires_an_allowlisted_command_run(self) -> None:
        config = json.loads(json.dumps(self.config))
        config["service_readback_prefixes"] = [["python3", "-m", "unittest"]]
        with patch(
            "evidence_gatherer.run_command",
            return_value={"ok": True, "status": "passed", "exit_code": 0},
        ):
            payload = gather_evidence(
                [],
                cwd=ROOT,
                config=config,
                service_evidence=[{
                    "source": "calendar/event-1",
                    "readback_command": [sys.executable, "-m", "unittest", "--help"],
                }],
            )
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["service_results"][0]["provenance"], "evidence_gatherer_command")

    def test_general_command_allowlist_is_not_service_readback_authority(self) -> None:
        with patch("evidence_gatherer.run_command") as runner:
            payload = gather_evidence(
                [], cwd=ROOT, config=self.config,
                service_evidence=[{
                    "source": "calendar/event-1",
                    "readback_command": [sys.executable, "-m", "unittest", "--help"],
                }],
            )
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["service_results"][0]["provenance"], "untrusted_command")
        runner.assert_not_called()


class ConsensusPlanTests(unittest.TestCase):
    def _task(self) -> dict:
        return {
            "task_id": "task-1",
            "task_name": "repair briefing workflow",
            "profile": "risky_edit",
            "command": ["python3", "scripts/job.py"],
            "checkpoint_paths": ["scripts", "tests"],
        }

    def test_default_plan_reaches_unanimous_first_round(self) -> None:
        result = run_consensus(build_plan(self._task()))
        self.assertTrue(result["ok"])
        self.assertEqual(result["round_count"], 1)
        self.assertEqual(
            [row["decision"] for row in result["rounds"][0]["reviews"]],
            ["approve", "approve", "approve"],
        )

    def test_structural_revision_is_capped_and_repaired_on_second_round(self) -> None:
        plan = build_plan(self._task())
        plan["non_goals"] = []
        plan["rollback_note"] = ""
        result = run_consensus(plan)
        self.assertTrue(result["ok"])
        self.assertEqual(result["round_count"], 2)

    def test_traversal_scope_is_blocked(self) -> None:
        plan = build_plan(self._task())
        plan["scope"] = ["../outside"]
        review = review_plan(plan, "architect", round_number=1)
        self.assertEqual(review["decision"], "block")
        self.assertFalse(run_consensus(plan)["ok"])

    def test_scope_prefix_collision_is_not_accepted(self) -> None:
        result = evaluate_scope(["scripts_evil/payload.py"], {"scope": ["scripts"]})
        self.assertFalse(result["ok"])
        self.assertEqual(result["violations"], ["scripts_evil/payload.py"])

    def test_nested_atomic_scope_is_allowed_within_plan_scope(self) -> None:
        plan = build_plan(
            {"task_id": "nested-scope", "task_name": "edit nested file", "profile": "risky_edit"},
            scope=["scripts"],
        )
        plan["tasks"][0]["scope"] = ["scripts/nested/tool.py"]
        review = review_plan(plan, "architect", round_number=1)
        self.assertEqual(review["decision"], "approve")


class ReadableTaskStateTests(unittest.TestCase):
    def test_bundle_is_resumable_and_redacts_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            state_root = workspace / ".openclaw"
            task = {
                "task_id": "state-1",
                "task_name": "demo",
                "profile": "risky_edit",
                "status": "failed",
                "operational_status": "needs_verification",
                "failure_reason": "Bearer abcdefghijklmnop",
                "next_action": "rerun tests",
                "evidence_gathering": {"api_token": "should-not-appear"},
            }
            meta = write_task_state_bundle(
                task,
                touched_paths=["scripts/demo.py"],
                workspace=workspace,
                state_root=state_root,
            )
            for name in ("state", "plan", "evidence", "blockers"):
                self.assertTrue((workspace / meta[name]).is_file())
            combined = "\n".join((workspace / meta[name]).read_text(encoding="utf-8") for name in ("state", "evidence", "blockers"))
            self.assertIn("rerun tests", combined)
            self.assertNotIn("abcdefghijklmnop", combined)
            self.assertNotIn("should-not-appear", combined)
            self.assertIn("[REDACTED]", combined)


class RuntimeFilesystemEvidenceTests(unittest.TestCase):
    def test_service_operation_needs_live_readback_to_be_verified(self) -> None:
        from runtime_contract import evaluate_finalization, prepare_runtime_contract
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            task = {"profile": "service_ops", "status": "completed", "exit_code": 0, "command": ["tool", "update"]}
            prepare_runtime_contract(task, [], workspace=workspace)
            self.assertFalse(evaluate_finalization(task)["ok"])
            task["evidence_refs"] = ["service_readback:calendar/event-1"]
            task["completion_claims"] = []
            prepare_runtime_contract(task, [], workspace=workspace)
            self.assertFalse(evaluate_finalization(task)["ok"])
            task["evidence_gathering"] = {
                "service_results": [{
                    "kind": "service_readback",
                    "ok": True,
                    "source": "calendar/event-1",
                    "provenance": "evidence_gatherer_command",
                }]
            }
            task["completion_claims"] = []
            prepare_runtime_contract(task, [], workspace=workspace)
            self.assertTrue(evaluate_finalization(task)["ok"])

    def test_empty_claims_and_unexecuted_remote_handoff_fail_closed(self) -> None:
        from runtime_contract import evaluate_finalization
        self.assertFalse(evaluate_finalization({"profile": "workspace_edit", "completion_claims": []})["ok"])
        remote = {
            "profile": "remote_handoff",
            "status": "handoff_required",
            "completion_claims": [{
                "claim_id": "forged",
                "claim": "forged",
                "evidence_type": "process_exit",
                "evidence_refs": ["process_exit:0"],
            }],
            "evidence_refs": ["process_exit:0"],
        }
        self.assertFalse(evaluate_finalization(remote)["ok"])


class VerifiedExecutionTests(unittest.TestCase):
    def _plan(self) -> dict:
        return {
            "task_id": "verified-1",
            "objective": "repair shared workflow",
            "profile": "risky_edit",
            "scope": ["scripts", "tests"],
            "tasks": [
                {
                    "task_id": "atomic-1",
                    "title": "apply fix",
                    "command": ["python3", "scripts/fix.py"],
                    "scope": ["scripts", "tests"],
                    "acceptance_criteria": ["focused tests pass"],
                    "evidence_commands": [["python3", "-m", "unittest", "tests.test_fix"]],
                    "max_attempts": 3,
                }
            ],
        }

    def test_executor_and_verifier_are_separate_and_architect_closes(self) -> None:
        from verified_execution import execute_verified_plan
        seen = {}
        def executor(plan, task, attempt):
            seen["executor"] = task["role_prompt"]
            return {"run_id": "run-1", "exit_code": 0, "stdout": "ok", "stderr": ""}
        def verifier(plan, task, execution):
            seen["verifier"] = task["role_prompt"]
            return {"ok": True, "decision": "pass", "criteria": [{"ok": True}]}
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            result = execute_verified_plan(
                self._plan(),
                workspace=workspace,
                state_root=workspace / ".openclaw",
                executor=executor,
                verifier=verifier,
            )
        self.assertTrue(result["ok"])
        attempt = result["tasks"][0]["attempts"][0]
        self.assertEqual(attempt["execution"]["role_marker"], "[role:executor]")
        self.assertEqual(attempt["verification"]["role_marker"], "[role:verifier]")
        self.assertEqual(result["final_architect_review"]["role_marker"], "[role:architect]")
        self.assertIn("Execute only", seen["executor"])
        self.assertIn("Independently evaluate", seen["verifier"])
        self.assertIn("Review the plan", result["final_architect_review"]["role_prompt"])

    def test_unknown_live_role_marker_fails_before_execution(self) -> None:
        import verified_execution
        with tempfile.TemporaryDirectory() as tmpdir, patch.object(verified_execution, "EXECUTOR_ROLE_MARKER", "[role:wizard]"), self.assertRaisesRegex(ValueError, "unknown role"):
            workspace = Path(tmpdir)
            verified_execution.execute_verified_plan(
                self._plan(),
                workspace=workspace,
                state_root=workspace / ".openclaw",
                executor=lambda plan, task, attempt: self.fail("executor must not run"),
            )

    def test_same_failure_blocks_after_three_attempts(self) -> None:
        from verified_execution import execute_verified_plan
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            result = execute_verified_plan(
                self._plan(),
                workspace=workspace,
                state_root=workspace / ".openclaw",
                executor=lambda plan, task, attempt: {"run_id": f"run-{attempt}", "exit_code": 1, "stdout": "", "stderr": "same error 123"},
                verifier=lambda plan, task, execution: {"ok": False, "decision": "fail", "failure_reason": "missing evidence"},
            )
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(len(result["tasks"][0]["attempts"]), 3)
        self.assertIn("repeated 3 times", result["blocker"])

    def test_standalone_default_executor_uses_non_recursive_attempt_flag(self) -> None:
        from verified_execution import execute_verified_plan
        completed = subprocess.CompletedProcess([], 0, stdout="ok", stderr="")
        with tempfile.TemporaryDirectory() as tmpdir, patch("verified_execution.subprocess.run", return_value=completed) as spawn:
            workspace = Path(tmpdir)
            result = execute_verified_plan(
                self._plan(),
                workspace=workspace,
                state_root=workspace / ".openclaw",
                verifier=lambda plan, task, execution: {"ok": True, "decision": "pass", "criteria": [{"ok": True}]},
            )
        self.assertTrue(result["ok"])
        runner_command = spawn.call_args.args[0]
        self.assertIn("--verified-attempt", runner_command)
        self.assertNotIn("--verified-execution", runner_command)
        import scripts.run_with_profile as run_with_profile
        parsed = run_with_profile.parse_run_args(["inspect_local", "--verified-attempt", "--", "echo", "ok"])
        self.assertTrue(parsed.verified_attempt)
        self.assertFalse(parsed.verified_execution)
        self.assertEqual(parsed.command, ["echo", "ok"])

    def test_plan_without_evidence_command_fails_before_execution(self) -> None:
        from verified_execution import execute_verified_plan
        plan = self._plan()
        plan["tasks"][0]["evidence_commands"] = []
        with tempfile.TemporaryDirectory() as tmpdir, self.assertRaisesRegex(ValueError, "evidence command"):
            workspace = Path(tmpdir)
            execute_verified_plan(plan, workspace=workspace, state_root=workspace / ".openclaw")

    def test_unsafe_plan_id_and_scope_fail_before_execution(self) -> None:
        from verified_execution import execute_verified_plan
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            bad_id = self._plan()
            bad_id["task_id"] = ".."
            with self.assertRaisesRegex(ValueError, "must start"):
                execute_verified_plan(bad_id, workspace=workspace, state_root=workspace / ".openclaw")
            bad_scope = self._plan()
            bad_scope["tasks"][0]["scope"] = ["../outside"]
            with self.assertRaisesRegex(ValueError, "unsafe scope"):
                execute_verified_plan(bad_scope, workspace=workspace, state_root=workspace / ".openclaw")

    def test_default_verifier_evaluates_each_criterion_against_matching_evidence(self) -> None:
        from verified_execution import default_verifier
        with tempfile.TemporaryDirectory() as tmpdir:
            state_root = Path(tmpdir)
            row = {
                "task_id": "run-1",
                "status": "completed",
                "exit_code": 0,
                "finalization_gate": {"ok": True},
                "scope_gate": {"ok": True},
                "evidence_gathering": {
                    "ok": True,
                    "command_results": [{"kind": "test", "ok": True, "argv": ["python3", "-m", "unittest"]}],
                },
                "evidence_refs": ["process_exit:0"],
                "completion_claims": [{
                    "claim_id": "command_completed",
                    "claim": "command_completed",
                    "evidence_type": "process_exit",
                    "evidence_refs": ["process_exit:0"],
                }],
            }
            (state_root / "task-ledger.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
            task = {"acceptance_criteria": [
                "focused tests pass",
                "deployment is delightful",
                "focused tests pass and service readback succeeds",
                "focused tests fail",
            ]}
            result = default_verifier({}, task, {"run_id": "run-1"}, state_root=state_root)
        self.assertTrue(result["criteria"][0]["ok"])
        self.assertEqual(result["criteria"][0]["matched_evidence_kind"], "test")
        self.assertFalse(result["criteria"][1]["ok"])
        self.assertEqual(result["criteria"][1]["reason"], "criterion_not_bound_to_evidence")
        self.assertFalse(result["criteria"][2]["ok"])
        self.assertEqual(result["criteria"][2]["matched_evidence_kind"], "test+service_readback")
        self.assertFalse(result["criteria"][3]["ok"])
        self.assertEqual(result["criteria"][3]["reason"], "criterion_not_bound_to_evidence")
        self.assertFalse(result["ok"])

    def test_run_with_profile_verified_flag_delegates_to_verified_plan_loop(self) -> None:
        import scripts.run_with_profile as run_with_profile
        command = [sys.executable, "-m", "unittest", "--help"]
        args = argparse.Namespace(
            profile="inspect_local", command=command, verified_execution=True,
            task_id=None, task_name="verified CLI", path=None,
            acceptance=["primary command exits successfully"], evidence_command_json=[command],
        )
        with patch("scripts.verified_execution.execute_verified_plan", return_value={"ok": True, "status": "completed"}) as execute:
            self.assertEqual(run_with_profile.cmd_run(args), 0)
        plan = execute.call_args.args[0]
        self.assertEqual(plan["tasks"][0]["command"], command)
        self.assertTrue(callable(execute.call_args.kwargs["executor"]))


class TouchedPathTests(unittest.TestCase):
    def test_tests_tree_and_newly_deleted_tracked_file_are_reported(self) -> None:
        from scripts.run_with_profile import _collect_deleted_paths, _collect_recent_paths
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            (workspace / "tests").mkdir()
            (workspace / "tests" / "test_new.py").write_text("pass\n", encoding="utf-8")
            (workspace / "scripts").mkdir()
            deleted = workspace / "scripts" / "old.py"
            deleted.write_text("old\n", encoding="utf-8")
            subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)
            subprocess.run(["git", "add", "scripts/old.py"], cwd=workspace, check=True)
            before = _collect_deleted_paths(workspace)
            deleted.unlink()
            paths = _collect_recent_paths(workspace, 0, deleted_before=before)
        self.assertIn("tests/test_new.py", paths)
        self.assertIn("scripts/old.py", paths)


class RuntimeRoleInjectionTests(unittest.TestCase):
    def _register(self, role: str) -> dict:
        from scripts.long_running_runtime import empty_runtime_state, register_agent
        return register_agent(
            empty_runtime_state(),
            agent_id="agent-1",
            role=role,
            allowed_tools=["read_file"],
            memory_scope="task",
            model_policy={"tier": "fast"},
            skill_profile="research",
            timeout=60,
            owner="coordinator",
            version="1",
            output_contract={"type": "review"},
        )

    def test_role_marker_is_expanded_at_registration(self) -> None:
        entry = self._register("[role:critic]")
        self.assertEqual(entry["role_id"], "critic")
        self.assertIn("Actively search", entry["role_prompt"])

    def test_unknown_role_marker_fails_fast(self) -> None:
        with self.assertRaisesRegex(ValueError, "unknown role"):
            self._register("[role:wizard]")


if __name__ == "__main__":
    unittest.main()
