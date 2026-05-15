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

## Operations Digest Boundary

OpenHuman-inspired dogfooding in OpenClaw produced a reusable operations pattern that belongs in Helm as a public-safe primitive:

- artifact registry rows should store path identity, content hash, byte size, role, source task metadata, and duplicate links
- connector freshness rows should store last attempt, last success, stale threshold, last error, token scope label, and artifact count
- daily digest output should summarize task status, capture status, artifact roles, connector freshness, and review queue pressure
- retention policy should compact high-volume JSONL logs into recoverable archives instead of silently deleting history
- status surfaces should expose summaries and pointers, not raw private memory, personal schedules, credentials, or task content

The OpenClaw-specific part stays private:

- actual connector names tied to personal accounts
- raw memory captures, notes, schedules, and command outputs
- local vault paths and private workspace state
- user-specific automation cadence and notification routing

The portable Helm rule is: preserve evidence and freshness metadata, but keep private content behind the workspace boundary.

## Promotion rule

Promote to Helm only when a pattern is:

- reusable outside one private assistant
- free of personal memory, account data, tokens, and schedules
- expressible as policy, CLI behavior, docs, or examples
- covered by tests when it affects runtime behavior

Do not move private OpenClaw memory or personal automations into Helm core.

## Privacy boundary

OpenClaw may dogfood stronger privacy filtering than public Helm ships.

The reusable part that belongs in Helm is the boundary primitive:

- scan private text before external tool, subagent, API, report, or remote handoff boundaries
- tokenize recoverable values into stable labels
- keep the raw mapping in a local vault
- audit tokenize and restore events
- redact secrets instead of storing them as recoverable labels

The private part that stays in OpenClaw is the real vault, personal detector tuning, restore authorization, and any raw private memory.

For the public primitive, see [Privacy Boundary](../privacy-boundary.md).
