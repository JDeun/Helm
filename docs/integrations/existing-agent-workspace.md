# Add Helm to an Existing Agent Workspace

Use this when you already have a long-lived local agent workspace or service directory and do not want to start from the demo workspace.

Helm should usually stay in its own workspace and adopt the existing agent workspace as read-only context first.

## Setup

Create the Helm workspace with the README Quickstart, then verify it:

```bash
helm doctor --path ~/.helm/workspace
```

## Adopt the existing workspace

```bash
helm adopt --path ~/.helm/workspace --from-path ~/agent-workspace --kind generic --name local-agent-main
helm sources --path ~/.helm/workspace
```

If the workspace is OpenClaw-shaped, prefer the OpenClaw integration guide.

## Inspect adopted state

```bash
helm context --path ~/.helm/workspace --adapter local-agent-main --include notes tasks commands --limit 8
helm dashboard --path ~/.helm/workspace
```

## Wrap operational commands

```bash
helm profile --path ~/.helm/workspace run inspect_local \
  --task-name "inspect agent workspace" \
  -- git -C ~/agent-workspace status --short
```

For risky operations, create a checkpoint first.

```bash
helm checkpoint create --path ~/.helm/workspace --label before-agent-workspace-change --include ~/agent-workspace
helm profile --path ~/.helm/workspace run risky_edit \
  --task-name "agent workspace verification" \
  -- ./verify-agent-workspace.sh
helm checkpoint recommend --path ~/.helm/workspace
```

## Inspect the outcome

```bash
helm status --path ~/.helm/workspace --brief
helm report --path ~/.helm/workspace --format markdown
```
