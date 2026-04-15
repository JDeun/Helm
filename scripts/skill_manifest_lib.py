#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from pathlib import Path


SKILL_ROOTS = ("skill_drafts", "skills")


def load_json(path: Path, default: object) -> object:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def load_legacy_skill_policies(path: Path) -> dict[str, dict]:
    data = load_json(path, {})
    return data.get("skills", {}) if isinstance(data, dict) else {}


def load_skill_contract_manifests(workspace: Path) -> dict[str, dict]:
    manifests: dict[str, dict] = {}
    for root_name in SKILL_ROOTS:
        root = workspace / root_name
        if not root.exists():
            continue
        for contract_path in sorted(root.glob("*/contract.json")):
            data = load_json(contract_path, {})
            if isinstance(data, dict) and data:
                manifests[contract_path.parent.name] = data
    return manifests


def resolve_skill_dir(workspace: Path, skill: str) -> Path | None:
    for root_name in SKILL_ROOTS:
        path = workspace / root_name / skill
        if path.exists():
            return path
    return None


def load_skill_markdown(workspace: Path, skill: str) -> str | None:
    skill_dir = resolve_skill_dir(workspace, skill)
    if skill_dir:
        path = skill_dir / "SKILL.md"
        if path.exists():
            return path.read_text(encoding="utf-8")
    return None


def audit_skill_markdown_contracts(text: str, manifest: dict) -> list[str]:
    warnings: list[str] = []
    normalized = text.casefold()
    allowed = manifest.get("allowed_profiles") or []
    context = manifest.get("context") or {}
    approval_keywords = manifest.get("approval_keywords") or []
    runner = manifest.get("runner") or {}

    required_sections = (
        "## core rule",
        "## input contract",
        "## decision contract",
        "## output contract",
        "## failure contract",
    )
    for section in required_sections:
        if section not in normalized:
            warnings.append(f"SKILL.md missing `{section}` section")

    if "## input contract" in normalized and "ask first when missing" not in normalized:
        warnings.append("SKILL.md input contract missing `Ask first when missing` guidance")

    if "## decision contract" in normalized and "red flags" not in normalized:
        warnings.append("SKILL.md decision contract missing `Red flags` guidance")

    if "## output contract" in normalized:
        if "default output format" not in normalized:
            warnings.append("SKILL.md output contract missing `Default output format` guidance")
        if "always include" not in normalized:
            warnings.append("SKILL.md output contract missing `Always include` guidance")

    if "## failure contract" in normalized:
        if "failure types" not in normalized:
            warnings.append("SKILL.md failure contract missing `Failure types` guidance")
        if "fallback behavior" not in normalized:
            warnings.append("SKILL.md failure contract missing `Fallback behavior` guidance")
        if "user-facing failure language" not in normalized:
            warnings.append("SKILL.md failure contract missing `User-facing failure language` guidance")

    placeholder_patterns = (
        r"^state the narrow rule that governs this skill\.$",
        r"^this should explain how the skill stays safe, focused, and predictable\.$",
        r"^- required inputs$",
        r"^- optional inputs$",
        r"^- ask first when missing$",
        r"^- if the request is broad or ambiguous, how it must be narrowed$",
        r"^- state the decision order explicitly\.$",
        r"^- list the red flags weaker models are likely to miss\.$",
        r"^- default output format$",
        r"^- failure types$",
    )
    for pattern in placeholder_patterns:
        if re.search(pattern, normalized, re.MULTILINE):
            warnings.append("SKILL.md still contains template-style placeholder language")
            break

    if context.get("required"):
        context_markers = (
            "## context contract",
            "prior context",
            "saved context",
            "hydrate",
            "hydration",
            "recent failures",
        )
        if not any(marker in normalized for marker in context_markers):
            warnings.append("manifest requires context but SKILL.md does not explain context dependency")

    if "remote_handoff" in allowed:
        approval_markers = (
            "approval",
            "ask for user confirmation",
            "hitl",
            "human-in-the-loop",
            "handoff",
            "account-bound",
            "remote_handoff",
        )
        if not any(marker in normalized for marker in approval_markers):
            warnings.append("manifest allows remote handoff but SKILL.md does not expose an approval or handoff boundary")

    if approval_keywords:
        if "approval" not in normalized and "ask for user confirmation" not in normalized and "hitl" not in normalized:
            warnings.append("manifest declares approval_keywords but SKILL.md does not describe the approval boundary")

    if any(profile in allowed for profile in ("service_ops", "risky_edit")):
        execution_markers = (
            "## execution contract",
            "## tool routing contract",
            "## execution profiles",
            "commands:",
            "```bash",
            "tools",
            "apis",
            "scripts",
        )
        if not any(marker in normalized for marker in execution_markers):
            warnings.append("mutation or service-capable manifest lacks concrete execution guidance in SKILL.md")

    if runner.get("strict_required"):
        runner_markers = (
            "strict runner",
            "guarded runner",
            "runner example",
            "strict harness",
            "entrypoint",
        )
        if not any(marker in normalized for marker in runner_markers):
            warnings.append("manifest requires a strict runner but SKILL.md does not explain the guarded runner path")

    return warnings


def load_skill_policies(workspace: Path, legacy_policy_path: Path) -> dict[str, dict]:
    policies = load_legacy_skill_policies(legacy_policy_path)
    manifests = load_skill_contract_manifests(workspace)
    for skill, manifest in manifests.items():
        allowed = manifest.get("allowed_profiles")
        default = manifest.get("default_profile")
        if allowed or default:
            current = dict(policies.get(skill, {}))
            if allowed:
                current["allowed_profiles"] = allowed
            if default:
                current["default_profile"] = default
            policies[skill] = current
    return policies


