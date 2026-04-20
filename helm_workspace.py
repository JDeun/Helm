from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


DEFAULT_WORKSPACE = Path.home() / ".helm" / "workspace"


@dataclass(frozen=True)
class WorkspaceLayout:
    root: Path
    kind: str
    source: str
    markers: tuple[str, ...]
    state_dir_name: str

    @property
    def state_root(self) -> Path:
        return self.root / self.state_dir_name

    @property
    def checkpoints_root(self) -> Path:
        return self.state_root / "checkpoints"


def _normalized(path: Path) -> Path:
    return path.expanduser().resolve()


def _marker_matches(root: Path, relative_paths: tuple[str, ...]) -> list[str]:
    matches: list[str] = []
    for relative in relative_paths:
        if (root / relative).exists():
            matches.append(relative)
    return matches


def _existing_paths(candidates: tuple[Path, ...]) -> list[Path]:
    return [path.expanduser().resolve() for path in candidates if path.expanduser().exists()]


def _state_artifact_paths(root: Path, state_dir_name: str) -> tuple[Path, ...]:
    state_root = root / state_dir_name
    return (
        state_root / "task-ledger.jsonl",
        state_root / "command-log.jsonl",
        state_root / "checkpoints",
        state_root / "checkpoints" / "index.json",
    )


def _has_state_artifacts(root: Path, state_dir_name: str) -> bool:
    return any(path.exists() for path in _state_artifact_paths(root, state_dir_name))


def _prune_nested(paths: list[Path]) -> list[Path]:
    ordered = sorted(dict.fromkeys(paths), key=lambda path: len(path.parts), reverse=True)
    kept: list[Path] = []
    for path in ordered:
        if any(path in existing.parents for existing in kept):
            continue
        kept.append(path)
    return kept


def suggest_external_sources(home: Path | None = None) -> dict[str, list[Path]]:
    base = (home or Path.home()).expanduser().resolve()
    suggestions = {
        "openclaw": _existing_paths(
            (
                base / ".openclaw" / "workspace",
                base / ".openclaw",
                base / "openclaw",
                base / "OpenClaw",
            )
        ),
        "hermes": _existing_paths(
            (
                base / ".hermes",
                base / "hermes",
                base / "Hermes",
            )
        ),
        "obsidian": _existing_paths(
            (
                base / "Obsidian",
                base / "Vaults",
                base / "Documents" / "Obsidian",
                base / "Documents" / "Vaults",
                base / "Documents" / "Notes",
                base / "Notes",
            )
        ),
        "obsidian_app": _existing_paths(
            (
                Path("/Applications/Obsidian.app"),
                base / "Applications" / "Obsidian.app",
            )
        ),
    }
    suggestions["openclaw"] = _prune_nested(suggestions["openclaw"])
    suggestions["hermes"] = _prune_nested(suggestions["hermes"])
    obsidian_vaults: list[Path] = []
    for parent in suggestions["obsidian"]:
        if (parent / ".obsidian").exists():
            obsidian_vaults.append(parent)
            continue
        for child in parent.iterdir():
            if child.is_dir() and (child / ".obsidian").exists():
                obsidian_vaults.append(child.resolve())
    suggestions["obsidian"] = _prune_nested(list(dict.fromkeys(obsidian_vaults)))
    return suggestions


def detect_layout(root: Path) -> WorkspaceLayout:
    resolved = _normalized(root)

    helm_markers = _marker_matches(
        resolved,
        (
            ".helm",
        ),
    )
    if helm_markers:
        return WorkspaceLayout(
            root=resolved,
            kind="helm",
            source="markers",
            markers=tuple(helm_markers),
            state_dir_name=".helm",
        )

    openclaw_markers = _marker_matches(resolved, (".openclaw",))
    if openclaw_markers and _has_state_artifacts(resolved, ".openclaw"):
        return WorkspaceLayout(
            root=resolved,
            kind="openclaw",
            source="markers",
            markers=tuple(openclaw_markers),
            state_dir_name=".openclaw",
        )

    hermes_markers = _marker_matches(
        resolved,
        (
            ".hermes",
            "hermes",
            "notes",
            "history",
        ),
    )
    if hermes_markers:
        return WorkspaceLayout(
            root=resolved,
            kind="hermes",
            source="markers",
            markers=tuple(hermes_markers),
            state_dir_name=".hermes",
        )

    generic_markers = _marker_matches(
        resolved,
        (
            "references",
            "docs",
            "scripts",
            ".obsidian",
        ),
    )
    if generic_markers:
        return WorkspaceLayout(
            root=resolved,
            kind="generic",
            source="markers",
            markers=tuple(generic_markers),
            state_dir_name=".helm",
        )

    return WorkspaceLayout(
        root=resolved,
        kind="unknown",
        source="fallback",
        markers=(),
        state_dir_name=".helm",
    )


def discover_workspace(start: Path | None = None) -> WorkspaceLayout:
    explicit = os.environ.get("HELM_WORKSPACE")
    if explicit:
        layout = detect_layout(Path(explicit))
        return WorkspaceLayout(
            root=layout.root,
            kind=layout.kind,
            source="env",
            markers=layout.markers,
            state_dir_name=layout.state_dir_name,
        )

    if start is None:
        start = Path.cwd()
    current = _normalized(start)
    for candidate in (current, *current.parents):
        layout = detect_layout(candidate)
        if layout.kind != "unknown":
            return WorkspaceLayout(
                root=layout.root,
                kind=layout.kind,
                source="cwd",
                markers=layout.markers,
                state_dir_name=layout.state_dir_name,
            )

    layout = detect_layout(DEFAULT_WORKSPACE)
    if layout.kind == "unknown":
        return WorkspaceLayout(
            root=_normalized(DEFAULT_WORKSPACE),
            kind="helm",
            source="default",
            markers=(),
            state_dir_name=".helm",
        )
    return WorkspaceLayout(
        root=layout.root,
        kind=layout.kind,
        source="default",
        markers=layout.markers,
        state_dir_name=layout.state_dir_name,
    )


def resolve_nested_workspace(root: Path) -> WorkspaceLayout | None:
    resolved = _normalized(root)
    candidates = (
        resolved / ".helm" / "workspace",
        resolved / ".openclaw" / "workspace",
    )
    for candidate in candidates:
        if not candidate.exists():
            continue
        layout = detect_layout(candidate)
        if layout.kind != "unknown":
            return WorkspaceLayout(
                root=layout.root,
                kind=layout.kind,
                source="nested",
                markers=layout.markers,
                state_dir_name=layout.state_dir_name,
            )
    return None


def get_workspace_layout() -> WorkspaceLayout:
    return discover_workspace()
