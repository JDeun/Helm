from __future__ import annotations

import argparse
import difflib
import json
import os
import shutil
import subprocess
import sys
from collections import Counter
from contextlib import contextmanager
from pathlib import Path

from helm_context import adopt_context_source, configured_context_sources, load_context_sources, onboarding_root
from helm_workspace import DEFAULT_WORKSPACE, detect_layout, discover_workspace, suggest_external_sources
from scripts.skill_manifest_lib import load_skill_policies


ROOT = Path(__file__).resolve().parent
REFERENCES_ROOT = ROOT / "references"
SCRIPT_ROOT = ROOT / "scripts"
REQUIRED_REFERENCE_FILES = (
    "execution_profiles.json",
    "skill-capture-template.md",
    "skill-contract-template.json",
)

ASCII_BANNER = r"""
██╗  ██╗███████╗██╗     ███╗   ███╗
██║  ██║██╔════╝██║     ████╗ ████║
███████║█████╗  ██║     ██╔████╔██║
██╔══██║██╔══╝  ██║     ██║╚██╔╝██║
██║  ██║███████╗███████╗██║ ╚═╝ ██║
╚═╝  ╚═╝╚══════╝╚══════╝╚═╝     ╚═╝

                   stability-first agent operations
"""

HELM_PRIMARY = "\033[38;2;230;236;244m"
HELM_ACCENT = "\033[38;2;105;162;255m"
HELM_MUTED = "\033[38;2;137;161;196m"
ANSI_RESET = "\033[0m"


def color_enabled() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    term = os.environ.get("TERM", "")
    return sys.stdout.isatty() and term.lower() != "dumb"


def render_banner() -> str:
    if not color_enabled():
        return ASCII_BANNER
    lines = ASCII_BANNER.splitlines()
    rendered: list[str] = []
    for line in lines:
        if not line.strip():
            rendered.append(line)
            continue
        if "stability-first" in line:
            rendered.append(f"{HELM_MUTED}{line}{ANSI_RESET}")
            continue
        if "████" in line or "██" in line:
            midpoint = max(1, len(line) // 2)
            left = line[:midpoint]
            right = line[midpoint:]
            rendered.append(f"{HELM_PRIMARY}{left}{HELM_ACCENT}{right}{ANSI_RESET}")
            continue
        rendered.append(f"{HELM_PRIMARY}{line}{ANSI_RESET}")
    return "\n".join(rendered)


def read_json(path: Path, default: object) -> object:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def validate_workspace_config(root: Path) -> dict:
    issues: list[str] = []
    profiles_path = root / "references" / "execution_profiles.json"
    profiles_data = read_json(profiles_path, {})
    policies = load_skill_policies(root, root / "references" / "skill_profile_policies.json")

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

    return {
        "workspace": str(root),
        "profile_count": len(profiles),
        "skill_policy_count": len(skills),
        "issues": issues,
        "ok": not issues,
    }


def latest_tasks(entries: list[dict]) -> list[dict]:
    by_task: dict[str, dict] = {}
    for entry in entries:
        task_id = entry.get("task_id")
        if task_id:
            by_task[task_id] = entry
    return sorted(by_task.values(), key=lambda item: item.get("started_at", ""))


def relative_or_absolute(path: Path, base: Path) -> str:
    try:
        return str(path.relative_to(base))
    except ValueError:
        return str(path)


@contextmanager
def scoped_workspace(path: Path | None):
    previous = os.environ.get("HELM_WORKSPACE")
    if path is not None:
        os.environ["HELM_WORKSPACE"] = str(path)
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("HELM_WORKSPACE", None)
        else:
            os.environ["HELM_WORKSPACE"] = previous


def run_script(script_name: str, script_args: list[str], workspace: Path | None = None) -> int:
    script_path = SCRIPT_ROOT / script_name
    env = os.environ.copy()
    if workspace is not None:
        env["HELM_WORKSPACE"] = str(workspace)
    result = subprocess.run([sys.executable, str(script_path), *script_args], env=env)
    return result.returncode


def target_root(path: str | None, *, create: bool = False) -> Path:
    if path:
        root = Path(path).expanduser()
    else:
        root = Path.cwd()
    if create:
        root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def build_status_payload(root: Path) -> dict:
    layout = detect_layout(root)
    state_root = root / ".helm"
    context_sources = configured_context_sources(root)
    task_entries = latest_tasks(read_jsonl(state_root / "task-ledger.jsonl"))
    command_entries = read_jsonl(state_root / "command-log.jsonl")
    checkpoints = read_json(state_root / "checkpoints" / "index.json", [])

    draft_assessments: list[dict] = []
    drafts_root = root / "skill_drafts"
    if drafts_root.exists():
        for draft in sorted(drafts_root.iterdir()):
            assessment = draft / "meta" / "assessment.json"
            if assessment.exists():
                draft_assessments.append(json.loads(assessment.read_text(encoding="utf-8")))

    recent_tasks = task_entries[-10:]
    failed_commands = [entry for entry in command_entries[-100:] if entry.get("exit_code") not in (0, None)]
    finalization_counts = Counter(
        (entry.get("memory_capture") or {}).get("finalization_status", "unknown")
        for entry in recent_tasks
    )
    return {
        "workspace": str(root),
        "layout": layout.kind,
        "state_dir": str(state_root.relative_to(root)),
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
    }


def build_report_payload(root: Path, limit: int) -> dict:
    state_root = root / ".helm"
    tasks = latest_tasks(read_jsonl(state_root / "task-ledger.jsonl"))
    commands = read_jsonl(state_root / "command-log.jsonl")
    checkpoints = read_json(state_root / "checkpoints" / "index.json", [])
    context_sources = configured_context_sources(root)
    assessments: list[dict] = []
    drafts_root = root / "skill_drafts"
    if drafts_root.exists():
        for draft in sorted(drafts_root.iterdir()):
            assessment = draft / "meta" / "assessment.json"
            if assessment.exists():
                assessments.append(json.loads(assessment.read_text(encoding="utf-8")))

    recent_tasks = tasks[-limit:]
    failed_tasks = [task for task in recent_tasks if task.get("status") == "failed"]
    running_tasks = [task for task in recent_tasks if task.get("status") == "running"]
    handoffs = [task for task in recent_tasks if task.get("status") == "handoff_required"]
    failed_commands = [cmd for cmd in commands[-200:] if cmd.get("exit_code") not in (0, None)]
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
        "draft_assessments": assessments[-10:],
    }


