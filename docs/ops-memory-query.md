# Ops/Memory Unified Query

Use `scripts/ops_memory_query.py` when you need one query surface across:

- notes, `MEMORY.md`, and daily notes
- ontology entities and relations
- Helm task / command / checkpoint state
- adopted external workspaces such as OpenClaw or Hermes

## Why

The ontology already stores durable entities and relations, but operational state also lives in task, command, and checkpoint records. This query tool closes that gap by returning all of them in one normalized result stream.

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
```

Print a quick summary before the detailed rows:

```bash
helm context --path ~/.helm/workspace --include notes tasks commands --summary
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
- `--summary` prints adapter/source/kind counts before the detailed rows.
- This is read-only. It does not mutate memory, ontology, or task state.
