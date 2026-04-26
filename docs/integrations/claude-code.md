# Claude Code Integration

Helm does not replace Claude Code. Use Helm as the operations wrapper for commands, checkpoints, and run history while Claude Code remains the coding assistant.

## Basic pattern

Before a Claude Code task:

```bash
helm status --path ~/.helm/workspace --brief
helm profile --path ~/.helm/workspace run inspect_local \
  --task-name "preflight project state" \
  -- git -C ~/project status --short
```

For a risky edit:

```bash
helm checkpoint create --path ~/.helm/workspace --label before-claude-refactor --include ~/project/src
helm profile --path ~/.helm/workspace run risky_edit \
  --task-name "Claude Code refactor verification" \
  -- python3 -m pytest ~/project/tests -q
helm checkpoint recommend --path ~/.helm/workspace
```

## Suggested workflow

1. Ask Claude Code to inspect and propose the edit.
2. Use Helm `inspect_local` for read-only verification commands.
3. Create a checkpoint before broad edits.
4. Use Helm `workspace_edit` or `risky_edit` for scripts that mutate files.
5. End with `helm report --format markdown`.

## Why this helps

Claude Code handles implementation. Helm keeps the operational history explicit so later sessions can recover context from files, not memory of a chat.
