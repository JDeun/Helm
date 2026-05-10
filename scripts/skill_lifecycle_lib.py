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
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

from scripts.state_io import append_jsonl_atomic


class LifecycleError(Exception):
    """Raised when a lifecycle operation is rejected for a safety reason."""


SCHEMA_VERSION = 1
OUTCOME_SCHEMA_VERSION = 2

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
    "negative_claim_safe_probe_prefixes": [],
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


def compute_summary(
    usage: dict[str, Any],
    config: dict[str, Any],
    *,
    paths: LifecyclePaths | None = None,
) -> dict[str, Any]:
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

    pin_candidates: list[tuple[str, int]] = []
    for skill_id, entry in skills.items():
        if entry.get("state") in {"archived", "missing"}:
            continue
        if entry.get("pinned"):
            continue
        use_count = int(entry.get("use_count", 0) or 0)
        if use_count >= 3:
            pin_candidates.append((skill_id, use_count))
    pin_candidates.sort(key=lambda x: x[1], reverse=True)

    summary: dict[str, Any] = {
        "total": len(skills),
        "counts": counts,
        "never_used": never_used[:top_n],
        "least_recently_used": least_recently_used[:top_n],
        "archive_candidates": archive_candidates[:top_n],
        "pin_candidates": pin_candidates[:top_n],
        "umbrella_candidates": [],
        "negative_claim_candidates": [],
    }

    if paths is not None:
        summary["umbrella_candidates"] = [
            {
                "signal": cluster.signal,
                "token": cluster.token,
                "skill_ids": list(cluster.skill_ids),
            }
            for cluster in detect_umbrella_candidates(paths)
        ]
        summary["negative_claim_candidates"] = [
            {
                "claim_id": c.claim_id,
                "skill_id": c.skill_id,
                "skill_md": c.skill_md,
                "line_no": c.line_no,
                "keyword": c.keyword,
                "text": c.text,
            }
            for c in detect_negative_claims(paths)
        ]

    summary["recommended_actions"] = _build_recommended_actions(summary)
    return summary


def _build_recommended_actions(summary: dict[str, Any]) -> list[dict[str, str]]:
    actions: list[dict[str, str]] = []
    if summary["never_used"]:
        n = len(summary["never_used"])
        actions.append({
            "kind": "review_never_used",
            "detail": f"{n} skills never used since registration; consider archive or pin",
            "command": "helm skill-lifecycle status",
        })
    if summary["archive_candidates"]:
        n = len(summary["archive_candidates"])
        actions.append({
            "kind": "review_archive_candidates",
            "detail": f"{n} skills idle past policy threshold; review for archival",
            "command": "helm skill-lifecycle stale --dry-run",
        })
    if summary["pin_candidates"]:
        n = len(summary["pin_candidates"])
        actions.append({
            "kind": "consider_pinning",
            "detail": f"{n} actively used skills are not pinned; pin to protect from auto-stale",
            "command": "helm skill-lifecycle pin <skill>",
        })
    if summary["umbrella_candidates"]:
        n = len(summary["umbrella_candidates"])
        actions.append({
            "kind": "review_umbrella",
            "detail": f"{n} shared-token clusters surfaced; consider an umbrella router",
            "command": "helm skill-lifecycle umbrella",
        })
    if summary["negative_claim_candidates"]:
        n = len(summary["negative_claim_candidates"])
        actions.append({
            "kind": "review_negative_claims",
            "detail": f"{n} negative-claim candidate lines; triage before they mislead future runs",
            "command": "helm skill-lifecycle negative-claims",
        })
    return actions


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

    pin_rows = summary.get("pin_candidates") or []
    lines.append("## Pin Candidates")
    if not pin_rows:
        lines.append("- (none)")
    else:
        for skill_id, use_count in pin_rows:
            lines.append(f"- {skill_id} ({use_count} uses)")
    lines.append("")

    lines.append("## Umbrella Candidates")
    umbrella = summary.get("umbrella_candidates") or []
    if not umbrella:
        lines.append("- (none)")
    else:
        for cluster in umbrella:
            signal = cluster.get("signal", "name_token")
            token = cluster["token"]
            skills = cluster["skill_ids"]
            lines.append(f"### {signal}: `{token}` ({len(skills)} skills)")
            for skill_id in skills:
                lines.append(f"- {skill_id}")
            lines.append("")
    lines.append("")

    lines.append("## Negative Claim Revalidation Candidates")
    claims = summary.get("negative_claim_candidates") or []
    if not claims:
        lines.append("- (none)")
        lines.append("")
    else:
        for claim in claims:
            lines.append(f"- `{claim['skill_id']}` {claim['skill_md']}:{claim['line_no']} [{claim['keyword']}]")
            lines.append(f"  > {claim['text']}")
        lines.append("")

    actions = summary.get("recommended_actions") or []
    lines.append("## Recommended Actions")
    if not actions:
        lines.append("- (none)")
    else:
        for action in actions:
            lines.append(f"- **{action['kind']}** — {action['detail']}")
            if action.get("command"):
                lines.append(f"  - run: `{action['command']}`")
    lines.append("")

    return "\n".join(lines)


def render_report_json(usage: dict[str, Any], summary: dict[str, Any]) -> str:
    payload = {
        "generated_at": utc_now_iso(),
        "summary": summary,
        "skills": usage.get("skills", {}),
    }
    return json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True)


