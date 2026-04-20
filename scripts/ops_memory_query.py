#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from helm_context import ContextSource, configured_context_sources
from helm_workspace import get_workspace_layout


WORKSPACE = get_workspace_layout().root
SOURCE_CHOICES = ("notes", "memory", "ontology", "tasks", "commands", "checkpoints")
MODE_PRESETS = {
    "travel": {
        "query": "travel",
        "include": ["notes", "memory", "ontology", "tasks"],
        "description": "Travel, itinerary, reminder, and trip state.",
    },
    "wealth": {
        "query": "ledger",
        "include": ["notes", "memory", "ontology", "tasks", "commands"],
        "description": "Ledger, obligations, market checks, and wealth operations.",
    },
    "local": {
        "query": "cafe",
        "include": ["notes", "memory", "ontology", "tasks", "commands"],
        "description": "Nearby discovery preferences, trip-adjacent venue context, and provider failures.",
    },
    "kservice": {
        "query": "subway",
        "include": ["notes", "memory", "ontology", "tasks", "commands"],
        "description": "Korean daily-service directives, utilities, and provider failures.",
    },
    "failures": {
        "query": None,
        "include": ["tasks", "commands"],
        "description": "Recent failed operational traces.",
    },
    "rollback": {
        "query": None,
        "include": ["tasks", "checkpoints"],
        "description": "Risky tasks and nearby checkpoints for recovery planning.",
    },
}


@dataclass
class SearchResult:
    adapter: str
    adapter_kind: str
    source: str
    kind: str
    timestamp: str | None
    title: str
    excerpt: str
    metadata: dict


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows: list[dict] = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            print(f"warning: ignoring malformed JSONL line {lineno} in {path}: {exc}", file=sys.stderr)
            continue
        if not isinstance(payload, dict):
            print(f"warning: ignoring non-object JSONL line {lineno} in {path}", file=sys.stderr)
            continue
        rows.append(payload)
    return rows


def read_json_array(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, list):
        return []
    return [item for item in payload if isinstance(item, dict)]


def apply_mode_defaults(args: argparse.Namespace) -> None:
    if not args.mode:
        return
    preset = MODE_PRESETS[args.mode]
    if args.query is None and preset.get("query") is not None:
        args.query = preset["query"]
    if not args.include:
        args.include = list(preset["include"])
    if args.mode == "failures":
        args.failed_only = True


def matches_query(blob: str, query: str | None) -> bool:
    if not query:
        return True
    return query.casefold() in blob.casefold()


def matches_skill(metadata: dict, skill: str | None) -> bool:
    if not skill:
        return True
    return metadata.get("skill") == skill or metadata.get("task_skill") == skill


def matches_task_id(metadata: dict, task_id: str | None) -> bool:
    if not task_id:
        return True
    return metadata.get("task_id") == task_id


def matches_entity(metadata: dict, entity: str | None) -> bool:
    if not entity:
        return True
    return entity in {
        metadata.get("entity_id"),
        metadata.get("from"),
        metadata.get("to"),
    }


def matches_since(timestamp: str | None, since: str | None) -> bool:
    if not since or not timestamp:
        return True
    return timestamp >= since


def result_for(source: ContextSource, area: str, kind: str, timestamp: str | None, title: str, excerpt: str, metadata: dict) -> SearchResult:
    enriched = dict(metadata)
    enriched.setdefault("workspace", str(source.root))
    return SearchResult(
        adapter=source.name,
        adapter_kind=source.kind,
        source=area,
        kind=kind,
        timestamp=timestamp,
        title=title,
        excerpt=excerpt,
        metadata=enriched,
    )


def latest_tasks(entries: list[dict]) -> list[dict]:
    by_task: dict[str, dict] = {}
    for entry in entries:
        task_id = entry.get("task_id")
        if task_id:
            by_task[task_id] = entry
    return list(by_task.values())


