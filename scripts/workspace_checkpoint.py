#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import tarfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


WORKSPACE = Path.home() / ".openclaw" / "workspace"
CHECKPOINT_ROOT = WORKSPACE / ".openclaw" / "checkpoints"


@dataclass
class CheckpointRecord:
    checkpoint_id: str
    label: str
    created_at: str
    paths: list[str]
    archive: str


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def resolve_workspace_path(raw: str) -> Path:
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = WORKSPACE / candidate
    resolved = candidate.resolve()
    workspace_resolved = WORKSPACE.resolve()
    if workspace_resolved not in resolved.parents and resolved != workspace_resolved:
        raise ValueError(f"path escapes workspace: {raw}")
    if not resolved.exists():
        raise ValueError(f"path does not exist: {raw}")
    return resolved


def relpath(path: Path) -> str:
    return str(path.resolve().relative_to(WORKSPACE.resolve()))


def ensure_root() -> None:
    CHECKPOINT_ROOT.mkdir(parents=True, exist_ok=True)


def load_index() -> list[CheckpointRecord]:
    index_path = CHECKPOINT_ROOT / "index.json"
    if not index_path.exists():
        return []
    data = json.loads(index_path.read_text(encoding="utf-8"))
    return [CheckpointRecord(**item) for item in data]


def save_index(records: Iterable[CheckpointRecord]) -> None:
    index_path = CHECKPOINT_ROOT / "index.json"
    payload = [asdict(record) for record in records]
    index_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def archive_members(record: CheckpointRecord) -> list[str]:
    archive_path = WORKSPACE / record.archive
    if not archive_path.exists():
        raise ValueError(f"archive missing: {archive_path}")
    with tarfile.open(archive_path, "r:gz") as tar:
        return sorted(member.name for member in tar.getmembers() if member.isfile())


def create_checkpoint(args: argparse.Namespace) -> int:
    ensure_root()
    timestamp = utc_now()
    checkpoint_id = f"{timestamp}-{args.label}"
    target_dir = CHECKPOINT_ROOT / checkpoint_id
    target_dir.mkdir(parents=True, exist_ok=False)

    resolved_paths = [resolve_workspace_path(path) for path in args.path]
    archive_path = target_dir / "snapshot.tar.gz"
    manifest_path = target_dir / "manifest.json"

    with tarfile.open(archive_path, "w:gz") as tar:
        for path in resolved_paths:
            tar.add(path, arcname=relpath(path))

    record = CheckpointRecord(
        checkpoint_id=checkpoint_id,
        label=args.label,
        created_at=timestamp,
        paths=[relpath(path) for path in resolved_paths],
        archive=str(archive_path.relative_to(WORKSPACE)),
    )
    manifest_path.write_text(json.dumps(asdict(record), indent=2), encoding="utf-8")

    records = load_index()
    records.append(record)
    save_index(records)

    print(json.dumps(asdict(record), indent=2))
    return 0


def list_checkpoints(_: argparse.Namespace) -> int:
    ensure_root()
    records = load_index()
    if not records:
        print("No checkpoints found.")
        return 0
    for record in records:
        print(f"{record.checkpoint_id}\t{record.label}\t{', '.join(record.paths)}")
    return 0


def find_record(checkpoint_id: str) -> CheckpointRecord:
    for record in load_index():
        if record.checkpoint_id == checkpoint_id:
            return record
    raise ValueError(f"checkpoint not found: {checkpoint_id}")


def show_checkpoint(args: argparse.Namespace) -> int:
    ensure_root()
    record = find_record(args.checkpoint_id)
    print(json.dumps(asdict(record), indent=2))
    return 0


def preview_checkpoint(args: argparse.Namespace) -> int:
    ensure_root()
    record = find_record(args.checkpoint_id)
    members = archive_members(record)
    print(json.dumps({"checkpoint": asdict(record), "files": members}, indent=2))
    return 0


def restore_checkpoint(args: argparse.Namespace) -> int:
    ensure_root()
    record = find_record(args.checkpoint_id)
    archive_path = WORKSPACE / record.archive
    if not archive_path.exists():
        raise ValueError(f"archive missing: {archive_path}")

    with tarfile.open(archive_path, "r:gz") as tar:
        members = tar.getmembers()
        for member in members:
            member_path = (WORKSPACE / member.name).resolve()
            workspace_resolved = WORKSPACE.resolve()
            if workspace_resolved not in member_path.parents and member_path != workspace_resolved:
                raise ValueError(f"archive member escapes workspace: {member.name}")
        tar.extractall(path=WORKSPACE)

    print(f"Restored {record.checkpoint_id} into {WORKSPACE}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create and restore local workspace checkpoints.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    create = subparsers.add_parser("create", help="Create a checkpoint archive for one or more workspace paths.")
    create.add_argument("--label", required=True, help="Short checkpoint label, e.g. risky-router-edit")
    create.add_argument("--path", action="append", required=True, help="Workspace-relative or absolute path to include.")
    create.set_defaults(func=create_checkpoint)

    show = subparsers.add_parser("show", help="Show checkpoint metadata.")
    show.add_argument("checkpoint_id")
    show.set_defaults(func=show_checkpoint)

    preview = subparsers.add_parser("preview", help="Preview files stored in a checkpoint archive.")
    preview.add_argument("checkpoint_id")
    preview.set_defaults(func=preview_checkpoint)

    listing = subparsers.add_parser("list", help="List available checkpoints.")
    listing.set_defaults(func=list_checkpoints)

    restore = subparsers.add_parser("restore", help="Restore files from a checkpoint archive.")
    restore.add_argument("checkpoint_id")
    restore.set_defaults(func=restore_checkpoint)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