def _require_skill(usage: dict[str, Any], skill_id: str) -> dict[str, Any]:
    skills = usage.get("skills", {})
    entry = skills.get(skill_id)
    if entry is None:
        raise LifecycleError(f"unknown skill: {skill_id}")
    return entry


def set_pinned(paths: LifecyclePaths, skill_id: str, *, pinned: bool) -> dict[str, Any]:
    usage = load_usage(paths)
    entry = _require_skill(usage, skill_id)
    if bool(entry.get("pinned")) == pinned:
        return entry
    entry["pinned"] = pinned
    save_usage(paths, usage)
    append_event(
        paths,
        {
            "event": "skill_pinned" if pinned else "skill_unpinned",
            "skill_id": skill_id,
        },
    )
    return entry


@dataclass
class TransitionPreview:
    skill_id: str
    from_state: str
    to_state: str
    reason: str


def stale_candidates(usage: dict[str, Any], config: dict[str, Any]) -> list[TransitionPreview]:
    now = datetime.now(timezone.utc)
    stale_after = float(config.get("stale_after_days", 45))
    never_used_after = float(config.get("never_used_stale_after_days", 30))
    protect = set(config.get("protect_sources", []))

    candidates: list[TransitionPreview] = []
    for skill_id, entry in usage.get("skills", {}).items():
        if entry.get("state") != "active":
            continue
        if entry.get("pinned"):
            continue
        if entry.get("source") in protect:
            continue
        last_used = entry.get("last_used_at")
        if last_used is None:
            age = _days_since(entry.get("first_seen_at"), now) or 0.0
            if age >= never_used_after:
                candidates.append(
                    TransitionPreview(
                        skill_id=skill_id,
                        from_state="active",
                        to_state="stale",
                        reason=f"never used and {age:.0f}d since first seen",
                    )
                )
        else:
            idle = _days_since(last_used, now) or 0.0
            if idle >= stale_after:
                candidates.append(
                    TransitionPreview(
                        skill_id=skill_id,
                        from_state="active",
                        to_state="stale",
                        reason=f"{idle:.0f}d idle since last use",
                    )
                )
    return candidates


def apply_stale(paths: LifecyclePaths, candidates: list[TransitionPreview]) -> list[str]:
    if not candidates:
        return []
    usage = load_usage(paths)
    applied: list[str] = []
    now = utc_now_iso()
    for preview in candidates:
        entry = usage["skills"].get(preview.skill_id)
        if entry is None or entry.get("state") != preview.from_state:
            continue
        if entry.get("pinned"):
            continue
        entry["state"] = preview.to_state
        entry["last_reviewed_at"] = now
        applied.append(preview.skill_id)
    if applied:
        save_usage(paths, usage)
        for skill_id in applied:
            append_event(
                paths,
                {
                    "event": "skill_stale",
                    "skill_id": skill_id,
                    "reason": "policy",
                },
            )
    return applied


@dataclass
class ArchivePlan:
    skill_id: str
    source_dir: Path
    target_dir: Path
    relative_archive_path: str
    file_count: int = 0
    total_bytes: int = 0
    sample_files: tuple[str, ...] = ()


def _summarize_directory(directory: Path, *, sample_limit: int = 10) -> tuple[int, int, tuple[str, ...]]:
    file_count = 0
    total_bytes = 0
    samples: list[str] = []
    for path in sorted(directory.rglob("*")):
        if not path.is_file():
            continue
        file_count += 1
        try:
            total_bytes += path.stat().st_size
        except OSError:
            pass
        if len(samples) < sample_limit:
            try:
                samples.append(str(path.relative_to(directory)))
            except ValueError:
                samples.append(path.name)
    return file_count, total_bytes, tuple(samples)


def plan_archive(paths: LifecyclePaths, skill_id: str, config: dict[str, Any]) -> ArchivePlan:
    usage = load_usage(paths)
    entry = _require_skill(usage, skill_id)

    if entry.get("state") == "archived":
        raise LifecycleError(f"already archived: {skill_id}")
    if entry.get("state") == "missing":
        raise LifecycleError(f"cannot archive a missing skill: {skill_id}")
    if entry.get("pinned"):
        raise LifecycleError(f"pinned skill cannot be archived: {skill_id}")
    protect = set(config.get("protect_sources", []))
    if entry.get("source") in protect:
        raise LifecycleError(f"protected source ({entry.get('source')}); refusing to archive: {skill_id}")

    source_md = paths.workspace / entry["path"]
    source_dir = source_md.parent
    if not source_dir.exists():
        raise LifecycleError(f"skill directory missing on disk: {source_dir}")

    target_dir = paths.archive_root / source_dir.name
    if target_dir.exists():
        raise LifecycleError(f"archive target already exists: {target_dir}")

    relative_archive = (target_dir / "SKILL.md").relative_to(paths.workspace).as_posix()
    file_count, total_bytes, sample_files = _summarize_directory(source_dir)
    return ArchivePlan(
        skill_id=skill_id,
        source_dir=source_dir,
        target_dir=target_dir,
        relative_archive_path=relative_archive,
        file_count=file_count,
        total_bytes=total_bytes,
        sample_files=sample_files,
    )


def apply_archive(paths: LifecyclePaths, plan: ArchivePlan) -> dict[str, Any]:
    plan.target_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(plan.source_dir), str(plan.target_dir))

    usage = load_usage(paths)
    entry = _require_skill(usage, plan.skill_id)
    entry["state"] = "archived"
    entry["archived_at"] = utc_now_iso()
    entry["archive_path"] = plan.relative_archive_path
    save_usage(paths, usage)
    append_event(
        paths,
        {
            "event": "skill_archived",
            "skill_id": plan.skill_id,
            "from_path": entry["path"],
            "archive_path": plan.relative_archive_path,
        },
    )
    return entry


