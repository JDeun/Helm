from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from helm_workspace import suggest_external_sources


class WorkspaceDetectionTests(unittest.TestCase):
    def test_suggest_external_sources_skips_non_directory_obsidian_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir)
            (home / "Notes").write_text("not a directory\n", encoding="utf-8")
            parent = home / "Documents" / "Obsidian"
            parent.mkdir(parents=True)
            vault = parent / "VaultA"
            (vault / ".obsidian").mkdir(parents=True)

            suggestions = suggest_external_sources(home)

            self.assertIn(vault.resolve(), suggestions["obsidian"])


if __name__ == "__main__":
    unittest.main()
