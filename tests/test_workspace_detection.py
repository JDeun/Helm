from __future__ import annotations

import tempfile
from pathlib import Path

from helm_workspace import suggest_external_sources


def test_suggest_external_sources_skips_non_directory_obsidian_candidates() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        home = Path(tmpdir)
        (home / "Notes").write_text("not a directory\n", encoding="utf-8")
        parent = home / "Documents" / "Obsidian"
        parent.mkdir(parents=True)
        vault = parent / "VaultA"
        (vault / ".obsidian").mkdir(parents=True)

        suggestions = suggest_external_sources(home)

        assert vault.resolve() in suggestions["obsidian"]
