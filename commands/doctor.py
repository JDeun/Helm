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
from scripts.discovery import discover_environment, snapshot_to_json


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

    # --- Discovery sections ---
    try:
        snapshot = discover_environment(workspace=root, timeout_ms=500)
        discovery_payload = snapshot_to_json(snapshot)
    except Exception:
        snapshot = None
        discovery_payload = None

    # --- Ops DB check ---
    ops_db_path = state_root / "ops-index.sqlite3"
    checks.append({
        "name": "ops-db",
        "ok": True,  # ops db missing is not a failure
        "detail": f"present at {ops_db_path}" if ops_db_path.exists() else "missing (run helm db init to create)",
    })
    if snapshot:
        discovery_payload["ops_db"] = {
            "path": str(ops_db_path),
            "status": "present" if ops_db_path.exists() else "missing",
        }

    healthy = all(item["ok"] for item in checks)
    payload = {
        "workspace": str(root),
        "layout": layout.kind,
        "healthy": healthy,
        "checks": checks,
    }
    if snapshot:
        payload["discovery"] = discovery_payload
        payload["hardware"] = discovery_payload.get("hardware", {})
        payload["runtime_model_state"] = discovery_payload.get("runtime_model_state", {})
        payload["helm_intelligence_state"] = discovery_payload.get("helm_intelligence_state", {})
        payload["guard"] = {
            "default_mode": "enforce",
            "unknown_command_action": "require_approval",
            "policy_file": str(root / "references" / "guard_policy.json"),
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

    if snapshot and not args.json:
        print()
        print("Discovery:")
        print(f"  runtime_kind: {snapshot.runtime.kind}")
        print(f"  runtime_confidence: {snapshot.runtime.confidence}")
        print(f"  adapter: {snapshot.runtime.adapter}")
        print(f"  markers: {', '.join(snapshot.runtime.markers) or 'none'}")
        print()
        print("Hardware:")
        print(f"  os: {snapshot.hardware.os_name}")
        print(f"  machine: {snapshot.hardware.machine}")
        if snapshot.hardware.is_apple_silicon:
            print("  apple_silicon: true")
        if snapshot.hardware.memory_total_gb is not None:
            print(f"  memory_total_gb: {snapshot.hardware.memory_total_gb:.1f}")
            print(f"  low_ram_strategy: {snapshot.hardware.low_ram}")
        print(f"  python_version: {snapshot.hardware.python_version}")
        print()
        rms = snapshot.runtime_model_state
        print("Runtime model state:")
        print(f"  detected: {rms.runtime_model_detected}")
        print(f"  mode: {rms.mode}")
        print(f"  priority: {rms.priority}")
        print(f"  readiness: {rms.readiness}")
        if rms.api_candidates:
            print("  api_candidates:")
            for c in rms.api_candidates:
                name = c.get("provider", c) if isinstance(c, dict) else c
                print(f"    - {name}")
        if rms.local_candidates:
            print("  local_candidates:")
            for c in rms.local_candidates:
                name = c.get("provider", c) if isinstance(c, dict) else c
                print(f"    - {name}")
        print()
        his = snapshot.helm_intelligence_state
        print("Helm intelligence state:")
        print(f"  mode: {his.mode}")
        print(f"  cloud_calls_enabled: {his.cloud_calls_enabled}")
        print(f"  local_model_calls_enabled: {his.local_model_calls_enabled}")
        print(f"  reason: {his.reason}")
        print()
        print("Guard:")
        print("  default_mode: enforce")
        policy_path = root / "references" / "guard_policy.json"
        print(f"  policy_file: {policy_path}")
        print(f"  policy_exists: {policy_path.exists()}")
        print()
        print("Ops DB:")
        print(f"  path: {ops_db_path}")
        print(f"  status: {'present' if ops_db_path.exists() else 'missing'}")

    return 0 if healthy else 1


def cmd_survey(args: argparse.Namespace) -> int:
    root = target_root(args.path or str(DEFAULT_WORKSPACE))
    payload = build_onboarding_payload(root)
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0
    print(format_onboarding_text(payload))
    return 0
