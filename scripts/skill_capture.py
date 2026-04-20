#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from helm_workspace import get_workspace_layout
from scripts.skill_manifest_lib import load_skill_policies as load_manifest_policies


WORKSPACE = get_workspace_layout().root
SKILLS_ROOT = WORKSPACE / "skills"
DRAFTS_ROOT = WORKSPACE / "skill_drafts"
TEMPLATE_PATH = WORKSPACE / "references" / "skill-capture-template.md"
TASK_LEDGER = get_workspace_layout().state_root / "task-ledger.jsonl"
COMMAND_LOG = get_workspace_layout().state_root / "command-log.jsonl"
CONTRACT_TEMPLATE_PATH = WORKSPACE / "references" / "skill-contract-template.json"

PLACEHOLDER_MARKERS = (
    "Describe the workflow this skill owns",
    "List the user intents or trigger phrases",
    "Required inputs",
    "Optional context",
    "Clarifying questions to ask only when necessary",
    "State the real commands, tools, or APIs to use.",
    "Prefer deterministic scripts over freeform shell improvisation.",
    "State what a successful answer must include.",
    "List what this skill should not do.",
)
PROFILE_MARKERS = ("inspect_local", "workspace_edit", "risky_edit", "service_ops", "remote_handoff")
ARTIFACT_PLACEHOLDERS = (
    "Replace this with the stable procedure learned from the source task.",
    "Document one or more commands that prove the draft skill still works.",
    "Store reusable output templates and boilerplate here.",
    "Store deterministic helper scripts here when the workflow needs real execution.",
)


def render_template(name: str, description: str, emoji: str) -> str:
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    return (
        template.replace("__SKILL_NAME__", name)
        .replace("__SKILL_DESCRIPTION__", description)
        .replace("__EMOJI__", emoji)
    )


def render_contract_template(name: str) -> str:
    template = CONTRACT_TEMPLATE_PATH.read_text(encoding="utf-8")
    return template.replace("__SKILL_NAME__", name)


def create_skill(args: argparse.Namespace) -> int:
    skill_dir = SKILLS_ROOT / args.name
    if skill_dir.exists():
        raise SystemExit(f"Skill already exists: {skill_dir}")

    skill_dir.mkdir(parents=True, exist_ok=False)
    for child in ("references", "templates", "scripts", "checks"):
        (skill_dir / child).mkdir()

    skill_md = render_template(args.name, args.description, args.emoji)
    if args.bins or args.env:
        extra = []
        if args.bins:
            extra.append(f"      bins: {args.bins!r}")
        if args.env:
            extra.append(f"      env: {args.env!r}")
        skill_md = skill_md.replace("      bins: []\n      env: []", "\n".join(extra))

    (skill_dir / "SKILL.md").write_text(skill_md + "\n", encoding="utf-8")
    (skill_dir / "contract.json").write_text(render_contract_template(args.name) + "\n", encoding="utf-8")
    (skill_dir / "references" / "README.md").write_text(
        "# References\n\nStore durable workflow notes, routing rules, and source material here.\n",
        encoding="utf-8",
    )
    (skill_dir / "templates" / "README.md").write_text(
        "# Templates\n\nStore reusable output templates and boilerplate here.\n",
        encoding="utf-8",
    )
    (skill_dir / "scripts" / "README.md").write_text(
        "# Scripts\n\nStore deterministic helper scripts here when the workflow needs real execution.\n",
        encoding="utf-8",
    )
    (skill_dir / "checks" / "README.md").write_text(
        "# Checks\n\nStore validation notes, smoke tests, or command examples here.\n",
        encoding="utf-8",
    )
    print(skill_dir)
    return 0


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows: list[dict] = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            print(f"warning: ignoring malformed JSONL line {lineno} in {path}: {exc}", file=sys.stderr)
            continue
        if not isinstance(payload, dict):
            print(f"warning: ignoring non-object JSONL line {lineno} in {path}", file=sys.stderr)
            continue
        rows.append(payload)
    return rows


def load_policies() -> dict[str, dict]:
    return load_manifest_policies(WORKSPACE, WORKSPACE / "references" / "skill_profile_policies.json")


def extract_frontmatter_description(path: Path) -> str | None:
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        return None
    for line in text.splitlines()[1:]:
        if line == "---":
            break
        if line.startswith("description:"):
            return line.partition(":")[2].strip().strip('"')
    return None


