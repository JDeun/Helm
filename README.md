<p align="center">
  <img src="assets/helm-icon-v2.png" alt="Helm icon" width="108" />
</p>

<h1 align="center">Helm</h1>

<p align="center"><strong>Stop long-running coding agents from losing context, making unsafe edits, and becoming impossible to audit.</strong></p>

<p align="center">Helm is a local operations layer for AI agent workspaces: profiles before commands, checkpoints before risky work, durable task history after the chat is gone.</p>

<p align="center"><strong>Current release: v0.9.6</strong></p>

<p align="center">
  <a href="https://v0-helm-agent-ops.vercel.app/">Landing page</a> ·
  <a href="README.ko.md">한국어 README</a>
</p>

<p align="center">
  <a href="https://pypi.org/project/helm-agent-ops/"><img alt="PyPI version" src="https://img.shields.io/pypi/v/helm-agent-ops?style=flat-square"></a>
  <a href="https://pypi.org/project/helm-agent-ops/"><img alt="PyPI Python versions" src="https://img.shields.io/pypi/pyversions/helm-agent-ops?style=flat-square"></a>
  <a href="https://github.com/JDeun/Helm/actions/workflows/publish.yml"><img alt="Publish to PyPI" src="https://img.shields.io/github/actions/workflow/status/JDeun/Helm/publish.yml?branch=main&label=publish&style=flat-square"></a>
  <img alt="License MIT" src="https://img.shields.io/badge/license-MIT-0f172a?style=flat-square">
  <img alt="Stability first" src="https://img.shields.io/badge/focus-stability--first-334155?style=flat-square">
  <img alt="Runtime agnostic" src="https://img.shields.io/badge/runtime-agnostic-475569?style=flat-square">
</p>

<p align="center">
  <a href="#quickstart">Quickstart</a> ·
  <a href="#why-helm">Why Helm</a> ·
  <a href="#what-helm-adds">What Helm Adds</a> ·
  <a href="#workflows">Workflows</a> ·
  <a href="#docs">Docs</a> ·
  <a href="https://v0-helm-agent-ops.vercel.app/">Landing Page</a>
</p>

## Quickstart

Install from PyPI:

```bash
python -m pip install helm-agent-ops
helm --help
```

Or use the workspace bootstrap installer:

```bash
curl -fsSL https://raw.githubusercontent.com/JDeun/Helm/main/install.sh | bash
helm doctor --path ~/.helm/workspace
helm profile --path ~/.helm/workspace run inspect_local --task-name "first Helm inspection" -- git status --short
helm status --path ~/.helm/workspace --brief
helm dashboard --path ~/.helm/workspace
```

The installer installs Helm and creates `~/.helm/workspace`. If `helm` is not found afterward, use the PATH line printed by the installer.

Need a different workspace?

```bash
curl -fsSL https://raw.githubusercontent.com/JDeun/Helm/main/install.sh | bash -s -- \
  --workspace ~/work/helm
```

## Why Helm

Helm is for developers who already use coding agents for real work and need the session to leave behind something more durable than chat history.

Use Helm when you want to:

- run agent-adjacent commands under explicit risk profiles
- block destructive or out-of-profile commands before they execute
- create visible recovery points before broad edits
- keep task and command history in local files
- rehydrate future runs from workspace state instead of memory alone
- review what happened after a long session ends

Helm is not another agent runtime. It is the operating layer around the one you already run.

Use it when an OpenClaw/Hermes-style workspace, or a similar self-hosted agent service, has moved past demos and needs repeated work to stay:

- bounded by explicit execution profiles
- recoverable through checkpoints
- inspectable through task and command logs
- resumable from files instead of chat history
- governed by skill contracts and local policy

If the agent only runs one-off demos, Helm is probably unnecessary.

## Research Background

Helm's design direction is aligned with the findings in [Harness Design Determines Operational Stability in Small Language Models](https://arxiv.org/abs/2605.12129), which experimentally studies how planning, verification, and recovery harnesses affect the operational stability of small language models.

See [`docs/research-background.md`](docs/research-background.md) for the connection between the paper and Helm's workspace-level operations layer.

## Three-Minute Demo

