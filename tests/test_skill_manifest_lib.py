from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.skill_manifest_lib import audit_skill_markdown_contracts, manifest_quality_audit


class SkillManifestLibTests(unittest.TestCase):
    def test_template_headings_do_not_trigger_placeholder_warning(self) -> None:
        skill_md = """# Demo

## Core rule

Use the strict runner for all mutations.

## Input contract

- Required inputs: account and date range
- Optional inputs: prior task references
- Ask first when missing: ask for the account id
- If the request is broad or ambiguous, how it must be narrowed: require one ledger target

## Decision contract

- State the decision order explicitly: validate, inspect, then mutate
- Route outward only when the request becomes account-bound or needs a human login.
- Red flags: ambiguous account, missing ledger period, or request to imply success after a failed write.

## Execution contract

- State the real commands, tools, or APIs to use.
- Prefer deterministic scripts over freeform shell improvisation.
- If the workflow has risk, mention the execution profile or checkpoint rule.

## Output contract

- Default output format: summary plus changed files
- Always include: account, date range, and next step
- Length rule: short unless reconciliation failed
- Do not say or Do not imply: success when writeback failed

## Failure contract

- Failure types: missing input, tool failure
- Fallback behavior: stop before mutation
- User-facing failure language: explain the blocked step plainly
"""
        warnings = audit_skill_markdown_contracts(skill_md, {"allowed_profiles": ["inspect_local"]})
        self.assertNotIn("SKILL.md still contains template-style placeholder language", warnings)

    def test_manifest_without_skill_markdown_is_flagged(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "references").mkdir()
            (root / "skills" / "demo-skill").mkdir(parents=True)
            (root / "references" / "execution_profiles.json").write_text(
                json.dumps({"profiles": {"inspect_local": {}, "service_ops": {}}}),
                encoding="utf-8",
            )
            (root / "skills" / "demo-skill" / "contract.json").write_text(
                json.dumps(
                    {
                        "skill": "demo-skill",
                        "allowed_profiles": ["inspect_local", "service_ops"],
                        "default_profile": "inspect_local",
                        "context": {"required": False, "query": "demo"},
                    }
                ),
                encoding="utf-8",
            )

            report = manifest_quality_audit(root, root / "references" / "execution_profiles.json")

            self.assertFalse(report["ok"])
            self.assertEqual(report["flagged_count"], 1)
            self.assertIn("manifest exists but SKILL.md operator contract is missing", report["items"][0]["warnings"])

    def test_skill_draft_scripts_without_runner_are_flagged(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "references").mkdir()
            (root / "skill_drafts" / "draft-skill" / "scripts").mkdir(parents=True)
            (root / "references" / "execution_profiles.json").write_text(
                json.dumps({"profiles": {"service_ops": {}, "risky_edit": {}}}),
                encoding="utf-8",
            )
            (root / "skill_drafts" / "draft-skill" / "contract.json").write_text(
                json.dumps(
                    {
                        "skill": "draft-skill",
                        "allowed_profiles": ["service_ops"],
                        "default_profile": "service_ops",
                        "context": {"required": False, "query": "draft"},
                    }
                ),
                encoding="utf-8",
            )
            (root / "skill_drafts" / "draft-skill" / "scripts" / "runner.sh").write_text(
                "#!/bin/sh\nexit 0\n",
                encoding="utf-8",
            )

            report = manifest_quality_audit(root, root / "references" / "execution_profiles.json")

            self.assertFalse(report["ok"])
            warnings = report["items"][0]["warnings"]
            self.assertIn("skill ships scripts but no runner guidance is declared", warnings)

    def test_file_intake_manifest_without_skill_guidance_is_flagged(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "references").mkdir()
            (root / "skills" / "file-skill").mkdir(parents=True)
            (root / "references" / "execution_profiles.json").write_text(
                json.dumps({"profiles": {"inspect_local": {}}}),
                encoding="utf-8",
            )
            (root / "skills" / "file-skill" / "contract.json").write_text(
                json.dumps(
                    {
                        "skill": "file-skill",
                        "allowed_profiles": ["inspect_local"],
                        "default_profile": "inspect_local",
                        "file_intake": {"required": True},
                    }
                ),
                encoding="utf-8",
            )
            (root / "skills" / "file-skill" / "SKILL.md").write_text(
                "# File Skill\n\n## Core rule\n\nInspect first.\n",
                encoding="utf-8",
            )

            report = manifest_quality_audit(root, root / "references" / "execution_profiles.json")

            self.assertFalse(report["ok"])
            warnings = report["items"][0]["warnings"]
            self.assertIn("manifest requires file intake evidence but SKILL.md does not explain the intake boundary", warnings)


if __name__ == "__main__":
    unittest.main()
