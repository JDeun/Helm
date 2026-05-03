# tests/test_skill_lifecycle.py
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.skill_lifecycle_lib import (
    DEFAULT_CONFIG,
    LifecyclePaths,
    append_event,
    compute_summary,
    iter_skills,
    load_config,
    load_usage,
    render_report_json,
    render_report_markdown,
    save_config,
    scan,
)


def _write_skill(workspace: Path, name: str, *, frontmatter: dict[str, str] | None = None, body: str = "body\n") -> Path:
    skill_dir = workspace / "skills" / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    fm_lines = ["---"]
    fm = frontmatter if frontmatter is not None else {"name": name, "description": f"{name} description"}
    for key, value in fm.items():
        fm_lines.append(f"{key}: {value}")
    fm_lines.append("---")
    fm_lines.append("")
    fm_lines.append(body)
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text("\n".join(fm_lines), encoding="utf-8")
    return skill_md


def test_scan_registers_all_skills(tmp_path: Path) -> None:
    _write_skill(tmp_path, "alpha")
    _write_skill(tmp_path, "beta")
    _write_skill(tmp_path, "gamma")

    paths = LifecyclePaths.for_workspace(tmp_path)
    result = scan(paths)
    assert result.total == 3
    assert sorted(result.added) == ["alpha", "beta", "gamma"]
    assert result.refreshed == []
    assert result.missing == []

    usage = load_usage(paths)
    assert set(usage["skills"].keys()) == {"alpha", "beta", "gamma"}


def test_scan_is_idempotent(tmp_path: Path) -> None:
    _write_skill(tmp_path, "alpha")
    paths = LifecyclePaths.for_workspace(tmp_path)
    scan(paths)
    second = scan(paths)
    assert second.added == []
    assert second.refreshed == []
    events = paths.events_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(events) == 1


def test_scan_does_not_modify_skill_md(tmp_path: Path) -> None:
    skill_md = _write_skill(tmp_path, "alpha")
    before_mtime = skill_md.stat().st_mtime_ns
    before_content = skill_md.read_text(encoding="utf-8")

    paths = LifecyclePaths.for_workspace(tmp_path)
    scan(paths)
    scan(paths)

    assert skill_md.stat().st_mtime_ns == before_mtime
    assert skill_md.read_text(encoding="utf-8") == before_content


def test_scan_dry_run_does_not_write(tmp_path: Path) -> None:
    _write_skill(tmp_path, "alpha")
    paths = LifecyclePaths.for_workspace(tmp_path)
    result = scan(paths, dry_run=True)

    assert result.added == ["alpha"]
    assert not paths.usage_path.exists()
    assert not paths.events_path.exists()


def test_scan_marks_missing(tmp_path: Path) -> None:
    skill_md = _write_skill(tmp_path, "alpha")
    paths = LifecyclePaths.for_workspace(tmp_path)
    scan(paths)

    skill_md.unlink()
    skill_md.parent.rmdir()
    second = scan(paths)
    assert second.missing == ["alpha"]
    usage = load_usage(paths)
    assert usage["skills"]["alpha"]["state"] == "missing"


def test_scan_detects_archived(tmp_path: Path) -> None:
    archive_dir = tmp_path / "skills" / ".archive" / "old"
    archive_dir.mkdir(parents=True)
    (archive_dir / "SKILL.md").write_text("---\nname: old\n---\n", encoding="utf-8")

    paths = LifecyclePaths.for_workspace(tmp_path)
    result = scan(paths)
    assert "old" in result.added
    assert result.archived_only == ["old"]

    usage = load_usage(paths)
    entry = usage["skills"]["old"]
    assert entry["state"] == "archived"
    assert entry["archive_path"] == "skills/.archive/old/SKILL.md"


def test_scan_skips_dot_directories(tmp_path: Path) -> None:
    dot_dir = tmp_path / "skills" / ".hidden"
    dot_dir.mkdir(parents=True)
    (dot_dir / "SKILL.md").write_text("---\nname: hidden\n---\n", encoding="utf-8")

    paths = LifecyclePaths.for_workspace(tmp_path)
    result = scan(paths)
    assert result.added == []
    assert result.total == 0