@dataclass
class RestorePlan:
    skill_id: str
    source_dir: Path
    target_dir: Path
    relative_path: str


def plan_restore(paths: LifecyclePaths, skill_id: str) -> RestorePlan:
    usage = load_usage(paths)
    entry = _require_skill(usage, skill_id)
    if entry.get("state") != "archived":
        raise LifecycleError(f"not archived: {skill_id}")

    archive_rel = entry.get("archive_path")
    if not archive_rel:
        raise LifecycleError(f"missing archive_path metadata: {skill_id}")
    source_md = paths.workspace / archive_rel
    source_dir = source_md.parent
    if not source_dir.exists():
        raise LifecycleError(f"archived directory missing on disk: {source_dir}")

    original_md = paths.workspace / entry.get("path", "")
    target_dir = original_md.parent
    if target_dir.exists():
        raise LifecycleError(f"restore target already exists: {target_dir}")

    return RestorePlan(
        skill_id=skill_id,
        source_dir=source_dir,
        target_dir=target_dir,
        relative_path=entry["path"],
    )


def apply_restore(paths: LifecyclePaths, plan: RestorePlan) -> dict[str, Any]:
    plan.target_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(plan.source_dir), str(plan.target_dir))

    usage = load_usage(paths)
    entry = _require_skill(usage, plan.skill_id)
    entry["state"] = "active"
    entry["archive_path"] = None
    entry["archived_at"] = None
    entry["reactivated_at"] = utc_now_iso()
    save_usage(paths, usage)
    append_event(
        paths,
        {
            "event": "skill_restored",
            "skill_id": plan.skill_id,
            "to_path": plan.relative_path,
        },
    )
    return entry


_COUNTER_BY_EVENT = {
    "skill_used": "use_count",
    "skill_viewed": "view_count",
    "skill_promoted": "patch_count",
}

_TIMESTAMP_BY_EVENT = {
    "skill_used": "last_used_at",
    "skill_viewed": "last_viewed_at",
    "skill_success": "last_successful_apply_at",
    "skill_promoted": "last_patched_at",
}


