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
    LifecycleError,
    LifecyclePaths,
    append_event,
    apply_archive,
    apply_restore,
    apply_stale,
    compute_summary,
    detect_negative_claims,
    detect_umbrella_candidates,
    iter_skills,
    load_config,
    load_usage,
    plan_archive,
    plan_restore,
    read_events,
    record_runner_event,
    render_report_json,
    render_report_markdown,
    save_config,
    save_usage,
    scan,
    set_pinned,
    stale_candidates,
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


def _set_first_seen(paths: LifecyclePaths, skill_id: str, days_ago: int) -> None:
    from datetime import datetime, timedelta, timezone
    usage = load_usage(paths)
    when = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat(timespec="seconds")
    usage["skills"][skill_id]["first_seen_at"] = when
    save_usage(paths, usage)


def _set_last_used(paths: LifecyclePaths, skill_id: str, days_ago: int) -> None:
    from datetime import datetime, timedelta, timezone
    usage = load_usage(paths)
    when = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat(timespec="seconds")
    usage["skills"][skill_id]["last_used_at"] = when
    save_usage(paths, usage)


def test_set_pinned_round_trip(tmp_path: Path) -> None:
    _write_skill(tmp_path, "alpha")
    paths = LifecyclePaths.for_workspace(tmp_path)
    scan(paths)

    set_pinned(paths, "alpha", pinned=True)
    assert load_usage(paths)["skills"]["alpha"]["pinned"] is True

    set_pinned(paths, "alpha", pinned=False)
    assert load_usage(paths)["skills"]["alpha"]["pinned"] is False

    events = [json.loads(line) for line in paths.events_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    event_types = [e["event"] for e in events]
    assert "skill_pinned" in event_types
    assert "skill_unpinned" in event_types


def test_set_pinned_unknown_skill_raises(tmp_path: Path) -> None:
    paths = LifecyclePaths.for_workspace(tmp_path)
    scan(paths)
    try:
        set_pinned(paths, "nope", pinned=True)
    except LifecycleError as exc:
        assert "unknown skill" in str(exc)
    else:
        raise AssertionError("expected LifecycleError")


def test_stale_candidates_excludes_pinned_and_protected(tmp_path: Path) -> None:
    _write_skill(tmp_path, "alpha")
    _write_skill(tmp_path, "beta")
    bundled = tmp_path / "skills" / "gamma"
    bundled.mkdir(parents=True)
    (bundled / "SKILL.md").write_text("---\nname: gamma\n---\n", encoding="utf-8")
    (bundled / ".bundled").write_text("", encoding="utf-8")

    paths = LifecyclePaths.for_workspace(tmp_path)
    scan(paths)
    _set_first_seen(paths, "alpha", 90)
    _set_first_seen(paths, "beta", 90)
    _set_first_seen(paths, "gamma", 90)
    set_pinned(paths, "beta", pinned=True)

    usage = load_usage(paths)
    candidates = stale_candidates(usage, DEFAULT_CONFIG)
    skill_ids = {c.skill_id for c in candidates}
    assert "alpha" in skill_ids
    assert "beta" not in skill_ids  # pinned
    assert "gamma" not in skill_ids  # protected source


def test_stale_uses_last_used_when_present(tmp_path: Path) -> None:
    _write_skill(tmp_path, "alpha")
    paths = LifecyclePaths.for_workspace(tmp_path)
    scan(paths)
    _set_first_seen(paths, "alpha", 200)
    _set_last_used(paths, "alpha", 5)

    usage = load_usage(paths)
    candidates = stale_candidates(usage, DEFAULT_CONFIG)
    assert candidates == []

    _set_last_used(paths, "alpha", 60)
    usage = load_usage(paths)
    candidates = stale_candidates(usage, DEFAULT_CONFIG)
    assert any(c.skill_id == "alpha" for c in candidates)


def test_apply_stale_transitions_state(tmp_path: Path) -> None:
    _write_skill(tmp_path, "alpha")
    paths = LifecyclePaths.for_workspace(tmp_path)
    scan(paths)
    _set_first_seen(paths, "alpha", 90)

    usage = load_usage(paths)
    candidates = stale_candidates(usage, DEFAULT_CONFIG)
    applied = apply_stale(paths, candidates)
    assert applied == ["alpha"]
    assert load_usage(paths)["skills"]["alpha"]["state"] == "stale"
    events = read_events(paths, skill_id="alpha")
    assert any(e["event"] == "skill_stale" for e in events)


def test_archive_moves_skill_and_updates_metadata(tmp_path: Path) -> None:
    skill_md = _write_skill(tmp_path, "alpha")
    paths = LifecyclePaths.for_workspace(tmp_path)
    scan(paths)

    plan = plan_archive(paths, "alpha", DEFAULT_CONFIG)
    apply_archive(paths, plan)

    assert not (tmp_path / "skills" / "alpha").exists()
    archived_md = tmp_path / "skills" / ".archive" / "alpha" / "SKILL.md"
    assert archived_md.exists()
    entry = load_usage(paths)["skills"]["alpha"]
    assert entry["state"] == "archived"
    assert entry["archive_path"] == "skills/.archive/alpha/SKILL.md"
    assert entry["archived_at"] is not None
    events = read_events(paths, skill_id="alpha")
    assert any(e["event"] == "skill_archived" for e in events)


def test_archive_rejects_pinned(tmp_path: Path) -> None:
    _write_skill(tmp_path, "alpha")
    paths = LifecyclePaths.for_workspace(tmp_path)
    scan(paths)
    set_pinned(paths, "alpha", pinned=True)
    try:
        plan_archive(paths, "alpha", DEFAULT_CONFIG)
    except LifecycleError as exc:
        assert "pinned" in str(exc)
    else:
        raise AssertionError("expected LifecycleError")


def test_archive_rejects_bundled_source(tmp_path: Path) -> None:
    bundled = tmp_path / "skills" / "alpha"
    bundled.mkdir(parents=True)
    (bundled / "SKILL.md").write_text("---\nname: alpha\n---\n", encoding="utf-8")
    (bundled / ".bundled").write_text("", encoding="utf-8")

    paths = LifecyclePaths.for_workspace(tmp_path)
    scan(paths)
    try:
        plan_archive(paths, "alpha", DEFAULT_CONFIG)
    except LifecycleError as exc:
        assert "protected source" in str(exc)
    else:
        raise AssertionError("expected LifecycleError")


def test_archive_rejects_target_collision(tmp_path: Path) -> None:
    _write_skill(tmp_path, "alpha")
    archive_dir = tmp_path / "skills" / ".archive" / "alpha"
    archive_dir.mkdir(parents=True)
    (archive_dir / "SKILL.md").write_text("---\nname: alpha-stub\n---\n", encoding="utf-8")

    paths = LifecyclePaths.for_workspace(tmp_path)
    scan(paths)
    try:
        plan_archive(paths, "alpha", DEFAULT_CONFIG)
    except LifecycleError as exc:
        assert "archive target" in str(exc)
    else:
        raise AssertionError("expected LifecycleError")


def test_archive_then_restore_roundtrip(tmp_path: Path) -> None:
    _write_skill(tmp_path, "alpha", body="payload\n")
    paths = LifecyclePaths.for_workspace(tmp_path)
    scan(paths)

    apply_archive(paths, plan_archive(paths, "alpha", DEFAULT_CONFIG))
    assert load_usage(paths)["skills"]["alpha"]["state"] == "archived"

    apply_restore(paths, plan_restore(paths, "alpha"))
    assert (tmp_path / "skills" / "alpha" / "SKILL.md").exists()
    assert not (tmp_path / "skills" / ".archive" / "alpha").exists()
    entry = load_usage(paths)["skills"]["alpha"]
    assert entry["state"] == "active"
    assert entry["archive_path"] is None
    assert entry["archived_at"] is None
    assert entry["reactivated_at"] is not None


def test_restore_rejects_target_collision(tmp_path: Path) -> None:
    archive_dir = tmp_path / "skills" / ".archive" / "alpha"
    archive_dir.mkdir(parents=True)
    (archive_dir / "SKILL.md").write_text("---\nname: alpha\n---\n", encoding="utf-8")
    paths = LifecyclePaths.for_workspace(tmp_path)
    scan(paths)
    # New live skill at the same name (shouldn't normally happen, but guard)
    live_dir = tmp_path / "skills" / "alpha"
    live_dir.mkdir(parents=True)
    (live_dir / "SKILL.md").write_text("---\nname: alpha\n---\n", encoding="utf-8")
    try:
        plan_restore(paths, "alpha")
    except LifecycleError as exc:
        # Either collision or "not archived" depending on which scan saw first
        assert "exists" in str(exc) or "not archived" in str(exc)
    else:
        raise AssertionError("expected LifecycleError")


def test_read_events_filters_by_skill_and_limit(tmp_path: Path) -> None:
    paths = LifecyclePaths.for_workspace(tmp_path)
    append_event(paths, {"event": "skill_used", "skill_id": "alpha"})
    append_event(paths, {"event": "skill_used", "skill_id": "beta"})
    append_event(paths, {"event": "skill_success", "skill_id": "alpha"})

    alpha_events = read_events(paths, skill_id="alpha")
    assert len(alpha_events) == 2
    assert all(e["skill_id"] == "alpha" for e in alpha_events)

    last_one = read_events(paths, limit=1)
    assert len(last_one) == 1
    assert last_one[0]["event"] == "skill_success"


def test_record_runner_event_increments_use_count(tmp_path: Path) -> None:
    _write_skill(tmp_path, "alpha")
    paths = LifecyclePaths.for_workspace(tmp_path)
    scan(paths)

    ok = record_runner_event(tmp_path, skill_id="alpha", event="skill_used", extra={"task_id": "t1"})
    assert ok is True
    entry = load_usage(paths)["skills"]["alpha"]
    assert entry["use_count"] == 1
    assert entry["last_used_at"] is not None

    record_runner_event(tmp_path, skill_id="alpha", event="skill_used")
    assert load_usage(paths)["skills"]["alpha"]["use_count"] == 2

    events = read_events(paths, skill_id="alpha")
    used_events = [e for e in events if e["event"] == "skill_used"]
    assert len(used_events) == 2
    assert used_events[0].get("task_id") == "t1"


def test_record_runner_event_success_sets_last_successful(tmp_path: Path) -> None:
    _write_skill(tmp_path, "alpha")
    paths = LifecyclePaths.for_workspace(tmp_path)
    scan(paths)

    record_runner_event(tmp_path, skill_id="alpha", event="skill_success")
    entry = load_usage(paths)["skills"]["alpha"]
    assert entry["last_successful_apply_at"] is not None
    # success does not increment use_count (use is recorded by skill_used)
    assert entry["use_count"] == 0


def test_record_runner_event_failure_does_not_set_success_ts(tmp_path: Path) -> None:
    _write_skill(tmp_path, "alpha")
    paths = LifecyclePaths.for_workspace(tmp_path)
    scan(paths)

    record_runner_event(tmp_path, skill_id="alpha", event="skill_failure", extra={"reason": "timeout"})
    entry = load_usage(paths)["skills"]["alpha"]
    assert entry["last_successful_apply_at"] is None
    events = read_events(paths, skill_id="alpha")
    failure = next(e for e in events if e["event"] == "skill_failure")
    assert failure.get("reason") == "timeout"


def test_record_runner_event_promoted_increments_patch_count(tmp_path: Path) -> None:
    _write_skill(tmp_path, "alpha")
    paths = LifecyclePaths.for_workspace(tmp_path)
    scan(paths)

    record_runner_event(tmp_path, skill_id="alpha", event="skill_promoted")
    entry = load_usage(paths)["skills"]["alpha"]
    assert entry["patch_count"] == 1
    assert entry["last_patched_at"] is not None


def test_record_runner_event_skips_when_not_initialized(tmp_path: Path) -> None:
    # No scan, no usage.json
    ok = record_runner_event(tmp_path, skill_id="alpha", event="skill_used")
    assert ok is False


def test_record_runner_event_skips_unknown_skill(tmp_path: Path) -> None:
    _write_skill(tmp_path, "alpha")
    paths = LifecyclePaths.for_workspace(tmp_path)
    scan(paths)

    ok = record_runner_event(tmp_path, skill_id="ghost", event="skill_used")
    assert ok is False


def test_record_runner_event_handles_missing_skill_id(tmp_path: Path) -> None:
    paths = LifecyclePaths.for_workspace(tmp_path)
    scan(paths)
    assert record_runner_event(tmp_path, skill_id=None, event="skill_used") is False
    assert record_runner_event(tmp_path, skill_id="", event="skill_used") is False


def test_detect_negative_claims_finds_keywords(tmp_path: Path) -> None:
    skill_dir = tmp_path / "skills" / "alpha"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: alpha\n"
        "---\n\n"
        "Notes:\n"
        "- this command does not work right now\n"
        "- API is unavailable until further notice\n"
        "- 이 도구는 안 됨\n"
        "- 일반 텍스트는 그대로 둔다\n",
        encoding="utf-8",
    )
    paths = LifecyclePaths.for_workspace(tmp_path)
    candidates = detect_negative_claims(paths)
    keywords = sorted({c.keyword for c in candidates})
    assert "does not work" in keywords
    assert "unavailable" in keywords
    assert "안 됨" in keywords
    assert all(c.skill_id == "alpha" for c in candidates)
    assert all(c.claim_id.startswith("sha256:") for c in candidates)


