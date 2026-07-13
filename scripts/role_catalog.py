#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


MARKER_RE = re.compile(r"\[role:([a-z][a-z0-9-]*)\]")


def default_catalog_path() -> Path:
    return Path(__file__).resolve().parents[1] / "references" / "role_catalog.json"


def load_role_catalog(path: Path | None = None) -> dict[str, dict]:
    target = path or default_catalog_path()
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"role catalog not found: {target}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid role catalog JSON: {exc}") from exc
    roles = payload.get("roles") if isinstance(payload, dict) else None
    if not isinstance(roles, dict) or not roles:
        raise ValueError("role catalog requires a non-empty roles object")
    for role_id, role in roles.items():
        if not isinstance(role_id, str) or not MARKER_RE.fullmatch(f"[role:{role_id}]"):
            raise ValueError(f"invalid role id: {role_id!r}")
        if not isinstance(role, dict) or not str(role.get("prompt") or "").strip():
            raise ValueError(f"role {role_id!r} requires a prompt")
    return roles


def role_markers(text: str) -> list[str]:
    return MARKER_RE.findall(text or "")


def resolve_role(role_id: str, *, catalog_path: Path | None = None) -> dict:
    roles = load_role_catalog(catalog_path)
    if role_id not in roles:
        raise ValueError(f"unknown role: {role_id}")
    return {"role_id": role_id, **roles[role_id]}


def expand_role_markers(text: str, *, catalog_path: Path | None = None) -> dict:
    markers = role_markers(text)
    if not markers:
        raise ValueError("role marker missing; expected [role:<role-id>]")
    if len(markers) != 1:
        raise ValueError("exactly one role marker is allowed")
    role = resolve_role(markers[0], catalog_path=catalog_path)
    expanded = MARKER_RE.sub(f"[role:{role['role_id']}]\n{role['prompt']}", text, count=1)
    return {"role": role, "original": text, "expanded": expanded}


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate and expand a centralized agent role marker.")
    parser.add_argument("text")
    parser.add_argument("--catalog", type=Path)
    args = parser.parse_args()
    try:
        payload = expand_role_markers(args.text, catalog_path=args.catalog)
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
