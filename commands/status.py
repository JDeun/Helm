from __future__ import annotations

import argparse
import html
import json
import shutil
from collections import Counter
from pathlib import Path

from commands import (
    DEFAULT_WORKSPACE,
    REFERENCES_ROOT,
    REQUIRED_REFERENCE_FILES,
    configured_context_sources,
    detect_layout,
    read_json,
    read_jsonl,
    relative_or_absolute,
    state_root_for,
    memory_review_queue_count_for,
    suggest_external_sources,
    target_root,
)
from commands.context import (
    build_capability_diff_payload,
    build_onboarding_payload,
    build_run_contract_payload,
    build_session_card_payload,
    format_onboarding_text,
    latest_tasks,
    load_draft_assessments,
)


def build_status_payload(root: Path) -> dict:
    layout = detect_layout(root)
    state_root = state_root_for(root)
    context_sources = configured_context_sources(root)
    task_entries = latest_tasks(read_jsonl(state_root / "task-ledger.jsonl"))
    command_entries = read_jsonl(state_root / "command-log.jsonl")
    checkpoints = read_json(state_root / "checkpoints" / "index.json", [])

    draft_assessments = load_draft_assessments(root)

    recent_tasks = task_entries[-10:]
    failed_commands = [entry for entry in command_entries[-100:] if entry.get("exit_code") not in (0, None)]
    finalization_counts = Counter(
        (entry.get("memory_capture") or {}).get("finalization_status", "unknown")
        for entry in recent_tasks
    )
    memory_operations = read_jsonl(state_root / "memory-operations.jsonl")
    crystallized_sessions = read_jsonl(state_root / "crystallized-sessions.jsonl")
    return {
        "workspace": str(root),
        "layout": layout.kind,
        "state_dir": relative_or_absolute(state_root, root),
        "context_sources": [
            {"name": source.name, "kind": source.kind, "root": str(source.root), "mode": source.mode}
            for source in context_sources
        ],
        "task_status_counts": dict(Counter(entry.get("status", "unknown") for entry in recent_tasks)),
        "finalization_counts": dict(finalization_counts),
        "recent_tasks": recent_tasks[-5:],
        "recent_failed_commands": failed_commands[-5:],
        "recent_checkpoints": checkpoints[-5:],
        "draft_assessments": draft_assessments[-5:],
        "recent_memory_operations": memory_operations[-5:],
        "recent_crystallized_sessions": crystallized_sessions[-5:],
        "memory_review_queue_count": memory_review_queue_count_for(root),
        "session_card": build_session_card_payload(root),
    }


def build_report_payload(root: Path, limit: int) -> dict:
    state_root = state_root_for(root)
    tasks = latest_tasks(read_jsonl(state_root / "task-ledger.jsonl"))
    commands = read_jsonl(state_root / "command-log.jsonl")
    checkpoints = read_json(state_root / "checkpoints" / "index.json", [])
    context_sources = configured_context_sources(root)
    assessments = load_draft_assessments(root)

    recent_tasks = tasks[-limit:]
    failed_tasks = [task for task in recent_tasks if task.get("status") == "failed"]
    running_tasks = [task for task in recent_tasks if task.get("status") == "running"]
    handoffs = [task for task in recent_tasks if task.get("status") == "handoff_required"]
    failed_commands = [cmd for cmd in commands[-200:] if cmd.get("exit_code") not in (0, None)]
    memory_operations = read_jsonl(state_root / "memory-operations.jsonl")
    crystallized_sessions = read_jsonl(state_root / "crystallized-sessions.jsonl")
    source_breakdown = Counter(source.kind for source in context_sources)
    finalization_counts = Counter(
        (task.get("memory_capture") or {}).get("finalization_status", "unknown")
        for task in recent_tasks
    )
    return {
        "workspace": str(root),
        "period_task_count": len(recent_tasks),
        "context_source_count": len(context_sources),
        "context_source_breakdown": dict(source_breakdown),
        "task_status_counts": dict(Counter(task.get("status", "unknown") for task in recent_tasks)),
        "finalization_counts": dict(finalization_counts),
        "failed_tasks": failed_tasks[-5:],
        "running_tasks": running_tasks[-5:],
        "handoff_tasks": handoffs[-5:],
        "failed_commands": failed_commands[-10:],
        "recent_checkpoints": checkpoints[-10:],
        "recent_memory_operations": memory_operations[-10:],
        "recent_crystallized_sessions": crystallized_sessions[-10:],
        "memory_review_queue_count": memory_review_queue_count_for(root),
        "draft_assessments": assessments[-10:],
    }


