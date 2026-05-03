"""Helm CLI commands for skill lifecycle management."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from commands import target_root
from scripts.skill_lifecycle_lib import (
    LifecycleError,
    LifecyclePaths,
    apply_archive,
    apply_restore,
    apply_stale,
    compute_summary,
    correlate_events_with_ledger,
    detect_negative_claims,
    detect_umbrella_candidates,
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
    save_config,
    save_usage,
    scan,
    set_pinned,
    stale_candidates,
)


def _human_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024.0 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} GB"


def _paths_for(args: argparse.Namespace) -> LifecyclePaths:
    root = target_root(args.path) if getattr(args, "path", None) else target_root(None)
    return LifecyclePaths.for_workspace(root)


def _ensure_config(paths: LifecyclePaths, *, write: bool = True) -> dict:
    config = load_config(paths)
    if write and not paths.config_path.exists():
        save_config(paths, config)
    return config


def cmd_skill_lifecycle_scan(args: argparse.Namespace) -> int:
    paths = _paths_for(args)
    _ensure_config(paths, write=not args.dry_run)
    result = scan(paths, dry_run=args.dry_run)

    if args.json:
        payload = {
            "workspace": str(paths.workspace),
            "added": result.added,
            "refreshed": result.refreshed,
            "missing": result.missing,
            "archived_only": result.archived_only,
            "total": result.total,
            "dry_run": args.dry_run,
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0

    label = "[dry-run] " if args.dry_run else ""
    print(f"{label}workspace: {paths.workspace}")
    print(f"{label}total tracked skills: {result.total}")
    print(f"{label}added: {len(result.added)}")
    for skill_id in result.added:
        print(f"  + {skill_id}")
    print(f"{label}refreshed: {len(result.refreshed)}")
    for skill_id in result.refreshed:
        print(f"  ~ {skill_id}")
    print(f"{label}missing: {len(result.missing)}")
    for skill_id in result.missing:
        print(f"  - {skill_id}")
    if result.archived_only:
        print(f"{label}archived-only entries: {len(result.archived_only)}")
        for skill_id in result.archived_only:
            print(f"  archived: {skill_id}")
    return 0


def cmd_skill_lifecycle_status(args: argparse.Namespace) -> int:
    paths = _paths_for(args)
    config = _ensure_config(paths, write=False)
    usage = load_usage(paths)
    summary = compute_summary(usage, config, paths=paths)

    if args.json:
        payload = {
            "workspace": str(paths.workspace),
            "summary": summary,
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0

    counts = summary["counts"]
    print(f"workspace: {paths.workspace}")
    print(f"total: {summary['total']}")
    print(
        "counts: "
        f"active={counts.get('active', 0)} "
        f"stale={counts.get('stale', 0)} "
        f"archived={counts.get('archived', 0)} "
        f"missing={counts.get('missing', 0)} "
        f"pinned={counts.get('pinned', 0)}"
    )
    if summary["never_used"]:
        print(f"never used: {len(summary['never_used'])}")
        for skill_id, age in summary["never_used"][:10]:
            age_label = f"{age:.0f}d" if age is not None else "?"
            print(f"  {skill_id} ({age_label} since first seen)")
    if summary["least_recently_used"]:
        print(f"least recently used: {len(summary['least_recently_used'])}")
        for skill_id, days in summary["least_recently_used"][:10]:
            days_label = f"{days:.0f}d" if days is not None else "?"
            print(f"  {skill_id} ({days_label} idle)")
    if summary["archive_candidates"]:
        print(f"archive candidates: {len(summary['archive_candidates'])}")
        for skill_id, days in summary["archive_candidates"][:10]:
            days_label = f"{days:.0f}d" if days is not None else "?"
            print(f"  {skill_id} ({days_label} idle)")
    return 0


def cmd_skill_lifecycle_report(args: argparse.Namespace) -> int:
    paths = _paths_for(args)
    config = _ensure_config(paths, write=False)
    usage = load_usage(paths)
    summary = compute_summary(usage, config, paths=paths)

    if args.format == "json":
        rendered = render_report_json(usage, summary)
    else:
        rendered = render_report_markdown(usage, summary)

    if args.out:
        out_path = Path(args.out).expanduser()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(rendered + ("\n" if not rendered.endswith("\n") else ""), encoding="utf-8")
        print(f"wrote report: {out_path}", file=sys.stderr)
    else:
        print(rendered)
    return 0


def cmd_skill_lifecycle_pin(args: argparse.Namespace) -> int:
    paths = _paths_for(args)
    _ensure_config(paths, write=False)
    try:
        set_pinned(paths, args.skill, pinned=True)
    except LifecycleError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"pinned: {args.skill}")
    return 0


def cmd_skill_lifecycle_unpin(args: argparse.Namespace) -> int:
    paths = _paths_for(args)
    _ensure_config(paths, write=False)
    try:
        set_pinned(paths, args.skill, pinned=False)
    except LifecycleError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"unpinned: {args.skill}")
    return 0


def cmd_skill_lifecycle_stale(args: argparse.Namespace) -> int:
    paths = _paths_for(args)
    config = _ensure_config(paths, write=args.apply)
    usage = load_usage(paths)
    candidates = stale_candidates(usage, config)

    if args.json:
        payload = {
            "workspace": str(paths.workspace),
            "apply": args.apply,
            "candidates": [
                {
                    "skill_id": p.skill_id,
                    "from_state": p.from_state,
                    "to_state": p.to_state,
                    "reason": p.reason,
                }
                for p in candidates
            ],
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        label = "[apply]" if args.apply else "[dry-run]"
        if not candidates:
            print(f"{label} no stale candidates")
        else:
            print(f"{label} stale candidates: {len(candidates)}")
            for preview in candidates:
                print(f"  {preview.skill_id}: {preview.from_state} -> {preview.to_state} ({preview.reason})")

    if args.apply and candidates:
        applied = apply_stale(paths, candidates)
        if not args.json:
            print(f"applied stale: {len(applied)}")
    return 0


def cmd_skill_lifecycle_archive(args: argparse.Namespace) -> int:
    paths = _paths_for(args)
    config = _ensure_config(paths, write=args.apply)
    try:
        plan = plan_archive(paths, args.skill, config)
    except LifecycleError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    label = "[apply]" if args.apply else "[dry-run]"
    print(f"{label} archive {plan.skill_id}")
    print(f"  from: {plan.source_dir}")
    print(f"  to:   {plan.target_dir}")
    print(f"  contents: {plan.file_count} files, {_human_bytes(plan.total_bytes)}")
    if plan.sample_files:
        for sample in plan.sample_files:
            print(f"    - {sample}")
        if plan.file_count > len(plan.sample_files):
            print(f"    - ... ({plan.file_count - len(plan.sample_files)} more)")

    if args.apply:
        apply_archive(paths, plan)
        print("archived")
    return 0


def cmd_skill_lifecycle_restore(args: argparse.Namespace) -> int:
    paths = _paths_for(args)
    _ensure_config(paths, write=args.apply)
    try:
        plan = plan_restore(paths, args.skill)
    except LifecycleError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    label = "[apply]" if args.apply else "[dry-run]"
    print(f"{label} restore {plan.skill_id}")
    print(f"  from: {plan.source_dir}")
    print(f"  to:   {plan.target_dir}")

    if args.apply:
        apply_restore(paths, plan)
        print("restored")
    return 0


def cmd_skill_lifecycle_negative_claims(args: argparse.Namespace) -> int:
    paths = _paths_for(args)
    _ensure_config(paths, write=args.persist)
    candidates = detect_negative_claims(paths)

    persisted = None
    if args.persist:
        persisted = persist_negative_claims(paths, ttl_days=args.ttl_days, confidence=args.confidence)

    if args.json:
        payload: dict[str, Any] = {
            "candidates": [
                {
                    "claim_id": c.claim_id,
                    "skill_id": c.skill_id,
                    "skill_md": c.skill_md,
                    "line_no": c.line_no,
                    "keyword": c.keyword,
                    "text": c.text,
                }
                for c in candidates
            ],
        }
        if persisted is not None:
            payload["persisted"] = persisted
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0

    if not candidates:
        print("(no negative-claim candidates)")
    else:
        print(f"negative-claim candidates: {len(candidates)}")
        for claim in candidates:
            print(f"  {claim.skill_id} ({claim.skill_md}:{claim.line_no}) [{claim.keyword}]")
            print(f"    > {claim.text}")

    if persisted is not None:
        print(f"persisted: added={persisted['added']} kept={persisted['kept']}")
    return 0


def cmd_skill_lifecycle_umbrella(args: argparse.Namespace) -> int:
    paths = _paths_for(args)
    _ensure_config(paths, write=False)
    clusters = detect_umbrella_candidates(paths, min_cluster_size=args.min_cluster_size)

    if args.json:
        payload = [
            {"signal": c.signal, "token": c.token, "skill_ids": list(c.skill_ids)}
            for c in clusters
        ]
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0

    if not clusters:
        print("(no umbrella candidates)")
        return 0

    print(f"umbrella candidate clusters: {len(clusters)}")
    for cluster in clusters:
        print(f"  [{cluster.signal}] `{cluster.token}` ({len(cluster.skill_ids)} skills)")
        for skill_id in cluster.skill_ids:
            print(f"    - {skill_id}")
    return 0


def cmd_skill_lifecycle_ledger(args: argparse.Namespace) -> int:
    paths = _paths_for(args)
    _ensure_config(paths, write=False)
    rows = correlate_events_with_ledger(paths, skill_id=args.skill, limit=args.limit)

    if args.json:
        print(json.dumps(rows, indent=2, ensure_ascii=False))
        return 0

    if not rows:
        print("(no events)")
        return 0
    for row in rows:
        ts = row.get("ts", "?")
        event = row.get("event", "?")
        skill_id = row.get("skill_id", "")
        task_name = row.get("task_name") or ""
        task_status = row.get("task_status") or ""
        exit_code = row.get("task_exit_code")
        suffix_parts = []
        if task_name:
            suffix_parts.append(f"task={task_name!r}")
        if task_status:
            suffix_parts.append(f"status={task_status}")
        if exit_code is not None:
            suffix_parts.append(f"exit={exit_code}")
        suffix = " ".join(suffix_parts)
        print(f"{ts} {event} {skill_id} {suffix}".rstrip())
    return 0


def cmd_skill_lifecycle_view(args: argparse.Namespace) -> int:
    """Manually record a skill_viewed event.

    Useful when atime-based observation is unreliable (e.g., macOS APFS
    deferred atime updates) — callers that explicitly opened a SKILL.md can
    record the view directly via this command instead.
    """
    paths = _paths_for(args)
    _ensure_config(paths, write=False)
    if not paths.usage_path.exists():
        print("error: lifecycle layer not initialized; run `helm skill-lifecycle scan` first", file=sys.stderr)
        return 2
    ok = record_runner_event(
        paths.workspace,
        skill_id=args.skill,
        event="skill_viewed",
        extra={"source": "manual"},
    )
    if not ok:
        print(f"error: unknown skill or recording failed: {args.skill}", file=sys.stderr)
        return 2
    entry = load_usage(paths)["skills"][args.skill]
    print(f"viewed: {args.skill} (view_count={entry['view_count']})")
    return 0


def cmd_skill_lifecycle_revalidation_due(args: argparse.Namespace) -> int:
    paths = _paths_for(args)
    _ensure_config(paths, write=False)
    rows = revalidation_due_claims(paths)

    if args.json:
        print(json.dumps(rows, indent=2, ensure_ascii=False))
        return 0

    if not rows:
        print("(no claims past their TTL)")
        return 0

    print(f"revalidation-due claims: {len(rows)}")
    for claim in rows:
        anchor = claim.get("anchor", "detected_at")
        print(
            f"  {claim['skill_id']} ({claim.get('skill_md', '?')}:{claim.get('line_no', '?')}) "
            f"[{claim.get('keyword', '?')}] overdue {claim['due_since_days']:.1f}d (anchor={anchor})"
        )
        text = claim.get("text") or ""
        if text:
            print(f"    > {text}")
    return 0


def cmd_skill_lifecycle_observe(args: argparse.Namespace) -> int:
    paths = _paths_for(args)
    _ensure_config(paths, write=not args.dry_run)
    result = observe(paths, dry_run=args.dry_run)

    if args.json:
        payload = {
            "baseline": result.baseline,
            "viewed": result.viewed,
            "patched": result.patched,
            "total_observed": result.total_observed,
            "dry_run": args.dry_run,
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0

    label = "[dry-run] " if args.dry_run else ""
    print(f"{label}observed: {result.total_observed}")
    if result.baseline:
        print(f"{label}baselined (first observation): {len(result.baseline)}")
    if result.viewed:
        print(f"{label}skill_viewed (atime advanced): {len(result.viewed)}")
        for skill_id in result.viewed:
            print(f"  ~ {skill_id}")
    if result.patched:
        print(f"{label}skill_patched (mtime advanced): {len(result.patched)}")
        for skill_id in result.patched:
            print(f"  + {skill_id}")
    return 0


def cmd_skill_lifecycle_events(args: argparse.Namespace) -> int:
    paths = _paths_for(args)
    _ensure_config(paths, write=False)
    rows = read_events(paths, skill_id=args.skill, limit=args.limit)

    if args.json:
        print(json.dumps(rows, indent=2, ensure_ascii=False))
        return 0

    if not rows:
        print("(no events)")
        return 0
    for row in rows:
        ts = row.get("ts", "?")
        event = row.get("event", "?")
        skill_id = row.get("skill_id", "")
        extra_keys = [k for k in row.keys() if k not in {"ts", "event", "skill_id"}]
        extras = " ".join(f"{k}={row[k]}" for k in extra_keys)
        print(f"{ts} {event} {skill_id} {extras}".rstrip())
    return 0
