<p align="center">
  <img src="assets/helm-icon-v2.png" alt="Helm icon" width="108" />
</p>

<h1 align="center">Helm</h1>

<p align="center"><strong>A stability-first operations layer for long-lived personal agents.</strong></p>

<p align="center">Bring execution discipline, file-native context hydration, audit trails, rollback guidance, and gated improvement to the agent runtime you already use.</p>

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
  <a href="#why-helm">Why Helm</a> ·
  <a href="#quick-start">Quick Start</a> ·
  <a href="#onboarding-and-workspace-model">Onboarding</a> ·
  <a href="#core-commands">Core Commands</a> ·
  <a href="#docs-and-demo">Docs and Demo</a>
</p>

![Helm social preview](assets/helm-social-preview.png)

## Why Helm

Most agent stacks can already call tools. The harder problem starts after that:

- choosing the right execution mode before a command runs
- reloading the right context from files and prior operations
- tracing high-level tasks and low-level commands together
- keeping rollback paths before risky edits
- reusing successful workflows without uncontrolled self-modification

Helm is for that operational layer.

It is especially useful if you already have:

- an existing agent runtime or workspace
- long-lived workflows or skills
- notes, memory, logs, or checkpoints that should influence future runs

Helm is runtime-agnostic in principle, but it is easiest to adopt when you already work in an OpenClaw-style or Hermes-style environment.

![Helm explainer cartoon](assets/helm-explainer-cartoon-ko.png)

Korean explainer cartoon for the Helm operating model:

- without Helm, agents act on partial context
- with Helm, context is re-hydrated from files and operational state
- execution runs under explicit profiles
- audits, checkpoints, and rollback paths stay visible

## What Helm Does

- execution profiles such as `inspect_local`, `workspace_edit`, and `risky_edit`
- file-native context hydration across notes, memory, ontology, tasks, commands, and checkpoints
- task and command audit trails
- checkpoint creation, inspection, and restore guidance
- gated skill drafting, review, approval, and rejection
- high-level status and reporting views

## Quick Start

```bash
curl -fsSL https://raw.githubusercontent.com/JDeun/Helm/main/install.sh | bash
helm onboard --path ~/.helm/workspace --use-detected
```

Use a custom workspace if needed:

```bash
curl -fsSL https://raw.githubusercontent.com/JDeun/Helm/main/install.sh | bash -s -- --workspace ~/work/helm
```

## Onboarding and Workspace Model

Helm should usually live in its own workspace and treat existing systems as read-only context sources first.

The default model is:

- keep Helm in a dedicated workspace
- keep runtime state in `.helm/`
- keep profiles, notes, policies, and skill rules as explicit files
- adopt existing OpenClaw, Hermes, and note vaults instead of mutating them

Recommended first-run flow:

```bash
helm init --path ~/.helm/workspace
helm survey --path ~/.helm/workspace
helm onboard --path ~/.helm/workspace --use-detected --dry-run
helm onboard --path ~/.helm/workspace --use-detected
```

By default, `helm onboard` applies the plan and then runs `doctor`, `validate`, and `status --verbose`.
If you only want the adoption step:

```bash
helm onboard --path ~/.helm/workspace --use-detected --skip-checks
```

Explicit adoption examples:

```bash
helm adopt --path ~/.helm/workspace --from-path ~/.openclaw/workspace --name openclaw-main
helm adopt --path ~/.helm/workspace --from-path ~/.hermes --name hermes-main
helm adopt --path ~/.helm/workspace --from-path ~/Documents/Obsidian/MyVault --kind generic --name obsidian-main
helm sources --path ~/.helm/workspace
```

Helm should not overwrite an existing OpenClaw or Hermes tree by default. Obsidian is optional. Helm cares about explicit file state, not a specific notes app, but adopting an existing vault as a read-only source is a strong default.

## Core Commands

Inspect execution profiles:

```bash
helm profile list --path ~/.helm/workspace
```

Hydrate context before routing:

```bash
helm context --path ~/.helm/workspace --describe-modes
helm context --path ~/.helm/workspace --mode failures --limit 5
helm context --path ~/.helm/workspace --include notes tasks commands --summary --limit 8
```

Adopt and query an external source:

```bash
helm adopt --path ~/.helm/workspace --from-path ~/.openclaw/workspace --name openclaw-main
helm context --path ~/.helm/workspace --adapter openclaw-main --include notes tasks commands --limit 8
```

Run a risky task with checkpoint discipline:

```bash
helm profile --path ~/.helm/workspace run risky_edit \
  --task-name "router refactor" \
  -- python3 -c 'print("hello")'
```

Inspect rollback candidates:

```bash
helm checkpoint recommend --path ~/.helm/workspace
helm checkpoint list --path ~/.helm/workspace
helm checkpoint show --path ~/.helm/workspace <checkpoint-id>
```

Create and review a draft skill:

```bash
helm skill --path ~/.helm/workspace draft-from-task \
  --task-id <task-id> \
  --name example-skill \
  --description "Example reusable workflow"

helm skill --path ~/.helm/workspace assess-draft --name example-skill
helm skill-diff --path ~/.helm/workspace --name example-skill
helm skill-approve --path ~/.helm/workspace --name example-skill --dry-run
```

Generate operational summaries:

```bash
helm status --path ~/.helm/workspace --verbose
helm report --path ~/.helm/workspace --format markdown
```

## File-Native Context Hydration

Helm’s context model is intentionally explicit.

Instead of relying on hidden prompt state, it re-reads durable files and operational traces from:

- notes and curated memory files
- file-native memory under `memory/`
- ontology entities and relations
- task ledger
- command log
- checkpoints
- adopted external sources

This is aligned with a wiki-style, externalized working-context approach, but implemented as a practical CLI and workspace layer rather than a clone of any one upstream system.

## Installation Notes

Local checkout install:

```bash
python3 -m pip install --user --no-build-isolation .
```

If `helm` is not on your `PATH`, the installer prints the user-level bin directory you should add to your shell profile.

## Docs and Demo

- [`docs/onboarding.md`](docs/onboarding.md)
- [`docs/release-checklist.md`](docs/release-checklist.md)
- [`docs/releases/0.1.0.md`](docs/releases/0.1.0.md)
- [`docs/router-context-hydration.md`](docs/router-context-hydration.md)
- [`docs/ops-memory-query.md`](docs/ops-memory-query.md)
- [`examples/demo-workspace`](examples/demo-workspace)
- [`CHANGELOG.md`](CHANGELOG.md)

Try the demo workspace:

```bash
helm survey --path examples/demo-workspace
helm doctor --path examples/demo-workspace
helm validate --path examples/demo-workspace
helm report --path examples/demo-workspace --format markdown
```

## Current Status

Helm is already usable as a public early release.

Included:

- Helm-native CLI packaging
- separate workspace model with read-only adoption
- file-native context hydration
- checkpoint, report, and skill review flows
- example workspace and release-oriented docs

Not included:

- private memory or ontology data
- personal agent overlays
- credentials or private task history

## Positioning

Helm is not:

- a new foundation model
- a chat UI
- a full autonomous agent platform
- a replacement for every runtime

Helm is:

- an operations layer
- a governance and observability layer
- a stability-first orchestration layer for local and personal agents

## Acknowledgements

Helm was shaped by practical iteration inside a real OpenClaw-based personal agent workspace and by broader ideas around Hermes-style runtime discipline, wiki-style externalized working context, skills-based workflow design, and checkpoint-oriented local operations.

It is not an official extension, endorsement, or collaboration with any referenced project or person.

## License

MIT
