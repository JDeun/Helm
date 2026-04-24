from __future__ import annotations

import argparse
import json
from pathlib import Path

from commands import (
    load_skill_policies,
    manifest_audit,
    read_json,
    target_root,
)
from scripts.skill_manifest_lib import load_skill_contract_manifests


def validate_workspace_config(root: Path) -> dict:
    issues: list[str] = []
    profiles_path = root / "references" / "execution_profiles.json"
    profiles_data = read_json(profiles_path, {})
    policies = load_skill_policies(root, root / "references" / "skill_profile_policies.json")
    manifest_report = manifest_audit(root, root / "references" / "skill_profile_policies.json", profiles_path)

    profiles = profiles_data.get("profiles", {}) if isinstance(profiles_data, dict) else {}
    if not isinstance(profiles, dict) or not profiles:
        issues.append("references/execution_profiles.json must define a non-empty `profiles` object.")
        profiles = {}

    valid_checkpoint_modes = {"never", "optional", "required", "manual"}
    for name, config in profiles.items():
        if not isinstance(config, dict):
            issues.append(f"profile `{name}` must map to an object.")
            continue
        for field in ("description", "backend", "checkpoint"):
            if field not in config:
                issues.append(f"profile `{name}` is missing required field `{field}`.")
        checkpoint_mode = config.get("checkpoint")
        if checkpoint_mode and checkpoint_mode not in valid_checkpoint_modes:
            issues.append(
                f"profile `{name}` uses invalid checkpoint mode `{checkpoint_mode}`. "
                f"Expected one of: {', '.join(sorted(valid_checkpoint_modes))}."
            )

    skills = policies
    for skill, policy in skills.items():
        if not isinstance(policy, dict):
            issues.append(f"skill policy `{skill}` must map to an object.")
            continue
        allowed = policy.get("allowed_profiles", [])
        default = policy.get("default_profile")
        if not isinstance(allowed, list) or not allowed:
            issues.append(f"skill policy `{skill}` must define a non-empty `allowed_profiles` list.")
            allowed = []
        for profile_name in allowed:
            if profile_name not in profiles:
                issues.append(f"skill policy `{skill}` references unknown profile `{profile_name}`.")
        if default is None:
            issues.append(f"skill policy `{skill}` is missing `default_profile`.")
        elif default not in profiles:
            issues.append(f"skill policy `{skill}` uses unknown default profile `{default}`.")
        elif allowed and default not in allowed:
            issues.append(f"skill policy `{skill}` default `{default}` is not present in `allowed_profiles`.")
    issues.extend(manifest_report["issues"])
    for skill in manifest_report["missing_contract_skills"]:
        issues.append(f"skill `{skill}` is missing contract.json")

    return {
        "workspace": str(root),
        "profile_count": len(profiles),
        "skill_policy_count": len(skills),
        "manifest_count": manifest_report["manifest_count"],
        "issues": issues,
        "ok": not issues,
    }


def cmd_validate(args: argparse.Namespace) -> int:
    root = target_root(args.path)
    payload = validate_workspace_config(root)
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0 if payload["ok"] else 1
    print(f"workspace={payload['workspace']}")
    print(f"profile_count={payload['profile_count']}")
    print(f"skill_policy_count={payload['skill_policy_count']}")
    if payload["ok"]:
        print("validation=ok")
        return 0
    print("validation=failed")
    for issue in payload["issues"]:
        print(f"issue={issue}")
    return 1