![Helm three-minute demo terminal capture](https://raw.githubusercontent.com/JDeun/Helm/main/assets/helm-three-minute-demo.gif)

```bash
helm profile --path ~/.helm/workspace run inspect_local \
  --task-name "inspect current repository" \
  -- git status --short

helm checkpoint create --path ~/.helm/workspace \
  --label before-risky-work \
  --include ~/.helm/workspace

helm report --path ~/.helm/workspace --format markdown
helm dashboard --path ~/.helm/workspace
```

This leaves a task ledger, command log, checkpoint record, and dashboard summary on disk.

## How Helm Fits

| Category | Better for | Helm adds |
| --- | --- | --- |
| Agent frameworks | prompts, planners, tool loops, agent graphs | profiles, guard decisions, checkpoints, task ledgers |
| Observability tools | hosted traces, service metrics, telemetry correlation | pre-execution policy and local recovery state |
| Eval tools | scoring model output or task success | operational history around repeated human-agent work |
| Shell wrappers | command convenience | workspace state, memory capture, reports, and recovery discipline |

## What Helm Adds

Core ideas:

- **Profile**: declares the allowed blast radius before a command runs, such as inspect-only, workspace edit, or risky edit.
- **Guardrail**: checks command shape against local policy before execution, blocking dangerous or out-of-profile actions.
- **Checkpoint**: preserves a visible recovery point before work that may need rollback.
- **Audit trail**: records what ran, under which profile, with what guard decision, and what task it belonged to.
- **File-backed memory**: keeps reusable context in files so later runs resume from durable state instead of chat history.
- **Context retrieval**: ranks notes, memory, ontology, tasks, commands, and checkpoints through one inspectable query surface.
- **Privacy boundary**: scans and tokenizes private text before it crosses tool, API, report, or remote handoff boundaries.
- **Operations digest**: summarizes capture status, artifact fingerprints, connector freshness, and review pressure without exposing private workspace contents.

| Repeated-agent problem | Helm adds |
| --- | --- |
| The agent forgets prior work | Context hydration from notes, memory, tasks, commands, and checkpoints |
| Risky edits happen too fast | Profiles, command guard, and checkpoint discipline |
| Runs are hard to explain later | Task ledger, command log, status, dashboard, and reports |
| Private context may leak into tools | `helm privacy` scan/tokenize/restore with local vault and audit events |
| Retrieval feels like a black box | `helm context --explain-ranking` with field, recency, graph, adapter, and source scores |
| Skill rules live in prompts | `SKILL.md` guidance plus `contract.json` execution policy |
| Model fallback is ad hoc | File-backed health checks and fallback selection |
| Operational state is scattered | Workspace layout, adopted sources, and SQLite query index |
| Long-lived integrations silently go stale | Connector freshness probes and daily digest review queues |

Helm is runtime-agnostic, but it is built first for persistent workspaces with state, memory, profiles, checkpoints, and task history.

![Helm explainer cartoon](assets/helm-explainer-cartoon-ko.png)

## Workflows

Inspect the workspace.

```bash
helm doctor --path ~/.helm/workspace
helm status --path ~/.helm/workspace --brief
helm dashboard --path ~/.helm/workspace
```

Run under a declared profile.

```bash
helm profile --path ~/.helm/workspace run inspect_local \
  --task-name "inspect repository state" \
  -- git status --short
```

Adopt existing systems as context sources.

```bash
helm survey --path ~/.helm/workspace
helm onboard --path ~/.helm/workspace --use-detected --dry-run
helm onboard --path ~/.helm/workspace --use-detected
```

Check rollback and recent state.

```bash
helm checkpoint-recommend --path ~/.helm/workspace
helm checkpoint list --path ~/.helm/workspace
helm task list --path ~/.helm/workspace --status running
helm task doctor --path ~/.helm/workspace
helm report --path ~/.helm/workspace --format markdown
```

Query durable context with inspectable ranking.

```bash
helm context --path ~/.helm/workspace --mode decisions --explain-ranking --json
helm context --path ~/.helm/workspace --mode timeline --since 2026-05-01
helm context --path ~/.helm/workspace --mode entity --entity project_helm
helm context --path ~/.helm/workspace --mode reflect-candidates
```

Run a privacy boundary preflight.

```bash
helm privacy --path ~/.helm/workspace scan --text "Contact alice@example.com" --json
helm privacy --path ~/.helm/workspace tokenize --scope task-123 --text "Contact alice@example.com"
```

Review stale negative claims in skill instructions.

```bash
helm skill-lifecycle negative-claims --path ~/.helm/workspace --persist
helm skill-lifecycle revalidation-due --path ~/.helm/workspace
helm skill-lifecycle revalidate-claim --path ~/.helm/workspace \
  --skill old-skill \
  --claim-id sha256:abc123 \
  --status resolved \
  --note "command now exists"
```

Probe model health.

```bash
helm health --path ~/.helm/workspace state --json
helm health --path ~/.helm/workspace select --json
```

Try the demo workspace.

```bash
helm doctor --path examples/demo-workspace
helm dashboard --path examples/demo-workspace
```

## Workspace Model

Keep Helm in a dedicated workspace. Treat existing systems as read-only context sources first.

- Helm state lives under `.helm/`
- profiles, notes, policies, and skill rules stay as explicit files
- OpenClaw, Hermes, and notes vaults can be adopted instead of overwritten
- JSONL remains the append-only source of truth; SQLite is a query index

## Docs

Start here:

- [`docs/three-minute-demo.md`](docs/three-minute-demo.md)
- [`docs/first-run.md`](docs/first-run.md)
- [`docs/onboarding.md`](docs/onboarding.md)
- [`docs/demos.md`](docs/demos.md)
- [`docs/integrations/openclaw.md`](docs/integrations/openclaw.md)
- [`docs/integrations/existing-agent-workspace.md`](docs/integrations/existing-agent-workspace.md)

Core concepts:

- [`docs/execution-profiles.md`](docs/execution-profiles.md)
- [`docs/memory-operations-policy.md`](docs/memory-operations-policy.md)
- [`docs/ops-memory-query.md`](docs/ops-memory-query.md)
- [`docs/privacy-boundary.md`](docs/privacy-boundary.md)
- [`docs/task-finalization.md`](docs/task-finalization.md)
- [`docs/task-state.md`](docs/task-state.md)
- [`docs/integrations/openclaw.md`](docs/integrations/openclaw.md)
- [`docs/adaptive-harness.md`](docs/adaptive-harness.md)
- [`docs/evidence-label-convention.md`](docs/evidence-label-convention.md)
- [`docs/hitl-decision-patterns.md`](docs/hitl-decision-patterns.md)
- [`docs/skill-quality-and-policy.md`](docs/skill-quality-and-policy.md)

Positioning:

- [`docs/opensource-product-definition.md`](docs/opensource-product-definition.md)
- [`docs/opensource-module-split.md`](docs/opensource-module-split.md)
- [`docs/helm-dogfooding-reference.md`](docs/helm-dogfooding-reference.md)
- [`docs/research-background.md`](docs/research-background.md)
- [`docs/public-launch-checklist.md`](docs/public-launch-checklist.md)
- [`docs/comparisons/agent-frameworks.md`](docs/comparisons/agent-frameworks.md)
- [`docs/comparisons/observability-tools.md`](docs/comparisons/observability-tools.md)
- [`docs/comparisons/eval-tools.md`](docs/comparisons/eval-tools.md)

Harness engineering principles:

- [`docs/harness-engineering/05-control-flow-is-not-memory.md`](docs/harness-engineering/05-control-flow-is-not-memory.md) — why workflow completion state must live outside the transcript, how the task-state container enforces this, and the anti-patterns to avoid.
- [`docs/harness-engineering/06-helm-vs-forge.md`](docs/harness-engineering/06-helm-vs-forge.md) — side-by-side comparison of Helm and Forge, what Helm absorbs from Forge's design, and where the two layers do not overlap.

Release details:

- [`CHANGELOG.md`](CHANGELOG.md)
- [`CONTRIBUTING.md`](CONTRIBUTING.md)
- [`SECURITY.md`](SECURITY.md)
- Latest: [`docs/releases/0.9.6.md`](docs/releases/0.9.6.md)
- Recent: [`0.9.5`](docs/releases/0.9.5.md), [`0.9.4`](docs/releases/0.9.4.md)

Older release notes live in [`docs/releases/`](docs/releases/).

## Status

Helm v0.9.6 hardens long-running orchestration paths with explicit timeout handling and keeps release metadata in sync. See [`docs/releases/0.9.6.md`](docs/releases/0.9.6.md).

Helm does not include private memory, personal agent overlays, credentials, or private task history.

## License

MIT