def test_detect_negative_claims_skips_code_fences(tmp_path: Path) -> None:
    skill_dir = tmp_path / "skills" / "alpha"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: alpha\n---\n\n"
        "```\n"
        "this command failed in a code block — should be skipped\n"
        "```\n"
        "real prose: this is unavailable today\n",
        encoding="utf-8",
    )
    paths = LifecyclePaths.for_workspace(tmp_path)
    candidates = detect_negative_claims(paths)
    keywords = {c.keyword for c in candidates}
    assert "unavailable" in keywords
    # the "failed" inside the fence must be skipped
    assert all(c.line_no >= 7 for c in candidates)


def test_detect_negative_claims_returns_empty_for_clean_skill(tmp_path: Path) -> None:
    skill_dir = tmp_path / "skills" / "alpha"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: alpha\n---\n\nA clean description with no negatives.\n",
        encoding="utf-8",
    )
    paths = LifecyclePaths.for_workspace(tmp_path)
    assert detect_negative_claims(paths) == []


def test_detect_umbrella_candidates_clusters_by_shared_token(tmp_path: Path) -> None:
    for name in ("alpha-search", "beta-search", "gamma-search", "lonely"):
        skill_dir = tmp_path / "skills" / name
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            f"---\nname: {name}\n---\n", encoding="utf-8"
        )
    paths = LifecyclePaths.for_workspace(tmp_path)
    clusters = detect_umbrella_candidates(paths, min_cluster_size=3)
    assert len(clusters) == 1
    assert clusters[0].token == "search"
    assert "alpha-search" in clusters[0].skill_ids
    assert "lonely" not in clusters[0].skill_ids


