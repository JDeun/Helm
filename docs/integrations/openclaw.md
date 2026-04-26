# OpenClaw Integration

OpenClaw is a personal long-lived agent environment. Helm is the reusable operations layer extracted from that style of workspace.

The recommended relationship is:

- keep OpenClaw as the private runtime and memory environment
- keep Helm as the public, reusable safety and operations layer
- adopt OpenClaw into Helm read-only when you need cross-workspace context
- promote only reusable, non-private OpenClaw patterns back into Helm

## Adopt an OpenClaw workspace

After creating the Helm workspace with the README Quickstart:

```bash
helm adopt --path ~/.helm/workspace --from-path ~/.openclaw/workspace --name openclaw-main
helm sources --path ~/.helm/workspace
helm context --path ~/.helm/workspace --adapter openclaw-main --include notes tasks commands --limit 8
```

## Inspect OpenClaw directly

Helm can inspect OpenClaw-shaped layouts without mutating their private data.

```bash
helm status --path ~/.openclaw/workspace --brief
helm report --path ~/.openclaw/workspace --format markdown
```

## Promotion rule

Promote to Helm only when a pattern is:

- reusable outside one private assistant
- free of personal memory, account data, tokens, and schedules
- expressible as policy, CLI behavior, docs, or examples
- covered by tests when it affects runtime behavior

Do not move private OpenClaw memory or personal automations into Helm core.
