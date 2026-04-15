from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


class CliValidationTests(unittest.TestCase):
    def test_helm_validate_reports_missing_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "references").mkdir()
            (root / "skills" / "demo-skill").mkdir(parents=True)
            (root / "references" / "execution_profiles.json").write_text(
                json.dumps({"profiles": {"inspect_local": {}, "workspace_edit": {}}}),
                encoding="utf-8",
            )
            (root / "references" / "skill_profile_policies.json").write_text(
                json.dumps({"skills": {}}),
                encoding="utf-8",
            )
            (root / "skills" / "demo-skill" / "SKILL.md").write_text("# Demo\n", encoding="utf-8")

            result = subprocess.run(
                [sys.executable, str(REPO_ROOT / "helm.py"), "validate", "--path", str(root), "--json"],
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 1)
            payload = json.loads(result.stdout)
            self.assertFalse(payload["ok"])
            self.assertIn("skill `demo-skill` is missing contract.json", payload["issues"])

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


if __name__ == "__main__":
    unittest.main()
