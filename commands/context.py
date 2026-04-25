from __future__ import annotations

import argparse
import json
from pathlib import Path

from commands import (
    DEFAULT_WORKSPACE,
    adopt_context_source,
    configured_context_sources,
    detect_layout,
    load_context_sources,
    onboarding_root,
    read_jsonl,
    state_root_for,
    suggest_external_sources,
    target_root,
    latest_snapshot_path,
)
from scripts.skill_manifest_lib import load_skill_contract_manifests


def latest_tasks(entries: list[dict]) -> list[dict]:
    by_task: dict[str, dict] = {}
    for entry in entries:
        task_id = entry.get("task_id")
        if task_id:
            by_task[task_id] = entry
    return sorted(by_task.values(), key=lambda item: item.get("started_at", ""))


def load_draft_assessments(root: Path) -> list[dict]:
    from commands import _warn_parse_failure
    assessments: list[dict] = []
    drafts_root = root / "skill_drafts"
    if not drafts_root.exists():
        return assessments
    for draft in sorted(drafts_root.iterdir()):
        if not draft.is_dir():
            continue
        assessment = draft / "meta" / "assessment.json"
        if not assessment.exists():
            continue
        try:
            payload = json.loads(assessment.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            _warn_parse_failure(assessment, str(exc))
            continue
        if not isinstance(payload, dict):
            _warn_parse_failure(assessment, "expected JSON object")
            continue
        assessments.append(payload)
    return assessments


def resolve_skill_contract_for_root(root: Path, skill: str | None) -> dict:
    if not skill:
        return {}
    return load_skill_contract_manifests(root).get(skill, {})


def _task_capability_fields(task: dict | None, root: Path) -> dict:
    if not task:
        return {}
    harness = ((task.get("meta") or {}).get("harness") or {})
    contract = resolve_skill_contract_for_root(root, task.get("skill"))
    return {
        "task_id": task.get("task_id"),
        "task_name": task.get("task_name"),
        "status": task.get("status"),
        "skill": task.get("skill"),
        "profile": task.get("profile"),
        "runtime_backend": task.get("runtime_backend") or task.get("backend"),
        "runtime_target_kind": task.get("runtime_target_kind"),
        "runtime_target": task.get("runtime_target"),
        "delivery_mode": task.get("delivery_mode"),
        "model_tier": harness.get("model_tier"),
        "enforcement_level": harness.get("enforcement_level"),
        "context_required": harness.get("context_required"),
        "context_satisfied": harness.get("context_satisfied"),
        "skill_contract_present": harness.get("skill_contract_present"),
        "browser_evidence_present": isinstance(harness.get("browser_evidence"), dict),
        "retrieval_evidence_present": isinstance(harness.get("retrieval_evidence"), dict),
        "file_intake_evidence_present": isinstance(harness.get("file_intake_evidence"), dict),
        "allowed_profiles": contract.get("allowed_profiles", []),
        "default_profile": contract.get("default_profile"),
        "context_sources": [source.name for source in configured_context_sources(root)],
    }


def build_run_contract_payload(root: Path, task_id: str | None = None) -> dict:
    tasks = latest_tasks(read_jsonl(state_root_for(root) / "task-ledger.jsonl"))
    target = None
    if task_id:
        target = next((task for task in tasks if task.get("task_id") == task_id), None)
    elif tasks:
        target = tasks[-1]
    return {
        "workspace": str(root),
        "task": _task_capability_fields(target, root),
        "contract": resolve_skill_contract_for_root(root, target.get("skill") if target else None),
    }


def build_capability_diff_payload(root: Path, older_task_id: str | None = None, newer_task_id: str | None = None) -> dict:
    tasks = latest_tasks(read_jsonl(state_root_for(root) / "task-ledger.jsonl"))
    older = None
    newer = None
    if older_task_id or newer_task_id:
        if older_task_id:
            older = next((task for task in tasks if task.get("task_id") == older_task_id), None)
        if newer_task_id:
            newer = next((task for task in tasks if task.get("task_id") == newer_task_id), None)
    elif len(tasks) >= 2:
        older, newer = tasks[-2], tasks[-1]
    elif tasks:
        newer = tasks[-1]
    older_fields = _task_capability_fields(older, root)
    newer_fields = _task_capability_fields(newer, root)
    changed: list[dict] = []
    keys = sorted(set(older_fields) | set(newer_fields))
    for key in keys:
        if older_fields.get(key) != newer_fields.get(key):
            changed.append({"field": key, "before": older_fields.get(key), "after": newer_fields.get(key)})
    return {
        "workspace": str(root),
        "older": older_fields,
        "newer": newer_fields,
        "changed": changed,
    }


def build_session_card_payload(root: Path) -> dict:
    status = build_run_contract_payload(root)
    task = status.get("task") or {}
    command_entries = read_jsonl(state_root_for(root) / "command-log.jsonl")
    recent_failures = [entry for entry in command_entries[-50:] if entry.get("exit_code") not in (0, None)]
    finalization = None
    tasks = latest_tasks(read_jsonl(state_root_for(root) / "task-ledger.jsonl"))
    if task.get("task_id"):
        raw_task = next((entry for entry in tasks if entry.get("task_id") == task.get("task_id")), None)
        if raw_task:
            finalization = (raw_task.get("memory_capture") or {}).get("finalization_status")
    return {
        "task_id": task.get("task_id"),
        "task_name": task.get("task_name"),
        "status": task.get("status"),
        "skill": task.get("skill"),
        "profile": task.get("profile"),
        "model_tier": task.get("model_tier"),
        "enforcement_level": task.get("enforcement_level"),
        "context_sources": task.get("context_sources", []),
        "recent_failure_count": len(recent_failures),
        "latest_failure_labels": [entry.get("label") for entry in recent_failures[-3:]],
        "finalization_status": finalization,
    }


def build_onboarding_payload(root: Path) -> dict:
    suggestions = suggest_external_sources()
    adopted = load_context_sources(root)
    adopted_roots = {source.root for source in adopted}
    resolved_root = root.resolve()
    candidates = {
        name: [str(path) for path in paths if path not in adopted_roots and path != resolved_root]
        for name, paths in suggestions.items()
        if name != "obsidian_app"
    }
    candidates = {name: paths for name, paths in candidates.items() if paths}
    actions: list[str] = []
    if candidates.get("openclaw"):
        actions.append(
            f"Adopt OpenClaw read-only: helm adopt --path {root} --from-path {candidates['openclaw'][0]} --name openclaw-main"
        )
    if candidates.get("hermes"):
        actions.append(
            f"Adopt Hermes read-only: helm adopt --path {root} --from-path {candidates['hermes'][0]} --name hermes-main"
        )
    if candidates.get("obsidian"):
        actions.append(
            "Adopt your note vault read-only: "
            f"helm adopt --path {root} --from-path {candidates['obsidian'][0]} --kind generic --name obsidian-main"
        )
    elif not any((source.root / ".obsidian").exists() for source in adopted):
        if suggestions.get("obsidian_app"):
            actions.append(
                "Connect an existing Markdown notes workspace or Obsidian vault for durable file-native context hydration."
            )
        else:
            actions.append(
                "Consider installing Obsidian, or at least keep durable Markdown notes under your Helm workspace."
            )
    if not adopted:
        actions.append("Run helm doctor and helm validate after adapting references and context sources.")
    return {
        "workspace": str(root),
        "layout": detect_layout(root).kind,
        "adopted_sources": [source.to_json() for source in adopted],
        "detected_candidates": candidates,
        "obsidian_app_installed": bool(suggestions.get("obsidian_app")),
        "actions": actions,
    }


def format_onboarding_text(payload: dict) -> str:
    lines = [
        f"workspace={payload['workspace']}",
        f"layout={payload['layout']}",
        f"adopted_sources={len(payload['adopted_sources'])}",
    ]
    if payload["detected_candidates"]:
        for name, paths in payload["detected_candidates"].items():
            lines.append(f"detected_{name}=" + ", ".join(paths))
    else:
        lines.append("detected_candidates=-")
    lines.append(f"obsidian_app_installed={'yes' if payload['obsidian_app_installed'] else 'no'}")
    for action in payload["actions"]:
        lines.append(f"next={action}")
    return "\n".join(lines)


def onboarding_action_plan(
    root: Path,
    payload: dict,
    *,
    use_detected: bool = False,
    adopt_openclaw: str | None = None,
    adopt_hermes: str | None = None,
    adopt_obsidian: str | None = None,
) -> list[dict]:
    plan: list[dict] = []

    def append_plan(kind: str, path: str, name: str) -> None:
        command = f"helm adopt --path {root} --from-path {path} "
        if kind == "generic":
            command += f"--kind generic --name {name}"
        else:
            command += f"--name {name}"
        plan.append({"kind": kind, "path": path, "name": name, "command": command})

    if use_detected:
        detected = payload["detected_candidates"]
        if detected.get("openclaw"):
            append_plan("openclaw", detected["openclaw"][0], "openclaw-main")
        if detected.get("hermes"):
            append_plan("hermes", detected["hermes"][0], "hermes-main")
        if detected.get("obsidian"):
            append_plan("generic", detected["obsidian"][0], "obsidian-main")

    if adopt_openclaw:
        append_plan("openclaw", str(target_root(adopt_openclaw)), "openclaw-main")
    if adopt_hermes:
        append_plan("hermes", str(target_root(adopt_hermes)), "hermes-main")
    if adopt_obsidian:
        append_plan("generic", str(target_root(adopt_obsidian)), "obsidian-main")

    deduped: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for item in plan:
        key = (item["kind"], item["path"])
        if key in seen:
            continue
        deduped.append(item)
        seen.add(key)
    return deduped


def task_finalization_status(task: dict) -> str:
    return (task.get("memory_capture") or {}).get("finalization_status", "unknown")


def build_recent_state_payload(root: Path, limit: int, *, pending_only: bool = False) -> dict:
    state_root = state_root_for(root)
    tasks = latest_tasks(read_jsonl(state_root / "task-ledger.jsonl"))
    if pending_only:
        tasks = [
            task
            for task in tasks
            if task_finalization_status(task) in {"capture_planned", "capture_partial"}
        ]
    selected = tasks[-limit:]
    items = []
    for task in selected:
        memory_capture = task.get("memory_capture") or {}
        items.append(
            {
                "task_id": task.get("task_id"),
                "task_name": task.get("task_name"),
                "profile": task.get("profile"),
                "status": task.get("status"),
                "started_at": task.get("started_at"),
                "finished_at": task.get("finished_at"),
                "finalization_status": memory_capture.get("finalization_status", "unknown"),
                "recommended_layers": memory_capture.get("recommended_layers", []),
                "event_types": memory_capture.get("event_types", []),
                "claim_state": memory_capture.get("claim_state", {}),
                "retention": memory_capture.get("retention", {}),
                "review_flags": memory_capture.get("review_flags", []),
                "supersession": memory_capture.get("supersession", {}),
                "crystallization": memory_capture.get("crystallization", {}),
                "summary": memory_capture.get("summary"),
            }
        )
    return {
        "workspace": str(root),
        "pending_only": pending_only,
        "count": len(items),
        "items": items,
    }


def build_state_snapshot_payload(root: Path, task_id: str | None = None) -> dict:
    from commands import _warn_parse_failure
    state_root = state_root_for(root)
    tasks = latest_tasks(read_jsonl(state_root / "task-ledger.jsonl"))
    target = None
    if task_id:
        target = next((task for task in tasks if task.get("task_id") == task_id), None)
    elif tasks:
        target = next((task for task in reversed(tasks) if task.get("state_snapshot")), None)

    snapshot_meta = (target or {}).get("state_snapshot") or {}
    snapshot_path = None
    if snapshot_meta.get("path"):
        snapshot_path = root / snapshot_meta["path"]
    elif not task_id:
        snapshot_path = latest_snapshot_path(state_root)

    content = None
    if snapshot_path and snapshot_path.exists():
        try:
            content = snapshot_path.read_text(encoding="utf-8")
        except OSError as exc:
            _warn_parse_failure(snapshot_path, str(exc))

    return {
        "workspace": str(root),
        "task": target,
        "snapshot": snapshot_meta or None,
        "snapshot_path": str(snapshot_path) if snapshot_path else None,
        "content": content,
    }


def cmd_sources(args: argparse.Namespace) -> int:
    root = target_root(args.path)
    sources = load_context_sources(root)
    onboarding_dir = onboarding_root(root)
    payload = []
    for source in sources:
        item = source.to_json()
        note_path = onboarding_dir / f"{source.name}.json"
        item["migration_note"] = str(note_path) if note_path.exists() else None
        payload.append(item)
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0
    if not payload:
        print("No external context sources registered.")
        return 0
    for item in payload:
        print(
            f"name={item['name']} kind={item['kind']} root={item['root']} "
            f"migration_note={item['migration_note'] or '-'}"
        )
    return 0


def cmd_context(args: argparse.Namespace) -> int:
    from commands import discover_workspace, run_script
    root = target_root(args.path) if args.path else discover_workspace().root
    if args.args:
        subcommand, *remainder = args.args
        if subcommand == "recent-state":
            parser = argparse.ArgumentParser(prog="helm context recent-state")
            parser.add_argument("--limit", type=int, default=10)
            parser.add_argument("--json", action="store_true")
            parsed = parser.parse_args(remainder)
            payload = build_recent_state_payload(root, parsed.limit)
            if parsed.json:
                print(json.dumps(payload, indent=2, ensure_ascii=False))
                return 0
            if not payload["items"]:
                print("No recent finalized tasks found.")
                return 0
            for item in payload["items"]:
                print(
                    f"{item['task_id']} status={item['status']} profile={item['profile']} "
                    f"finalization={item['finalization_status']} confidence={item['claim_state'].get('confidence_hint', '-')} "
                    f"retention={item['retention'].get('tier', '-')} review_flags={len(item['review_flags'])} name={item['task_name']}"
                )
            return 0
        if subcommand == "state-snapshot":
            parser = argparse.ArgumentParser(prog="helm context state-snapshot")
            parser.add_argument("--task-id")
            parser.add_argument("--json", action="store_true")
            parsed = parser.parse_args(remainder)
            payload = build_state_snapshot_payload(root, parsed.task_id)
            if parsed.json:
                print(json.dumps(payload, indent=2, ensure_ascii=False))
                return 0
            if not payload["content"]:
                print("No state snapshot found.")
                return 0
            print(payload["content"], end="" if payload["content"].endswith("\n") else "\n")
            return 0
    return run_script("ops_memory_query.py", args.args, root)


def cmd_adopt(args: argparse.Namespace) -> int:
    root = target_root(args.path or str(DEFAULT_WORKSPACE), create=True)
    if not (root / ".helm").exists():
        from commands.status import cmd_init
        cmd_init(argparse.Namespace(path=str(root), force=False, json=False))
    target = target_root(args.from_path)
    source = adopt_context_source(root, target, name=args.name, kind=args.kind)
    payload = source.to_json()
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0
    print(f"helm_workspace={root}")
    print(f"adopted_name={source.name}")
    print(f"adopted_kind={source.kind}")
    print(f"adopted_root={source.root}")
    print(f"migration_note={onboarding_root(root) / (source.name + '.json')}")
    return 0


def cmd_onboard(args: argparse.Namespace) -> int:
    root = target_root(args.path or str(DEFAULT_WORKSPACE), create=True)
    if not (root / ".helm").exists():
        from commands.status import cmd_init
        cmd_init(argparse.Namespace(path=str(root), force=False, json=False))

    payload = build_onboarding_payload(root)
    plan = onboarding_action_plan(
        root,
        payload,
        use_detected=args.use_detected,
        adopt_openclaw=args.adopt_openclaw,
        adopt_hermes=args.adopt_hermes,
        adopt_obsidian=args.adopt_obsidian,
    )

    if args.json:
        output = dict(payload)
        output["plan"] = plan
        print(json.dumps(output, indent=2, ensure_ascii=False))
        return 0

    print(format_onboarding_text(payload))
    if not plan:
        print("plan=No adoption actions selected. Use --use-detected or explicit --adopt-* flags.")
        return 0
    if args.dry_run:
        for item in plan:
            print(f"plan={item['command']}")
        if not args.skip_checks:
            print(f"plan=helm doctor --path {root}")
            print(f"plan=helm validate --path {root}")
            print(f"plan=helm status --path {root} --verbose")
        return 0

    for item in plan:
        source = adopt_context_source(root, Path(item["path"]), name=item["name"], kind=item["kind"])
        print(f"applied=adopted {source.kind} from {source.root} as {source.name}")
    if args.skip_checks:
        print(f"next=Run helm doctor --path {root}")
        print(f"next=Run helm validate --path {root}")
        print(f"next=Run helm status --path {root} --verbose")
        return 0

    from commands.doctor import cmd_doctor
    from commands.validate import cmd_validate
    from commands.status import cmd_status
    print(f"running=helm doctor --path {root}")
    doctor_code = cmd_doctor(argparse.Namespace(path=str(root), json=False))
    print(f"running=helm validate --path {root}")
    validate_code = cmd_validate(argparse.Namespace(path=str(root), json=False))
    print(f"running=helm status --path {root} --verbose")
    status_code = cmd_status(argparse.Namespace(path=str(root), json=False, verbose=True))
    return 0 if doctor_code == 0 and validate_code == 0 and status_code == 0 else 1