def format_report_markdown(payload: dict) -> str:
    lines = [
        "# Helm Report",
        "",
        f"- Workspace: `{payload['workspace']}`",
        f"- Tasks in window: `{payload['period_task_count']}`",
        f"- Context sources: `{payload['context_source_count']}` `{json.dumps(payload['context_source_breakdown'], ensure_ascii=False, sort_keys=True)}`",
        f"- Status counts: `{json.dumps(payload['task_status_counts'], ensure_ascii=False, sort_keys=True)}`",
        f"- Finalization counts: `{json.dumps(payload['finalization_counts'], ensure_ascii=False, sort_keys=True)}`",
        f"- Memory ops in window: `{len(payload.get('recent_memory_operations', []))}`",
        f"- Crystallized sessions in window: `{len(payload.get('recent_crystallized_sessions', []))}`",
        f"- Memory review queue: `{payload.get('memory_review_queue_count', 0)}`",
        "",
        "## Onboarding Actions",
    ]
    if payload.get("onboarding", {}).get("actions"):
        for action in payload["onboarding"]["actions"][:5]:
            lines.append(f"- {action}")
    else:
        lines.append("- None")
    lines.extend([
        "",
        "## Memory Operations",
    ])
    if payload.get("recent_memory_operations"):
        for item in payload["recent_memory_operations"][:5]:
            lines.append(
                f"- `{item.get('timestamp')}` `{item.get('operation')}` `{item.get('subject')}` scope=`{item.get('scope')}`"
            )
    else:
        lines.append("- None")
    lines.extend([
        "",
        "## Crystallized Sessions",
    ])
    if payload.get("recent_crystallized_sessions"):
        for item in payload["recent_crystallized_sessions"][:5]:
            crystal = item.get("crystallization") or {}
            lines.append(
                f"- `{item.get('task_id')}` `{crystal.get('question', item.get('task_name') or '-')}` result=`{crystal.get('result', '-')}`"
            )
    else:
        lines.append("- None")
    lines.extend([
        "",
        "## Failed Tasks",
    ])
    if payload["failed_tasks"]:
        for task in payload["failed_tasks"]:
            lines.append(
                f"- `{task.get('task_id')}` `{task.get('task_name')}` "
                f"[{task.get('profile')}] status=`{task.get('status')}`"
            )
    else:
        lines.append("- None")
    lines.extend(["", "## Failed Commands"])
    if payload["failed_commands"]:
        for command in payload["failed_commands"][:5]:
            lines.append(
                f"- task=`{command.get('task_id')}` component=`{command.get('component')}` "
                f"exit=`{command.get('exit_code')}` label=`{command.get('label')}`"
            )
    else:
        lines.append("- None")
    lines.extend(["", "## Recent Checkpoints"])
    if payload["recent_checkpoints"]:
        for checkpoint in payload["recent_checkpoints"][:5]:
            lines.append(
                f"- `{checkpoint.get('checkpoint_id')}` `{checkpoint.get('label')}` "
                f"paths={', '.join(checkpoint.get('paths', []))}"
            )
    else:
        lines.append("- None")
    return "\n".join(lines)


def format_report_html(payload: dict) -> str:
    markdown = format_report_markdown(payload)
    body_lines: list[str] = []
    in_list = False
    for line in markdown.splitlines():
        if line.startswith("# "):
            if in_list:
                body_lines.append("</ul>")
                in_list = False
            body_lines.append(f"<h1>{html.escape(line[2:])}</h1>")
        elif line.startswith("## "):
            if in_list:
                body_lines.append("</ul>")
                in_list = False
            body_lines.append(f"<h2>{html.escape(line[3:])}</h2>")
        elif line.startswith("- "):
            if not in_list:
                body_lines.append("<ul>")
                in_list = True
            body_lines.append(f"<li>{html.escape(line[2:])}</li>")
        elif not line.strip():
            if in_list:
                body_lines.append("</ul>")
                in_list = False
        else:
            body_lines.append(f"<p>{html.escape(line)}</p>")
    if in_list:
        body_lines.append("</ul>")
    return "\n".join(
        [
            "<!doctype html>",
            "<html lang=\"en\">",
            "<head>",
            "<meta charset=\"utf-8\">",
            "<title>Helm Report</title>",
            "<style>",
            "body{font-family:ui-sans-serif,system-ui,sans-serif;max-width:920px;margin:40px auto;padding:0 20px;line-height:1.55;color:#0f172a;background:#f8fafc}",
            "h1,h2{line-height:1.2}code{background:#e2e8f0;padding:0.1rem 0.25rem;border-radius:0.25rem}li{margin:0.3rem 0}",
            "</style>",
            "</head>",
            "<body>",
            *body_lines,
            "</body>",
            "</html>",
        ]
    )


