#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path


def load_json(path: Path, default: object) -> object:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def load_legacy_skill_policies(path: Path) -> dict[str, dict]:
    data = load_json(path, {})
    return data.get("skills", {}) if isinstance(data, dict) else {}


def load_skill_contract_manifests(workspace: Path) -> dict[str, dict]:
    manifests: dict[str, dict] = {}
    for root in (workspace / "skill_drafts", workspace / "skills"):
        if not root.exists():
            continue
        for contract_path in sorted(root.glob("*/contract.json")):
            data = load_json(contract_path, {})
            if isinstance(data, dict) and data:
                manifests[contract_path.parent.name] = data
    return manifests


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
