from __future__ import annotations

import argparse
import json
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import workspace_checkpoint


class WorkspaceCheckpointTests(unittest.TestCase):
    def test_restore_rejects_symlink_members(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            workspace = root / "workspace"
            checkpoints_root = workspace / ".helm" / "checkpoints"
            checkpoint_dir = checkpoints_root / "cp-1"
            checkpoint_dir.mkdir(parents=True)
            workspace.mkdir(exist_ok=True)

            archive_path = checkpoint_dir / "snapshot.tar.gz"
            with tarfile.open(archive_path, "w:gz") as tar:
                payload = workspace / "payload.txt"
                payload.write_text("payload\n", encoding="utf-8")
                tar.add(payload, arcname="payload.txt")
                info = tarfile.TarInfo("escape-link")
                info.type = tarfile.SYMTYPE
                info.linkname = "/tmp/outside-target"
                tar.addfile(info)

            record = workspace_checkpoint.CheckpointRecord(
                checkpoint_id="cp-1",
                label="test",
                created_at="20260420T000000Z",
                paths=["payload.txt"],
                archive=str(archive_path.relative_to(workspace)),
            )
            (checkpoints_root / "index.json").write_text(
                json.dumps([{
                    "checkpoint_id": record.checkpoint_id,
                    "label": record.label,
                    "created_at": record.created_at,
                    "paths": record.paths,
                    "archive": record.archive,
                }]),
                encoding="utf-8",
            )

            args = argparse.Namespace(checkpoint_id="cp-1")
            with patch.object(workspace_checkpoint, "WORKSPACE", workspace), patch.object(
                workspace_checkpoint, "CHECKPOINT_ROOT", checkpoints_root
            ):
                with self.assertRaisesRegex(ValueError, "unsupported link member"):
                    workspace_checkpoint.restore_checkpoint(args)


if __name__ == "__main__":
    unittest.main()