def format_status_brief(payload: dict, onboarding: dict) -> str:
    failed = payload["task_status_counts"].get("failed", 0)
    blocked = payload["task_status_counts"].get("blocked", 0)
    running = payload["task_status_counts"].get("running", 0)
    risk_count = failed + blocked + running + len(payload["recent_failed_commands"])
    health = "attention" if risk_count or payload["memory_review_queue_count"] else "ok"
    lines = [
        f"health={health}",
        f"workspace={payload['workspace']}",
        f"layout={payload['layout']}",
        "tasks=" + json.dumps(payload["task_status_counts"], ensure_ascii=False, sort_keys=True),
        f"failed_commands={len(payload['recent_failed_commands'])}",
        f"checkpoints={len(payload['recent_checkpoints'])}",
        f"memory_review_queue={payload['memory_review_queue_count']}",
        f"onboarding_actions={len(onboarding['actions'])}",
    ]
    if payload["recent_failed_commands"]:
        command = payload["recent_failed_commands"][-1]
        lines.append(
            "latest_failed_command="
            f"task={command.get('task_id')} exit={command.get('exit_code')} label={command.get('label')}"
        )
    if payload["recent_checkpoints"]:
        checkpoint = payload["recent_checkpoints"][-1]
        lines.append(
            "latest_checkpoint="
            f"{checkpoint.get('checkpoint_id')} label={checkpoint.get('label')}"
        )
    if payload["memory_review_queue_count"]:
        lines.append(f"next=helm memory review-queue --path {payload['workspace']}")
    elif onboarding["actions"]:
        lines.append(f"next={onboarding['actions'][0]}")
    return "\n".join(lines)


def format_dashboard_text(payload: dict, onboarding: dict) -> str:
    lines = [
        "Helm Dashboard",
        f"workspace: {payload['workspace']}",
        f"layout: {payload['layout']}",
        "",
        "Status",
        "  tasks: " + json.dumps(payload["task_status_counts"], ensure_ascii=False, sort_keys=True),
        "  finalization: " + json.dumps(payload["finalization_counts"], ensure_ascii=False, sort_keys=True),
        f"  failed commands: {len(payload['recent_failed_commands'])}",
        f"  memory review queue: {payload['memory_review_queue_count']}",
        "",
        "Recent tasks",
    ]
    if payload["recent_tasks"]:
        for task in payload["recent_tasks"][-5:]:
            lines.append(
                f"  - {task.get('task_id')} [{task.get('status')}] "
                f"{task.get('task_name') or '-'} profile={task.get('profile') or '-'}"
            )
    else:
        lines.append("  - none")
    lines.append("")
    lines.append("Recent checkpoints")
    if payload["recent_checkpoints"]:
        for checkpoint in payload["recent_checkpoints"][-5:]:
            lines.append(f"  - {checkpoint.get('checkpoint_id')} {checkpoint.get('label') or '-'}")
    else:
        lines.append("  - none")
    lines.append("")
    lines.append("Next actions")
    if payload["memory_review_queue_count"]:
        lines.append(f"  - helm memory review-queue --path {payload['workspace']}")
    for action in onboarding["actions"][:5]:
        lines.append(f"  - {action}")
    if not payload["memory_review_queue_count"] and not onboarding["actions"]:
        lines.append("  - none")
    return "\n".join(lines)


