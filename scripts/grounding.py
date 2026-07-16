"""Grounding-by-guidance-injection + deterministic template fallback.

Pure helpers (primitives P5 + P16) for getting a weak local model to produce
grounded, correctly-shaped output: inject the selected skill's SKILL.md
guidance (plus any declared reference text and memory context) into a
system-preamble string, and provide a deterministic template fallback for
when the model output is unusable.

No live model calls happen here — callers pass paths/args and wire the
result into their own prompt/response flow.
"""
from __future__ import annotations

from pathlib import Path

_FRONTMATTER_DELIM = "---"


def _strip_frontmatter(text: str) -> str:
    """Drop a leading ``---`` YAML frontmatter block, if present."""
    if not text.startswith(_FRONTMATTER_DELIM + "\n"):
        return text
    closing = text.find("\n" + _FRONTMATTER_DELIM, len(_FRONTMATTER_DELIM) + 1)
    if closing == -1:
        return text
    rest_start = text.find("\n", closing + 1)
    if rest_start == -1:
        return ""
    return text[rest_start + 1 :]


def _load_skill_guidance(skill_dir: Path) -> str:
    """Return the SKILL.md contract/guidance body, minus frontmatter.

    Missing or empty SKILL.md is handled gracefully by returning "".
    """
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists() or not skill_md.is_file():
        return ""
    try:
        text = skill_md.read_text(encoding="utf-8")
    except OSError:
        return ""
    return _strip_frontmatter(text).strip()


def _load_reference_text(skill_dir: Path) -> str:
    """Return concatenated text from any declared reference files.

    Reference material lives under ``skill_dir/references/`` (see
    scripts/skill_capture.py's skill scaffold). Missing directory or files
    are handled gracefully.
    """
    references_dir = skill_dir / "references"
    if not references_dir.exists() or not references_dir.is_dir():
        return ""
    chunks: list[str] = []
    for path in sorted(references_dir.rglob("*")):
        if not path.is_file():
            continue
        try:
            content = path.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeDecodeError):
            continue
        if content:
            chunks.append(content)
    return "\n\n".join(chunks)


def build_grounding(skill_dir: Path, request: str, *, memory_context: str = "") -> str:
    """Assemble a system-preamble string grounding a weak model's response.

    Combines (in order): the skill's SKILL.md contract/guidance sections,
    any declared reference text under ``skill_dir/references/``, the
    supplied memory context, and the user's request. Any missing or partial
    section is skipped rather than raising.
    """
    sections: list[str] = []

    guidance = _load_skill_guidance(skill_dir)
    if guidance:
        sections.append(guidance)

    reference_text = _load_reference_text(skill_dir)
    if reference_text:
        sections.append("## Reference material\n\n" + reference_text)

    if memory_context.strip():
        sections.append("## Memory context\n\n" + memory_context.strip())

    sections.append("## Request\n\n" + request.strip())

    return "\n\n".join(sections) + "\n"


def render_deterministic_template(skill_dir: Path, args: dict) -> str:
    """Render a deterministic, correctly-shaped fallback answer from args.

    If the skill declares a template at ``skill_dir/templates/fallback.md``,
    placeholders of the form ``__FIELD_NAME__`` (uppercased arg key) are
    substituted with the corresponding arg value. Otherwise falls back to a
    plain, sorted ``key: value`` listing so the result is always
    deterministic regardless of dict ordering.
    """
    template_path = skill_dir / "templates" / "fallback.md"
    if template_path.exists() and template_path.is_file():
        try:
            template = template_path.read_text(encoding="utf-8")
        except OSError:
            template = ""
        if template:
            for key, value in args.items():
                placeholder = f"__{str(key).upper()}__"
                template = template.replace(placeholder, str(value))
            return template

    return "\n".join(f"{key}: {value}" for key, value in sorted(args.items(), key=lambda item: str(item[0])))


def should_use_deterministic_fallback(tier_mode: str, repair_budget_left: int) -> bool:
    """Return True when the caller must fall back to the deterministic template.

    True when ``tier_mode`` is ``"deterministic_only"`` (see
    ``scripts.intelligence_tier.IntelligenceTier.mode()``) or when the
    repair budget has been exhausted (``repair_budget_left <= 0``).
    """
    return tier_mode == "deterministic_only" or repair_budget_left <= 0
