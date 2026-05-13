from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from release_version_check import check_release


def write_release_files(root: Path, version: str) -> None:
    (root / "docs" / "releases").mkdir(parents=True)
    (root / "pyproject.toml").write_text(
        f'[project]\nname = "helm-agent-ops"\nversion = "{version}"\n',
        encoding="utf-8",
    )
    (root / "CITATION.cff").write_text(f'version: "{version}"\n', encoding="utf-8")
    (root / "README.md").write_text(f"Current release: v{version}\n", encoding="utf-8")
    (root / "README.ko.md").write_text(f"현재 릴리즈: v{version}\n", encoding="utf-8")
    (root / "CHANGELOG.md").write_text(f"# Changelog\n\n## [{version}] - 2026-05-13\n", encoding="utf-8")
    (root / "docs" / "releases" / f"{version}.md").write_text(f"# Helm v{version}\n", encoding="utf-8")


def test_check_release_accepts_consistent_versions(tmp_path: Path) -> None:
    write_release_files(tmp_path, "0.9.2")

    assert check_release(tmp_path) == []


def test_check_release_reports_legacy_setup_py(tmp_path: Path) -> None:
    write_release_files(tmp_path, "0.9.2")
    (tmp_path / "setup.py").write_text(
        'from setuptools import setup\nsetup(name="helm-agent-ops", version="0.9.2")\n',
        encoding="utf-8",
    )

    errors = check_release(tmp_path)

    assert "setup.py: remove legacy version-bearing packaging shim; pyproject.toml is the package version source" in errors


def test_check_release_reports_missing_version_field(tmp_path: Path) -> None:
    write_release_files(tmp_path, "0.9.2")
    (tmp_path / "README.md").write_text("# Helm\n", encoding="utf-8")

    errors = check_release(tmp_path)

    assert errors == ["README.md: version not found"]
