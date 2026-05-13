#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


PYPROJECT_VERSION_RE = re.compile(r"^version\s*=\s*[\"']([^\"']+)[\"']\s*$", re.MULTILINE)
CITATION_VERSION_RE = re.compile(r"^version:\s*[\"']?([^\"'\n]+)[\"']?\s*$", re.MULTILINE)
README_RELEASE_RE = re.compile(r"(?:Current release|현재 릴리즈):\s*v([0-9]+\.[0-9]+\.[0-9]+)")
CHANGELOG_SECTION_RE = re.compile(r"^## \[([0-9]+\.[0-9]+\.[0-9]+)\]", re.MULTILINE)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check Helm release version consistency.")
    parser.add_argument("--root", default=".", help="Repository root.")
    parser.add_argument("--version", help="Expected release version. Defaults to pyproject.toml.")
    return parser.parse_args()


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def pyproject_version(root: Path) -> str:
    return first_match(PYPROJECT_VERSION_RE, read_text(root / "pyproject.toml"), "pyproject.toml")


def first_match(pattern: re.Pattern[str], text: str, label: str) -> str:
    match = pattern.search(text)
    if not match:
        raise ValueError(f"{label}: version not found")
    return match.group(1)


def collect_versions(root: Path) -> dict[str, str]:
    return {
        "pyproject.toml": pyproject_version(root),
        "CITATION.cff": first_match(CITATION_VERSION_RE, read_text(root / "CITATION.cff"), "CITATION.cff"),
        "README.md": first_match(README_RELEASE_RE, read_text(root / "README.md"), "README.md"),
        "README.ko.md": first_match(README_RELEASE_RE, read_text(root / "README.ko.md"), "README.ko.md"),
    }


def check_release(root: Path, expected_version: str | None = None) -> list[str]:
    root = root.resolve()
    try:
        versions = collect_versions(root)
    except ValueError as exc:
        return [str(exc)]
    expected = expected_version or versions["pyproject.toml"]
    errors: list[str] = []

    for label, version in versions.items():
        if version != expected:
            errors.append(f"{label}: {version} != {expected}")

    setup_py = root / "setup.py"
    if setup_py.exists():
        errors.append("setup.py: remove legacy version-bearing packaging shim; pyproject.toml is the package version source")

    release_note = root / "docs" / "releases" / f"{expected}.md"
    if not release_note.exists():
        errors.append(f"{release_note.relative_to(root)}: missing release note")

    changelog = read_text(root / "CHANGELOG.md")
    if expected not in CHANGELOG_SECTION_RE.findall(changelog):
        errors.append(f"CHANGELOG.md: missing [{expected}] release section")

    return errors


def main() -> int:
    args = parse_args()
    errors = check_release(Path(args.root), expected_version=args.version)
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    print("release version check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
