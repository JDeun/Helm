"""Trace-driven extensions to the skill capture workflow.

This module is a sibling to :mod:`scripts.skill_capture` rather than an
extension of it because ``skill_capture.py`` already registers subcommands
named ``draft-from-task`` and ``assess-draft`` with incompatible signatures:

* The existing ``draft-from-task`` requires ``--name``, ``--description``, and
  ``--emoji`` and reads from the task-ledger JSONL file.
* The existing ``assess-draft`` identifies the draft by ``--name`` (a slug
  under the workspace drafts root) and always consults the workspace policy.

The commands here are trace-oriented counterparts:

* ``draft-from-task --task-id <id>`` reads a trace JSON file by task-id and
  synthesises a minimal skill-scaffold markdown stub without requiring a
  ``--name`` or ``--description`` argument.
* ``assess-draft --draft-path <path>`` accepts an arbitrary filesystem path and
  checks that the referenced ``SKILL.md`` (or standalone ``.md`` file)
  contains all required sections from ``references/skill-capture-template.md``.
  It prints ``OK`` or numbered issues.

Both commands are intentionally lightweight — they do not write to the
workspace state, task-ledger, or policy files.

CLI
---
::

    python3 scripts/skill_capture_ext.py draft-from-task --task-id <id> \\
        [--traces-dir <dir>] [--drafts-dir <dir>]

    python3 scripts/skill_capture_ext.py assess-draft --draft-path <path> \\
        [--template <path>]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

__all__ = [
    "draft_from_trace",
    "assess_draft_path",
]

# ---------------------------------------------------------------------------
# Required sections derived from references/skill-capture-template.md
# ---------------------------------------------------------------------------

_TEMPLATE_SECTIONS = [
    "## Core rule",
    "## Input contract",
    "## Decision contract",
    "## Execution contract",
    "## Output contract",
    "## Post-write validation contract",
    "## Failure contract",
    "## Do",
    "## Do not",
]


def _default_traces_dir() -> Path:
    env = os.environ.get("OPENCLAW_TRACES_DIR")
    if env:
        return Path(env)
    return Path.home() / ".openclaw" / "workspace" / ".openclaw" / "traces"


def _default_drafts_dir() -> Path:
    env = os.environ.get("OPENCLAW_DRAFTS_DIR")
    if env:
        return Path(env)
    return Path.home() / ".openclaw" / "workspace" / "skill_drafts"


def _load_template_sections(template_path: Path) -> list[str]:
    """Extract required section headings from *template_path*.

    Raises :class:`SystemExit` with a clear message if the file is absent.
    """
    if not template_path.exists():
        raise SystemExit(
            f"skill-capture-template.md not found at {template_path}. "
            "Cannot assess drafts without the reference template. "
            "Ensure the file exists before running assess-draft."
        )
    sections: list[str] = []
    for line in template_path.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            sections.append(line.rstrip())
    return sections if sections else _TEMPLATE_SECTIONS


def _slugify(text: str) -> str:
    """Convert *text* to a filesystem-safe slug."""
    parts = []
    for ch in text.casefold():
        if ch.isalnum():
            parts.append(ch)
        elif ch in (" ", "-", "_"):
            parts.append("-")
    slug = "".join(parts).strip("-")
    # Collapse repeated hyphens
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug[:64] or "unnamed-skill"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def draft_from_trace(
    task_id: str,
    *,
    traces_dir: Path | None = None,
    drafts_dir: Path | None = None,
) -> Path:
    """Read a trace by *task_id* and write a skill scaffold markdown stub.

    The stub is written to *drafts_dir/<slug>.md* where *slug* is derived from
    the trace's ``inputSummary``.  If the draft file already exists it is
    overwritten.

    Parameters
    ----------
    task_id:
        The ``taskId`` value recorded in the trace JSON file.
    traces_dir:
        Directory that contains the trace files (``*.json``).  Defaults to the
        canonical ``OPENCLAW_TRACES_DIR`` location.
    drafts_dir:
        Directory in which to write the draft markdown file.  Defaults to
        ``~/.openclaw/workspace/skill_drafts/`` (or ``OPENCLAW_DRAFTS_DIR``).

    Returns
    -------
    pathlib.Path
        Path of the written draft file.

    Raises
    ------
    SystemExit
        If the trace file for *task_id* cannot be found.
    """
    traces_dir = Path(traces_dir) if traces_dir else _default_traces_dir()
    drafts_dir = Path(drafts_dir) if drafts_dir else _default_drafts_dir()

    trace_path = traces_dir / f"{task_id}.json"
    if not trace_path.exists():
        raise SystemExit(f"Trace not found: {trace_path}")

    with trace_path.open("r", encoding="utf-8") as fh:
        trace = json.load(fh)

    task_name = trace.get("inputSummary") or task_id
    skill_name = trace.get("skill") or "unknown-skill"
    profile = trace.get("profile") or "unknown"
    outcome = trace.get("outcome") or "unknown"
    started_at = trace.get("startedAt") or ""
    tool_sequence = trace.get("toolSequence") or []

    slug = _slugify(task_name)
    drafts_dir.mkdir(parents=True, exist_ok=True)
    draft_path = drafts_dir / f"{slug}.md"

    # Build tool-sequence summary
    tool_lines: list[str] = []
    for entry in tool_sequence[:10]:
        if isinstance(entry, dict):
            name = entry.get("name") or "?"
            purpose = entry.get("purpose") or ""
            status = entry.get("status") or "?"
            tool_lines.append(f"- `{name}` ({status}): {purpose}")
    tools_section = "\n".join(tool_lines) if tool_lines else "- No tool calls recorded."

    content = f"""---