def recommend_checkpoint(root: Path, task_id: str | None = None) -> dict:
    state_root = root / ".helm"
    tasks = latest_tasks(read_jsonl(state_root / "task-ledger.jsonl"))
    checkpoints = read_json(state_root / "checkpoints" / "index.json", [])
    target = None
    if task_id:
        target = next((item for item in tasks if item.get("task_id") == task_id), None)
    else:
        risky = [item for item in tasks if item.get("profile") == "risky_edit"]
        if risky:
            target = risky[-1]
    if target is None:
        return {"task": None, "checkpoint": None}

    explicit = target.get("checkpoint_id")
    checkpoint = None
    if explicit:
        checkpoint = next((item for item in checkpoints if item.get("checkpoint_id") == explicit), None)
    if checkpoint is None and checkpoints:
        started_at = target.get("started_at", "")
        older = [item for item in checkpoints if item.get("created_at", "") <= started_at.replace("-", "").replace(":", "").replace("+00:00", "Z")]
        checkpoint = older[-1] if older else checkpoints[-1]
    return {"task": target, "checkpoint": checkpoint}


def task_finalization_status(task: dict) -> str:
    return (task.get("memory_capture") or {}).get("finalization_status", "unknown")


def build_recent_state_payload(root: Path, limit: int, *, pending_only: bool = False) -> dict:
    state_root = root / ".helm"
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
                "summary": memory_capture.get("summary"),
            }
        )
    return {
        "workspace": str(root),
        "pending_only": pending_only,
        "count": len(items),
        "items": items,
    }


def build_capture_state_payload(root: Path, limit: int) -> dict:
    state_root = root / ".helm"
    tasks = latest_tasks(read_jsonl(state_root / "task-ledger.jsonl"))
    recent_tasks = tasks[-limit:]
    finalization_counts = Counter(task_finalization_status(task) for task in recent_tasks)
    pending_tasks = [
        {
            "task_id": task.get("task_id"),
            "task_name": task.get("task_name"),
            "profile": task.get("profile"),
            "status": task.get("status"),
            "finalization_status": task_finalization_status(task),
            "recommended_layers": (task.get("memory_capture") or {}).get("recommended_layers", []),
        }
        for task in recent_tasks
        if task_finalization_status(task) in {"capture_planned", "capture_partial"}
    ]
    return {
        "workspace": str(root),
        "window": len(recent_tasks),
        "finalization_counts": dict(finalization_counts),
        "pending_tasks": pending_tasks,
    }


