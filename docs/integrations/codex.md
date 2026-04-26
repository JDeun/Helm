# Codex CLI Integration

Helm does not replace Codex. It wraps local commands and workspace state around a Codex-driven workflow so repeated agent work has profiles, audit trails, checkpoints, and reports.

## Basic pattern

Use Helm for the shell work that Codex asks to run or for wrapper scripts that launch repeatable Codex tasks.

```bash
helm profile --path ~/.helm/workspace run inspect_local \
  --task-name "inspect repo before Codex task" \
  -- git -C ~/project status --short
```

For edits with rollback discipline:

```bash
helm checkpoint create --path ~/.helm/workspace --label before-router-edit --include ~/project
helm profile --path ~/.helm/workspace run workspace_edit \
  --task-name "Codex-assisted router edit" \
  -- python3 ~/project/scripts/apply_router_patch.py
helm status --path ~/.helm/workspace --brief
```

## Recommended profiles

- `inspect_local`: repository inspection, `git status`, `rg`, `pytest --collect-only`
- `workspace_edit`: bounded file edits or formatting
- `risky_edit`: destructive cleanup, broad refactors, generated rewrites with checkpoint discipline
- `service_ops`: dependency install, networked probes, local service operations

## What Helm records

- Task name and profile
- Guard decision
- Command preview
- Exit status
- Finalization and memory-capture plan
- Nearby checkpoints

## Rule of thumb

Let Codex reason and edit. Let Helm declare the operational boundary around commands that matter.
