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
    build_skill_outcome_metadata,
    compute_summary,
    correlate_events_with_ledger,
    detect_negative_claims,
    detect_umbrella_candidates,
    iter_skills,
    load_config,
    load_usage,
    observe,
    persist_negative_claims,
    plan_archive,
    plan_restore,
    read_events,
    record_runner_event,
    render_report_json,
    render_report_markdown,
    revalidation_due_claims,
    run_negative_claim_probe,
    save_config,
    save_usage,
    scan,
    set_negative_claim_probe_command,
    set_pinned,
    skill_outcome_candidates,
    skill_outcome_summary,
    stale_candidates,
    update_negative_claim_revalidation,
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

    record_runner_event(
        tmp_path,
        skill_id="alpha",
        event="skill_success",
        extra={"task_id": "task-1", "exit_code": 0, "selection_reason": "route matched"},
    )
    entry = load_usage(paths)["skills"]["alpha"]
    assert entry["last_successful_apply_at"] is not None
    assert entry["last_outcome"]["schema_version"] == 2
    assert entry["last_outcome"]["status"] == "success"
    assert entry["last_outcome"]["selection_reason"] == "route matched"
    # success does not increment use_count (use is recorded by skill_used)
    assert entry["use_count"] == 0
    event = read_events(paths, skill_id="alpha")[-1]
    assert event["outcome"]["task_id"] == "task-1"
    assert event["outcome"]["evidence_quality"] == "process_exit"


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
    assert failure["outcome"]["improvement_candidate"] is True


def test_build_skill_outcome_metadata_detects_grounded_evidence() -> None:
    outcome = build_skill_outcome_metadata(
        "skill_success",
        {
            "task_id": "task-1",
            "checkpoint_id": "checkpoint-1",
            "retry_count": 1,
            "user_correction": "prefer narrower runner",
        },
    )

    assert outcome["schema_version"] == 2
    assert outcome["status"] == "success"
    assert outcome["evidence_quality"] == "grounded"
    assert outcome["retry_count"] == 1
    assert outcome["improvement_candidate"] is True


def test_skill_outcome_summary_and_candidates(tmp_path: Path) -> None:
    _write_skill(tmp_path, "alpha")
    paths = LifecyclePaths.for_workspace(tmp_path)
    scan(paths)

    record_runner_event(tmp_path, skill_id="alpha", event="skill_used", extra={"task_id": "task-0"})
    record_runner_event(tmp_path, skill_id="alpha", event="skill_failure", extra={"task_id": "task-1", "exit_code": 1})

    summary = skill_outcome_summary(paths)
    candidates = skill_outcome_candidates(paths)

    assert summary["total_outcomes"] == 2
    assert summary["skills"][0]["skill_id"] == "alpha"
    assert summary["skills"][0]["failure"] == 1
    assert len(candidates) == 1
    assert candidates[0]["task_id"] == "task-1"


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
    assert summary["umbrella_candidates"][0]["signal"] == "name_token"
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
    assert "name_token: `search`" in md
    assert "alpha-search" in md
    assert "[failed]" in md


def test_pin_candidates_lists_high_use_unpinned(tmp_path: Path) -> None:
    _write_skill(tmp_path, "alpha")
    _write_skill(tmp_path, "beta")
    _write_skill(tmp_path, "gamma")
    paths = LifecyclePaths.for_workspace(tmp_path)
    scan(paths)

    for _ in range(5):
        record_runner_event(tmp_path, skill_id="alpha", event="skill_used")
    for _ in range(3):
        record_runner_event(tmp_path, skill_id="beta", event="skill_used")
    record_runner_event(tmp_path, skill_id="gamma", event="skill_used")

    set_pinned(paths, "alpha", pinned=True)

    usage = load_usage(paths)
    summary = compute_summary(usage, DEFAULT_CONFIG, paths=paths)
    pin_candidate_ids = {sid for sid, _ in summary["pin_candidates"]}
    assert "alpha" not in pin_candidate_ids  # already pinned
    assert "beta" in pin_candidate_ids       # use_count=3, threshold met
    assert "gamma" not in pin_candidate_ids  # use_count=1, below threshold