def build_finalize_payload(root: Path, task_id: str | None) -> dict:
    recommendation = recommend_checkpoint(root, task_id)
    task = recommendation.get("task")
    checkpoint = recommendation.get("checkpoint")
    memory_capture = (task or {}).get("memory_capture") or {}
    return {
        "workspace": str(root),
        "task": task,
        "checkpoint": checkpoint,
        "finalization": {
            "status": memory_capture.get("finalization_status", "unknown"),
            "relevant": memory_capture.get("relevant", False),
            "recommended_layers": memory_capture.get("recommended_layers", []),
            "event_types": memory_capture.get("event_types", []),
            "reasons": memory_capture.get("reasons", []),
            "summary": memory_capture.get("summary"),
        },
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


def build_onboarding_payload(root: Path) -> dict:
    suggestions = suggest_external_sources()
    adopted = load_context_sources(root)
    adopted_roots = {source.root for source in adopted}
    candidates = {
        name: [str(path) for path in paths if path not in adopted_roots]
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


def write_json_file(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


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


def cmd_doctor(args: argparse.Namespace) -> int:
    root = target_root(args.path)
    layout = detect_layout(root)
    state_root = root / ".helm"
    suggestions = suggest_external_sources()

    checks: list[dict] = []
    for filename in REQUIRED_REFERENCE_FILES:
        path = root / "references" / filename
        ok = path.exists()
        detail = "present" if ok else "missing"
        if ok and path.suffix == ".json":
            try:
                json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                ok = False
                detail = f"invalid json: {exc}"
        checks.append({"name": f"references/{filename}", "ok": ok, "detail": detail})

    for relative in ("skills", "skill_drafts", "memory", ".helm", ".helm/checkpoints"):
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


def cmd_status(args: argparse.Namespace) -> int:
    root = target_root(args.path)
    payload = build_status_payload(root)
    onboarding = build_onboarding_payload(root)
    if args.json:
        payload["onboarding"] = onboarding
        print(json.dumps(payload, indent=2, ensure_ascii=False))
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
    print(f"draft_assessments={len(payload['draft_assessments'])}")
    print(f"onboarding_actions={len(onboarding['actions'])}")
    if args.verbose:
        for source in payload["context_sources"]:
            print(f"context_source={source['name']} kind={source['kind']} root={source['root']}")
        for task in payload["recent_tasks"]:
            print(
                "recent_task="
                f"{task.get('task_id')} status={task.get('status')} profile={task.get('profile')} "
                f"name={task.get('task_name')}"
            )
        for action in onboarding["actions"]:
            print(f"next={action}")
    return 0


def cmd_adopt(args: argparse.Namespace) -> int:
    root = target_root(args.path or str(DEFAULT_WORKSPACE), create=True)
    if not (root / ".helm").exists():
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

    print(f"running=helm doctor --path {root}")
    doctor_code = cmd_doctor(argparse.Namespace(path=str(root), json=False))
    print(f"running=helm validate --path {root}")
    validate_code = cmd_validate(argparse.Namespace(path=str(root), json=False))
    print(f"running=helm status --path {root} --verbose")
    status_code = cmd_status(argparse.Namespace(path=str(root), json=False, verbose=True))
    return 0 if doctor_code == 0 and validate_code == 0 and status_code == 0 else 1
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


def cmd_survey(args: argparse.Namespace) -> int:
    root = target_root(args.path or str(DEFAULT_WORKSPACE), create=True)
    if not (root / ".helm").exists():
        cmd_init(argparse.Namespace(path=str(root), force=False, json=False))
    payload = build_onboarding_payload(root)
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0
    print(format_onboarding_text(payload))
    return 0


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


def cmd_skill_review(args: argparse.Namespace) -> int:
    root = target_root(args.path)
    diff_args = argparse.Namespace(path=str(root), name=args.name, json=args.json)
    return cmd_skill_diff(diff_args)


def cmd_skill_approve(args: argparse.Namespace) -> int:
    root = target_root(args.path)
    script_args = ["promote-draft", "--name", args.name, "--approve"]
    if args.dry_run:
        script_args.append("--dry-run")
    return run_script("skill_capture.py", script_args, root)


def cmd_skill_reject(args: argparse.Namespace) -> int:
    root = target_root(args.path)
    draft_root = root / "skill_drafts" / args.name
    if not draft_root.exists():
        print(f"draft not found: {draft_root}", file=sys.stderr)
        return 1
    payload = {
        "draft": args.name,
        "status": "rejected",
        "reason": args.reason,
    }
    write_json_file(draft_root / "meta" / "rejection.json", payload)
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0
    print(f"draft={args.name}")
    print("status=rejected")
    print(f"reason={args.reason}")
    return 0


def cmd_checkpoint_recommend(args: argparse.Namespace) -> int:
    root = target_root(args.path)
    payload = recommend_checkpoint(root, args.task_id)
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0
    task = payload["task"]
    checkpoint = payload["checkpoint"]
    if task is None:
        print("No risky task found.")
        return 0
    print(f"task_id={task.get('task_id')}")
    print(f"task_name={task.get('task_name')}")
    print(f"profile={task.get('profile')}")
    print(f"status={task.get('status')}")
    if checkpoint is None:
        print("checkpoint_id=-")
        return 0
    print(f"checkpoint_id={checkpoint.get('checkpoint_id')}")
    print(f"checkpoint_label={checkpoint.get('label')}")
    print("checkpoint_paths=" + ", ".join(checkpoint.get("paths", [])))
    print(f"restore_hint=helm checkpoint --path {root} restore {checkpoint.get('checkpoint_id')}")
    return 0


def cmd_checkpoint_list(args: argparse.Namespace) -> int:
    root = target_root(args.path)
    checkpoints = read_json(root / ".helm" / "checkpoints" / "index.json", [])
    if args.json:
        print(json.dumps(checkpoints, indent=2, ensure_ascii=False))
        return 0
    if not checkpoints:
        print("No checkpoints found.")
        return 0
    for checkpoint in checkpoints[-args.limit:]:
        print(
            f"{checkpoint.get('checkpoint_id')} "
            f"label={checkpoint.get('label')} "
            f"paths={', '.join(checkpoint.get('paths', []))}"
        )
    return 0


def cmd_checkpoint_show(args: argparse.Namespace) -> int:
    root = target_root(args.path)
    checkpoints = read_json(root / ".helm" / "checkpoints" / "index.json", [])
    checkpoint = next((item for item in checkpoints if item.get("checkpoint_id") == args.checkpoint_id), None)
    if checkpoint is None:
        print(f"checkpoint not found: {args.checkpoint_id}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(checkpoint, indent=2, ensure_ascii=False))
        return 0
    print(f"checkpoint_id={checkpoint.get('checkpoint_id')}")
    print(f"label={checkpoint.get('label')}")
    print(f"created_at={checkpoint.get('created_at')}")
    print("paths=" + ", ".join(checkpoint.get("paths", [])))
    print(f"archive={checkpoint.get('archive')}")
    print(f"preview_hint=helm checkpoint --path {root} preview {checkpoint.get('checkpoint_id')}")
    print(f"restore_hint=helm checkpoint --path {root} restore {checkpoint.get('checkpoint_id')}")
    return 0


def cmd_checkpoint_preview(args: argparse.Namespace) -> int:
    root = target_root(args.path)
    script_args = ["preview", args.checkpoint_id]
    return run_script("workspace_checkpoint.py", script_args, root)


def cmd_checkpoint_restore(args: argparse.Namespace) -> int:
    root = target_root(args.path)
    print(f"Restoring checkpoint {args.checkpoint_id} into {root}")
    print(f"Preview first with: helm checkpoint --path {root} preview {args.checkpoint_id}")
    script_args = ["restore", args.checkpoint_id]
    return run_script("workspace_checkpoint.py", script_args, root)


def cmd_checkpoint_create(args: argparse.Namespace) -> int:
    root = target_root(args.path)
    script_args = ["create", "--label", args.label]
    for item in args.include:
        script_args.extend(["--path", item])
    return run_script("workspace_checkpoint.py", script_args, root)


def cmd_checkpoint_finalize(args: argparse.Namespace) -> int:
    root = target_root(args.path)
    payload = build_finalize_payload(root, args.task_id)
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0
    task = payload["task"]
    if task is None:
        print("No matching task found.")
        return 0
    finalization = payload["finalization"]
    checkpoint = payload["checkpoint"]
    print(f"task_id={task.get('task_id')}")
    print(f"task_name={task.get('task_name')}")
    print(f"status={task.get('status')}")
    print(f"finalization_status={finalization['status']}")
    print("recommended_layers=" + ", ".join(finalization["recommended_layers"]))
    print("event_types=" + ", ".join(finalization["event_types"]))
    for reason in finalization["reasons"]:
        print(f"reason={reason}")
    if checkpoint:
        print(f"checkpoint_id={checkpoint.get('checkpoint_id')}")
        print(f"checkpoint_label={checkpoint.get('label')}")
    return 0


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


def cmd_skill_diff(args: argparse.Namespace) -> int:
    root = target_root(args.path)
    draft_path = root / "skill_drafts" / args.name / "SKILL.md"
    live_path = root / "skills" / args.name / "SKILL.md"
    if not draft_path.exists():
        print(f"draft not found: {draft_path}", file=sys.stderr)
        return 1
    draft_lines = draft_path.read_text(encoding="utf-8").splitlines()
    if live_path.exists():
        live_lines = live_path.read_text(encoding="utf-8").splitlines()
        from_label = str(relative_or_absolute(live_path, root))
    else:
        live_lines = []
        from_label = "(no live skill)"
    diff = list(
        difflib.unified_diff(
            live_lines,
            draft_lines,
            fromfile=from_label,
            tofile=str(relative_or_absolute(draft_path, root)),
            lineterm="",
        )
    )
    if args.json:
        print(
            json.dumps(
                {
                    "workspace": str(root),
                    "draft": str(relative_or_absolute(draft_path, root)),
                    "live": str(relative_or_absolute(live_path, root)) if live_path.exists() else None,
                    "diff": diff,
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return 0
    if not diff:
        print("No differences found.")
        return 0
    print("\n".join(diff))
    return 0


def cmd_profile(args: argparse.Namespace) -> int:
    root = target_root(args.path) if args.path else discover_workspace().root
    return run_script("run_with_profile.py", args.args, root)


def cmd_context(args: argparse.Namespace) -> int:
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
                    f"finalization={item['finalization_status']} name={item['task_name']}"
                )
            return 0
    return run_script("ops_memory_query.py", args.args, root)


def cmd_checkpoint(args: argparse.Namespace) -> int:
    root = target_root(args.path) if args.path else discover_workspace().root
    if args.args:
        subcommand, *remainder = args.args
        if subcommand == "finalize":
            parser = argparse.ArgumentParser(prog="helm checkpoint finalize")
            parser.add_argument("--task-id")
            parser.add_argument("--json", action="store_true")
            parsed = parser.parse_args(remainder)
            return cmd_checkpoint_finalize(argparse.Namespace(path=str(root), task_id=parsed.task_id, json=parsed.json))
    return run_script("workspace_checkpoint.py", args.args, root)


def cmd_skill(args: argparse.Namespace) -> int:
    root = target_root(args.path) if args.path else discover_workspace().root
    return run_script("skill_capture.py", args.args, root)


def cmd_ops(args: argparse.Namespace) -> int:
    root = target_root(args.path) if args.path else discover_workspace().root
    if not args.args:
        print("Use `helm ops daily|tasks|commands ...`", file=sys.stderr)
        return 2
    subcommand, *remainder = args.args
    if subcommand == "capture-state":
        parser = argparse.ArgumentParser(prog="helm ops capture-state")
        parser.add_argument("--limit", type=int, default=20)
        parser.add_argument("--json", action="store_true")
        parsed = parser.parse_args(remainder)
        payload = build_capture_state_payload(root, parsed.limit)
        if parsed.json:
            print(json.dumps(payload, indent=2, ensure_ascii=False))
            return 0
        print(f"workspace={payload['workspace']}")
        print("finalization_counts=" + json.dumps(payload["finalization_counts"], ensure_ascii=False, sort_keys=True))
        print(f"pending_tasks={len(payload['pending_tasks'])}")
        for task in payload["pending_tasks"][:10]:
            print(
                f"pending={task['task_id']} profile={task['profile']} "
                f"finalization={task['finalization_status']} name={task['task_name']}"
            )
        return 0
    mapping = {
        "daily": "ops_daily_report.py",
        "tasks": "task_ledger_report.py",
        "commands": "command_log_report.py",
    }
    script_name = mapping.get(subcommand)
    if script_name is None:
        print(f"Unknown ops subcommand: {subcommand}", file=sys.stderr)
        return 2
    return run_script(script_name, remainder, root)


def cmd_memory(args: argparse.Namespace) -> int:
    root = target_root(args.path) if args.path else discover_workspace().root
    help_parser = argparse.ArgumentParser(prog="helm memory")
    help_parser.add_argument("subcommand", nargs="?", help="Currently supported: pending-captures")
    if not args.args:
        help_parser.print_help()
        return 0
    if args.args[0] in {"-h", "--help"}:
        help_parser.print_help()
        return 0
    subcommand, *remainder = args.args
    if subcommand != "pending-captures":
        print(f"Unknown memory subcommand: {subcommand}", file=sys.stderr)
        return 2
    parser = argparse.ArgumentParser(prog="helm memory pending-captures")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--json", action="store_true")
    parsed = parser.parse_args(remainder)
    payload = build_recent_state_payload(root, parsed.limit, pending_only=True)
    if parsed.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0
    if not payload["items"]:
        print("No pending durable captures found.")
        return 0
    for item in payload["items"]:
        print(
            f"{item['task_id']} profile={item['profile']} status={item['status']} "
            f"finalization={item['finalization_status']} layers={','.join(item['recommended_layers'])} "
            f"name={item['task_name']}"
        )
    return 0


def cmd_harness(args: argparse.Namespace) -> int:
    root = target_root(args.path) if args.path else discover_workspace().root
    return run_script("adaptive_harness.py", args.args, root)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=render_banner() + "\nHelm CLI for stability-first agent operations.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    detect = subparsers.add_parser("detect", help="Detect the workspace layout at a path.")
    detect.add_argument("--path", help="Workspace path to inspect. Defaults to the current directory.")
    detect.add_argument("--json", action="store_true")
    detect.set_defaults(func=cmd_detect)

    init = subparsers.add_parser("init", help="Initialize a Helm-native workspace.")
    init.add_argument("--path", help=f"Workspace path to initialize. Defaults to {DEFAULT_WORKSPACE}.")
    init.add_argument("--force", action="store_true", help="Overwrite reference files and MEMORY.md if they already exist.")
    init.add_argument("--json", action="store_true")
    init.set_defaults(func=cmd_init)

    doctor = subparsers.add_parser("doctor", help="Validate Helm workspace structure and references.")
    doctor.add_argument("--path", help="Workspace path to inspect. Defaults to the current directory.")
    doctor.add_argument("--json", action="store_true")
    doctor.set_defaults(func=cmd_doctor)

    survey = subparsers.add_parser("survey", help="Show onboarding guidance for external runtimes and note vaults.")
    survey.add_argument("--path", help=f"Helm workspace path. Defaults to {DEFAULT_WORKSPACE}.")
    survey.add_argument("--json", action="store_true")
    survey.set_defaults(func=cmd_survey)

    validate = subparsers.add_parser("validate", help="Validate execution profiles and skill policy consistency.")
    validate.add_argument("--path", help="Workspace path to inspect. Defaults to the current directory.")
    validate.add_argument("--json", action="store_true")
    validate.set_defaults(func=cmd_validate)

    status = subparsers.add_parser("status", help="Summarize recent Helm operational state.")
    status.add_argument("--path", help="Workspace path to inspect. Defaults to the current directory.")
    status.add_argument("--json", action="store_true")
    status.add_argument("--verbose", action="store_true")
    status.set_defaults(func=cmd_status)

    adopt = subparsers.add_parser("adopt", help="Register an external workspace as a read-only context source.")
    adopt.add_argument("--path", help=f"Helm workspace path. Defaults to {DEFAULT_WORKSPACE}.")
    adopt.add_argument("--from-path", required=True, help="External workspace root to adopt as a context source.")
    adopt.add_argument("--name", help="Stable source name inside Helm.")
    adopt.add_argument("--kind", choices=["openclaw", "hermes", "generic"], help="Override the detected source kind.")
    adopt.add_argument("--json", action="store_true")
    adopt.set_defaults(func=cmd_adopt)

    onboard = subparsers.add_parser("onboard", help="Guide and optionally apply onboarding actions for external runtimes and note vaults.")
    onboard.add_argument("--path", help=f"Helm workspace path. Defaults to {DEFAULT_WORKSPACE}.")
    onboard.add_argument("--use-detected", action="store_true", help="Prepare onboarding actions from auto-detected candidates.")
    onboard.add_argument("--adopt-openclaw", help="Explicit OpenClaw workspace path to adopt read-only.")
    onboard.add_argument("--adopt-hermes", help="Explicit Hermes workspace path to adopt read-only.")
    onboard.add_argument("--adopt-obsidian", help="Explicit Obsidian vault or Markdown notes root to adopt read-only.")
    onboard.add_argument("--dry-run", action="store_true", help="Print the onboarding plan without applying it.")
    onboard.add_argument("--skip-checks", action="store_true", help="Do not run doctor, validate, and status after applying the onboarding plan.")
    onboard.add_argument("--json", action="store_true")
    onboard.set_defaults(func=cmd_onboard)

    sources = subparsers.add_parser("sources", help="List adopted external context sources and migration notes.")
    sources.add_argument("--path", help="Helm workspace path. Defaults to the current directory.")
    sources.add_argument("--json", action="store_true")
    sources.set_defaults(func=cmd_sources)

    profile = subparsers.add_parser("profile", help="Work with execution profiles and profiled runs.")
    profile.add_argument("--path", help="Workspace path to target.")
    profile.add_argument("args", nargs=argparse.REMAINDER)
    profile.set_defaults(func=cmd_profile)

    context = subparsers.add_parser("context", help="Query Helm memory, task, command, and checkpoint state.")
    context.add_argument("--path", help="Workspace path to target.")
    context.add_argument("args", nargs=argparse.REMAINDER)
    context.set_defaults(func=cmd_context)

    checkpoint = subparsers.add_parser("checkpoint", help="Create, inspect, restore, and recommend checkpoints.")
    checkpoint_subparsers = checkpoint.add_subparsers(dest="checkpoint_command", required=True)

    checkpoint_list = checkpoint_subparsers.add_parser("list", help="List recent checkpoints.")
    checkpoint_list.add_argument("--path", help="Workspace path to inspect. Defaults to the current directory.")
    checkpoint_list.add_argument("--limit", type=int, default=20)
    checkpoint_list.add_argument("--json", action="store_true")
    checkpoint_list.set_defaults(func=cmd_checkpoint_list)

    checkpoint_show = checkpoint_subparsers.add_parser("show", help="Show checkpoint metadata.")
    checkpoint_show.add_argument("checkpoint_id")
    checkpoint_show.add_argument("--path", help="Workspace path to inspect. Defaults to the current directory.")
    checkpoint_show.add_argument("--json", action="store_true")
    checkpoint_show.set_defaults(func=cmd_checkpoint_show)

    checkpoint_preview = checkpoint_subparsers.add_parser("preview", help="Preview files inside a checkpoint archive.")
    checkpoint_preview.add_argument("checkpoint_id")
    checkpoint_preview.add_argument("--path", help="Workspace path to inspect. Defaults to the current directory.")
    checkpoint_preview.set_defaults(func=cmd_checkpoint_preview)

    checkpoint_restore = checkpoint_subparsers.add_parser("restore", help="Restore files from a checkpoint archive.")
    checkpoint_restore.add_argument("checkpoint_id")
    checkpoint_restore.add_argument("--path", help="Workspace path to inspect. Defaults to the current directory.")
    checkpoint_restore.set_defaults(func=cmd_checkpoint_restore)

    checkpoint_create = checkpoint_subparsers.add_parser("create", help="Create a checkpoint for one or more paths.")
    checkpoint_create.add_argument("--path", dest="path", help="Workspace path to target. Defaults to the current directory.")
    checkpoint_create.add_argument("--label", required=True, help="Short checkpoint label.")
    checkpoint_create.add_argument("--include", action="append", required=True, help="Workspace-relative path to include. Repeatable.")
    checkpoint_create.set_defaults(func=cmd_checkpoint_create)

    checkpoint_recommend_sub = checkpoint_subparsers.add_parser("recommend", help="Recommend the checkpoint to use for a risky task.")
    checkpoint_recommend_sub.add_argument("--path", help="Workspace path to inspect. Defaults to the current directory.")
    checkpoint_recommend_sub.add_argument("--task-id", help="Specific task id to inspect. Defaults to the latest risky task.")
    checkpoint_recommend_sub.add_argument("--json", action="store_true")
    checkpoint_recommend_sub.set_defaults(func=cmd_checkpoint_recommend)

    checkpoint_finalize = checkpoint_subparsers.add_parser("finalize", help="Inspect finalization state together with the recommended checkpoint.")
    checkpoint_finalize.add_argument("--path", help="Workspace path to inspect. Defaults to the current directory.")
    checkpoint_finalize.add_argument("--task-id", help="Specific task id to inspect. Defaults to the latest risky task when applicable.")
    checkpoint_finalize.add_argument("--json", action="store_true")
    checkpoint_finalize.set_defaults(func=cmd_checkpoint_finalize)

    checkpoint_recommend = subparsers.add_parser("checkpoint-recommend", help="Recommend the checkpoint to use for a risky task.")
    checkpoint_recommend.add_argument("--path", help="Workspace path to inspect. Defaults to the current directory.")
    checkpoint_recommend.add_argument("--task-id", help="Specific task id to inspect. Defaults to the latest risky task.")
    checkpoint_recommend.add_argument("--json", action="store_true")
    checkpoint_recommend.set_defaults(func=cmd_checkpoint_recommend)

    skill = subparsers.add_parser("skill", help="Create and promote Helm skills.")
    skill.add_argument("--path", help="Workspace path to target.")
    skill.add_argument("args", nargs=argparse.REMAINDER)
    skill.set_defaults(func=cmd_skill)

    skill_diff = subparsers.add_parser("skill-diff", help="Show the diff between a draft skill and the live skill, if any.")
    skill_diff.add_argument("--path", help="Workspace path to inspect. Defaults to the current directory.")
    skill_diff.add_argument("--name", required=True, help="Draft skill slug under skill_drafts/.")
    skill_diff.add_argument("--json", action="store_true")
    skill_diff.set_defaults(func=cmd_skill_diff)

    skill_review = subparsers.add_parser("skill-review", help="Alias for reviewing a draft skill diff.")
    skill_review.add_argument("--path", help="Workspace path to inspect. Defaults to the current directory.")
    skill_review.add_argument("--name", required=True, help="Draft skill slug under skill_drafts/.")
    skill_review.add_argument("--json", action="store_true")
    skill_review.set_defaults(func=cmd_skill_review)

    skill_approve = subparsers.add_parser("skill-approve", help="Approve and promote a draft skill.")
    skill_approve.add_argument("--path", help="Workspace path to inspect. Defaults to the current directory.")
    skill_approve.add_argument("--name", required=True, help="Draft skill slug under skill_drafts/.")
    skill_approve.add_argument("--dry-run", action="store_true")
    skill_approve.set_defaults(func=cmd_skill_approve)

    skill_reject = subparsers.add_parser("skill-reject", help="Reject a draft skill and store the rejection reason.")
    skill_reject.add_argument("--path", help="Workspace path to inspect. Defaults to the current directory.")
    skill_reject.add_argument("--name", required=True, help="Draft skill slug under skill_drafts/.")
    skill_reject.add_argument("--reason", required=True, help="Short rejection reason.")
    skill_reject.add_argument("--json", action="store_true")
    skill_reject.set_defaults(func=cmd_skill_reject)

    ops = subparsers.add_parser("ops", help="Inspect daily, task, and command reports.")
    ops.add_argument("--path", help="Workspace path to target.")
    ops.add_argument("args", nargs=argparse.REMAINDER)
    ops.set_defaults(func=cmd_ops)

    memory = subparsers.add_parser("memory", help="Inspect finalization-driven durable memory work queues.")
    memory.add_argument("--path", help="Workspace path to target.")
    memory.add_argument("args", nargs=argparse.REMAINDER)
    memory.set_defaults(func=cmd_memory)

    harness = subparsers.add_parser("harness", help="Run adaptive harness preflight and guarded execution flows.")
    harness.add_argument("--path", help="Workspace path to target.")
    harness.add_argument("args", nargs=argparse.REMAINDER)
    harness.set_defaults(func=cmd_harness)

    report = subparsers.add_parser("report", help="Produce a high-level Helm operations report.")
    report.add_argument("--path", help="Workspace path to inspect. Defaults to the current directory.")
    report.add_argument("--limit", type=int, default=20)
    report.add_argument("--format", choices=["text", "json", "markdown"], default="text")
    report.set_defaults(func=cmd_report)
    return parser


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]

    passthrough = {
        "profile": cmd_profile,
        "context": cmd_context,
        "skill": cmd_skill,
        "ops": cmd_ops,
        "memory": cmd_memory,
        "checkpoint": cmd_checkpoint,
        "harness": cmd_harness,
    }
    if argv and argv[0] in passthrough:
        command = argv[0]
        workspace: str | None = None
        forwarded: list[str] = []
        idx = 1
        while idx < len(argv):
            token = argv[idx]
            if token == "--path":
                if idx + 1 >= len(argv):
                    raise SystemExit("--path requires a value")
                workspace = argv[idx + 1]
                idx += 2
                continue
            forwarded.append(token)
            idx += 1
        args = argparse.Namespace(path=workspace, args=forwarded)
        return passthrough[command](args)

    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
