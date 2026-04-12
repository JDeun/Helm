# Router Context Hydration

Upper-layer router skills should not rely only on the current user message when durable context or recent operational state may matter.

Before routing, use `scripts/ops_memory_query.py` to hydrate relevant context from:

- `MEMORY.md`
- daily notes under `memory/`
- ontology entities and relations
- task ledger
- command log
- checkpoints

## Rule

Do a focused read before tool selection whenever one of these is true:

- the task may depend on an existing plan, directive, or prior preference
- the task may continue or correct a recent failed workflow
- the task may touch reminders, recurring operations, or existing records
- the task may conflict with previously saved constraints

Skip the query only when the user asks a clearly stand-alone one-shot question and there is no realistic value in historical state.

## Query style

Prefer narrow, domain-biased reads instead of one giant search.

Examples:

```bash
python3 ~/.openclaw/workspace/scripts/ops_memory_query.py travel --include memory ontology tasks --limit 8
python3 ~/.openclaw/workspace/scripts/ops_memory_query.py ledger --include memory ontology tasks commands --limit 8
python3 ~/.openclaw/workspace/scripts/ops_memory_query.py subway --include memory ontology tasks --limit 6
python3 ~/.openclaw/workspace/scripts/ops_memory_query.py --include tasks commands --failed-only --limit 6
```

## What to look for

- active directives or user preferences in ontology
- recent daily-note facts in the same domain
- the latest successful or failed task in the same workflow
- command-level failures that explain why a provider or wrapper should be avoided
- checkpoints when the user is asking to continue or undo risky edits

## Output discipline

Do not dump the whole retrieved context back to the user.
Use it to improve routing, avoid repeated mistakes, and surface only the few facts that materially affect the answer.