def load_profiles(profile_path: Path) -> dict[str, dict]:
    data = load_json(profile_path, {})
    return data.get("profiles", {}) if isinstance(data, dict) else {}


def validate_contract_manifest(skill: str, manifest: dict, profiles: dict[str, dict]) -> list[str]:
    issues: list[str] = []
    allowed = manifest.get("allowed_profiles")
    default = manifest.get("default_profile")
    context = manifest.get("context", {})
    runner = manifest.get("runner", {})
    approval_keywords = manifest.get("approval_keywords", [])

    if allowed is not None:
        if not isinstance(allowed, list) or not allowed:
            issues.append(f"{skill}: allowed_profiles must be a non-empty list when present")
        else:
            for profile_name in allowed:
                if profile_name not in profiles:
                    issues.append(f"{skill}: unknown profile `{profile_name}` in allowed_profiles")
    if default is not None:
        if not isinstance(default, str):
            issues.append(f"{skill}: default_profile must be a string")
        elif default not in profiles:
            issues.append(f"{skill}: unknown default_profile `{default}`")
        elif isinstance(allowed, list) and allowed and default not in allowed:
            issues.append(f"{skill}: default_profile `{default}` must be included in allowed_profiles")

    if context:
        if not isinstance(context, dict):
            issues.append(f"{skill}: context must be an object")
        else:
            required = context.get("required")
            include = context.get("include")
            if required is not None and not isinstance(required, bool):
                issues.append(f"{skill}: context.required must be a boolean")
            if include is not None and (
                not isinstance(include, list) or any(not isinstance(item, str) for item in include)
            ):
                issues.append(f"{skill}: context.include must be a string list")

    if approval_keywords and (
        not isinstance(approval_keywords, list) or any(not isinstance(item, str) for item in approval_keywords)
    ):
        issues.append(f"{skill}: approval_keywords must be a string list")

    if runner:
        if not isinstance(runner, dict):
            issues.append(f"{skill}: runner must be an object")
        else:
            entrypoint = runner.get("entrypoint")
            strict_required = runner.get("strict_required")
            if entrypoint is not None and not isinstance(entrypoint, str):
                issues.append(f"{skill}: runner.entrypoint must be a string")
            if strict_required is not None and not isinstance(strict_required, bool):
                issues.append(f"{skill}: runner.strict_required must be a boolean")
            if strict_required and not entrypoint:
                issues.append(f"{skill}: runner.entrypoint is required when strict_required is true")

    return issues


def manifest_audit(workspace: Path, legacy_policy_path: Path, profile_path: Path) -> dict:
    profiles = load_profiles(profile_path)
    manifests = load_skill_contract_manifests(workspace)
    issues: list[str] = []
    missing: list[str] = []

    skills_root = workspace / "skills"
    if skills_root.exists():
        for skill_dir in sorted(skills_root.iterdir()):
            if skill_dir.is_dir() and (skill_dir / "SKILL.md").exists() and skill_dir.name not in manifests:
                missing.append(skill_dir.name)

    for skill, manifest in manifests.items():
        issues.extend(validate_contract_manifest(skill, manifest, profiles))

    return {
        "profiles": sorted(profiles.keys()),
        "manifest_count": len(manifests),
        "missing_contract_skills": missing,
        "issues": issues,
        "ok": not issues and not missing,
    }


def manifest_quality_audit(workspace: Path, profile_path: Path) -> dict:
    profiles = load_profiles(profile_path)
    manifests = load_skill_contract_manifests(workspace)
    full_profile_set = sorted(profiles.keys())
    items: list[dict] = []

    for skill, manifest in sorted(manifests.items()):
        allowed = manifest.get("allowed_profiles") or []
        default = manifest.get("default_profile")
        context = manifest.get("context") or {}
        runner = manifest.get("runner") or {}
        approval_keywords = manifest.get("approval_keywords") or []
        warnings: list[str] = []

        if sorted(allowed) == full_profile_set:
            warnings.append("allowed_profiles still spans every profile")
        if default == "inspect_local" and any(p in allowed for p in ("workspace_edit", "risky_edit")) and not runner:
            warnings.append("default_profile remains inspect_local while local mutation-capable profiles are enabled")
        if not context.get("required") and not context.get("query"):
            warnings.append("context policy is still generic")
        if not approval_keywords and "remote_handoff" in allowed:
            warnings.append("approval_keywords missing despite remote handoff capability")
        if not runner and "risky_edit" in allowed:
            warnings.append("runner policy missing for a risky-edit capable skill")

        skill_dir = resolve_skill_dir(workspace, skill)
        if skill_dir is None:
            warnings.append("manifest exists but skill directory is missing")
        elif not (skill_dir / "SKILL.md").exists():
            warnings.append("manifest exists but SKILL.md operator contract is missing")

        if skill_dir and (skill_dir / "scripts").exists() and not runner and any(
            p in allowed for p in ("service_ops", "risky_edit")
        ):
            warnings.append("skill ships scripts but no runner guidance is declared")

        skill_md = load_skill_markdown(workspace, skill)
        if skill_md:
            warnings.extend(audit_skill_markdown_contracts(skill_md, manifest))

        if warnings:
            items.append(
                {
                    "skill": skill,
                    "allowed_profiles": allowed,
                    "default_profile": default,
                    "warnings": warnings,
                }
            )

    return {
        "manifest_count": len(manifests),
        "flagged_count": len(items),
        "items": items,
        "ok": len(items) == 0,
    }
