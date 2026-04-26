# Helm First Run

This is the shortest path from a clean install to a useful Helm signal.

## Goal

Run one safe command through Helm, inspect the operational state it leaves behind, and confirm where later tasks will be audited.

## Quickstart Path

```bash
curl -fsSL https://raw.githubusercontent.com/JDeun/Helm/main/install.sh | bash
helm doctor --path ~/.helm/workspace
helm profile --path ~/.helm/workspace run inspect_local --task-name "first Helm inspection" -- git status --short
helm status --path ~/.helm/workspace --brief
helm report --path ~/.helm/workspace --format markdown
```

## What happened

- `install.sh` installed Helm and created the default dedicated workspace with policy files.
- `helm doctor` checked whether the workspace is structurally usable.
- `inspect_local` ran a read-oriented command under a declared execution profile.
- `helm status --brief` summarized whether the workspace needs attention.
- `helm report` rendered the recent task window as an inspectable artifact.

## First-day concepts

- Execution profiles decide the blast radius before a command runs.
- The task ledger records what happened so later runs do not depend on chat memory.
- The command guard blocks or flags dangerous command shapes before execution.
- Checkpoints give risky edits a visible recovery path.
- Memory capture decides which completed work should become durable file state.

## Later concepts

- Skill contracts
- Model health fallback policy
- External context-source adoption
- SQLite operations index
- Draft skill review and promotion
