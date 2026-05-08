# Ops/Memory Unified Query

Use `scripts/ops_memory_query.py` when you need one query surface across:

- notes, `MEMORY.md`, and daily notes
- ontology entities and relations
- Helm task / command / checkpoint state
- adopted external workspaces such as OpenClaw or Hermes

## Why

The ontology already stores durable entities and relations, but operational state also lives in task, command, and checkpoint records. This query tool closes that gap by returning all of them in one normalized result stream.

For routing policy and when to run these queries, see [Router Context Hydration](./router-context-hydration.md).

## Examples

Recent cross-layer context:

```bash
helm context --path ~/.helm/workspace
```

Search Hermes-related context everywhere:

```bash
helm context --path ~/.helm/workspace Hermes
```

Use a router-friendly preset:

```bash
helm context --path ~/.helm/workspace --mode travel
helm context --path ~/.helm/workspace --mode wealth
helm context --path ~/.helm/workspace --mode local
helm context --path ~/.helm/workspace --mode kservice
helm context --path ~/.helm/workspace --mode failures
helm context --path ~/.helm/workspace --mode rollback
helm context --path ~/.helm/workspace --mode decisions
helm context --path ~/.helm/workspace --mode timeline --since 2026-05-01
helm context --path ~/.helm/workspace --mode entity --entity project_helm
helm context --path ~/.helm/workspace --mode reflect-candidates
```

Print a quick summary before the detailed rows:

```bash
helm context --path ~/.helm/workspace --include notes tasks commands --summary
```

Explain why results ranked where they did:

```bash
helm context --path ~/.helm/workspace decision --explain-ranking --json
```

Search only ontology and memory:

```bash
helm context --path ~/.helm/workspace travel --include ontology memory
```

Inspect recent failed operations:

```bash
helm context --path ~/.helm/workspace --include tasks commands --failed-only
```

Trace one task end-to-end:

```bash
helm context --path ~/.helm/workspace \
  --include tasks commands \
  --task-id <task-id> \
  --json
```

Find everything linked to one ontology entity:

```bash
helm context --path ~/.helm/workspace \
  --include ontology \
  --entity person_kevin
```

Adopt and inspect an external source:

```bash
helm adopt --path ~/.helm/workspace --from-path ~/.openclaw/workspace --name openclaw-main
helm context --path ~/.helm/workspace --adapter openclaw-main --include notes tasks commands --limit 8
```

## Notes

- `--describe-modes` prints all built-in presets with their default sources and query bias.
- `--adapter` restricts the query to one registered context source.
- `--latest-tasks` is useful when you want one row per task instead of queued/running/completed transitions.
- `--since` accepts simple lexical timestamps such as `2026-04-12` or full ISO-like prefixes.
- `--explain-ranking` adds ranking metadata with query score, adapter priority, source priority, and timestamp. This is a debugging surface, not a stable scoring contract.
- `--summary` prints adapter/source/kind counts before the detailed rows.
- This is read-only. It does not mutate memory, ontology, or task state.

## Hindsight-Inspired Roadmap

Helm's query layer is intentionally file-native and read-only. It should absorb useful memory-system ideas without becoming dependent on a specific external memory service.

The useful pattern from Hindsight is multi-strategy recall:

- lexical search for names, paths, commands, IDs, and exact phrases
- temporal search for dates, recent state, recurring tasks, and supersession history
- ontology graph traversal for entities and relations
- operational search across task ledgers, command logs, checkpoints, and memory review queues
- result fusion with visible evidence and a token budget

Current Helm behavior is simpler: it gathers matching rows from each source and sorts by query hits, adapter priority, source priority, timestamp, and title. `--explain-ranking` makes that visible so future changes can be evaluated instead of hidden behind a black-box score.

Implementation should move in small steps:

1. Keep the current exact/token scorer inspectable.
2. Add source-specific scoring metadata and tests before changing ranking behavior.
3. Extend temporal and entity-centered modes using existing timestamps and ontology JSONL.
4. Add graph-neighborhood expansion only after entity IDs are stable enough in fixtures.
5. Consider BM25/RRF only after the simpler scoring surfaces have measurable gaps.

Do not retain private OpenClaw memory into a third-party memory backend as part of this roadmap. If an external backend is evaluated, use public-safe or tokenized fixtures first.
