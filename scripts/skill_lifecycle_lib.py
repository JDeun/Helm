"""Skill lifecycle metadata, scan, and reporting library.

Sidecar telemetry for skills installed in a Helm/OpenClaw workspace. Records
view/use/patch counts and lifecycle state without modifying SKILL.md content.

Layout under workspace root:
    .openclaw/skill-lifecycle/usage.json     central per-skill index
    .openclaw/skill-lifecycle/events.jsonl   append-only event log
    .openclaw/skill-lifecycle/config.json    policy/config

Skills are discovered under <workspace>/skills/. Archived skills live in
<workspace>/skills/.archive/<skill-name>/.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

from scripts.state_io import append_jsonl_atomic


SCHEMA_VERSION = 1

DEFAULT_CONFIG: dict[str, Any] = {
    "enabled": True,
    "stale_after_days": 45,
    "archive_after_days": 120,
    "never_used_stale_after_days": 30,
    "auto_archive": False,
    "auto_stale": False,
    "hide_archived_from_registry": True,
    "hide_stale_from_prompt": False,
    "protect_sources": ["bundled", "hub"],
    "negative_claim_ttl_days": 30,
    "report_top_n": 20,
}


@dataclass(frozen=True)
class LifecyclePaths:
    workspace: Path
    skills_root: Path
    archive_root: Path
    lifecycle_root: Path
    usage_path: Path
    events_path: Path
    config_path: Path

    @classmethod
    def for_workspace(cls, workspace: Path) -> "LifecyclePaths":
        skills_root = workspace / "skills"
        archive_root = skills_root / ".archive"
        lifecycle_root = workspace / ".openclaw" / "skill-lifecycle"
        return cls(
            workspace=workspace,
            skills_root=skills_root,
            archive_root=archive_root,
            lifecycle_root=lifecycle_root,
            usage_path=lifecycle_root / "usage.json",
            events_path=lifecycle_root / "events.jsonl",
            config_path=lifecycle_root / "config.json",
        )


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    text = json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def load_config(paths: LifecyclePaths) -> dict[str, Any]:
    if not paths.config_path.exists():
        return dict(DEFAULT_CONFIG)
    try:
        loaded = json.loads(paths.config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return dict(DEFAULT_CONFIG)
    merged = dict(DEFAULT_CONFIG)
    if isinstance(loaded, dict):
        merged.update(loaded)
    return merged


def save_config(paths: LifecyclePaths, config: dict[str, Any]) -> None:
    _atomic_write_json(paths.config_path, config)


def load_usage(paths: LifecyclePaths) -> dict[str, Any]:
    if not paths.usage_path.exists():
        return {"version": SCHEMA_VERSION, "skills": {}}
    try:
        loaded = json.loads(paths.usage_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": SCHEMA_VERSION, "skills": {}}
    if not isinstance(loaded, dict):
        return {"version": SCHEMA_VERSION, "skills": {}}
    loaded.setdefault("version", SCHEMA_VERSION)
    skills = loaded.get("skills")
    if not isinstance(skills, dict):
        loaded["skills"] = {}
    return loaded


def save_usage(paths: LifecyclePaths, usage: dict[str, Any]) -> None:
    _atomic_write_json(paths.usage_path, usage)


def append_event(paths: LifecyclePaths, event: dict[str, Any]) -> None:
    payload = dict(event)
    payload.setdefault("ts", utc_now_iso())
    append_jsonl_atomic(paths.events_path, payload)


_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---", re.DOTALL)


def _parse_frontmatter(skill_md: Path) -> dict[str, str]:
    try:
        text = skill_md.read_text(encoding="utf-8")
    except OSError:
        return {}
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}
    block = match.group(1)
    fields: dict[str, str] = {}
    for line in block.splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            fields[key.strip()] = value.strip()
    return fields


def _classify_source(skill_dir: Path, frontmatter: dict[str, str]) -> str:
    declared = frontmatter.get("source")
    if declared:
        return declared
    if (skill_dir / ".bundled").exists():
        return "bundled"
    if (skill_dir / ".hub").exists():
        return "hub"
    return "workspace"


def _new_metadata(
    *,
    skill_id: str,
    relative_path: str,
    source: str,
    state: str = "active",
    archive_path: str | None = None,
    archived_at: str | None = None,
) -> dict[str, Any]:
    now = utc_now_iso()
    return {
        "skill_id": skill_id,
        "path": relative_path,
        "source": source,
        "state": state,
        "pinned": False,
        "created_at": now,
        "first_seen_at": now,
        "last_viewed_at": None,
        "last_used_at": None,
        "last_successful_apply_at": None,
        "use_count": 0,
        "view_count": 0,
        "patch_count": 0,
        "last_patched_at": None,
        "archived_at": archived_at,
        "archive_path": archive_path,
        "reactivated_at": None,
        "last_reviewed_at": None,
        "negative_claims": [],
        "notes": [],
    }


@dataclass(frozen=True)
class DiscoveredSkill:
    skill_id: str
    skill_md: Path
    relative_path: str
    source: str
    is_archived: bool
    archive_relative_path: str | None


def iter_skills(paths: LifecyclePaths) -> Iterator[DiscoveredSkill]:
    if not paths.skills_root.exists():
        return
    for entry in sorted(paths.skills_root.iterdir(), key=lambda p: p.name):
        if not entry.is_dir():
            continue
        if entry.name.startswith("."):
            continue
        skill_md = entry / "SKILL.md"
        if not skill_md.exists():
            continue
        frontmatter = _parse_frontmatter(skill_md)
        skill_id = frontmatter.get("name") or entry.name
        relative = skill_md.relative_to(paths.workspace).as_posix()
        yield DiscoveredSkill(
            skill_id=skill_id,
            skill_md=skill_md,
            relative_path=relative,
            source=_classify_source(entry, frontmatter),
            is_archived=False,
            archive_relative_path=None,
        )

    if paths.archive_root.exists():
        for entry in sorted(paths.archive_root.iterdir(), key=lambda p: p.name):
            if not entry.is_dir():
                continue
            skill_md = entry / "SKILL.md"
            if not skill_md.exists():
                continue
            frontmatter = _parse_frontmatter(skill_md)
            skill_id = frontmatter.get("name") or entry.name
            archive_relative = skill_md.relative_to(paths.workspace).as_posix()
            origin_relative = (paths.skills_root / entry.name / "SKILL.md").relative_to(paths.workspace).as_posix()
            yield DiscoveredSkill(
                skill_id=skill_id,
                skill_md=skill_md,
                relative_path=origin_relative,
                source=_classify_source(entry, frontmatter),
                is_archived=True,
                archive_relative_path=archive_relative,
            )


@dataclass
class ScanResult:
    added: list[str]
    refreshed: list[str]
    missing: list[str]
    archived_only: list[str]
    total: int


def scan(paths: LifecyclePaths, *, dry_run: bool = False) -> ScanResult:
    """Reconcile usage.json with the filesystem.

    - Creates entries for newly discovered skills.
    - Marks entries as `state=missing` if their SKILL.md no longer exists in
      either the active skills directory or the archive.
    - Detects skills present only in the archive directory and ensures their
      state is `archived`.
    Does not modify SKILL.md content.
    """

    usage = load_usage(paths)
    skills_index: dict[str, Any] = usage.setdefault("skills", {})

    discovered: dict[str, DiscoveredSkill] = {}
    for item in iter_skills(paths):
        existing = discovered.get(item.skill_id)
        if existing is None or (existing.is_archived and not item.is_archived):
            discovered[item.skill_id] = item

    added: list[str] = []
    refreshed: list[str] = []
    archived_only: list[str] = []

    for skill_id, item in discovered.items():
        entry = skills_index.get(skill_id)
        if entry is None:
            entry = _new_metadata(
                skill_id=skill_id,
                relative_path=item.relative_path,
                source=item.source,
                state="archived" if item.is_archived else "active",
                archive_path=item.archive_relative_path,
                archived_at=utc_now_iso() if item.is_archived else None,
            )
            skills_index[skill_id] = entry
            added.append(skill_id)
            if item.is_archived:
                archived_only.append(skill_id)
            continue

        changed = False
        if entry.get("path") != item.relative_path:
            entry["path"] = item.relative_path
            changed = True
        if entry.get("source") != item.source and not entry.get("source_locked"):
            entry["source"] = item.source
            changed = True
        if item.is_archived:
            if entry.get("state") != "archived":
                entry["state"] = "archived"
                entry.setdefault("archived_at", utc_now_iso())
                changed = True
            if entry.get("archive_path") != item.archive_relative_path:
                entry["archive_path"] = item.archive_relative_path
                changed = True
        else:
            if entry.get("state") == "archived":
                entry["state"] = "active"
                entry["reactivated_at"] = utc_now_iso()
                entry["archive_path"] = None
                changed = True
        if changed:
            refreshed.append(skill_id)

    missing: list[str] = []
    for skill_id, entry in skills_index.items():
        if skill_id in discovered:
            continue
        if entry.get("state") == "missing":
            continue
        entry["state"] = "missing"
        entry["last_reviewed_at"] = utc_now_iso()
        missing.append(skill_id)

    if not dry_run:
        save_usage(paths, usage)
        for skill_id in added:
            append_event(
                paths,
                {
                    "event": "skill_registered",
                    "skill_id": skill_id,
                    "source": skills_index[skill_id].get("source"),
                },
            )
        for skill_id in missing:
            append_event(
                paths,
                {
                    "event": "skill_missing",
                    "skill_id": skill_id,
                },
            )

    return ScanResult(
        added=added,
        refreshed=refreshed,
        missing=missing,
        archived_only=archived_only,
        total=len(skills_index),
    )


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _days_since(value: str | None, now: datetime) -> float | None:
    parsed = _parse_iso(value)
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return (now - parsed).total_seconds() / 86400.0


def compute_summary(usage: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    skills = usage.get("skills", {})
    counts = {"active": 0, "stale": 0, "archived": 0, "missing": 0, "pinned": 0}
    for entry in skills.values():
        state = entry.get("state", "active")
        counts[state] = counts.get(state, 0) + 1
        if entry.get("pinned"):
            counts["pinned"] += 1

    now = datetime.now(timezone.utc)
    top_n = int(config.get("report_top_n", 20))
    stale_after = float(config.get("stale_after_days", 45))
    never_used_stale_after = float(config.get("never_used_stale_after_days", 30))

    never_used: list[tuple[str, float | None]] = []
    least_recently_used: list[tuple[str, float | None]] = []
    archive_candidates: list[tuple[str, float | None]] = []

    for skill_id, entry in skills.items():
        if entry.get("state") in {"archived", "missing"}:
            continue
        if entry.get("pinned"):
            continue
        last_used = entry.get("last_used_at")
        days = _days_since(last_used, now)
        if last_used is None:
            age_days = _days_since(entry.get("first_seen_at"), now) or 0.0
            if age_days >= never_used_stale_after:
                archive_candidates.append((skill_id, age_days))
            never_used.append((skill_id, age_days))
        else:
            least_recently_used.append((skill_id, days))
            if days is not None and days >= stale_after:
                archive_candidates.append((skill_id, days))

    never_used.sort(key=lambda x: (x[1] or 0.0), reverse=True)
    least_recently_used.sort(key=lambda x: (x[1] or 0.0), reverse=True)
    archive_candidates.sort(key=lambda x: (x[1] or 0.0), reverse=True)

    return {
        "total": len(skills),
        "counts": counts,
        "never_used": never_used[:top_n],
        "least_recently_used": least_recently_used[:top_n],
        "archive_candidates": archive_candidates[:top_n],
    }


def render_report_markdown(usage: dict[str, Any], summary: dict[str, Any]) -> str:
    lines: list[str] = []
    counts = summary["counts"]
    lines.append("# Skill Lifecycle Report")
    lines.append("")
    lines.append(f"- Generated: {utc_now_iso()}")
    lines.append(f"- Total: {summary['total']}")
    lines.append(f"- Active: {counts.get('active', 0)}")
    lines.append(f"- Stale: {counts.get('stale', 0)}")
    lines.append(f"- Archived: {counts.get('archived', 0)}")
    lines.append(f"- Missing: {counts.get('missing', 0)}")
    lines.append(f"- Pinned: {counts.get('pinned', 0)}")
    lines.append("")

    def _block(title: str, rows: Iterable[tuple[str, float | None]], unit: str) -> None:
        lines.append(f"## {title}")
        materialized = list(rows)
        if not materialized:
            lines.append("- (none)")
            lines.append("")
            return
        for skill_id, value in materialized:
            if value is None:
                lines.append(f"- {skill_id}")
            else:
                lines.append(f"- {skill_id} ({value:.0f} {unit})")
        lines.append("")

    _block("Never Used", summary["never_used"], "days since first seen")
    _block("Least Recently Used", summary["least_recently_used"], "days since last use")
    _block("Archive Candidates", summary["archive_candidates"], "days idle")

    lines.append("## Umbrella Candidates")
    lines.append("- (M4 — pending implementation)")
    lines.append("")
    lines.append("## Negative Claim Revalidation Candidates")
    lines.append("- (M4 — pending implementation)")
    lines.append("")

    return "\n".join(lines)


def render_report_json(usage: dict[str, Any], summary: dict[str, Any]) -> str:
    payload = {
        "generated_at": utc_now_iso(),
        "summary": summary,
        "skills": usage.get("skills", {}),
    }
    return json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True)
