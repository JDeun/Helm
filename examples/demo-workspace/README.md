# Demo Workspace

This example workspace is a runnable Helm-native layout for demos, smoke tests, and screenshots.

## What it contains

- `.helm/` runtime state with sample task, command, and checkpoint records
- `memory/` notes and ontology files
- `references/` profile and policy files copied from the repo defaults
- `skills/` and `skill_drafts/` directories to mirror a real workspace layout

## Suggested commands

```bash
helm doctor --path examples/demo-workspace
helm status --path examples/demo-workspace --verbose
helm report --path examples/demo-workspace --format markdown
helm context --path examples/demo-workspace --include notes tasks commands --limit 8
helm checkpoint-recommend --path examples/demo-workspace
```

