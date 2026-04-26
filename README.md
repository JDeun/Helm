<p align="center">
  <img src="assets/helm-icon-v2.png" alt="Helm icon" width="108" />
</p>

<h1 align="center">Helm</h1>

<p align="center"><strong>The safety and memory layer for agents you run more than once.</strong></p>

<p align="center">Helm wraps long-lived agent workspaces with profiles, guardrails, checkpoints, audit trails, and file-backed memory.</p>

<p align="center"><strong>Current release: v0.6.5</strong></p>

<p align="center">
  <a href="README.ko.md">한국어 README</a>
</p>

<p align="center">
  <img alt="License MIT" src="https://img.shields.io/badge/license-MIT-0f172a?style=flat-square">
  <img alt="Python 3.10+" src="https://img.shields.io/badge/python-3.10%2B-1d4ed8?style=flat-square">
  <img alt="Stability first" src="https://img.shields.io/badge/focus-stability--first-334155?style=flat-square">
  <img alt="Runtime agnostic" src="https://img.shields.io/badge/runtime-agnostic-475569?style=flat-square">
</p>

<p align="center">
  <a href="#quickstart">Quickstart</a> ·
  <a href="#why-helm">Why Helm</a> ·
  <a href="#what-helm-adds">What Helm Adds</a> ·
  <a href="#workflows">Workflows</a> ·
  <a href="#docs">Docs</a>
</p>

## Quickstart

```bash
curl -fsSL https://raw.githubusercontent.com/JDeun/Helm/main/install.sh | bash
helm doctor --path ~/.helm/workspace
helm profile --path ~/.helm/workspace run inspect_local --task-name "first Helm inspection" -- git status --short
helm status --path ~/.helm/workspace --brief
helm dashboard --path ~/.helm/workspace
```

The installer installs Helm and initializes `~/.helm/workspace` by default. If `helm` is not found after installation, use the PATH line printed by the installer.

Need a different workspace?

```bash
curl -fsSL https://raw.githubusercontent.com/JDeun/Helm/main/install.sh | bash -s -- --workspace ~/work/helm
```

## Why Helm

Helm is not an agent runtime. It is the operational layer around an agent runtime.

Use Helm when you already run an OpenClaw/Hermes-style long-lived agent workspace, or a similar self-hosted agent service, and need repeated work to stay:

- bounded by explicit execution profiles
- recoverable through checkpoints
- inspectable through task and command logs
- resumable from files instead of chat history
- governed by skill contracts and local policy

If you only need a one-off chatbot demo, Helm is probably unnecessary.

## What Helm Adds

| Problem in repeated agent work | Helm layer |
| --- | --- |
| The agent forgets prior work | File-native context hydration from notes, memory, tasks, commands, and checkpoints |
| Risky edits happen too fast | Execution profiles, command guard, and checkpoint discipline |
| Runs are hard to explain later | Task ledger, command log, status, dashboard, and reports |
| Skills become prompt folklore | `SKILL.md` guidance plus `contract.json` execution policy |
| Model fallback is ad hoc | File-backed model-health probing and fallback selection |
| Operational state is scattered | Workspace layout, adopted context sources, and SQLite query index |

Helm is runtime-agnostic, but it is designed first for persistent agent workspaces with state, memory, profiles, checkpoints, and task history.

![Helm explainer cartoon](assets/helm-explainer-cartoon-ko.png)

## Workflows

Inspect the workspace:

```bash
helm doctor --path ~/.helm/workspace
helm status --path ~/.helm/workspace --brief
helm dashboard --path ~/.helm/workspace
```

Run a command under a declared profile:

```bash
helm profile --path ~/.helm/workspace run inspect_local \
  --task-name "inspect repository state" \
  -- git status --short
```

Adopt existing systems as context sources:

```bash
helm survey --path ~/.helm/workspace
helm onboard --path ~/.helm/workspace --use-detected --dry-run
helm onboard --path ~/.helm/workspace --use-detected
```

Check rollback and recent operational state:

```bash
helm checkpoint-recommend --path ~/.helm/workspace
helm checkpoint list --path ~/.helm/workspace
helm report --path ~/.helm/workspace --format markdown
```

Probe model health:

```bash
helm health --path ~/.helm/workspace state --json
helm health --path ~/.helm/workspace select --json
```

Try the demo workspace:

```bash
helm doctor --path examples/demo-workspace
helm dashboard --path examples/demo-workspace
```

## Workspace Model

Helm should usually live in a dedicated workspace and treat existing systems as read-only context sources first.

- Helm state lives under `.helm/`
- profiles, notes, policies, and skill rules stay as explicit files
- OpenClaw, Hermes, and notes vaults can be adopted instead of overwritten
- JSONL remains the append-only source of truth; SQLite is a query index

## Docs

Start here:

- [`docs/first-run.md`](docs/first-run.md)
- [`docs/onboarding.md`](docs/onboarding.md)
- [`docs/demos.md`](docs/demos.md)
- [`docs/integrations/openclaw.md`](docs/integrations/openclaw.md)
- [`docs/integrations/existing-agent-workspace.md`](docs/integrations/existing-agent-workspace.md)

Core concepts:

- [`docs/execution-profiles.md`](docs/execution-profiles.md)
- [`docs/memory-operations-policy.md`](docs/memory-operations-policy.md)
- [`docs/task-finalization.md`](docs/task-finalization.md)
- [`docs/adaptive-harness.md`](docs/adaptive-harness.md)
- [`docs/skill-quality-and-policy.md`](docs/skill-quality-and-policy.md)

Positioning:

- [`docs/comparisons/agent-frameworks.md`](docs/comparisons/agent-frameworks.md)
- [`docs/comparisons/observability-tools.md`](docs/comparisons/observability-tools.md)
- [`docs/comparisons/eval-tools.md`](docs/comparisons/eval-tools.md)

Release details:

- [`CHANGELOG.md`](CHANGELOG.md)
- [`docs/releases/0.6.5.md`](docs/releases/0.6.5.md)

## Status

Helm v0.6.5 focuses on OpenClaw/Hermes-style adoption docs, long-lived workspace integration, command guard hardening, and local operational visibility with `status --brief`, `dashboard`, and HTML reports.

Helm does not include private memory, personal agent overlays, credentials, or private task history.

## License

MIT
