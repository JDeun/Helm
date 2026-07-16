"""Tests for commands/reconcile.py — drift-tolerant idempotent reference reconcile.

Coverage:
 1. Missing files, dry-run  → reported missing, nothing written, not converged
 2. Missing files, apply     → added, files match desired, converged
 3. All present & matching    → unchanged, converged, idempotent (no rewrite)
 4. Drift, apply (no force)   → drift preserved (local override), reported, not converged
 5. Drift, apply + force      → overwritten from desired, converged
 6. Source missing            → reported source_missing, ok False, no crash
 7. Idempotent re-apply       → second apply is all-unchanged and converged
 8. Unreadable target (dir)   → treated as missing, no crash (drift tolerance)
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from commands import REFERENCES_ROOT, REQUIRED_REFERENCE_FILES  # noqa: E402
from commands.reconcile import (  # noqa: E402
    cmd_reconcile,
    reconcile_workspace_references,
)


def _setup(tmp_path):
    desired = tmp_path / "desired"
    desired.mkdir()
    (desired / "a.json").write_text('{"k": 1}\n', encoding="utf-8")
    (desired / "b.json").write_text('{"k": 2}\n', encoding="utf-8")
    root = tmp_path / "ws"
    (root / "references").mkdir(parents=True)
    return desired, root, ("a.json", "b.json")


def test_missing_dry_run_writes_nothing(tmp_path):
    desired, root, req = _setup(tmp_path)
    report = reconcile_workspace_references(
        root, apply=False, required_files=req, references_root=desired
    )
    assert report["dry_run"] is True
    assert report["summary"]["missing_skipped"] == 2
    assert report["converged"] is False
    assert report["ok"] is True
    # nothing written
    assert not (root / "references" / "a.json").exists()
    assert not (root / "references" / "b.json").exists()


def test_missing_apply_adds_and_converges(tmp_path):
    desired, root, req = _setup(tmp_path)
    report = reconcile_workspace_references(
        root, apply=True, required_files=req, references_root=desired
    )
    assert report["summary"]["added"] == 2
    assert report["converged"] is True
    assert (root / "references" / "a.json").read_text(encoding="utf-8") == '{"k": 1}\n'
    assert (root / "references" / "b.json").read_text(encoding="utf-8") == '{"k": 2}\n'


def test_matching_is_unchanged_and_idempotent(tmp_path):
    desired, root, req = _setup(tmp_path)
    for name in req:
        (root / "references" / name).write_text(
            (desired / name).read_text(encoding="utf-8"), encoding="utf-8"
        )
    before = {n: (root / "references" / n).stat().st_mtime_ns for n in req}
    report = reconcile_workspace_references(
        root, apply=True, required_files=req, references_root=desired
    )
    assert report["summary"]["unchanged"] == 2
    assert report["converged"] is True
    # idempotent: unchanged files were not rewritten
    after = {n: (root / "references" / n).stat().st_mtime_ns for n in req}
    assert before == after


def test_drift_without_force_preserves_local(tmp_path):
    desired, root, req = _setup(tmp_path)
    (root / "references" / "a.json").write_text('{"k": 999}\n', encoding="utf-8")
    (root / "references" / "b.json").write_text(
        (desired / "b.json").read_text(encoding="utf-8"), encoding="utf-8"
    )
    report = reconcile_workspace_references(
        root, apply=True, force=False, required_files=req, references_root=desired
    )
    assert report["summary"]["drift_skipped"] == 1
    assert report["summary"]["unchanged"] == 1
    assert report["converged"] is False
    # local override preserved, not clobbered
    assert (root / "references" / "a.json").read_text(encoding="utf-8") == '{"k": 999}\n'


def test_drift_with_force_overwrites(tmp_path):
    desired, root, req = _setup(tmp_path)
    (root / "references" / "a.json").write_text('{"k": 999}\n', encoding="utf-8")
    (root / "references" / "b.json").write_text(
        (desired / "b.json").read_text(encoding="utf-8"), encoding="utf-8"
    )
    report = reconcile_workspace_references(
        root, apply=True, force=True, required_files=req, references_root=desired
    )
    assert report["summary"]["overwritten"] == 1
    assert report["converged"] is True
    assert (root / "references" / "a.json").read_text(encoding="utf-8") == '{"k": 1}\n'


def test_source_missing_is_reported_not_fatal(tmp_path):
    desired, root, _ = _setup(tmp_path)
    req = ("a.json", "b.json", "c.json")  # c.json has no desired source
    report = reconcile_workspace_references(
        root, apply=True, required_files=req, references_root=desired
    )
    assert report["summary"]["source_missing"] == 1
    assert report["ok"] is False
    statuses = {e["file"]: e["status"] for e in report["files"]}
    assert statuses["c.json"] == "source_missing"


def test_reapply_is_idempotent(tmp_path):
    desired, root, req = _setup(tmp_path)
    first = reconcile_workspace_references(
        root, apply=True, required_files=req, references_root=desired
    )
    assert first["summary"]["added"] == 2
    second = reconcile_workspace_references(
        root, apply=True, required_files=req, references_root=desired
    )
    assert second["summary"]["unchanged"] == 2
    assert second["summary"]["added"] == 0
    assert second["converged"] is True


def test_directory_target_is_blocked_not_copied_into(tmp_path):
    # A directory shadowing a reference path must be reported (blocked), never
    # copied into — and must not report false success / converged.
    desired, root, req = _setup(tmp_path)
    (root / "references" / "a.json").mkdir()
    report = reconcile_workspace_references(
        root, apply=True, required_files=req, references_root=desired
    )
    statuses = {e["file"]: e["status"] for e in report["files"]}
    assert statuses["a.json"] == "not_a_regular_file"
    assert report["summary"]["blocked"] == 1
    assert report["converged"] is False
    assert report["ok"] is False
    # nothing was written into the directory
    assert (root / "references" / "a.json").is_dir()
    assert not (root / "references" / "a.json" / "a.json").exists()


def test_symlink_target_not_followed_outside_workspace(tmp_path):
    # A drifted reference that is a symlink to an external file must NOT be
    # overwritten through the link (which would write outside the workspace).
    desired, root, req = _setup(tmp_path)
    outside = tmp_path / "OUTSIDE_secret.json"
    outside.write_text('{"secret": true}\n', encoding="utf-8")
    link = root / "references" / "a.json"
    link.symlink_to(outside)
    (root / "references" / "b.json").write_text(
        (desired / "b.json").read_text(encoding="utf-8"), encoding="utf-8"
    )
    report = reconcile_workspace_references(
        root, apply=True, force=True, required_files=req, references_root=desired
    )
    statuses = {e["file"]: e["status"] for e in report["files"]}
    assert statuses["a.json"] == "not_a_regular_file"
    assert report["summary"]["blocked"] == 1
    assert report["ok"] is False
    # the external victim file was NOT overwritten
    assert outside.read_text(encoding="utf-8") == '{"secret": true}\n'


def test_cli_smoke_against_packaged_references(tmp_path, capsys):
    """End-to-end against the real packaged desired snapshot via cmd_reconcile."""
    import argparse
    import json as _json

    references_dir = tmp_path / "references"
    references_dir.mkdir(parents=True)
    for name in REQUIRED_REFERENCE_FILES:
        (references_dir / name).write_bytes((REFERENCES_ROOT / name).read_bytes())

    args = argparse.Namespace(path=str(tmp_path), apply=False, force=False, json=True)
    rc = cmd_reconcile(args)
    out = _json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["ok"] is True
    assert out["converged"] is True
    assert out["summary"]["unchanged"] == len(REQUIRED_REFERENCE_FILES)