def normalized_tokens(text: str) -> set[str]:
    cleaned = []
    for char in text.casefold():
        cleaned.append(char if char.isalnum() else " ")
    return {token for token in "".join(cleaned).split() if len(token) > 2}


def latest_task_by_id(task_id: str) -> dict:
    latest: dict | None = None
    for entry in read_jsonl(TASK_LEDGER):
        if entry.get("task_id") == task_id:
            latest = entry
    if latest is None:
        raise SystemExit(f"Task not found: {task_id}")
    return latest


def task_commands(task_id: str) -> list[dict]:
    return [entry for entry in read_jsonl(COMMAND_LOG) if entry.get("task_id") == task_id]


def render_draft_template(
    *,
    name: str,
    description: str,
    emoji: str,
    task: dict,
    commands: list[dict],
) -> str:
    task_command = task.get("command_preview") or " ".join(task.get("command", []))
    command_lines = []
    for command in commands[:5]:
        label = command.get("label") or "command"
        preview = " ".join(command.get("command", []))
        command_lines.append(f"- `{label}`: `{preview}`")
    command_section = "\n".join(command_lines) if command_lines else "- No linked low-level commands were recorded."

    skill_md = render_template(name, description, emoji)
    body = f"""
## Draft Provenance

- source task id: `{task.get("task_id")}`
- source skill: `{task.get("skill") or "-"}`
- source profile: `{task.get("profile") or "-"}`
- source task name: `{task.get("task_name") or "-"}`
- source command: `{task_command}`

## Candidate Execution Signals

{command_section}

## Review Checklist

- Replace generic trigger bullets with real activation conditions.
- Document source priority if the workflow can be driven by multiple kinds of evidence or input material.
- Convert the observed command path into stable scripts or documented tool calls.
- Add at least one durable reference, template, script, or check before promotion.
- Decide the right execution profile and whether checkpointing is required.
- Update `SKILLS_REGISTRY.md` only after the draft is promoted into `skills/`.
""".strip()
    return skill_md + "\n\n" + body + "\n"


