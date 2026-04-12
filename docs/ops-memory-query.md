# Ops/Memory Unified Query

Use `scripts/ops_memory_query.py` when you need one query surface across:

- `MEMORY.md` and daily notes
- ontology entities and relations
- `.openclaw/task-ledger.jsonl`
- `.openclaw/command-log.jsonl`
- `.openclaw/checkpoints/index.json`

## Why

The ontology already stores durable entities and relations, but operational state also lives in task, command, and checkpoint records. This query tool closes that gap by returning all of them in one normalized result stream.

## Examples

Recent cross-layer context:

```bash
python3 ~/.openclaw/workspace/scripts/ops_memory_query.py
```

Search Hermes-related context everywhere:

```bash
python3 ~/.openclaw/workspace/scripts/ops_memory_query.py Hermes
```

Use a router-friendly preset:

```bash
python3 ~/.openclaw/workspace/scripts/ops_memory_query.py --mode travel
python3 ~/.openclaw/workspace/scripts/ops_memory_query.py --mode wealth
python3 ~/.openclaw/workspace/scripts/ops_memory_query.py --mode local
python3 ~/.openclaw/workspace/scripts/ops_memory_query.py --mode kservice
python3 ~/.openclaw/workspace/scripts/ops_memory_query.py --mode failures
python3 ~/.openclaw/workspace/scripts/ops_memory_query.py --mode rollback
```

Search only ontology and memory:

```bash
python3 ~/.openclaw/workspace/scripts/ops_memory_query.py travel --include ontology memory
```

Inspect recent failed operations:

```bash
python3 ~/.openclaw/workspace/scripts/ops_memory_query.py --include tasks commands --failed-only
```

Trace one task end-to-end:

```bash
python3 ~/.openclaw/workspace/scripts/ops_memory_query.py \
  --include tasks commands \
  --task-id <task-id> \
  --json
```

Find everything linked to one ontology entity:

```bash
python3 ~/.openclaw/workspace/scripts/ops_memory_query.py \
  --include ontology \
  --entity person_kevin
```

## Notes

- `--describe-modes` prints all built-in presets with their default sources and query bias.
- `--latest-tasks` is useful when you want one row per task instead of queued/running/completed transitions.
- `--since` accepts simple lexical timestamps such as `2026-04-12` or full ISO-like prefixes.
- This is read-only. It does not mutate memory, ontology, or task state.