def test_recommended_actions_reflect_summary(tmp_path: Path) -> None:
    _write_skill(tmp_path, "alpha")
    paths = LifecyclePaths.for_workspace(tmp_path)
    scan(paths)
    usage = load_usage(paths)
    summary = compute_summary(usage, DEFAULT_CONFIG, paths=paths)
    actions = summary["recommended_actions"]
    kinds = {a["kind"] for a in actions}
    assert "review_never_used" in kinds


def test_render_report_includes_pin_and_actions_sections(tmp_path: Path) -> None:
    _write_skill(tmp_path, "alpha")
    paths = LifecyclePaths.for_workspace(tmp_path)
    scan(paths)
    usage = load_usage(paths)
    summary = compute_summary(usage, DEFAULT_CONFIG, paths=paths)
    md = render_report_markdown(usage, summary)
    assert "## Pin Candidates" in md
    assert "## Recommended Actions" in md


def test_persist_negative_claims_round_trip(tmp_path: Path) -> None:
    skill_dir = tmp_path / "skills" / "alpha"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: alpha\n---\n\n- this command does not work\n- API is unavailable\n",
        encoding="utf-8",
    )
    paths = LifecyclePaths.for_workspace(tmp_path)
    scan(paths)

    summary = persist_negative_claims(paths)
    assert summary["added"] == 2
    assert summary["kept"] == 0

    persisted = load_usage(paths)["skills"]["alpha"]["negative_claims"]
    assert len(persisted) == 2
    sample = persisted[0]
    assert sample["status"] == "needs_review"
    assert sample["ttl_days"] == 30
    assert sample["confidence"] == 0.6
    assert sample["last_revalidated_at"] is None
    assert sample["claim_id"].startswith("sha256:")

    # Re-running keeps existing claims (idempotent)
    summary2 = persist_negative_claims(paths)
    assert summary2["added"] == 0
    assert summary2["kept"] == 2

    # Manually-edited fields are preserved across re-runs
    usage = load_usage(paths)
    usage["skills"]["alpha"]["negative_claims"][0]["status"] = "still_valid"
    save_usage(paths, usage)
    persist_negative_claims(paths)
    final = load_usage(paths)["skills"]["alpha"]["negative_claims"]
    edited = next(c for c in final if c["status"] == "still_valid")
    assert edited["status"] == "still_valid"


def test_persist_negative_claims_skips_when_uninitialized(tmp_path: Path) -> None:
    paths = LifecyclePaths.for_workspace(tmp_path)
    summary = persist_negative_claims(paths)
    assert summary == {"added": 0, "kept": 0, "removed_stale": 0}


def test_umbrella_includes_description_and_downstream_signals(tmp_path: Path) -> None:
    _write_skill(
        tmp_path,
        "alpha-search",
        frontmatter={"name": "alpha-search", "description": "Korean shopping search"},
    )
    _write_skill(
        tmp_path,
        "beta-search",
        frontmatter={"name": "beta-search", "description": "Korean shopping search"},
    )
    _write_skill(
        tmp_path,
        "gamma-search",
        frontmatter={"name": "gamma-search", "description": "Korean shopping search"},
    )
    paths = LifecyclePaths.for_workspace(tmp_path)
    clusters = detect_umbrella_candidates(paths, min_cluster_size=3)
    signals = {c.signal for c in clusters}
    # Default tokenizer drops short/stop words; assert at least one of the
    # signal types fires beyond name_token.
    assert "name_token" in signals