def text_files_under(root: Path) -> list[Path]:
    if not root.exists():
        return []
    files: list[Path] = []
    for pattern in ("*.md", "*.txt", "*.json", "*.jsonl"):
        files.extend(sorted(root.rglob(pattern)))
    return sorted(dict.fromkeys(files))


def load_note_results(source: ContextSource, args: argparse.Namespace) -> Iterable[SearchResult]:
    files: list[Path] = []
    files.extend(source.curated_memory_files)
    for root in source.notes_roots:
        files.extend(text_files_under(root))
    for path in sorted(dict.fromkeys(files)):
        if not path.exists() or not path.is_file():
            continue
        if source.ontology_root in path.parents:
            continue
        relpath = path.relative_to(source.root)
        for lineno, line in enumerate(path.read_text(encoding="utf-8", errors="ignore").splitlines(), start=1):
            stripped = line.strip()
            if not stripped:
                continue
            if not matches_query(stripped, args.query):
                continue
            timestamp = None
            if path.parent.name == "memory" and path.stem[:4].isdigit():
                timestamp = path.stem
            yield result_for(
                source,
                "notes",
                "note-line",
                timestamp,
                str(relpath),
                stripped,
                {"path": str(relpath), "line": lineno},
            )


def load_memory_results(source: ContextSource, args: argparse.Namespace) -> Iterable[SearchResult]:
    memory_root = source.memory_root
    if not memory_root.exists():
        return
    for path in sorted(memory_root.rglob("*")):
        if not path.is_file():
            continue
        if source.ontology_root in path.parents:
            continue
        relpath = path.relative_to(source.root)
        if path.suffix in {".json", ".jsonl"}:
            blob = path.read_text(encoding="utf-8", errors="ignore")
            if not matches_query(blob, args.query):
                continue
            yield result_for(source, "memory", "structured-file", None, str(relpath), blob[:240], {"path": str(relpath)})
            continue
        if path.suffix not in {".md", ".txt"}:
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8", errors="ignore").splitlines(), start=1):
            stripped = line.strip()
            if not stripped:
                continue
            if not matches_query(stripped, args.query):
                continue
            yield result_for(
                source,
                "memory",
                "memory-line",
                None,
                str(relpath),
                stripped,
                {"path": str(relpath), "line": lineno},
            )


def load_ontology_results(source: ContextSource, args: argparse.Namespace) -> Iterable[SearchResult]:
    for entity in read_jsonl(source.ontology_root / "entities.jsonl"):
        properties = entity.get("properties", {})
        blob = json.dumps(entity, ensure_ascii=False)
        if not matches_query(blob, args.query):
            continue
        metadata = {
            "entity_id": entity.get("id"),
            "entity_type": entity.get("type"),
            "name": properties.get("name"),
            "status": properties.get("status"),
        }
        if not matches_entity(metadata, args.entity):
            continue
        excerpt = properties.get("notes") or properties.get("description") or blob
        yield result_for(
            source,
            "ontology",
            "entity",
            properties.get("captured_at") or properties.get("acquired_date"),
            f"{entity.get('id')} ({entity.get('type')})",
            excerpt,
            metadata,
        )

    for relation in read_jsonl(source.ontology_root / "relations.jsonl"):
        blob = json.dumps(relation, ensure_ascii=False)
        if not matches_query(blob, args.query):
            continue
        metadata = {
            "from": relation.get("from"),
            "to": relation.get("to"),
            "relation_type": relation.get("relation_type"),
        }
        if not matches_entity(metadata, args.entity):
            continue
        yield result_for(
            source,
            "ontology",
            "relation",
            None,
            f"{relation.get('from')} {relation.get('relation_type')} {relation.get('to')}",
            json.dumps(relation.get("properties", {}), ensure_ascii=False),
            metadata,
        )


