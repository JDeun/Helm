from __future__ import annotations

import functools
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


@functools.lru_cache(maxsize=4)
def _suggest_external_sources_cached(base_str: str) -> dict[str, tuple[Path, ...]]:
    """Cache the disk-walk portion of ``suggest_external_sources``.

    ``helm detect`` and ``helm doctor`` both call ``suggest_external_sources``,
    and a user with a large ``~/Documents`` tree pays the iterdir + .obsidian
    probe cost on every call. The result rarely changes within a single
    process lifetime, so cache by resolved-base path. Stored as tuples so
    the cached value is immutable; the public function returns lists to
    keep the API shape.
    """
    base = Path(base_str)
    raw = _suggest_external_sources_uncached(base)
    return {key: tuple(value) for key, value in raw.items()}


def _suggest_external_sources_uncached(base: Path) -> dict[str, list[Path]]:
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
        if not parent.is_dir():
            continue
        if (parent / ".obsidian").exists():
            obsidian_vaults.append(parent)
            continue
        try:
            children = list(parent.iterdir())
        except OSError:
            continue
        for child in children:
            if child.is_dir() and (child / ".obsidian").exists():
                obsidian_vaults.append(child.resolve())
    suggestions["obsidian"] = _prune_nested(list(dict.fromkeys(obsidian_vaults)))
    return suggestions


def suggest_external_sources(home: Path | None = None) -> dict[str, list[Path]]:
    base = (home or Path.home()).expanduser().resolve()
    cached = _suggest_external_sources_cached(str(base))
    # Return a fresh dict[list] each call so callers can mutate safely.
    return {key: list(value) for key, value in cached.items()}


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


# Cache the discovery walk for the lifetime of the process. A single
# ``helm`` invocation imports several ``scripts.*`` modules transitively,
# each of which calls ``get_workspace_layout()`` at import time. Without
# the cache each call re-walks ``Path.cwd()`` parents and re-probes
# workspace markers; for a 4-7 module CLI invocation that's a meaningful
# fraction of cold-start latency (R0 #5/#9 / R2 I4).
#
# Test-time monkey-patch contract: a test that mutates the environment
# in a way that should affect discovery (``HELM_WORKSPACE``, cwd, marker
# files on disk) MUST call ``get_workspace_layout.cache_clear()`` (or
# the convenience wrapper ``clear_workspace_layout_cache()``) before
# expecting fresh discovery. The existing test suite uses subprocesses
# for ``HELM_WORKSPACE`` variation, so the in-process cache does not
# interfere; this constraint is documented for any future test that
# wants to swap the env mid-process.
@functools.lru_cache(maxsize=1)
def get_workspace_layout() -> WorkspaceLayout:
    return discover_workspace()


def clear_workspace_layout_cache() -> None:
    """Invalidate the cached layout returned by :func:`get_workspace_layout`.

    Provided for tests that change ``HELM_WORKSPACE`` / cwd / on-disk
    markers mid-process and need the next call to re-walk.
    """
    get_workspace_layout.cache_clear()
