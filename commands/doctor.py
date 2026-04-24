from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from commands import (
    REQUIRED_REFERENCE_FILES,
    load_context_sources,
    relative_or_absolute,
    state_root_for,
    suggest_external_sources,
    target_root,
    DEFAULT_WORKSPACE,
    detect_layout,
)
from commands.context import build_onboarding_payload, format_onboarding_text


def cmd_doctor(args: argparse.Namespace) -> int:
    root = target_root(args.path)
    layout = detect_layout(root)
    state_root = state_root_for(root)
    suggestions = suggest_external_sources()

    checks: list[dict] = []
    for filename in REQUIRED_REFERENCE_FILES:
        path = root / "references" / filename
        ok = path.exists()
        detail = "present" if ok else "missing"
        if ok and path.suffix == ".json":
            try:
                json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                ok = False
                detail = f"invalid json: {exc}"
        checks.append({"name": f"references/{filename}", "ok": ok, "detail": detail})

    state_dir_relative = relative_or_absolute(state_root, root)
    checkpoints_relative = relative_or_absolute(state_root / "checkpoints", root)
    for relative in ("skills", "skill_drafts", "memory", state_dir_relative, checkpoints_relative):
        path = root / relative
        checks.append(
            {
                "name": relative,
                "ok": path.exists(),
                "detail": "present" if path.exists() else "missing",
            }
        )

    context_sources = load_context_sources(root)
    if not context_sources:
        checks.append(
            {
                "name": ".helm/context_sources.json",
                "ok": True,
                "detail": "no external context sources registered",
            }
        )
    else:
        for source in context_sources:
            exists = source.root.exists()
            checks.append(
                {
                    "name": f"context-source:{source.name}",
                    "ok": exists,
                    "detail": f"{source.kind} -> {source.root}" if exists else f"missing target: {source.root}",
                }
            )

    adopted_roots = {source.root for source in context_sources}
    adopted_kinds = {source.kind for source in context_sources}
    for kind in ("openclaw", "hermes"):
        candidates = [path for path in suggestions.get(kind, []) if path not in adopted_roots]
        if candidates:
            checks.append(
                {
                    "name": f"onboarding:{kind}",
                    "ok": True,
                    "detail": (
                        f"candidate detected at {candidates[0]}. "
                        f"Consider `helm adopt --path {root} --from-path {candidates[0]} --name {kind}-main`."
                    ),
                }
            )
        elif kind in adopted_kinds:
            checks.append(
                {
                    "name": f"onboarding:{kind}",
                    "ok": True,
                    "detail": "already adopted",
                }
            )

    obsidian_candidates = [path for path in suggestions.get("obsidian", []) if path not in adopted_roots]
    if obsidian_candidates:
        checks.append(
            {
                "name": "onboarding:obsidian",
                "ok": True,
                "detail": (
                    f"Obsidian vault candidate detected at {obsidian_candidates[0]}. "
                    "Obsidian is optional, but a Markdown vault is strongly recommended for durable file-native notes. "
                    f"Consider `helm adopt --path {root} --from-path {obsidian_candidates[0]} --kind generic --name obsidian-main`."
                ),
            }
        )
    elif any((source.root / ".obsidian").exists() for source in context_sources):
        checks.append(
            {
                "name": "onboarding:obsidian",
                "ok": True,
                "detail": "already adopted",
            }
        )
    else:
        checks.append(
            {
                "name": "onboarding:notes-vault",
                "ok": True,
                "detail": (
                    "No Obsidian vault was detected in common locations. "
                    "Obsidian is optional, but explicit Markdown notes are recommended for durable context hydration."
                ),
            }
        )

    if layout.kind in {"openclaw", "hermes"}:
        checks.append(
            {
                "name": "workspace-separation",
                "ok": False,
                "detail": (
                    f"Detected external {layout.kind} layout. Helm should usually be initialized in a separate workspace "
                    "and adopt external state explicitly."
                ),
            }
        )

    healthy = all(item["ok"] for item in checks)
    payload = {
        "workspace": str(root),
        "layout": layout.kind,
        "healthy": healthy,
        "checks": checks,
    }
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0 if healthy else 1
    print(f"workspace={payload['workspace']}")
    print(f"layout={payload['layout']}")
    print(f"healthy={'yes' if healthy else 'no'}")
    for item in checks:
        status = "ok" if item["ok"] else "fail"
        print(f"{status:>4} {item['name']}: {item['detail']}")
    return 0 if healthy else 1


def cmd_survey(args: argparse.Namespace) -> int:
    root = target_root(args.path or str(DEFAULT_WORKSPACE))
    payload = build_onboarding_payload(root)
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0
    print(format_onboarding_text(payload))
    return 0
