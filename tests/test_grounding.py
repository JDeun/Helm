"""Tests for scripts/grounding.py.

Coverage targets:
1. build_grounding assembles SKILL.md contract sections into the preamble.
2. build_grounding includes memory_context when provided.
3. build_grounding includes declared reference text from skill_dir/references/.
4. build_grounding never crashes on missing/partial guidance (empty skill_dir).
5. build_grounding always includes the request text.
6. render_deterministic_template renders deterministically from args using a
   template file when present.
7. render_deterministic_template falls back to a plain key/value rendering
   when no template file exists, and is deterministic (sorted keys).
8. should_use_deterministic_fallback truth table over tier_mode and
   repair_budget_left.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.grounding import (  # noqa: E402
    build_grounding,
    render_deterministic_template,
    should_use_deterministic_fallback,
)


_SKILL_MD = """---
description: "Demo skill"
---
# Demo Skill

## Core rule

Use the strict runner for all mutations.

## Input contract

- Required inputs: account and date range
- Ask first when missing: ask for the account id

## Decision contract

- Red flags: ambiguous account, missing ledger period.

## Output contract

- Default output format: summary plus changed files
- Always include: account, date range, and next step

## Failure contract

- Failure types: missing input, tool failure
- Fallback behavior: stop before mutation
"""


# ---------------------------------------------------------------------------
# build_grounding
# ---------------------------------------------------------------------------

def test_build_grounding_includes_skill_md_contract_sections(tmp_path: Path) -> None:
    skill_dir = tmp_path / "demo-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(_SKILL_MD, encoding="utf-8")

    preamble = build_grounding(skill_dir, "reconcile the ledger")

    assert "Use the strict runner for all mutations." in preamble
    assert "Default output format: summary plus changed files" in preamble
    assert "Failure types: missing input, tool failure" in preamble


def test_build_grounding_includes_memory_context(tmp_path: Path) -> None:
    skill_dir = tmp_path / "demo-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(_SKILL_MD, encoding="utf-8")

    preamble = build_grounding(skill_dir, "reconcile the ledger", memory_context="last run flagged account 42")

    assert "last run flagged account 42" in preamble


def test_build_grounding_includes_declared_reference_text(tmp_path: Path) -> None:
    skill_dir = tmp_path / "demo-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(_SKILL_MD, encoding="utf-8")
    references = skill_dir / "references"
    references.mkdir()
    (references / "routing-notes.md").write_text("Route ambiguous ledgers to a human first.", encoding="utf-8")

    preamble = build_grounding(skill_dir, "reconcile the ledger")

    assert "Route ambiguous ledgers to a human first." in preamble


def test_build_grounding_always_includes_request(tmp_path: Path) -> None:
    skill_dir = tmp_path / "demo-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(_SKILL_MD, encoding="utf-8")

    preamble = build_grounding(skill_dir, "reconcile the ledger for account 42")

    assert "reconcile the ledger for account 42" in preamble


def test_build_grounding_handles_missing_skill_md_gracefully(tmp_path: Path) -> None:
    skill_dir = tmp_path / "empty-skill"
    skill_dir.mkdir()

    preamble = build_grounding(skill_dir, "do the thing")

    assert isinstance(preamble, str)
    assert "do the thing" in preamble


def test_build_grounding_handles_nonexistent_skill_dir_gracefully(tmp_path: Path) -> None:
    skill_dir = tmp_path / "does-not-exist"

    preamble = build_grounding(skill_dir, "do the thing")

    assert isinstance(preamble, str)
    assert "do the thing" in preamble


def test_build_grounding_handles_empty_skill_md(tmp_path: Path) -> None:
    skill_dir = tmp_path / "blank-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("", encoding="utf-8")

    preamble = build_grounding(skill_dir, "do the thing")

    assert isinstance(preamble, str)
    assert "do the thing" in preamble


def test_build_grounding_skips_empty_memory_context(tmp_path: Path) -> None:
    skill_dir = tmp_path / "demo-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(_SKILL_MD, encoding="utf-8")

    preamble = build_grounding(skill_dir, "reconcile the ledger", memory_context="   ")

    assert "Memory context" not in preamble


# ---------------------------------------------------------------------------
# render_deterministic_template
# ---------------------------------------------------------------------------

def test_render_deterministic_template_uses_template_file_placeholders(tmp_path: Path) -> None:
    skill_dir = tmp_path / "demo-skill"
    templates = skill_dir / "templates"
    templates.mkdir(parents=True)
    (templates / "fallback.md").write_text(
        "Account: __ACCOUNT__\nDate range: __DATE_RANGE__\n", encoding="utf-8"
    )

    rendered = render_deterministic_template(skill_dir, {"account": "42", "date_range": "2026-07"})

    assert rendered == "Account: 42\nDate range: 2026-07\n"


def test_render_deterministic_template_is_deterministic_repeat_call(tmp_path: Path) -> None:
    skill_dir = tmp_path / "demo-skill"
    templates = skill_dir / "templates"
    templates.mkdir(parents=True)
    (templates / "fallback.md").write_text("Account: __ACCOUNT__\n", encoding="utf-8")

    first = render_deterministic_template(skill_dir, {"account": "42"})
    second = render_deterministic_template(skill_dir, {"account": "42"})

    assert first == second


def test_render_deterministic_template_falls_back_without_template_file(tmp_path: Path) -> None:
    skill_dir = tmp_path / "demo-skill"
    skill_dir.mkdir()

    rendered = render_deterministic_template(skill_dir, {"b": "2", "a": "1"})

    assert rendered == "a: 1\nb: 2"


def test_render_deterministic_template_handles_empty_args(tmp_path: Path) -> None:
    skill_dir = tmp_path / "demo-skill"
    skill_dir.mkdir()

    rendered = render_deterministic_template(skill_dir, {})

    assert rendered == ""


def test_render_deterministic_template_handles_missing_skill_dir(tmp_path: Path) -> None:
    skill_dir = tmp_path / "does-not-exist"

    rendered = render_deterministic_template(skill_dir, {"a": "1"})

    assert rendered == "a: 1"


# ---------------------------------------------------------------------------
# should_use_deterministic_fallback
# ---------------------------------------------------------------------------

def test_should_use_deterministic_fallback_when_deterministic_only() -> None:
    assert should_use_deterministic_fallback("deterministic_only", 3) is True


def test_should_use_deterministic_fallback_when_budget_exhausted() -> None:
    assert should_use_deterministic_fallback("local_model_available", 0) is True


def test_should_use_deterministic_fallback_when_budget_negative() -> None:
    assert should_use_deterministic_fallback("cloud_available", -1) is True


def test_should_use_deterministic_fallback_false_when_local_available_and_budget_left() -> None:
    assert should_use_deterministic_fallback("local_model_available", 2) is False


def test_should_use_deterministic_fallback_false_when_cloud_available_and_budget_left() -> None:
    assert should_use_deterministic_fallback("cloud_available", 1) is False


def test_should_use_deterministic_fallback_returns_bool_type() -> None:
    result = should_use_deterministic_fallback("cloud_available", 5)
    assert result is False and isinstance(result, bool)
