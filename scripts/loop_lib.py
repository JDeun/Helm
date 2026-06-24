from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml


REQUIRED_FIELDS = (
    "id",
    "title",
    "use_when",
    "steps",
    "required_evidence",
    "stop_conditions",
)


def load_loop_file(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".json":
        payload = json.loads(text)
    else:
        payload = yaml.safe_load(text)
    if not isinstance(payload, dict):
        raise ValueError(f"loop file must contain an object: {path}")
    return payload


def validate_loop(payload: dict[str, Any]) -> dict[str, Any]:
    issues: list[str] = []
    for field in REQUIRED_FIELDS:
        if not payload.get(field):
            issues.append(f"missing `{field}`")
    if payload.get("steps") and not isinstance(payload["steps"], list):
        issues.append("`steps` must be a list")
    if payload.get("required_evidence") and not isinstance(payload["required_evidence"], list):
        issues.append("`required_evidence` must be a list")
    stop_conditions = payload.get("stop_conditions")
    if stop_conditions and not isinstance(stop_conditions, dict):
        issues.append("`stop_conditions` must be an object")
    if stop_conditions and not any(key in stop_conditions for key in ("success", "blocked", "incomplete")):
        issues.append("`stop_conditions` must define success, blocked, or incomplete")
    return {"id": payload.get("id"), "ok": not issues, "issues": issues}


def candidate_loop_paths(root: Path) -> list[Path]:
    paths: list[Path] = []
    for rel in ("references/loops", "examples/loops"):
        folder = root / rel
        if folder.exists():
            paths.extend(sorted(folder.glob("*.yaml")))
            paths.extend(sorted(folder.glob("*.json")))
    return paths


def find_loop(root: Path, loop_id: str) -> tuple[Path, dict[str, Any]] | None:
    for path in candidate_loop_paths(root):
        payload = load_loop_file(path)
        if payload.get("id") == loop_id or path.stem == loop_id:
            return path, payload
    return None