def build_skill_outcome_metadata(event: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    extra = extra or {}
    exit_code = extra.get("exit_code")
    task_id = extra.get("task_id")
    status = "unknown"
    if event == "skill_success":
        status = "success"
    elif event == "skill_failure":
        status = "failure"
    elif event == "skill_used":
        status = "started"
    elif event == "skill_promoted":
        status = "promoted"
    evidence_quality = str(extra.get("evidence_quality") or "unknown")
    if evidence_quality == "unknown":
        if extra.get("completion_evidence") or extra.get("checkpoint_id") or extra.get("write_validation"):
            evidence_quality = "grounded"
        elif exit_code is not None:
            evidence_quality = "process_exit"
    retry_count = int(extra.get("retry_count") or 0)
    improvement_candidate = bool(
        extra.get("improvement_candidate")
        or event == "skill_failure"
        or retry_count > 0
        or extra.get("user_correction")
    )
    return {
        "schema_version": OUTCOME_SCHEMA_VERSION,
        "task_id": task_id,
        "status": status,
        "exit_code": exit_code,
        "selection_reason": extra.get("selection_reason"),
        "evidence_quality": evidence_quality,
        "user_correction": extra.get("user_correction"),
        "retry_count": retry_count,
        "improvement_candidate": improvement_candidate,
    }


def skill_outcome_rows(paths: LifecyclePaths, *, skill_id: str | None = None, limit: int | None = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for event in read_events(paths, skill_id=skill_id, limit=None):
        outcome = event.get("outcome")
        if not isinstance(outcome, dict):
            continue
        row = {
            "ts": event.get("ts"),
            "event": event.get("event"),
            "skill_id": event.get("skill_id"),
            **outcome,
        }
        rows.append(row)
    return rows[-limit:] if limit else rows


def skill_outcome_summary(paths: LifecyclePaths) -> dict[str, Any]:
    rows = skill_outcome_rows(paths)
    by_skill: dict[str, dict[str, Any]] = {}
    for row in rows:
        skill_id = str(row.get("skill_id") or "")
        if not skill_id:
            continue
        bucket = by_skill.setdefault(
            skill_id,
            {
                "skill_id": skill_id,
                "total": 0,
                "success": 0,
                "failure": 0,
                "improvement_candidates": 0,
                "evidence_quality": {},
            },
        )
        bucket["total"] += 1
        status = str(row.get("status") or "unknown")
        if status in {"success", "failure"}:
            bucket[status] += 1
        if row.get("improvement_candidate"):
            bucket["improvement_candidates"] += 1
        evidence_quality = str(row.get("evidence_quality") or "unknown")
        bucket["evidence_quality"][evidence_quality] = bucket["evidence_quality"].get(evidence_quality, 0) + 1
    return {"total_outcomes": len(rows), "skills": sorted(by_skill.values(), key=lambda item: item["skill_id"])}


def skill_outcome_candidates(paths: LifecyclePaths, *, limit: int | None = None) -> list[dict[str, Any]]:
    candidates = [
        row for row in skill_outcome_rows(paths)
        if row.get("improvement_candidate") or row.get("status") == "failure"
    ]
    candidates.sort(key=lambda row: str(row.get("ts") or ""))
    return candidates[-limit:] if limit else candidates


def record_runner_event(
    workspace: Path,
    *,
    skill_id: str | None,
    event: str,
    extra: dict[str, Any] | None = None,
) -> bool:
    """Update lifecycle counters/timestamps and append the event.

    Fail-soft: returns False on any error so callers in execution paths do not
    break when lifecycle metadata is unavailable. Skips silently when
    skill_id is empty or when usage.json has not yet been initialized.
    """

    if not skill_id:
        return False
    paths = LifecyclePaths.for_workspace(workspace)
    if not paths.usage_path.exists():
        # Lifecycle layer not initialized for this workspace; do not bootstrap
        # implicitly from a runner — that should be an explicit `scan`.
        return False

    try:
        usage = load_usage(paths)
        entry = usage.get("skills", {}).get(skill_id)
        if entry is None:
            return False
        now = utc_now_iso()
        counter_key = _COUNTER_BY_EVENT.get(event)
        if counter_key:
            entry[counter_key] = int(entry.get(counter_key, 0) or 0) + 1
        ts_key = _TIMESTAMP_BY_EVENT.get(event)
        if ts_key:
            entry[ts_key] = now
        outcome = build_skill_outcome_metadata(event, extra)
        entry["last_outcome"] = outcome
        save_usage(paths, usage)
        payload: dict[str, Any] = {"event": event, "skill_id": skill_id, "outcome": outcome}
        if extra:
            payload.update(extra)
        append_event(paths, payload)
        return True
    except Exception:
        return False


NEGATIVE_CLAIM_KEYWORDS: tuple[str, ...] = (
    "does not work",
    "doesn't work",
    "unavailable",
    "not installed",
    "not supported",
    "failed",
    "안 됨",
    "없음",
    "불가",
    "실패",
    "지원하지 않음",
)


@dataclass(frozen=True)
class ClaimCandidate:
    skill_id: str
    skill_md: str
    line_no: int
    text: str
    keyword: str
    claim_id: str


def _hash_claim(skill_id: str, line_no: int, text: str) -> str:
    import hashlib

    digest = hashlib.sha256(f"{skill_id}|{line_no}|{text}".encode("utf-8")).hexdigest()
    return f"sha256:{digest[:16]}"


DEFAULT_CLAIM_TTL_DAYS = 30
DEFAULT_CLAIM_CONFIDENCE = 0.6
DEFAULT_CLAIM_STATUS = "needs_review"


def revalidation_due_claims(paths: LifecyclePaths) -> list[dict[str, Any]]:
    """Return persisted negative claims past their TTL window.

    A claim is "due for revalidation" when:
      - it has a non-null detected_at and ttl_days,
      - and (last_revalidated_at or detected_at) + ttl_days is in the past,
      - and its status is not already "resolved".

    The returned dicts inherit the persisted claim shape and add
    `skill_id`, `due_since_days`, and `anchor` ("last_revalidated_at" or
    "detected_at").
    """
    if not paths.usage_path.exists():
        return []
    usage = load_usage(paths)
    now = datetime.now(timezone.utc)
    due: list[dict[str, Any]] = []
    for skill_id, entry in usage.get("skills", {}).items():
        for claim in entry.get("negative_claims") or []:
            if not isinstance(claim, dict):
                continue
            if claim.get("status") == "resolved":
                continue
            ttl = claim.get("ttl_days")
            if not isinstance(ttl, (int, float)) or ttl <= 0:
                continue
            anchor_key = "last_revalidated_at" if claim.get("last_revalidated_at") else "detected_at"
            anchor_value = claim.get(anchor_key)
            anchor_dt = _parse_iso(anchor_value)
            if anchor_dt is None:
                continue
            if anchor_dt.tzinfo is None:
                anchor_dt = anchor_dt.replace(tzinfo=timezone.utc)
            due_at = anchor_dt + timedelta(days=float(ttl))
            if due_at <= now:
                overdue_days = (now - due_at).total_seconds() / 86400.0
                merged = dict(claim)
                merged["skill_id"] = skill_id
                merged["anchor"] = anchor_key
                merged["due_since_days"] = round(overdue_days, 1)
                probe_command = claim.get("probe_command")
                if isinstance(probe_command, str) and probe_command.strip():
                    merged["probe_allowed"] = is_negative_claim_probe_allowed(
                        load_config(paths),
                        probe_command,
                    )
                due.append(merged)
    due.sort(key=lambda c: c["due_since_days"], reverse=True)
    return due


def _argv_matches_prefix(argv: list[str], prefix: list[str]) -> bool:
    return bool(prefix) and len(argv) >= len(prefix) and argv[: len(prefix)] == prefix


def is_negative_claim_probe_allowed(config: dict[str, Any], command: str) -> bool:
    """Return whether a negative-claim probe command is explicitly allowed.

    Probe execution is opt-in. The command must be parseable without shell
    semantics and match one configured argv prefix exactly.
    """
    try:
        argv = shlex.split(command)
    except ValueError:
        return False
    if not argv:
        return False
    prefixes = config.get("negative_claim_safe_probe_prefixes") or []
    for raw in prefixes:
        if isinstance(raw, str):
            try:
                prefix = shlex.split(raw)
            except ValueError:
                continue
        elif isinstance(raw, list) and all(isinstance(part, str) for part in raw):
            prefix = raw
        else:
            continue
        if _argv_matches_prefix(argv, prefix):
            return True
    return False


def _find_claim(usage: dict[str, Any], skill_id: str, claim_id: str) -> dict[str, Any]:
    entry = usage.get("skills", {}).get(skill_id)
    if entry is None:
        raise LifecycleError(f"unknown skill: {skill_id}")
    for claim in entry.get("negative_claims") or []:
        if isinstance(claim, dict) and claim.get("claim_id") == claim_id:
            return claim
    raise LifecycleError(f"unknown claim for {skill_id}: {claim_id}")


def update_negative_claim_revalidation(
    paths: LifecyclePaths,
    *,
    skill_id: str,
    claim_id: str,
    status: str,
    note: str | None = None,
    probe_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Record a manual or probed negative-claim revalidation result."""
    if status not in {"needs_review", "still_valid", "resolved"}:
        raise LifecycleError(f"invalid claim status: {status}")
    if not paths.usage_path.exists():
        raise LifecycleError("lifecycle layer not initialized; run scan first")
    usage = load_usage(paths)
    claim = _find_claim(usage, skill_id, claim_id)
    now = utc_now_iso()
    claim["status"] = status
    claim["last_revalidated_at"] = now
    if note is not None:
        claim["revalidation_note"] = note
    if probe_result is not None:
        claim["last_probe"] = probe_result
    save_usage(paths, usage)
    event = {
        "event": "negative_claim_revalidated",
        "skill_id": skill_id,
        "claim_id": claim_id,
        "status": status,
    }
    if probe_result is not None:
        event["probe_exit_code"] = probe_result.get("exit_code")
    append_event(paths, event)
    return dict(claim)


def set_negative_claim_probe_command(
    paths: LifecyclePaths,
    *,
    skill_id: str,
    claim_id: str,
    command: str,
) -> dict[str, Any]:
    """Attach or replace a probe command on a persisted negative claim."""
    if not command.strip():
        raise LifecycleError("probe command cannot be empty")
    try:
        shlex.split(command)
    except ValueError as exc:
        raise LifecycleError(f"invalid probe command: {exc}") from exc
    if not paths.usage_path.exists():
        raise LifecycleError("lifecycle layer not initialized; run scan first")
    usage = load_usage(paths)
    claim = _find_claim(usage, skill_id, claim_id)
    claim["probe_command"] = command
    save_usage(paths, usage)
    append_event(paths, {
        "event": "negative_claim_probe_set",
        "skill_id": skill_id,
        "claim_id": claim_id,
    })
    return dict(claim)


def run_negative_claim_probe(
    paths: LifecyclePaths,
    *,
    skill_id: str,
    claim_id: str,
    timeout_seconds: int = 30,
) -> dict[str, Any]:
    """Run an allowlisted probe command and update claim status.

    The persisted claim must contain `probe_command`. Exit code 0 resolves
    the negative claim; non-zero marks it `still_valid`.
    """
    if not paths.usage_path.exists():
        raise LifecycleError("lifecycle layer not initialized; run scan first")
    usage = load_usage(paths)
    claim = _find_claim(usage, skill_id, claim_id)
    command = claim.get("probe_command")
    if not isinstance(command, str) or not command.strip():
        raise LifecycleError(f"claim has no probe_command: {claim_id}")
    config = load_config(paths)
    if not is_negative_claim_probe_allowed(config, command):
        raise LifecycleError(f"probe command is not allowlisted: {command}")
    try:
        argv = shlex.split(command)
    except ValueError as exc:
        raise LifecycleError(f"invalid probe command: {exc}") from exc
    started_at = utc_now_iso()
    try:
        result = subprocess.run(
            argv,
            cwd=str(paths.workspace),
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
        )
        probe_result = {
            "command": command,
            "started_at": started_at,
            "finished_at": utc_now_iso(),
            "exit_code": result.returncode,
            "stdout": result.stdout[-2000:],
            "stderr": result.stderr[-2000:],
        }
    except subprocess.TimeoutExpired as exc:
        probe_result = {
            "command": command,
            "started_at": started_at,
            "finished_at": utc_now_iso(),
            "exit_code": None,
            "timeout_seconds": timeout_seconds,
            "stdout": (exc.stdout or "")[-2000:] if isinstance(exc.stdout, str) else "",
            "stderr": (exc.stderr or "")[-2000:] if isinstance(exc.stderr, str) else "",
        }
    status = "resolved" if probe_result.get("exit_code") == 0 else "still_valid"
    return update_negative_claim_revalidation(
        paths,
        skill_id=skill_id,
        claim_id=claim_id,
        status=status,
        note="probe command executed",
        probe_result=probe_result,
    )


def persist_negative_claims(
    paths: LifecyclePaths,
    *,
    ttl_days: int = DEFAULT_CLAIM_TTL_DAYS,
    confidence: float = DEFAULT_CLAIM_CONFIDENCE,
) -> dict[str, int]:
    """Persist detected negative claims into per-skill metadata.

    Stable claim_ids prevent duplicate writes across re-runs. Returns a
    summary {"added": N, "kept": M, "removed_stale": K} describing what
    changed in usage.json. Existing claims that no longer match (e.g., line
    edited away) are left in place with their existing status — only new
    claims are inserted, so manual `last_revalidated_at` / `status` edits
    are preserved.
    """

    if not paths.usage_path.exists():
        return {"added": 0, "kept": 0, "removed_stale": 0}

    usage = load_usage(paths)
    candidates = detect_negative_claims(paths)
    grouped: dict[str, list[ClaimCandidate]] = {}
    for c in candidates:
        grouped.setdefault(c.skill_id, []).append(c)

    now = utc_now_iso()
    added = 0
    kept = 0
    for skill_id, claims in grouped.items():
        entry = usage.get("skills", {}).get(skill_id)
        if entry is None:
            continue
        existing = entry.get("negative_claims") or []
        existing_ids = {c.get("claim_id") for c in existing if isinstance(c, dict)}
        for claim in claims:
            if claim.claim_id in existing_ids:
                kept += 1
                continue
            existing.append({
                "claim_id": claim.claim_id,
                "text": claim.text,
                "keyword": claim.keyword,
                "skill_md": claim.skill_md,
                "line_no": claim.line_no,
                "detected_at": now,
                "last_revalidated_at": None,
                "ttl_days": ttl_days,
                "confidence": confidence,
                "status": DEFAULT_CLAIM_STATUS,
            })
            added += 1
        entry["negative_claims"] = existing
    save_usage(paths, usage)
    return {"added": added, "kept": kept, "removed_stale": 0}


def detect_negative_claims(paths: LifecyclePaths) -> list[ClaimCandidate]:
    if not paths.skills_root.exists():
        return []
    candidates: list[ClaimCandidate] = []
    for item in iter_skills(paths):
        try:
            text = item.skill_md.read_text(encoding="utf-8")
        except OSError:
            continue
        in_fence = False
        for line_no, raw in enumerate(text.splitlines(), start=1):
            stripped = raw.strip()
            if stripped.startswith("```"):
                in_fence = not in_fence
                continue
            if in_fence:
                continue
            lowered = stripped.lower()
            for keyword in NEGATIVE_CLAIM_KEYWORDS:
                needle = keyword.lower()
                if needle in lowered:
                    candidates.append(
                        ClaimCandidate(
                            skill_id=item.skill_id,
                            skill_md=item.skill_md.relative_to(paths.workspace).as_posix(),
                            line_no=line_no,
                            text=stripped[:240],
                            keyword=keyword,
                            claim_id=_hash_claim(item.skill_id, line_no, stripped),
                        )
                    )
                    break
    return candidates


@dataclass(frozen=True)
class UmbrellaCluster:
    token: str
    skill_ids: tuple[str, ...]
    signal: str = "name_token"


_UMBRELLA_STOP_TOKENS = {
    "ko",
    "ops",
    "and",
    "the",
    "v1",
    "v2",
    "data",
    "info",
}

_DESCRIPTION_STOP_WORDS = {
    "the", "a", "an", "and", "or", "for", "to", "of", "in", "on", "at", "by",
    "with", "from", "as", "is", "are", "be", "use", "used", "using", "user",
    "this", "that", "these", "those", "it", "its", "into", "when", "where",
    "what", "which", "how", "should", "must", "can", "will", "do", "does",
    "not", "no", "yes", "any", "all", "some", "more", "most", "less", "than",
    "but", "also", "if", "so", "such", "via",
    # Common action verbs that appear across many SKILL.md descriptions
    "need", "needs", "needed", "want", "wants", "wanted",
    "make", "makes", "made", "making", "get", "gets", "getting",
    "find", "finds", "finding", "show", "shows", "showing",
    "give", "gives", "giving", "take", "takes", "taking",
    "ask", "asks", "asking", "say", "says", "saying", "tell", "tells",
    "run", "runs", "running", "call", "calls", "calling",
    "add", "adds", "adding", "set", "sets", "setting",
    "skill", "skills", "task", "tasks", "tool", "tools", "agent", "agents",
    # Korean stop fragments (filter shorter forms by length already)
    "있다", "없다", "한다", "된다", "있을", "없을", "또는", "그리고",
    "사용", "필요", "관련", "처리", "기반", "포함", "제공",
}


def _tokenize_description(text: str) -> set[str]:
    tokens: set[str] = set()
    for raw in re.findall(r"[A-Za-z가-힣]+", text):
        lowered = raw.lower()
        if len(lowered) < 4:
            continue
        if lowered in _DESCRIPTION_STOP_WORDS:
            continue
        tokens.add(lowered)
    return tokens


def _detect_umbrella_by_name_token(
    skill_tokens: dict[str, set[str]],
    *,
    min_cluster_size: int,
) -> list[UmbrellaCluster]:
    token_to_skills: dict[str, set[str]] = {}
    for skill_id, tokens in skill_tokens.items():
        for token in tokens:
            token_to_skills.setdefault(token, set()).add(skill_id)

    clusters: list[UmbrellaCluster] = []
    for token, skills in sorted(token_to_skills.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        if len(skills) < min_cluster_size:
            continue
        clusters.append(
            UmbrellaCluster(
                token=token,
                skill_ids=tuple(sorted(skills)),
                signal="name_token",
            )
        )
    return clusters


def _detect_umbrella_by_description(
    skill_descriptions: dict[str, str],
    *,
    min_cluster_size: int,
    max_clusters: int = 15,
) -> list[UmbrellaCluster]:
    if not skill_descriptions:
        return []
    skill_tokens: dict[str, set[str]] = {
        sid: _tokenize_description(desc) for sid, desc in skill_descriptions.items() if desc
    }
    token_to_skills: dict[str, set[str]] = {}
    for skill_id, tokens in skill_tokens.items():
        for token in tokens:
            token_to_skills.setdefault(token, set()).add(skill_id)

    total = len(skill_descriptions) or 1
    # Tokens shared by more than ~25% of skills are too generic to be useful
    # as umbrella clusters — they tell you "many skills mention this" rather
    # than "this group could share a router".
    too_generic_threshold = max(min_cluster_size + 1, (total // 4) + 1)
    clusters: list[UmbrellaCluster] = []
    for token, skills in sorted(token_to_skills.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        if len(skills) < min_cluster_size:
            continue
        if len(skills) >= too_generic_threshold:
            continue
        clusters.append(
            UmbrellaCluster(
                token=token,
                skill_ids=tuple(sorted(skills)),
                signal="description_token",
            )
        )
        if len(clusters) >= max_clusters:
            break
    return clusters


def _detect_umbrella_by_execution_profile(
    skill_profiles: dict[str, str],
    *,
    min_cluster_size: int,
) -> list[UmbrellaCluster]:
    """Group skills by their declared default execution profile.

    Skills sharing the same default profile have aligned risk / capability
    requirements, so they are reasonable candidates to live behind a common
    umbrella router that selects between them at runtime.
    """
    if not skill_profiles:
        return []
    profile_to_skills: dict[str, set[str]] = {}
    for skill_id, profile in skill_profiles.items():
        if not profile:
            continue
        profile_to_skills.setdefault(profile, set()).add(skill_id)

    clusters: list[UmbrellaCluster] = []
    for profile, skills in sorted(profile_to_skills.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        if len(skills) < min_cluster_size:
            continue
        clusters.append(
            UmbrellaCluster(
                token=profile,
                skill_ids=tuple(sorted(skills)),
                signal="execution_profile",
            )
        )
    return clusters


def _load_skill_profiles(paths: LifecyclePaths) -> dict[str, str]:
    """Read skill -> default execution profile from the workspace policy file.

    Fail-soft: returns {} if the file is missing or unreadable.
    """
    policy_path = paths.workspace / "references" / "skill_profile_policies.json"
    if not policy_path.exists():
        return {}
    try:
        data = json.loads(policy_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    skills_block = data.get("skills") if isinstance(data, dict) else None
    if not isinstance(skills_block, dict):
        return {}
    profiles: dict[str, str] = {}
    for skill_id, entry in skills_block.items():
        if not isinstance(entry, dict):
            continue
        default = entry.get("default_profile")
        if isinstance(default, str) and default:
            profiles[skill_id] = default
    return profiles


def _detect_umbrella_by_downstream_share(
    skill_downstreams: dict[str, set[str]],
    *,
    min_cluster_size: int,
    min_shared: int = 2,
) -> list[UmbrellaCluster]:
    if not skill_downstreams:
        return []
    downstream_to_skills: dict[str, set[str]] = {}
    for skill_id, downstream in skill_downstreams.items():
        for d in downstream:
            downstream_to_skills.setdefault(d, set()).add(skill_id)

    clusters: list[UmbrellaCluster] = []
    seen_groupings: set[tuple[str, ...]] = set()
    for downstream, skills in sorted(downstream_to_skills.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        if len(skills) < min_cluster_size:
            continue
        key = tuple(sorted(skills))
        if key in seen_groupings:
            continue
        seen_groupings.add(key)
        clusters.append(
            UmbrellaCluster(
                token=downstream,
                skill_ids=key,
                signal="downstream_share",
            )
        )
    return clusters


def _scan_skill_meta(paths: LifecyclePaths) -> tuple[
    dict[str, set[str]],
    dict[str, str],
    dict[str, set[str]],
]:
    """Return (name_tokens, descriptions, downstream_refs) for active skills."""
    name_tokens: dict[str, set[str]] = {}
    descriptions: dict[str, str] = {}
    downstream_refs: dict[str, set[str]] = {}

    skill_ids: set[str] = set()
    payloads: dict[str, str] = {}
    for item in iter_skills(paths):
        if item.is_archived:
            continue
        skill_ids.add(item.skill_id)
        try:
            text = item.skill_md.read_text(encoding="utf-8")
        except OSError:
            continue
        payloads[item.skill_id] = text
        tokens = {
            t.lower()
            for t in re.split(r"[-_]", item.skill_id)
            if len(t) >= 3 and t.lower() not in _UMBRELLA_STOP_TOKENS
        }
        name_tokens[item.skill_id] = tokens
        frontmatter = _parse_frontmatter(item.skill_md)
        descriptions[item.skill_id] = frontmatter.get("description", "")

    backtick_re = re.compile(r"`([a-z][a-z0-9-]{2,})`")
    for skill_id, text in payloads.items():
        refs: set[str] = set()
        for match in backtick_re.findall(text):
            if match in skill_ids and match != skill_id:
                refs.add(match)
        if refs:
            downstream_refs[skill_id] = refs

    return name_tokens, descriptions, downstream_refs


def detect_umbrella_candidates(paths: LifecyclePaths, *, min_cluster_size: int = 3) -> list[UmbrellaCluster]:
    if not paths.skills_root.exists():
        return []
    name_tokens, descriptions, downstream_refs = _scan_skill_meta(paths)
    skill_profiles = _load_skill_profiles(paths)

    clusters: list[UmbrellaCluster] = []
    clusters.extend(_detect_umbrella_by_name_token(name_tokens, min_cluster_size=min_cluster_size))
    clusters.extend(_detect_umbrella_by_description(descriptions, min_cluster_size=min_cluster_size))
    clusters.extend(_detect_umbrella_by_downstream_share(downstream_refs, min_cluster_size=min_cluster_size))
    clusters.extend(_detect_umbrella_by_execution_profile(skill_profiles, min_cluster_size=min_cluster_size))
    return clusters


def read_events(paths: LifecyclePaths, *, skill_id: str | None = None, limit: int | None = None) -> list[dict[str, Any]]:
    if not paths.events_path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with paths.events_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if skill_id and entry.get("skill_id") != skill_id:
                continue
            rows.append(entry)
    if limit is not None and limit >= 0:
        rows = rows[-limit:]
    return rows


def _task_ledger_path(workspace: Path) -> Path:
    return workspace / ".openclaw" / "task-ledger.jsonl"


def read_task_ledger_index(workspace: Path) -> dict[str, dict[str, Any]]:
    """Return the latest task-ledger row per task_id keyed by task_id.

    The ledger writes multiple rows per task (queued -> running -> finished).
    We keep the last row written per task_id, which holds final status.
    Fail-soft: returns {} if the ledger is missing or unreadable.
    """

    ledger = _task_ledger_path(workspace)
    if not ledger.exists():
        return {}
    index: dict[str, dict[str, Any]] = {}
    try:
        with ledger.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                task_id = row.get("task_id")
                if isinstance(task_id, str) and task_id:
                    index[task_id] = row
    except OSError:
        return {}
    return index


@dataclass
class ObserveResult:
    baseline: list[str]
    viewed: list[str]
    patched: list[str]
    total_observed: int


def observe(paths: LifecyclePaths, *, dry_run: bool = False) -> ObserveResult:
    """Record skill_viewed / skill_patched events by polling SKILL.md stat.

    Compares the current mtime and atime of each tracked SKILL.md against the
    last observation stored in usage.json. mtime advance -> skill_patched
    (patch_count, last_patched_at). atime advance -> skill_viewed
    (view_count, last_viewed_at). On first observation, baseline timestamps
    are recorded without emitting events.

    Caveat: macOS APFS and many Linux mounts defer or disable atime updates.
    Where atime tracking is unreliable, skill_viewed will under-report; the
    mtime path remains accurate for actual edits.
    """

    if not paths.usage_path.exists():
        return ObserveResult(baseline=[], viewed=[], patched=[], total_observed=0)

    usage = load_usage(paths)
    skills_index = usage.get("skills", {})
    baseline: list[str] = []
    viewed: list[str] = []
    patched: list[str] = []
    total = 0

    for skill_id, entry in skills_index.items():
        if entry.get("state") in {"missing"}:
            continue
        relative = entry.get("path") or ""
        skill_md = paths.workspace / relative
        if not skill_md.exists():
            continue
        try:
            stat = skill_md.stat()
        except OSError:
            continue
        total += 1
        current_mtime = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(timespec="seconds")
        current_atime = datetime.fromtimestamp(stat.st_atime, tz=timezone.utc).isoformat(timespec="seconds")
        last_mtime = entry.get("last_mtime_seen")
        last_atime = entry.get("last_atime_seen")

        if last_mtime is None and last_atime is None:
            baseline.append(skill_id)
            if not dry_run:
                entry["last_mtime_seen"] = current_mtime
                entry["last_atime_seen"] = current_atime
            continue

        mtime_advanced = last_mtime is not None and current_mtime > last_mtime
        atime_advanced = last_atime is not None and current_atime > last_atime

        if mtime_advanced:
            patched.append(skill_id)
            if not dry_run:
                entry["patch_count"] = int(entry.get("patch_count", 0) or 0) + 1
                entry["last_patched_at"] = current_mtime
                entry["last_mtime_seen"] = current_mtime
        if atime_advanced:
            viewed.append(skill_id)
            if not dry_run:
                entry["view_count"] = int(entry.get("view_count", 0) or 0) + 1
                entry["last_viewed_at"] = current_atime
                entry["last_atime_seen"] = current_atime

        if not (mtime_advanced or atime_advanced) and not dry_run:
            # Refresh baseline pointer even when no change so future observations
            # do not flag a one-time stat oddity as advancement.
            entry["last_mtime_seen"] = current_mtime
            entry["last_atime_seen"] = current_atime

    if not dry_run:
        save_usage(paths, usage)
        for skill_id in patched:
            append_event(paths, {"event": "skill_patched", "skill_id": skill_id, "source": "observer"})
        for skill_id in viewed:
            append_event(paths, {"event": "skill_viewed", "skill_id": skill_id, "source": "observer"})

    return ObserveResult(
        baseline=baseline,
        viewed=viewed,
        patched=patched,
        total_observed=total,
    )


def correlate_events_with_ledger(
    paths: LifecyclePaths,
    *,
    skill_id: str | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Return events joined with their task-ledger row when task_id is set."""

    events = read_events(paths, skill_id=skill_id, limit=limit)
    ledger = read_task_ledger_index(paths.workspace)
    enriched: list[dict[str, Any]] = []
    for event in events:
        merged: dict[str, Any] = dict(event)
        task_id = event.get("task_id")
        if task_id and task_id in ledger:
            row = ledger[task_id]
            merged["task_name"] = row.get("task_name")
            merged["task_status"] = row.get("status")
            merged["task_started_at"] = row.get("started_at")
            merged["task_finished_at"] = row.get("finished_at")
            merged["task_exit_code"] = row.get("exit_code")
            merged["task_profile"] = row.get("profile")
        enriched.append(merged)
    return enriched