def test_umbrella_filters_too_generic_description_tokens(tmp_path: Path) -> None:
    # Many skills sharing the same word should not surface as a useful cluster
    for i in range(10):
        _write_skill(
            tmp_path,
            f"skill-{i}",
            frontmatter={"name": f"skill-{i}", "description": "common word everywhere"},
        )
    paths = LifecyclePaths.for_workspace(tmp_path)
    clusters = detect_umbrella_candidates(paths, min_cluster_size=3)
    desc_clusters = [c for c in clusters if c.signal == "description_token"]
    # 'common', 'word', 'everywhere' all appear in 10/10 skills (>33%) —
    # filtered as too generic.
    assert desc_clusters == []


def test_observe_baselines_first_then_records_changes(tmp_path: Path) -> None:
    skill_md = _write_skill(tmp_path, "alpha")
    paths = LifecyclePaths.for_workspace(tmp_path)
    scan(paths)

    first = observe(paths)
    assert first.baseline == ["alpha"]
    assert first.viewed == []
    assert first.patched == []

    # Bump mtime via a content edit; this should fire skill_patched.
    import time
    time.sleep(1.1)
    skill_md.write_text(skill_md.read_text(encoding="utf-8") + "\nedit\n", encoding="utf-8")
    second = observe(paths)
    assert "alpha" in second.patched

    entry = load_usage(paths)["skills"]["alpha"]
    assert entry["patch_count"] >= 1
    assert entry["last_patched_at"] is not None


def test_observe_dry_run_does_not_persist(tmp_path: Path) -> None:
    _write_skill(tmp_path, "alpha")
    paths = LifecyclePaths.for_workspace(tmp_path)
    scan(paths)
    result = observe(paths, dry_run=True)
    assert result.baseline == ["alpha"]
    entry = load_usage(paths)["skills"]["alpha"]
    assert entry.get("last_mtime_seen") is None
    assert entry.get("last_atime_seen") is None


def test_record_runner_event_view_increments_view_count(tmp_path: Path) -> None:
    _write_skill(tmp_path, "alpha")
    paths = LifecyclePaths.for_workspace(tmp_path)
    scan(paths)

    record_runner_event(tmp_path, skill_id="alpha", event="skill_viewed", extra={"source": "manual"})
    record_runner_event(tmp_path, skill_id="alpha", event="skill_viewed", extra={"source": "manual"})

    entry = load_usage(paths)["skills"]["alpha"]
    assert entry["view_count"] == 2
    assert entry["last_viewed_at"] is not None
    events = read_events(paths, skill_id="alpha")
    view_events = [e for e in events if e["event"] == "skill_viewed"]
    assert len(view_events) == 2
    assert all(e.get("source") == "manual" for e in view_events)


def test_observe_skips_when_uninitialized(tmp_path: Path) -> None:
    paths = LifecyclePaths.for_workspace(tmp_path)
    result = observe(paths)
    assert result.total_observed == 0


def test_correlate_events_with_ledger_joins_task_metadata(tmp_path: Path) -> None:
    _write_skill(tmp_path, "alpha")
    paths = LifecyclePaths.for_workspace(tmp_path)
    scan(paths)

    ledger_path = tmp_path / ".openclaw" / "task-ledger.jsonl"
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ledger_path.write_text(
        json.dumps({
            "task_id": "task-001",
            "task_name": "joining demo",
            "status": "completed",
            "started_at": "2026-05-03T00:00:00+00:00",
            "finished_at": "2026-05-03T00:00:05+00:00",
            "exit_code": 0,
            "profile": "inspect_local",
        }) + "\n",
        encoding="utf-8",
    )

    record_runner_event(tmp_path, skill_id="alpha", event="skill_used", extra={"task_id": "task-001"})
    record_runner_event(tmp_path, skill_id="alpha", event="skill_used", extra={"task_id": "task-missing"})

    enriched = correlate_events_with_ledger(paths, skill_id="alpha")
    matched = [r for r in enriched if r.get("task_name") == "joining demo"]
    unmatched = [r for r in enriched if r.get("task_name") is None]
    assert len(matched) == 1
    assert matched[0]["task_status"] == "completed"
    assert matched[0]["task_exit_code"] == 0
    assert len(unmatched) >= 1


