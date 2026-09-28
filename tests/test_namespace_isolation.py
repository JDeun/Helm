"""The four unprefixed top-level names must not come back.

`scripts` and `commands` are among the most common directory names in Python
projects; publishing them as top-level packages silently shadows a user's own.
It already happened to helm's only user (14 pytest collection errors, which abort
the whole suite and hide every other result).
"""
from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHADOWING_NAMES = ("scripts", "commands", "references", "memory_tree")


def test_references_lives_under_the_helm_package() -> None:
    assert (ROOT / "helm" / "references" / "role_catalog.json").is_file()
    assert not (ROOT / "references").exists()


def test_pyproject_moved_the_references_package_data_key() -> None:
    # Scoped to the key this task actually moves. `package-data` also holds
    # "scripts.compression", which Task 6 moves — asserting "every key is
    # helm-prefixed" here would fail from this commit until Task 6 lands, leaving
    # the suite red across three commits and breaking the plan's own invariant
    # that every task ends green. The strict all-keys assertion is added in Task 6.
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    package_data = data["tool"]["setuptools"]["package-data"]
    assert "references" not in package_data
    assert "helm.references" in package_data


def test_memory_tree_lives_under_the_helm_package() -> None:
    from helm.memory_tree import MemoryTree
    from helm.memory_tree.tree import MemoryTree as SameClass

    assert MemoryTree is SameClass
    assert not (ROOT / "memory_tree").exists()


def test_commands_live_under_the_helm_package() -> None:
    from helm.commands import REFERENCES_ROOT, SCRIPT_ROOT
    from helm.commands.checkpoint import cmd_checkpoint_create  # noqa: F401

    assert REFERENCES_ROOT == ROOT / "helm" / "references"
    assert (SCRIPT_ROOT / "skill_capture.py").is_file()
    assert not (ROOT / "commands").exists()


def test_scripts_live_under_the_helm_package() -> None:
    from helm.commands import SCRIPT_ROOT
    from helm.scripts import command_guard, jsonl_io, run_with_profile  # noqa: F401

    assert SCRIPT_ROOT == ROOT / "helm" / "scripts"
    assert not (ROOT / "scripts").exists()


def test_no_script_puts_the_package_dir_on_sys_path() -> None:
    """Inserting helm/ onto sys.path would make `import scripts` work again."""
    offenders = []
    for path in sorted((ROOT / "helm" / "scripts").rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        if "sys.path.insert(0, str(ROOT))" in text or "sys.path.insert(0, str(_ROOT))" in text:
            offenders.append(str(path.relative_to(ROOT)))
    assert offenders == [], (
        "these files would re-expose helm/ as a sys.path root, making the shadowing "
        f"names importable again: {offenders}"
    )


def test_package_data_keys_are_all_helm_prefixed() -> None:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    package_data = data["tool"]["setuptools"]["package-data"]
    assert all(key.startswith("helm.") for key in package_data), package_data


def test_the_breaking_change_is_a_major_version() -> None:
    """443 imports moved with no compat shim — that is 1.0, not 0.13.1."""
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert data["project"]["version"] == "1.0.0"

    import helm

    assert helm.__version__ == "1.0.0"


def test_release_version_check_passes() -> None:
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, str(ROOT / "helm" / "scripts" / "release_version_check.py"),
         "--root", str(ROOT)],
        capture_output=True, text=True, cwd=str(ROOT), check=False,
    )
    assert result.returncode == 0, result.stderr
