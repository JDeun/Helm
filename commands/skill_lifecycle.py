"""Helm CLI commands for skill lifecycle management (M1: read-only)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from commands import target_root
from scripts.skill_lifecycle_lib import (
    LifecyclePaths,
    compute_summary,
    load_config,
    load_usage,
    render_report_json,
    render_report_markdown,
    save_config,
    scan,
)


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
    summary = compute_summary(usage, config)

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
    summary = compute_summary(usage, config)

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