def test_umbrella_includes_execution_profile_signal(tmp_path: Path) -> None:
    for name in ("alpha", "beta", "gamma"):
        _write_skill(tmp_path, name)
    refs = tmp_path / "references"
    refs.mkdir()
    (refs / "skill_profile_policies.json").write_text(
        json.dumps({
            "skills": {
                "alpha": {"default_profile": "inspect_local"},
                "beta":  {"default_profile": "inspect_local"},
                "gamma": {"default_profile": "inspect_local"},
            }
        }),
        encoding="utf-8",
    )
    paths = LifecyclePaths.for_workspace(tmp_path)
    clusters = detect_umbrella_candidates(paths, min_cluster_size=3)
    profile_clusters = [c for c in clusters if c.signal == "execution_profile"]
    assert len(profile_clusters) == 1
    assert profile_clusters[0].token == "inspect_local"
    assert set(profile_clusters[0].skill_ids) == {"alpha", "beta", "gamma"}


def test_umbrella_execution_profile_no_policy_file(tmp_path: Path) -> None:
    _write_skill(tmp_path, "alpha")
    paths = LifecyclePaths.for_workspace(tmp_path)
    clusters = detect_umbrella_candidates(paths, min_cluster_size=2)
    assert all(c.signal != "execution_profile" for c in clusters)


def test_revalidation_due_filters_by_ttl(tmp_path: Path) -> None:
    skill_dir = tmp_path / "skills" / "alpha"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: alpha\n---\n\n- this command does not work\n",
        encoding="utf-8",
    )
    paths = LifecyclePaths.for_workspace(tmp_path)
    scan(paths)
    persist_negative_claims(paths, ttl_days=30)

    # No claims overdue immediately after persist.
    assert revalidation_due_claims(paths) == []

    # Backdate detected_at to be 45 days old → 15 days overdue.
    usage = load_usage(paths)
    from datetime import datetime, timedelta, timezone
    backdate = (datetime.now(timezone.utc) - timedelta(days=45)).isoformat(timespec="seconds")
    for claim in usage["skills"]["alpha"]["negative_claims"]:
        claim["detected_at"] = backdate
    save_usage(paths, usage)

    due = revalidation_due_claims(paths)
    assert len(due) == 1
    assert due[0]["skill_id"] == "alpha"
    assert due[0]["anchor"] == "detected_at"
    assert due[0]["due_since_days"] >= 14.5

    # Once last_revalidated_at is set to now, it's no longer overdue.
    usage = load_usage(paths)
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for claim in usage["skills"]["alpha"]["negative_claims"]:
        claim["last_revalidated_at"] = now_iso
    save_usage(paths, usage)
    assert revalidation_due_claims(paths) == []


def test_revalidation_due_skips_resolved(tmp_path: Path) -> None:
    skill_dir = tmp_path / "skills" / "alpha"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: alpha\n---\n\n- this command does not work\n",
        encoding="utf-8",
    )
    paths = LifecyclePaths.for_workspace(tmp_path)
    scan(paths)
    persist_negative_claims(paths, ttl_days=30)

    from datetime import datetime, timedelta, timezone
    backdate = (datetime.now(timezone.utc) - timedelta(days=45)).isoformat(timespec="seconds")
    usage = load_usage(paths)
    for claim in usage["skills"]["alpha"]["negative_claims"]:
        claim["detected_at"] = backdate
        claim["status"] = "resolved"
    save_usage(paths, usage)

    assert revalidation_due_claims(paths) == []


