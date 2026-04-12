#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable


WORKSPACE = Path.home() / ".openclaw" / "workspace"
MEMORY_ROOT = WORKSPACE / "memory"
ONTOLOGY_ROOT = MEMORY_ROOT / "ontology"
STATE_ROOT = WORKSPACE / ".openclaw"

SOURCE_CHOICES = ("memory", "ontology", "tasks", "commands", "checkpoints")
MODE_PRESETS = {
    "travel": {
        "query": "travel",
        "include": ["memory", "ontology", "tasks"],
        "description": "Travel, itinerary, reminder, and trip state.",
    },
    "wealth": {
        "query": "ledger",
        "include": ["memory", "ontology", "tasks", "commands"],
        "description": "Ledger, obligations, market checks, and wealth operations.",
    },
    "local": {
        "query": "cafe",
        "include": ["memory", "ontology", "tasks", "commands"],
        "description": "Nearby discovery preferences, trip-adjacent venue context, and provider failures.",
    },
    "kservice": {
        "query": "subway",
        "include": ["memory", "ontology", "tasks", "commands"],
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
    source: str
    kind: str
    timestamp: str | None
    title: str
    excerpt: str
    metadata: dict


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


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


def load_memory_results(args: argparse.Namespace) -> Iterable[SearchResult]:
    files: list[Path] = []
    curated = WORKSPACE / "MEMORY.md"
    if curated.exists():
        files.append(curated)
    if MEMORY_ROOT.exists():
        files.extend(sorted(MEMORY_ROOT.glob("*.md")))

    for path in files:
        relpath = path.relative_to(WORKSPACE)
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            stripped = line.strip()
            if not stripped:
                continue
            if not matches_query(stripped, args.query):
                continue
            timestamp = None
            if path.parent == MEMORY_ROOT and path.stem[:4].isdigit():
                timestamp = path.stem
            yield SearchResult(
                source="memory",
                kind="note-line",
                timestamp=timestamp,
                title=str(relpath),
                excerpt=stripped,
                metadata={"path": str(relpath), "line": lineno},
            )


def load_ontology_results(args: argparse.Namespace) -> Iterable[SearchResult]:
    for entity in read_jsonl(ONTOLOGY_ROOT / "entities.jsonl"):
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
        yield SearchResult(
            source="ontology",
            kind="entity",
            timestamp=properties.get("captured_at") or properties.get("acquired_date"),
            title=f"{entity.get('id')} ({entity.get('type')})",
            excerpt=excerpt,
            metadata=metadata,
        )

    for relation in read_jsonl(ONTOLOGY_ROOT / "relations.jsonl"):
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
        yield SearchResult(
            source="ontology",
            kind="relation",
            timestamp=None,
            title=f"{relation.get('from')} {relation.get('relation_type')} {relation.get('to')}",
            excerpt=json.dumps(relation.get("properties", {}), ensure_ascii=False),
            metadata=metadata,
        )


def latest_tasks(entries: list[dict]) -> list[dict]:
    by_task: dict[str, dict] = {}
    for entry in entries:
        task_id = entry.get("task_id")
        if task_id:
            by_task[task_id] = entry
    return list(by_task.values())


def load_task_results(args: argparse.Namespace) -> Iterable[SearchResult]:
    entries = read_jsonl(STATE_ROOT / "task-ledger.jsonl")
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
        yield SearchResult(
            source="tasks",
            kind="task",
            timestamp=timestamp,
            title=entry.get("task_name", "-"),
            excerpt=(
                f"skill={entry.get('skill') or '-'} "
                f"profile={entry.get('profile') or '-'} "
                f"status={entry.get('status') or '-'} "
                f"runtime={entry.get('runtime_backend') or entry.get('backend') or '-'} "
                f"command={entry.get('command_preview') or entry.get('command')}"
            ),
            metadata={
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


def load_command_results(args: argparse.Namespace) -> Iterable[SearchResult]:
    entries = read_jsonl(STATE_ROOT / "command-log.jsonl")
    for entry in entries:
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
        yield SearchResult(
            source="commands",
            kind="command",
            timestamp=timestamp,
            title=entry.get("label") or "command",
            excerpt=" ".join(entry.get("command", [])),
            metadata={
                "task_id": entry.get("task_id"),
                "task_skill": entry.get("task_skill"),
                "task_profile": entry.get("task_profile"),
                "component": entry.get("component"),
                "exit_code": entry.get("exit_code"),
            },
        )


def load_checkpoint_results(args: argparse.Namespace) -> Iterable[SearchResult]:
    index_path = STATE_ROOT / "checkpoints" / "index.json"
    if not index_path.exists():
        return
    records = json.loads(index_path.read_text(encoding="utf-8"))
    for record in records:
        blob = json.dumps(record, ensure_ascii=False)
        if not matches_query(blob, args.query):
            continue
        timestamp = record.get("created_at")
        if not matches_since(timestamp, args.since):
            continue
        yield SearchResult(
            source="checkpoints",
            kind="checkpoint",
            timestamp=timestamp,
            title=record.get("checkpoint_id", "checkpoint"),
            excerpt=f"label={record.get('label')} paths={', '.join(record.get('paths', []))}",
            metadata={
                "label": record.get("label"),
                "paths": record.get("paths", []),
                "archive": record.get("archive"),
            },
        )


def collect_results(args: argparse.Namespace) -> list[SearchResult]:
    selected = set(args.include)
    results: list[SearchResult] = []
    if "memory" in selected:
        results.extend(load_memory_results(args))
    if "ontology" in selected:
        results.extend(load_ontology_results(args))
    if "tasks" in selected:
        results.extend(load_task_results(args))
    if "commands" in selected:
        results.extend(load_command_results(args))
    if "checkpoints" in selected:
        results.extend(load_checkpoint_results(args))

    results.sort(key=lambda item: (item.timestamp or "", item.source, item.title))
    if not args.ascending:
        results.reverse()
    return results[: args.limit]


def print_results(results: list[SearchResult], json_output: bool) -> None:
    if json_output:
        print(json.dumps([asdict(item) for item in results], indent=2, ensure_ascii=False))
        return
    for item in results:
        print(f"[{item.source}:{item.kind}] {item.timestamp or '-'} {item.title}")
        print(f"  {item.excerpt}")
        if item.metadata:
            print(f"  meta={json.dumps(item.metadata, ensure_ascii=False, sort_keys=True)}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Query OpenClaw memory, ontology, task ledger, command log, and checkpoints with one interface."
    )
    parser.add_argument("query", nargs="?", help="Free-text query. If omitted, returns recent items from selected sources.")
    parser.add_argument("--mode", choices=sorted(MODE_PRESETS.keys()), help="Apply a router-friendly preset.")
    parser.add_argument("--describe-modes", action="store_true", help="List built-in mode presets and exit.")
    parser.add_argument("--include", nargs="+", choices=SOURCE_CHOICES, default=None)
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--skill", help="Filter task or command results by skill name.")
    parser.add_argument("--task-id", help="Filter task or command results by task_id.")
    parser.add_argument("--entity", help="Filter ontology relations/entities by entity id.")
    parser.add_argument("--since", help="Lower bound for timestamps, e.g. 2026-04-12 or 2026-04-12T09:00.")
    parser.add_argument("--failed-only", action="store_true")
    parser.add_argument("--latest-tasks", action="store_true", help="Collapse task ledger entries to latest state per task_id.")
    parser.add_argument("--ascending", action="store_true", help="Sort oldest first instead of newest first.")
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
    print_results(results, args.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
