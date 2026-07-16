"""`helm reconcile` — drift-tolerant, idempotent re-apply of workspace
reference files against the packaged desired snapshot.

`helm init` seeds *missing* reference files and `--force` overwrites them
unconditionally (clobbering local edits). Neither reconciles: neither tells
you what drifted, and neither can re-apply only the drifted files while
preserving intentional local overrides.

This command closes that gap. It compares each required reference file in the
workspace against the packaged desired version and classifies it:

  * ``unchanged``      — workspace already matches desired (no-op)
  * ``missing``        — absent/unreadable in workspace → added (apply mode)
  * ``drifted``        — differs from desired → **skipped and reported** so a
                         local override is preserved, unless ``force`` is set,
                         which re-applies the desired version
  * ``source_missing`` — the desired file is absent (broken snapshot) →
                         reported, never fatal

Nothing is written in dry-run (``apply=False``). Re-running after an apply is a
no-op (idempotent). This mirrors the OpenClaw tolerant patch/config re-apply
pattern: skip+report on drift instead of crashing or clobbering. It is the
shared spine behind the "declarative desired-state" and "idempotent reconcile"
operating primitives.
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from commands import (
    DEFAULT_WORKSPACE,
    REFERENCES_ROOT,
    REQUIRED_REFERENCE_FILES,
    target_root,
)


def _read_bytes(path: Path) -> bytes | None:
    """Return the file's bytes, or None if it is absent/unreadable.

    Drift tolerance: an unreadable target (missing, a directory, permission
    error) must never crash a reconcile — it is treated as "needs applying".
    """
    try:
        return path.read_bytes()
    except OSError:
        return None


def reconcile_workspace_references(
    root: Path,
    *,
    apply: bool = False,
    force: bool = False,
    required_files: tuple[str, ...] | None = None,
    references_root: Path | None = None,
) -> dict:
    """Reconcile workspace reference files against the packaged desired snapshot.

    Parameters
    ----------
    root:
        Workspace root; reference files live under ``root / "references"``.
    apply:
        When False (default) this is a dry-run — the report is computed but
        nothing is written. When True, missing files are added and (with
        *force*) drifted files are overwritten.
    force:
        With *apply*, overwrite drifted files from the desired snapshot. Without
        it, drifted files are preserved as local overrides and only reported.
    required_files / references_root:
        Overridable for testing; default to the packaged
        ``REQUIRED_REFERENCE_FILES`` / ``REFERENCES_ROOT``.

    Returns a report dict: ``workspace``, ``dry_run``, per-file ``files``,
    ``summary`` counts, ``converged`` (workspace matches desired with no
    outstanding drift/missing/source gaps), and ``ok`` (no broken-snapshot
    source_missing entries).
    """
    if required_files is None:
        required_files = REQUIRED_REFERENCE_FILES
    if references_root is None:
        references_root = REFERENCES_ROOT

    references_dir = root / "references"
    files: list[dict] = []
    summary = {
        "unchanged": 0,
        "added": 0,
        "overwritten": 0,
        "drift_skipped": 0,
        "missing_skipped": 0,
        "source_missing": 0,
    }

    for filename in required_files:
        source = references_root / filename
        target = references_dir / filename
        source_bytes = _read_bytes(source)

        if source_bytes is None:
            status, action, bucket = "source_missing", "none", "source_missing"
        else:
            target_bytes = _read_bytes(target)
            if target_bytes is None:
                if apply:
                    references_dir.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, target)
                    status, action, bucket = "missing", "added", "added"
                else:
                    status, action, bucket = "missing", "skipped", "missing_skipped"
            elif target_bytes == source_bytes:
                status, action, bucket = "unchanged", "none", "unchanged"
            elif apply and force:
                references_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
                status, action, bucket = "drifted", "overwritten", "overwritten"
            else:
                status, action, bucket = "drifted", "skipped", "drift_skipped"

        summary[bucket] += 1
        files.append({"file": filename, "status": status, "action": action})

    converged = (
        summary["drift_skipped"] == 0
        and summary["missing_skipped"] == 0
        and summary["source_missing"] == 0
    )
    ok = summary["source_missing"] == 0
    return {
        "workspace": str(root),
        "dry_run": not apply,
        "files": files,
        "summary": summary,
        "converged": converged,
        "ok": ok,
    }


def cmd_reconcile(args: argparse.Namespace) -> int:
    root = target_root(args.path or str(DEFAULT_WORKSPACE), create=bool(args.apply))
    report = reconcile_workspace_references(root, apply=args.apply, force=args.force)

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0 if report["ok"] else 1

    print(f"workspace={report['workspace']}")
    print(f"mode={'dry-run' if report['dry_run'] else 'apply'}")
    for entry in report["files"]:
        print(f"{entry['status']:<14} {entry['action']:<12} {entry['file']}")
    s = report["summary"]
    print(
        "summary "
        f"unchanged={s['unchanged']} added={s['added']} overwritten={s['overwritten']} "
        f"drift_skipped={s['drift_skipped']} missing_skipped={s['missing_skipped']} "
        f"source_missing={s['source_missing']}"
    )
    print(f"converged={report['converged']}")
    return 0 if report["ok"] else 1