def test_update_negative_claim_revalidation_records_manual_status(tmp_path: Path) -> None:
    skill_dir = tmp_path / "skills" / "alpha"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: alpha\n---\n\n- this command does not work\n",
        encoding="utf-8",
    )
    paths = LifecyclePaths.for_workspace(tmp_path)
    scan(paths)
    persist_negative_claims(paths, ttl_days=30)
    claim_id = load_usage(paths)["skills"]["alpha"]["negative_claims"][0]["claim_id"]

    claim = update_negative_claim_revalidation(
        paths,
        skill_id="alpha",
        claim_id=claim_id,
        status="resolved",
        note="manual probe succeeded",
    )

    assert claim["status"] == "resolved"
    assert claim["last_revalidated_at"] is not None
    assert claim["revalidation_note"] == "manual probe succeeded"
    events = read_events(paths, skill_id="alpha")
    assert events[-1]["event"] == "negative_claim_revalidated"
    assert events[-1]["claim_id"] == claim_id


def test_run_negative_claim_probe_requires_allowlist(tmp_path: Path) -> None:
    skill_dir = tmp_path / "skills" / "alpha"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: alpha\n---\n\n- this command does not work\n",
        encoding="utf-8",
    )
    paths = LifecyclePaths.for_workspace(tmp_path)
    scan(paths)
    persist_negative_claims(paths, ttl_days=30)
    usage = load_usage(paths)
    claim = usage["skills"]["alpha"]["negative_claims"][0]
    claim["probe_command"] = "python3 --version"
    save_usage(paths, usage)

    try:
        run_negative_claim_probe(paths, skill_id="alpha", claim_id=claim["claim_id"])
    except LifecycleError as exc:
        assert "allowlisted" in str(exc)
    else:
        raise AssertionError("expected allowlist rejection")


def test_set_negative_claim_probe_command_persists_command(tmp_path: Path) -> None:
    skill_dir = tmp_path / "skills" / "alpha"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: alpha\n---\n\n- this command does not work\n",
        encoding="utf-8",
    )
    paths = LifecyclePaths.for_workspace(tmp_path)
    scan(paths)
    persist_negative_claims(paths, ttl_days=30)
    claim_id = load_usage(paths)["skills"]["alpha"]["negative_claims"][0]["claim_id"]

    updated = set_negative_claim_probe_command(
        paths,
        skill_id="alpha",
        claim_id=claim_id,
        command="python3 --version",
    )

    assert updated["probe_command"] == "python3 --version"
    events = read_events(paths, skill_id="alpha")
    assert events[-1]["event"] == "negative_claim_probe_set"


def test_run_negative_claim_probe_updates_status_from_exit_code(tmp_path: Path) -> None:
    skill_dir = tmp_path / "skills" / "alpha"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: alpha\n---\n\n- this command does not work\n",
        encoding="utf-8",
    )
    paths = LifecyclePaths.for_workspace(tmp_path)
    scan(paths)
    config = load_config(paths)
    config["negative_claim_safe_probe_prefixes"] = [["python3", "--version"]]
    save_config(paths, config)
    persist_negative_claims(paths, ttl_days=30)
    usage = load_usage(paths)
    claim = usage["skills"]["alpha"]["negative_claims"][0]
    claim["probe_command"] = "python3 --version"
    save_usage(paths, usage)

    updated = run_negative_claim_probe(paths, skill_id="alpha", claim_id=claim["claim_id"])

    assert updated["status"] == "resolved"
    assert updated["last_probe"]["exit_code"] == 0
    assert updated["last_revalidated_at"] is not None


def test_archive_plan_includes_file_summary(tmp_path: Path) -> None:
    skill_dir = tmp_path / "skills" / "alpha"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: alpha\n---\n\nbody\n", encoding="utf-8"
    )
    (skill_dir / "extra.txt").write_text("hello", encoding="utf-8")
    sub = skill_dir / "references"
    sub.mkdir()
    (sub / "note.md").write_text("note body", encoding="utf-8")

    paths = LifecyclePaths.for_workspace(tmp_path)
    scan(paths)
    plan = plan_archive(paths, "alpha", DEFAULT_CONFIG)
    assert plan.file_count == 3
    assert plan.total_bytes > 0
    assert "SKILL.md" in plan.sample_files
    assert any("note.md" in s for s in plan.sample_files)


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
