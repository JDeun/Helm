# Router Context Hydration

Upper-layer router skills should not rely only on the current user message when durable context or recent operational state may matter.

Before routing, use `helm context` or `scripts/ops_memory_query.py` to hydrate relevant context from:

- notes and curated memory files
- file-native memory under `memory/`
- ontology entities and relations
- task ledger
- command log
- checkpoints
- adopted external workspaces registered through `helm adopt`

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
helm context --path ~/.helm/workspace travel --include notes memory ontology tasks --limit 8
helm context --path ~/.helm/workspace ledger --include notes memory ontology tasks commands --limit 8
helm context --path ~/.helm/workspace subway --include notes memory ontology tasks --limit 6
helm context --path ~/.helm/workspace --include tasks commands --failed-only --limit 6
helm context --path ~/.helm/workspace --adapter openclaw-main --include notes tasks commands --limit 6
```

## What to look for

- active directives or user preferences in ontology
- recent daily-note facts in the same domain
- the latest successful or failed task in the same workflow
- command-level failures that explain why a provider or wrapper should be avoided
- checkpoints when the user is asking to continue or undo risky edits
- whether the decisive context lives in Helm-local state or an adopted external source

## Output discipline

Do not dump the whole retrieved context back to the user.
Use it to improve routing, avoid repeated mistakes, and surface only the few facts that materially affect the answer.