def test_detect_umbrella_candidates_respects_min_cluster_size(tmp_path: Path) -> None:
    for name in ("alpha-search", "beta-search"):
        skill_dir = tmp_path / "skills" / name
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(f"---\nname: {name}\n---\n", encoding="utf-8")
    paths = LifecyclePaths.for_workspace(tmp_path)
    assert detect_umbrella_candidates(paths, min_cluster_size=3) == []
    clusters = detect_umbrella_candidates(paths, min_cluster_size=2)
    assert len(clusters) == 1
    assert clusters[0].token == "search"


def test_detect_umbrella_skips_archived(tmp_path: Path) -> None:
    for name in ("alpha-search", "beta-search"):
        skill_dir = tmp_path / "skills" / name
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(f"---\nname: {name}\n---\n", encoding="utf-8")
    archived = tmp_path / "skills" / ".archive" / "gamma-search"
    archived.mkdir(parents=True)
    (archived / "SKILL.md").write_text("---\nname: gamma-search\n---\n", encoding="utf-8")

    paths = LifecyclePaths.for_workspace(tmp_path)
    clusters = detect_umbrella_candidates(paths, min_cluster_size=2)
    assert len(clusters) == 1
    assert "gamma-search" not in clusters[0].skill_ids


def test_compute_summary_with_paths_includes_candidates(tmp_path: Path) -> None:
    for name in ("alpha-search", "beta-search", "gamma-search"):
        skill_dir = tmp_path / "skills" / name
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            f"---\nname: {name}\n---\n\nthis tool is unavailable today\n",
            encoding="utf-8",
        )
    paths = LifecyclePaths.for_workspace(tmp_path)
    scan(paths)
    usage = load_usage(paths)
    summary = compute_summary(usage, DEFAULT_CONFIG, paths=paths)
    assert summary["umbrella_candidates"]
    assert summary["umbrella_candidates"][0]["token"] == "search"
    assert summary["negative_claim_candidates"]
    assert summary["negative_claim_candidates"][0]["keyword"] == "unavailable"


def test_render_report_includes_candidates(tmp_path: Path) -> None:
    for name in ("alpha-search", "beta-search", "gamma-search"):
        skill_dir = tmp_path / "skills" / name
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            f"---\nname: {name}\n---\n\nthis tool failed twice\n",
            encoding="utf-8",
        )
    paths = LifecyclePaths.for_workspace(tmp_path)
    scan(paths)
    usage = load_usage(paths)
    summary = compute_summary(usage, DEFAULT_CONFIG, paths=paths)
    md = render_report_markdown(usage, summary)
    assert "shared token: `search`" in md
    assert "alpha-search" in md
    assert "[failed]" in md


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
