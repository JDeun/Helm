from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from helm_workspace import WorkspaceLayout, detect_layout


@dataclass(frozen=True)
class ContextSource:
    name: str
    kind: str
    root: Path
    state_dir_name: str
    mode: str = "read-only"

    @property
    def state_root(self) -> Path:
        return self.root / self.state_dir_name

    @property
    def memory_root(self) -> Path:
        return self.root / "memory"

    @property
    def ontology_root(self) -> Path:
        return self.memory_root / "ontology"

    @property
    def notes_roots(self) -> tuple[Path, ...]:
        candidates: list[Path] = []
        if (self.root / ".obsidian").exists():
            candidates.append(self.root)
        if (self.root / "notes").exists():
            candidates.append(self.root / "notes")
        if self.kind in {"helm", "openclaw"} and self.memory_root.exists():
            candidates.append(self.memory_root)
        if self.kind == "hermes":
            for relative in ("notes", "history"):
                path = self.root / relative
                if path.exists():
                    candidates.append(path)
        return tuple(dict.fromkeys(candidates))

    @property
    def curated_memory_files(self) -> tuple[Path, ...]:
        candidates: list[Path] = []
        for relative in ("MEMORY.md", "memory/README.md"):
            path = self.root / relative
            if path.exists():
                candidates.append(path)
        return tuple(candidates)

    def to_json(self) -> dict:
        payload = asdict(self)
        payload["root"] = str(self.root)
        return payload


def context_sources_path(workspace_root: Path) -> Path:
    return workspace_root / ".helm" / "context_sources.json"


def onboarding_root(workspace_root: Path) -> Path:
    return workspace_root / ".helm" / "onboarding"


def source_from_layout(layout: WorkspaceLayout, *, name: str | None = None) -> ContextSource:
    return ContextSource(
        name=name or layout.kind,
        kind=layout.kind,
        root=layout.root,
        state_dir_name=layout.state_dir_name,
    )


def load_context_sources(workspace_root: Path) -> list[ContextSource]:
    path = context_sources_path(workspace_root)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"warning: ignoring malformed context source registry {path}: {exc}", file=sys.stderr)
        return []
    if not isinstance(data, dict):
        return []
    sources: list[ContextSource] = []
    raw_sources = data.get("sources", [])
    if not isinstance(raw_sources, list):
        return []
    for item in raw_sources:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        kind = item.get("kind")
        root = item.get("root")
        state_dir_name = item.get("state_dir_name")
        if not all(isinstance(value, str) and value.strip() for value in (name, kind, root, state_dir_name)):
            continue
        sources.append(
            ContextSource(
                name=name,
                kind=kind,
                root=Path(root).expanduser().resolve(),
                state_dir_name=state_dir_name,
                mode=item.get("mode", "read-only"),
            )
        )
    return sources


def save_context_sources(workspace_root: Path, sources: list[ContextSource]) -> None:
    path = context_sources_path(workspace_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"sources": [source.to_json() for source in sources]}
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def local_helm_source(workspace_root: Path) -> ContextSource:
    layout = detect_layout(workspace_root.resolve())
    kind = layout.kind if layout.kind != "unknown" else "helm"
    return ContextSource(
        name=f"{kind}-local",
        kind=kind,
        root=workspace_root.resolve(),
        state_dir_name=layout.state_dir_name,
    )


def configured_context_sources(workspace_root: Path) -> list[ContextSource]:
    root = workspace_root.resolve()
    sources = [local_helm_source(root)]
    seen = {root}
    for source in load_context_sources(root):
        if source.root in seen:
            continue
        sources.append(source)
        seen.add(source.root)
    return sources


def adopt_context_source(workspace_root: Path, target_root: Path, *, name: str | None = None, kind: str | None = None) -> ContextSource:
    resolved_workspace = workspace_root.resolve()
    resolved_target = target_root.expanduser().resolve()
    layout = detect_layout(resolved_target)
    adopted_kind = kind or ("generic" if layout.kind == "unknown" else layout.kind)
    adopted_name = name or f"{adopted_kind}-{resolved_target.name}"
    source = ContextSource(
        name=adopted_name,
        kind=adopted_kind,
        root=resolved_target,
        state_dir_name=".helm" if adopted_kind == "generic" else layout.state_dir_name,
    )
    sources = load_context_sources(resolved_workspace)
    filtered = [item for item in sources if item.name != source.name and item.root != source.root]
    filtered.append(source)
    save_context_sources(resolved_workspace, filtered)
    onboarding_dir = onboarding_root(resolved_workspace)
    onboarding_dir.mkdir(parents=True, exist_ok=True)
    migration_note = {
        "source_name": source.name,
        "source_kind": source.kind,
        "source_root": str(source.root),
        "mode": source.mode,
        "intent": "read-only adoption",
        "next_steps": [
            "Inspect `helm context --adapter <name>` output and confirm the mapped files are useful.",
            "Decide whether this source should remain read-only or be migrated into Helm-local state later.",
            "Create explicit Helm-native notes, references, or skills instead of mutating the external workspace directly.",
        ],
    }
    (onboarding_dir / f"{source.name}.json").write_text(
        json.dumps(migration_note, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return source