def test_iter_skills_classifies_source(tmp_path: Path) -> None:
    _write_skill(tmp_path, "alpha")
    bundled_dir = tmp_path / "skills" / "beta"
    bundled_dir.mkdir(parents=True)
    (bundled_dir / "SKILL.md").write_text("---\nname: beta\n---\n", encoding="utf-8")
    (bundled_dir / ".bundled").write_text("", encoding="utf-8")

    paths = LifecyclePaths.for_workspace(tmp_path)
    discovered = {item.skill_id: item.source for item in iter_skills(paths)}
    assert discovered["alpha"] == "workspace"
    assert discovered["beta"] == "bundled"


def test_load_config_returns_defaults(tmp_path: Path) -> None:
    paths = LifecyclePaths.for_workspace(tmp_path)
    config = load_config(paths)
    assert config["enabled"] is True
    assert config["stale_after_days"] == DEFAULT_CONFIG["stale_after_days"]


def test_save_config_round_trip(tmp_path: Path) -> None:
    paths = LifecyclePaths.for_workspace(tmp_path)
    save_config(paths, {"stale_after_days": 7, "report_top_n": 3})
    loaded = load_config(paths)
    assert loaded["stale_after_days"] == 7
    assert loaded["report_top_n"] == 3
    assert loaded["enabled"] is True  # filled from defaults


def test_compute_summary_counts(tmp_path: Path) -> None:
    _write_skill(tmp_path, "a")
    _write_skill(tmp_path, "b")
    paths = LifecyclePaths.for_workspace(tmp_path)
    scan(paths)
    usage = load_usage(paths)
    summary = compute_summary(usage, DEFAULT_CONFIG)
    assert summary["total"] == 2
    assert summary["counts"]["active"] == 2
    assert len(summary["never_used"]) == 2


def test_render_report_markdown_has_sections(tmp_path: Path) -> None:
    _write_skill(tmp_path, "a")
    paths = LifecyclePaths.for_workspace(tmp_path)
    scan(paths)
    usage = load_usage(paths)
    summary = compute_summary(usage, DEFAULT_CONFIG)
    md = render_report_markdown(usage, summary)
    assert "# Skill Lifecycle Report" in md
    assert "## Never Used" in md
    assert "## Least Recently Used" in md
    assert "## Archive Candidates" in md
    assert "## Umbrella Candidates" in md
    assert "## Negative Claim" in md


def test_render_report_json_parses(tmp_path: Path) -> None:
    _write_skill(tmp_path, "a")
    paths = LifecyclePaths.for_workspace(tmp_path)
    scan(paths)
    usage = load_usage(paths)
    summary = compute_summary(usage, DEFAULT_CONFIG)
    rendered = render_report_json(usage, summary)
    parsed = json.loads(rendered)
    assert "summary" in parsed
    assert parsed["summary"]["total"] == 1


def test_append_event_appends_lines(tmp_path: Path) -> None:
    paths = LifecyclePaths.for_workspace(tmp_path)
    append_event(paths, {"event": "skill_used", "skill_id": "a"})
    append_event(paths, {"event": "skill_success", "skill_id": "a"})
    lines = paths.events_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    parsed = [json.loads(line) for line in lines]
    assert parsed[0]["event"] == "skill_used"
    assert parsed[1]["event"] == "skill_success"
    assert all("ts" in entry for entry in parsed)


def test_atomic_write_uses_tmp_then_rename(tmp_path: Path) -> None:
    _write_skill(tmp_path, "a")
    paths = LifecyclePaths.for_workspace(tmp_path)
    scan(paths)
    # No leftover .tmp file from atomic write
    leftover = list(paths.lifecycle_root.glob("*.tmp"))
    assert leftover == []


def test_archived_skill_reactivation(tmp_path: Path) -> None:
    archive_dir = tmp_path / "skills" / ".archive" / "alpha"
    archive_dir.mkdir(parents=True)
    (archive_dir / "SKILL.md").write_text("---\nname: alpha\n---\n", encoding="utf-8")

    paths = LifecyclePaths.for_workspace(tmp_path)
    scan(paths)
    assert load_usage(paths)["skills"]["alpha"]["state"] == "archived"

    # Move to active location
    active_dir = tmp_path / "skills" / "alpha"
    active_dir.mkdir(parents=True)
    (active_dir / "SKILL.md").write_text("---\nname: alpha\n---\n", encoding="utf-8")
    (archive_dir / "SKILL.md").unlink()
    archive_dir.rmdir()

    scan(paths)
    entry = load_usage(paths)["skills"]["alpha"]
    assert entry["state"] == "active"
    assert entry["reactivated_at"] is not None
    assert entry["archive_path"] is None