def cmd_init(args: argparse.Namespace) -> int:
    root = target_root(args.path or str(DEFAULT_WORKSPACE), create=True)
    references_dir = root / "references"
    references_dir.mkdir(parents=True, exist_ok=True)

    for filename in REQUIRED_REFERENCE_FILES:
        source = REFERENCES_ROOT / filename
        target = references_dir / filename
        if target.exists() and not args.force:
            continue
        shutil.copy2(source, target)

    for directory in (
        root / ".helm",
        root / ".helm" / "checkpoints",
        root / "memory",
        root / "memory" / "ontology",
        root / "skills",
        root / "skill_drafts",
        root / "docs",
    ):
        directory.mkdir(parents=True, exist_ok=True)

    memory_file = root / "MEMORY.md"
    if args.force or not memory_file.exists():
        memory_file.write_text("# Workspace Memory\n\nStore durable operational notes here.\n", encoding="utf-8")

    if args.json:
        print(
            json.dumps(
                {
                    "workspace": str(root),
                    "state_dir": ".helm",
                    "references": [str((references_dir / name).relative_to(root)) for name in REQUIRED_REFERENCE_FILES],
                },
                indent=2,
                ensure_ascii=False,
            )
        )
    else:
        print(f"Initialized Helm workspace at {root}")
        print("State dir: .helm")
        print("Next step: run `helm doctor --path {}` to inspect external runtime and note-vault candidates.".format(root))
        print("Onboarding: run `helm survey --path {}` for adoption guidance.".format(root))
    return 0


