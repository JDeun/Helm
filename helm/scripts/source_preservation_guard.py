#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path


LONG_DOCUMENT_LINE_THRESHOLD = 120
PRESERVED_SUFFIXES = {".md", ".markdown", ".txt", ".rst", ".json", ".jsonl", ".yaml", ".yml"}


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def compare_files(before: Path, after: Path, *, protected_markers: list[str] | None = None) -> dict:
    before_text = _read_text(before)
    after_text = _read_text(after)
    issues: list[dict] = []
    markers = protected_markers or []
    for marker in markers:
        before_count = before_text.count(marker)
        after_count = after_text.count(marker)
        if before_count != after_count:
            issues.append(
                {
                    "type": "protected_marker_count_changed",
                    "marker": marker,
                    "before": before_count,
                    "after": after_count,
                }
            )
    before_lines = before_text.splitlines()
    after_lines = after_text.splitlines()
    # Multiset difference so that deleting some (but not all) copies of a
    # duplicated line is still counted, instead of being hidden by a plain
    # set difference when at least one copy survives in `after`.
    removed_counter = Counter(before_lines) - Counter(after_lines)
    removed_occurrences = sum(removed_counter.values())
    removed_unique = len(removed_counter)
    changed_ratio = 0.0
    if before_lines:
        changed_ratio = removed_occurrences / len(before_lines)
    if removed_occurrences >= 5 and changed_ratio > 0.35:
        issues.append(
            {
                "type": "large_source_removal",
                "removed_unique_lines": removed_unique,
                "before_lines": len(before_lines),
                "changed_ratio": round(changed_ratio, 3),
            }
        )
    return {
        "before": str(before),
        "after": str(after),
        "ok": not issues,
        "issues": issues,
        "summary": {
            "before_lines": len(before_lines),
            "after_lines": len(after_lines),
            "removed_unique_lines": removed_unique,
        },
    }


def guard_recommendation_for_path(path: Path, *, line_threshold: int = LONG_DOCUMENT_LINE_THRESHOLD) -> dict:
    try:
        line_count = len(path.read_text(encoding="utf-8", errors="ignore").splitlines())
    except OSError:
        line_count = 0
    suffix = path.suffix.casefold()
    recommended = suffix in PRESERVED_SUFFIXES and line_count >= line_threshold
    return {
        "path": str(path),
        "line_count": line_count,
        "suffix": suffix,
        "guard_recommended": recommended,
        "reason": "long_preserved_text" if recommended else "below_threshold_or_untracked_suffix",
        "line_threshold": line_threshold,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Detect broad or protected-source changes between two text files.")
    parser.add_argument("--before")
    parser.add_argument("--after")
    parser.add_argument("--protected-marker", action="append", default=[])
    parser.add_argument("--recommend-for")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if args.recommend_for:
        payload = guard_recommendation_for_path(Path(args.recommend_for).expanduser())
        exit_code = 0
    else:
        if not args.before or not args.after:
            raise SystemExit("Provide --before and --after, or use --recommend-for")
        payload = compare_files(Path(args.before).expanduser(), Path(args.after).expanduser(), protected_markers=args.protected_marker)
        exit_code = 0 if payload["ok"] else 2
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    elif args.recommend_for:
        print(f"guard_recommended={payload['guard_recommended']}")
        print(f"reason={payload['reason']}")
    else:
        print(f"ok={payload['ok']}")
        for issue in payload["issues"]:
            print(f"issue={issue['type']} detail={issue}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
