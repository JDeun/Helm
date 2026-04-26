# OpenHands-style Runtime Integration

For OpenHands or similar local agent runtimes, use Helm as an outer operations wrapper rather than replacing the runtime.

## Basic wrapper pattern

```bash
helm profile --path ~/.helm/workspace run workspace_edit \
  --task-name "OpenHands task wrapper" \
  -- ./run-openhands-task.sh
```

The wrapper script can start the runtime, pass a task prompt, or run the verification command that follows the runtime's edits.

## Recommended boundaries

- Use `inspect_local` for repo and environment inspection.
- Use `workspace_edit` for bounded local edits.
- Use `risky_edit` when the runtime can rewrite broad file trees.
- Use `service_ops` only when the runtime needs network, dependency, or service actions.

## Minimum useful sequence

```bash
helm checkpoint create --path ~/.helm/workspace --label before-agent-run --include ~/project
helm profile --path ~/.helm/workspace run risky_edit --task-name "agent-run verification" -- ./verify-agent-output.sh
helm status --path ~/.helm/workspace --brief
helm report --path ~/.helm/workspace --format html > helm-report.html
```