def load_task_results(source: ContextSource, args: argparse.Namespace) -> Iterable[SearchResult]:
    entries = read_jsonl(source.state_root / "task-ledger.jsonl")
    if args.latest_tasks:
        entries = latest_tasks(entries)
    for entry in entries:
        blob = json.dumps(entry, ensure_ascii=False)
        if not matches_query(blob, args.query):
            continue
        if not matches_skill(entry, args.skill):
            continue
        if not matches_task_id(entry, args.task_id):
            continue
        if args.failed_only and entry.get("status") != "failed":
            continue
        timestamp = entry.get("finished_at") or entry.get("started_execution_at") or entry.get("started_at")
        if not matches_since(timestamp, args.since):
            continue
        yield result_for(
            source,
            "tasks",
            "task",
            timestamp,
            entry.get("task_name", "-"),
            (
                f"skill={entry.get('skill') or '-'} "
                f"profile={entry.get('profile') or '-'} "
                f"status={entry.get('status') or '-'} "
                f"runtime={entry.get('runtime_backend') or entry.get('backend') or '-'} "
                f"command={entry.get('command_preview') or entry.get('command')}"
            ),
            {
                "task_id": entry.get("task_id"),
                "skill": entry.get("skill"),
                "profile": entry.get("profile"),
                "status": entry.get("status"),
                "delivery_mode": entry.get("delivery_mode"),
                "exit_code": entry.get("exit_code"),
                "runtime_backend": entry.get("runtime_backend") or entry.get("backend"),
                "runtime_target": entry.get("runtime_target"),
                "checkpoint_id": entry.get("checkpoint_id"),
            },
        )


def load_command_results(source: ContextSource, args: argparse.Namespace) -> Iterable[SearchResult]:
    for entry in read_jsonl(source.state_root / "command-log.jsonl"):
        blob = json.dumps(entry, ensure_ascii=False)
        if not matches_query(blob, args.query):
            continue
        if not matches_skill(entry, args.skill):
            continue
        if not matches_task_id(entry, args.task_id):
            continue
        if args.failed_only and entry.get("exit_code") in (0, None):
            continue
        timestamp = entry.get("finished_at") or entry.get("started_at")
        if not matches_since(timestamp, args.since):
            continue
        yield result_for(
            source,
            "commands",
            "command",
            timestamp,
            entry.get("label") or "command",
            " ".join(entry.get("command", [])),
            {
                "task_id": entry.get("task_id"),
                "task_skill": entry.get("task_skill") or entry.get("skill"),
                "task_profile": entry.get("task_profile") or entry.get("profile"),
                "component": entry.get("component"),
                "exit_code": entry.get("exit_code"),
            },
        )


def load_checkpoint_results(source: ContextSource, args: argparse.Namespace) -> Iterable[SearchResult]:
    index_path = source.state_root / "checkpoints" / "index.json"
    records = read_json_array(index_path)
    for record in records:
        blob = json.dumps(record, ensure_ascii=False)
        if not matches_query(blob, args.query):
            continue
        timestamp = record.get("created_at")
        if not matches_since(timestamp, args.since):
            continue
        yield result_for(
            source,
            "checkpoints",
            "checkpoint",
            timestamp,
            record.get("checkpoint_id", "checkpoint"),
            f"label={record.get('label')} paths={', '.join(record.get('paths', []))}",
            {
                "label": record.get("label"),
                "paths": record.get("paths", []),
                "archive": record.get("archive"),
            },
        )


