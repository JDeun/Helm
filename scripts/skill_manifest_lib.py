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