def cmd_detect(args: argparse.Namespace) -> int:
    root = target_root(args.path)
    layout = detect_layout(root)
    suggestions = suggest_external_sources()
    payload = {
        "workspace": str(layout.root),
        "layout": layout.kind,
        "source": layout.source,
        "state_dir": layout.state_dir_name,
        "markers": list(layout.markers),
        "onboarding_signals": {
            name: [str(path) for path in paths]
            for name, paths in suggestions.items()
            if paths
        },
    }
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0
    print(f"workspace={payload['workspace']}")
    print(f"layout={payload['layout']}")
    print(f"source={payload['source']}")
    print(f"state_dir={payload['state_dir']}")
    print(f"markers={', '.join(payload['markers']) if payload['markers'] else '-'}")
    for name, paths in payload["onboarding_signals"].items():
        print(f"detected_{name}=" + ", ".join(paths))
    if payload["layout"] in {"openclaw", "hermes", "generic"}:
        print(
            "suggestion=Use `helm adopt --path <helm-workspace> --from-path "
            f"{payload['workspace']}` to register it as an external context source."
        )
        print("suggestion=Or run `helm onboard --path <helm-workspace> --use-detected --dry-run` to prepare an adoption plan.")
    if payload["onboarding_signals"].get("obsidian"):
        first_vault = payload["onboarding_signals"]["obsidian"][0]
        print(
            "suggestion=If you keep durable notes in Obsidian, adopt the vault read-only with "
            f"`helm adopt --path <helm-workspace> --from-path {first_vault} --kind generic --name obsidian-main`."
        )
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    root = target_root(args.path)
    payload = build_status_payload(root)
    onboarding = build_onboarding_payload(root)
    if args.json:
        payload["onboarding"] = onboarding
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0
    if getattr(args, "brief", False):
        print(format_status_brief(payload, onboarding))
        return 0
    print(f"workspace={payload['workspace']}")
    print(f"layout={payload['layout']}")
    print(f"state_dir={payload['state_dir']}")
    print(f"context_sources={len(payload['context_sources'])}")
    print("task_status_counts=" + json.dumps(payload["task_status_counts"], ensure_ascii=False, sort_keys=True))
    print("finalization_counts=" + json.dumps(payload["finalization_counts"], ensure_ascii=False, sort_keys=True))
    print(f"recent_tasks={len(payload['recent_tasks'])}")
    print(f"recent_failed_commands={len(payload['recent_failed_commands'])}")
    print(f"recent_checkpoints={len(payload['recent_checkpoints'])}")
    print(f"recent_memory_operations={len(payload['recent_memory_operations'])}")
    print(f"recent_crystallized_sessions={len(payload['recent_crystallized_sessions'])}")
    print(f"memory_review_queue_count={payload['memory_review_queue_count']}")
    print(f"draft_assessments={len(payload['draft_assessments'])}")
    print(f"onboarding_actions={len(onboarding['actions'])}")
    card = payload["session_card"]
    print(
        "session_card="
        + json.dumps(
            {
                "task_id": card.get("task_id"),
                "task_name": card.get("task_name"),
                "status": card.get("status"),
                "skill": card.get("skill"),
                "profile": card.get("profile"),
                "model_tier": card.get("model_tier"),
                "enforcement_level": card.get("enforcement_level"),
                "finalization_status": card.get("finalization_status"),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    if args.verbose:
        for source in payload["context_sources"]:
            print(f"context_source={source['name']} kind={source['kind']} root={source['root']}")
        for task in payload["recent_tasks"]:
            print(
                "recent_task="
                f"{task.get('task_id')} status={task.get('status')} profile={task.get('profile')} "
                f"name={task.get('task_name')}"
            )
        for item in payload["recent_memory_operations"]:
            print(
                "memory_op="
                f"{item.get('timestamp')} op={item.get('operation')} scope={item.get('scope')} "
                f"subject={item.get('subject')}"
            )
        for item in payload["recent_crystallized_sessions"]:
            crystal = item.get("crystallization") or {}
            print(
                "crystallized="
                f"{item.get('task_id')} question={crystal.get('question') or item.get('task_name')} "
                f"result={crystal.get('result') or '-'}"
            )
        if payload["memory_review_queue_count"]:
            print(f"next=review memory queue: helm memory review-queue --path {root}")
        for action in onboarding["actions"]:
            print(f"next={action}")
    return 0


def cmd_dashboard(args: argparse.Namespace) -> int:
    root = target_root(args.path)
    payload = build_status_payload(root)
    onboarding = build_onboarding_payload(root)
    if args.json:
        payload["onboarding"] = onboarding
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0
    print(format_dashboard_text(payload, onboarding))
    return 0


def cmd_run_contract(args: argparse.Namespace) -> int:
    root = target_root(args.path)
    payload = build_run_contract_payload(root, task_id=args.task_id)
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0
    print(f"workspace={payload['workspace']}")
    task = payload.get("task") or {}
    print("task=" + json.dumps(task, ensure_ascii=False, sort_keys=True))
    print("contract=" + json.dumps(payload.get("contract") or {}, ensure_ascii=False, sort_keys=True))
    return 0


def cmd_capability_diff(args: argparse.Namespace) -> int:
    root = target_root(args.path)
    payload = build_capability_diff_payload(root, older_task_id=args.older_task_id, newer_task_id=args.newer_task_id)
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0
    print(f"workspace={payload['workspace']}")
    print("older=" + json.dumps(payload.get("older") or {}, ensure_ascii=False, sort_keys=True))
    print("newer=" + json.dumps(payload.get("newer") or {}, ensure_ascii=False, sort_keys=True))
    for item in payload["changed"]:
        print(f"changed={item['field']} before={json.dumps(item['before'], ensure_ascii=False)} after={json.dumps(item['after'], ensure_ascii=False)}")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    root = target_root(args.path)
    payload = build_report_payload(root, args.limit)
    payload["onboarding"] = build_onboarding_payload(root)
    if args.format == "json":
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0
    if args.format == "markdown":
        print(format_report_markdown(payload))
        return 0
    if args.format == "html":
        print(format_report_html(payload))
        return 0
    print(f"workspace={payload['workspace']}")
    print(f"tasks_in_window={payload['period_task_count']}")
    print(f"context_sources={payload['context_source_count']}")
    print("context_source_breakdown=" + json.dumps(payload["context_source_breakdown"], ensure_ascii=False, sort_keys=True))
    print("task_status_counts=" + json.dumps(payload["task_status_counts"], ensure_ascii=False, sort_keys=True))
    print("finalization_counts=" + json.dumps(payload["finalization_counts"], ensure_ascii=False, sort_keys=True))
    print(f"failed_tasks={len(payload['failed_tasks'])}")
    print(f"running_tasks={len(payload['running_tasks'])}")
    print(f"handoff_tasks={len(payload['handoff_tasks'])}")
    print(f"failed_commands={len(payload['failed_commands'])}")
    print(f"recent_checkpoints={len(payload['recent_checkpoints'])}")
    print(f"onboarding_actions={len(payload['onboarding']['actions'])}")
    for action in payload["onboarding"]["actions"][:3]:
        print(f"next={action}")
    return 0