def collect_results(args: argparse.Namespace) -> list[SearchResult]:
    selected = set(args.include)
    results: list[SearchResult] = []
    sources = configured_context_sources(WORKSPACE)
    if args.adapter:
        sources = [source for source in sources if source.name == args.adapter]
    loaders = {
        "notes": load_note_results,
        "memory": load_memory_results,
        "ontology": load_ontology_results,
        "tasks": load_task_results,
        "commands": load_command_results,
        "checkpoints": load_checkpoint_results,
    }
    for source in sources:
        for area in SOURCE_CHOICES:
            if area not in selected:
                continue
            results.extend(loaders[area](source, args))

    query_blob = args.query.casefold() if args.query else None

    def query_score(item: SearchResult) -> int:
        if not query_blob:
            return 0
        haystacks = [
            item.title.casefold(),
            item.excerpt.casefold(),
            json.dumps(item.metadata, ensure_ascii=False).casefold(),
        ]
        exact_hits = sum(blob.count(query_blob) for blob in haystacks)
        token_hits = 0
        for token in query_blob.split():
            if len(token) < 2:
                continue
            token_hits += sum(blob.count(token) for blob in haystacks)
        return exact_hits * 10 + token_hits

    def source_priority(item: SearchResult) -> int:
        return {
            "notes": 60,
            "memory": 50,
            "ontology": 40,
            "tasks": 30,
            "commands": 20,
            "checkpoints": 10,
        }.get(item.source, 0)

    def adapter_priority(item: SearchResult) -> int:
        if item.adapter == "helm-local":
            return 20
        if item.adapter_kind in {"openclaw", "hermes"}:
            return 10
        return 0

    results.sort(
        key=lambda item: (
            query_score(item),
            adapter_priority(item),
            source_priority(item),
            item.timestamp or "",
            item.title,
        ),
        reverse=not args.ascending,
    )
    return results[: args.limit]


def summarize_results(results: list[SearchResult]) -> dict:
    summary = {
        "total": len(results),
        "by_adapter": {},
        "by_source": {},
        "by_kind": {},
    }
    for item in results:
        summary["by_adapter"][item.adapter] = summary["by_adapter"].get(item.adapter, 0) + 1
        summary["by_source"][item.source] = summary["by_source"].get(item.source, 0) + 1
        summary["by_kind"][item.kind] = summary["by_kind"].get(item.kind, 0) + 1
    return summary


def print_results(results: list[SearchResult], json_output: bool) -> None:
    if json_output:
        print(json.dumps([asdict(item) for item in results], indent=2, ensure_ascii=False))
        return
    for item in results:
        print(f"[{item.adapter}:{item.source}:{item.kind}] {item.timestamp or '-'} {item.title}")
        print(f"  {item.excerpt}")
        if item.metadata:
            print(f"  meta={json.dumps(item.metadata, ensure_ascii=False, sort_keys=True)}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Query Helm and adopted external context sources through one file-native interface."
    )
    parser.add_argument("query", nargs="?", help="Free-text query. If omitted, returns recent items from selected sources.")
    parser.add_argument("--mode", choices=sorted(MODE_PRESETS.keys()), help="Apply a router-friendly preset.")
    parser.add_argument("--describe-modes", action="store_true", help="List built-in mode presets and exit.")
    parser.add_argument("--include", nargs="+", choices=SOURCE_CHOICES, default=None)
    parser.add_argument("--adapter", help="Restrict search to one registered context source name.")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--skill", help="Filter task or command results by skill name.")
    parser.add_argument("--task-id", help="Filter task or command results by task_id.")
    parser.add_argument("--entity", help="Filter ontology relations/entities by entity id.")
    parser.add_argument("--since", help="Lower bound for timestamps, e.g. 2026-04-12 or 2026-04-12T09:00.")
    parser.add_argument("--failed-only", action="store_true")
    parser.add_argument("--latest-tasks", action="store_true", help="Collapse task ledger entries to latest state per task_id.")
    parser.add_argument("--ascending", action="store_true", help="Sort oldest first instead of newest first.")
    parser.add_argument("--summary", action="store_true", help="Print adapter/source summary before detailed results.")
    parser.add_argument("--json", action="store_true")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.describe_modes:
        print(json.dumps(MODE_PRESETS, indent=2, ensure_ascii=False))
        return 0
    apply_mode_defaults(args)
    if args.include is None:
        args.include = list(SOURCE_CHOICES)
    results = collect_results(args)
    if not results:
        print("No results matched the query.")
        return 0
    if args.summary and not args.json:
        print(json.dumps(summarize_results(results), ensure_ascii=False, sort_keys=True))
    print_results(results, args.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