name: {slug}
description: "Skill draft generated from trace {task_id}"
metadata:
  openclaw:
    emoji: "🧩"
    requires:
      bins: []
      env: []
---

# {task_name}

## Core rule

(Derived from trace `{task_id}` — replace with the narrow rule that governs this skill.)

## Input contract

- Required inputs
- Optional inputs
- Ask first when missing

## Decision contract

- State the decision order explicitly.
- List the red flags weaker models are likely to miss.

## Execution contract

- State the real commands, tools, or APIs to use.
- Prefer deterministic scripts over freeform shell improvisation.

## Output contract

- Default output format
- Always include
- Length rule

## Post-write validation contract

- Required when this skill writes durable files or automation state.

## Failure contract

- Failure types
- Fallback behavior

## Do

- (fill in)

## Do not

- (fill in)

## Draft Provenance

- task_id: `{task_id}`
- task_name: `{task_name}`
- skill: `{skill_name}`
- profile: `{profile}`
- outcome: `{outcome}`
- started_at: `{started_at}`

## Observed Tool Sequence

{tools_section}
"""

    draft_path.write_text(content, encoding="utf-8")
    return draft_path


def assess_draft_path(
    draft_path: Path,
    *,
    template_path: Path | None = None,
) -> tuple[bool, list[str]]:
    """Check *draft_path* for required sections from the skill-capture template.

    Parameters
    ----------
    draft_path:
        Path to a markdown draft file (either a ``SKILL.md`` inside a skill
        directory or a standalone ``.md`` file).
    template_path:
        Path to ``skill-capture-template.md``.  Defaults to
        ``<repo-root>/references/skill-capture-template.md``.

    Returns
    -------
    (ok, issues)
        *ok* is ``True`` when no issues are found.  *issues* is a list of
        human-readable problem descriptions (empty when *ok* is ``True``).

    Raises
    ------
    SystemExit
        If ``skill-capture-template.md`` does not exist (cannot assess
        without the reference template).
    """
    draft_path = Path(draft_path)

    # Resolve actual file: if draft_path is a directory look for SKILL.md.
    if draft_path.is_dir():
        candidate = draft_path / "SKILL.md"
        if candidate.exists():
            draft_path = candidate
        else:
            return False, [f"Draft directory has no SKILL.md: {draft_path}"]

    if not draft_path.exists():
        return False, [f"Draft file not found: {draft_path}"]

    if template_path is None:
        template_path = _ROOT / "references" / "skill-capture-template.md"

    required_sections = _load_template_sections(template_path)
    text = draft_path.read_text(encoding="utf-8")

    issues: list[str] = []
    for section in required_sections:
        if section not in text:
            issues.append(f"Missing required section: {section!r}")

    return len(issues) == 0, issues


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _cmd_draft_from_task(args: argparse.Namespace) -> int:
    out = draft_from_trace(
        args.task_id,
        traces_dir=Path(args.traces_dir) if args.traces_dir else None,
        drafts_dir=Path(args.drafts_dir) if args.drafts_dir else None,
    )
    print(out)
    return 0


def _cmd_assess_draft(args: argparse.Namespace) -> int:
    template_path = Path(args.template) if getattr(args, "template", None) else None
    ok, issues = assess_draft_path(
        Path(args.draft_path),
        template_path=template_path,
    )
    if ok:
        print("OK")
        return 0
    for i, issue in enumerate(issues, start=1):
        print(f"{i}. {issue}")
    return 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Trace-driven skill-capture extensions."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # draft-from-task
    draft = subparsers.add_parser(
        "draft-from-task",
        help="Produce a skill scaffold stub from a trace JSON file.",
    )
    draft.add_argument(
        "--task-id",
        required=True,
        help="The taskId value recorded in the trace file.",
    )
    draft.add_argument(
        "--traces-dir",
        default=None,
        help="Directory containing trace JSON files.",
    )
    draft.add_argument(
        "--drafts-dir",
        default=None,
        help="Directory to write the draft markdown file into.",
    )
    draft.set_defaults(func=_cmd_draft_from_task)

    # assess-draft
    assess = subparsers.add_parser(
        "assess-draft",
        help="Check a draft markdown file for required template sections.",
    )
    assess.add_argument(
        "--draft-path",
        required=True,
        help="Path to the draft markdown file (or directory containing SKILL.md).",
    )
    assess.add_argument(
        "--template",
        default=None,
        help="Path to skill-capture-template.md (default: references/skill-capture-template.md).",
    )
    assess.set_defaults(func=_cmd_assess_draft)

    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