def draft_from_task(args: argparse.Namespace) -> int:
    task = latest_task_by_id(args.task_id)
    if task.get("status") != "completed":
        raise SystemExit(f"Task must be completed before drafting a skill: {args.task_id}")

    draft_dir = DRAFTS_ROOT / args.name
    if draft_dir.exists():
        raise SystemExit(f"Draft already exists: {draft_dir}")

    DRAFTS_ROOT.mkdir(parents=True, exist_ok=True)
    draft_dir.mkdir(parents=True, exist_ok=False)
    for child in ("references", "templates", "scripts", "checks", "meta"):
        (draft_dir / child).mkdir()

    commands = task_commands(args.task_id)
    skill_md = render_draft_template(
        name=args.name,
        description=args.description,
        emoji=args.emoji,
        task=task,
        commands=commands,
    )
    (draft_dir / "SKILL.md").write_text(skill_md, encoding="utf-8")
    (draft_dir / "contract.json").write_text(render_contract_template(args.name) + "\n", encoding="utf-8")
    (draft_dir / "references" / "workflow-notes.md").write_text(
        "# Workflow Notes\n\nReplace this with the stable procedure learned from the source task.\n",
        encoding="utf-8",
    )
    (draft_dir / "checks" / "smoke-test.md").write_text(
        "# Smoke Test\n\nDocument one or more commands that prove the draft skill still works.\n",
        encoding="utf-8",
    )
    (draft_dir / "meta" / "task-summary.json").write_text(
        json.dumps({"task": task, "commands": commands}, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(draft_dir)
    return 0


def draft_path(name: str) -> Path:
    return DRAFTS_ROOT / name


def nonempty_artifacts(root: Path) -> list[str]:
    found: list[str] = []
    for child in ("references", "templates", "scripts", "checks"):
        child_root = root / child
        if not child_root.exists():
            continue
        for path in sorted(child_root.rglob("*")):
            if not path.is_file():
                continue
            if path.name == "README.md" and path.read_text(encoding="utf-8").strip().startswith("# "):
                continue
            if path.stat().st_size == 0:
                continue
            found.append(str(path.relative_to(root)))
    return found


def substantive_artifacts(root: Path) -> tuple[list[str], list[str]]:
    substantive: list[str] = []
    placeholder_like: list[str] = []
    for relpath in nonempty_artifacts(root):
        path = root / relpath
        text = path.read_text(encoding="utf-8")
        if any(marker in text for marker in ARTIFACT_PLACEHOLDERS):
            placeholder_like.append(relpath)
            continue
        substantive.append(relpath)
    return substantive, placeholder_like


def duplicate_candidates(name: str, description: str) -> list[dict]:
    draft_tokens = normalized_tokens(f"{name} {description}")
    candidates: list[dict] = []
    if not SKILLS_ROOT.exists():
        return candidates
    for skill_dir in sorted(SKILLS_ROOT.iterdir()):
        if not skill_dir.is_dir():
            continue
        skill_md = skill_dir / "SKILL.md"
        existing_description = extract_frontmatter_description(skill_md) or ""
        existing_tokens = normalized_tokens(f"{skill_dir.name} {existing_description}")
        if not existing_tokens:
            continue
        overlap = len(draft_tokens & existing_tokens)
        union = len(draft_tokens | existing_tokens)
        similarity = overlap / union if union else 0.0
        if skill_dir.name == name or similarity >= 0.45:
            candidates.append(
                {
                    "skill": skill_dir.name,
                    "similarity": round(similarity, 3),
                    "description": existing_description,
                }
            )
    return candidates


def assess_draft(root: Path) -> dict:
    if not root.exists():
        raise SystemExit(f"Draft not found: {root}")
    skill_md_path = root / "SKILL.md"
    if not skill_md_path.exists():
        raise SystemExit(f"Draft missing SKILL.md: {root}")

    text = skill_md_path.read_text(encoding="utf-8")
    description = extract_frontmatter_description(skill_md_path) or ""
    placeholder_hits = [marker for marker in PLACEHOLDER_MARKERS if marker in text]
    profile_hits = [marker for marker in PROFILE_MARKERS if marker in text]
    artifacts, artifact_placeholders = substantive_artifacts(root)
    meta_task = root / "meta" / "task-summary.json"
    review_checklist_present = "## Review Checklist" in text
    policies = load_policies()
    policy = policies.get(root.name)
    policy_conflicts: list[str] = []
    if policy:
        allowed = set(policy.get("allowed_profiles", []))
        for profile in profile_hits:
            if allowed and profile not in allowed:
                policy_conflicts.append(profile)
    duplicates = duplicate_candidates(root.name, description)
    conflicting_duplicates = [item for item in duplicates if item["skill"] == root.name or item["similarity"] >= 0.7]

    checks = {
        "missing_placeholders": len(placeholder_hits) == 0,
        "has_execution_profile": len(profile_hits) > 0,
        "has_substantive_artifact": len(artifacts) > 0,
        "has_task_summary": meta_task.exists(),
        "has_review_checklist": review_checklist_present,
        "no_policy_conflict": len(policy_conflicts) == 0,
        "no_duplicate_skill_conflict": len(conflicting_duplicates) == 0,
    }
    passed = all(checks.values())
    return {
        "draft": str(root),
        "passed": passed,
        "checks": checks,
        "details": {
            "placeholder_hits": placeholder_hits,
            "profile_hits": profile_hits,
            "artifacts": artifacts,
            "artifact_placeholders": artifact_placeholders,
            "task_summary": str(meta_task.relative_to(root)) if meta_task.exists() else None,
            "policy_entry": policy,
            "policy_conflicts": policy_conflicts,
            "duplicate_candidates": duplicates[:5],
        },
    }


def follow_up_steps(name: str, report: dict, target_exists: bool) -> list[str]:
    steps: list[str] = []
    if not target_exists:
        steps.append(f"Review whether `{name}` should be added to SKILLS_REGISTRY.md.")
    if report["details"].get("policy_entry") is None:
        steps.append(f"Define `{name}` allowed/default profiles in `{name}/contract.json` before promotion.")
    steps.append(f"Run a routing-surface review if `{name}` changes shared dispatch or execution policy materially.")
    return steps


def assessment_summary(report: dict) -> list[str]:
    lines: list[str] = []
    checks = report["checks"]
    if checks["missing_placeholders"] is False:
        lines.append("Replace the remaining template placeholders in SKILL.md.")
    if checks["has_execution_profile"] is False:
        lines.append("Choose and document at least one execution profile for the draft.")
    if checks["has_substantive_artifact"] is False:
        lines.append("Add at least one real reference, template, script, or check artifact.")
    if checks["has_task_summary"] is False:
        lines.append("Add or restore meta/task-summary.json provenance for the source task.")
    if checks["has_review_checklist"] is False:
        lines.append("Restore the review checklist section so the draft remains reviewable.")
    if checks["no_policy_conflict"] is False:
        conflicts = ", ".join(report["details"]["policy_conflicts"])
        lines.append(f"Align the draft profiles with `{root.name}/contract.json` or the inherited manifest policy: {conflicts}.")
    if checks["no_duplicate_skill_conflict"] is False:
        duplicates = ", ".join(item["skill"] for item in report["details"]["duplicate_candidates"][:3])
        lines.append(f"Resolve overlap with existing skills before promotion: {duplicates}.")
    if not lines:
        lines.append("Draft is promotable. Review the diff once more, then promote with explicit approval.")
    return lines


def cmd_assess_draft(args: argparse.Namespace) -> int:
    root = draft_path(args.name)
    report = assess_draft(root)
    assessment_path = root / "meta" / "assessment.json"
    assessment_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(f"draft={report['draft']}")
        print(f"passed={report['passed']}")
        for key, value in report["checks"].items():
            print(f"{key}={value}")
        if report["details"]["placeholder_hits"]:
            print("placeholder_hits=" + ", ".join(report["details"]["placeholder_hits"]))
        if report["details"]["artifact_placeholders"]:
            print("artifact_placeholders=" + ", ".join(report["details"]["artifact_placeholders"]))
        if report["details"]["artifacts"]:
            print("artifacts=" + ", ".join(report["details"]["artifacts"]))
        if report["details"]["policy_conflicts"]:
            print("policy_conflicts=" + ", ".join(report["details"]["policy_conflicts"]))
        if report["details"]["duplicate_candidates"]:
            print(
                "duplicate_candidates="
                + ", ".join(
                    f"{item['skill']}:{item['similarity']}" for item in report["details"]["duplicate_candidates"][:5]
                )
            )
        for line in assessment_summary(report):
            print(f"next={line}")
    return 0 if report["passed"] else 1


def cmd_promote_draft(args: argparse.Namespace) -> int:
    root = draft_path(args.name)
    target = SKILLS_ROOT / args.name
    if target.exists():
        raise SystemExit(f"Target skill already exists: {target}")
    report = assess_draft(root)
    assessment_path = root / "meta" / "assessment.json"
    assessment_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if not report["passed"]:
        raise SystemExit(
            "Draft failed assessment. Run `skill_capture.py assess-draft --name "
            f"{args.name}` and fix the reported gaps before promotion."
        )
    if not args.approve:
        raise SystemExit("Promotion requires explicit approval via --approve.")
    payload = {
        "source": str(root),
        "target": str(target),
        "assessment_summary": assessment_summary(report),
        "next_steps": follow_up_steps(args.name, report, target.exists()),
    }
    if args.dry_run:
        payload["promotable"] = True
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0

    shutil.copytree(root, target)
    payload["promoted_to"] = str(target)
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Scaffold a new Helm workspace skill.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    create = subparsers.add_parser("create", help="Create a new skill scaffold.")
    create.add_argument("--name", required=True, help="Skill slug, e.g. example-skill")
    create.add_argument("--description", required=True, help="One-line skill description.")
    create.add_argument("--emoji", default="🧩", help="Emoji for openclaw metadata.")
    create.add_argument("--bins", nargs="*", default=[], help="Required binaries.")
    create.add_argument("--env", nargs="*", default=[], help="Required environment variables.")
    create.set_defaults(func=create_skill)

    draft = subparsers.add_parser("draft-from-task", help="Create a review-only skill draft from a completed task.")
    draft.add_argument("--task-id", required=True, help="Completed task_id recorded in task-ledger.jsonl")
    draft.add_argument("--name", required=True, help="Draft skill slug, e.g. new-ops-skill-ko")
    draft.add_argument("--description", required=True, help="One-line description for the draft skill.")
    draft.add_argument("--emoji", default="🧩", help="Emoji for openclaw metadata.")
    draft.set_defaults(func=draft_from_task)

    assess = subparsers.add_parser("assess-draft", help="Evaluate whether a draft is promotable.")
    assess.add_argument("--name", required=True, help="Draft skill slug under skill_drafts/")
    assess.add_argument("--json", action="store_true")
    assess.set_defaults(func=cmd_assess_draft)

    promote = subparsers.add_parser("promote-draft", help="Promote a reviewed draft into skills/ after assessment.")
    promote.add_argument("--name", required=True, help="Draft skill slug under skill_drafts/")
    promote.add_argument("--approve", action="store_true", help="Explicitly confirm promotion.")
    promote.add_argument("--dry-run", action="store_true", help="Validate promotability without copying into skills/.")
    promote.set_defaults(func=cmd_promote_draft)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
