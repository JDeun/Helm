from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from helm_context import adopt_context_source, configured_context_sources, load_context_sources, onboarding_root
from helm_workspace import DEFAULT_WORKSPACE, detect_layout, discover_workspace, resolve_nested_workspace, suggest_external_sources
from scripts.memory_ops import review_queue_items
from scripts.skill_manifest_lib import load_skill_contract_manifests, load_skill_policies, manifest_audit
from scripts.state_snapshot import latest_snapshot_path

ROOT = Path(__file__).resolve().parent.parent
REFERENCES_ROOT = ROOT / "references"
SCRIPT_ROOT = ROOT / "scripts"
REQUIRED_REFERENCE_FILES = (
    "execution_profiles.json",
    "model_recovery_policy.json",
    "skill-capture-template.md",
    "skill-contract-template.json",
)


def _warn_parse_failure(path: Path, detail: str) -> None:
    print(f"warning: ignoring malformed state file {path}: {detail}", file=sys.stderr)


def read_json(path: Path, default: object) -> object:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _warn_parse_failure(path, str(exc))
        return default


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows: list[dict] = []
    try:
        f = open(path, "r", encoding="utf-8")
    except OSError as exc:
        _warn_parse_failure(path, str(exc))
        return rows
    with f:
        for lineno, line in enumerate(f, start=1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                _warn_parse_failure(path, f"line {lineno}: {exc}")
                continue
            if not isinstance(payload, dict):
                _warn_parse_failure(path, f"line {lineno}: expected JSON object")
                continue
            rows.append(payload)
    return rows


def state_root_for(root: Path) -> Path:
    return detect_layout(root).state_root


def memory_review_queue_count_for(root: Path) -> int:
    return len(review_queue_items(state_root_for(root), None))


def relative_or_absolute(path: Path, base: Path) -> str:
    try:
        return str(path.relative_to(base))
    except ValueError:
        return str(path)


def target_root(path: str | None, *, create: bool = False) -> Path:
    if path:
        root = Path(path).expanduser()
    else:
        root = Path.cwd()
    if create:
        root.mkdir(parents=True, exist_ok=True)
        return root.resolve()
    resolved = root.resolve()
    layout = detect_layout(resolved)
    if layout.kind in {"helm", "openclaw", "hermes"}:
        return resolved
    nested = resolve_nested_workspace(resolved)
    if nested is not None:
        return nested.root
    return resolved


def run_script(script_name: str, script_args: list[str], workspace: Path | None = None) -> int:
    script_path = SCRIPT_ROOT / script_name
    env = os.environ.copy()
    if workspace is not None:
        env["HELM_WORKSPACE"] = str(workspace)
    result = subprocess.run([sys.executable, str(script_path), *script_args], env=env)
    return result.returncode


__all__ = [
    "ROOT",
    "REFERENCES_ROOT",
    "SCRIPT_ROOT",
    "REQUIRED_REFERENCE_FILES",
    "_warn_parse_failure",
    "read_json",
    "read_jsonl",
    "state_root_for",
    "memory_review_queue_count_for",
    "relative_or_absolute",
    "target_root",
    "run_script",
    "adopt_context_source",
    "configured_context_sources",
    "load_context_sources",
    "onboarding_root",
    "DEFAULT_WORKSPACE",
    "detect_layout",
    "discover_workspace",
    "resolve_nested_workspace",
    "suggest_external_sources",
    "load_skill_contract_manifests",
    "load_skill_policies",
    "manifest_audit",
    "latest_snapshot_path",
]
