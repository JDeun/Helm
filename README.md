<p align="center">
  <img src="assets/helm-icon-v2.png" alt="Helm icon" width="108" />
</p>

<h1 align="center">Helm</h1>

<p align="center"><strong>The safety and memory layer for agents you run more than once.</strong></p>

<p align="center">Helm helps long-lived agents keep context, boundaries, rollback visibility, and traceable execution without turning your runtime into a black box.</p>

<p align="center"><strong>Current release: v0.5.11</strong></p>

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
  <a href="#adaptive-harness">Adaptive Harness</a> ·
  <a href="#skill-quality-and-policy">Skill Quality</a> ·
  <a href="#docs-and-demo">Docs and Demo</a>
</p>

## Why Helm

Most agent stacks can already call tools. The harder problem starts when you keep using the same agent and expect it to behave like a system.

The usual failure pattern looks like this:

- the agent forgets what happened in prior runs
- a weaker local model drifts on multi-step work
- risky edits happen without visible rollback discipline
- tasks complete, but later nobody can explain why they ran that way
- skills accumulate, but their rules still live mostly in prose

Helm exists for that second layer of pain.

It gives you:

- a way to reload the right context from files and prior operations
- a way to choose the right execution mode before a command runs
- a way to trace high-level tasks and low-level commands together
- a way to keep rollback paths before risky edits
- a way to make skill policy inspectable instead of leaving it as prompt folklore

In the current release, that means:

- skills own their execution contract through `skills/<skill>/contract.json`
- smaller or weaker local models can be forced through narrower runners and stricter defaults
- operators can audit not only whether manifests exist, but whether they are still too generic to be trusted
- knowledge policy can be described independently from the runtime so memory, artifact, supersession, and review decisions stay inspectable
- typed memory operations and crystallized sessions can be recorded explicitly instead of living only in task prose
- review backlog can be inspected directly through `helm memory review-queue`
- status and reporting now follow the active workspace layout so OpenClaw-shaped workspaces do not silently hide Helm memory state

Helm is especially useful if you already have:

- an existing agent runtime or workspace
- long-lived workflows or skills
- notes, memory, logs, or checkpoints that should influence future runs

If your agent only runs one-off demos, Helm is probably unnecessary.
If your agent already does real repeated work, Helm becomes much easier to justify.

Helm is runtime-agnostic in principle, but it is easiest to adopt when you already work in an OpenClaw-style or Hermes-style environment.

## Example Scenario

A coding agent is asked to refactor a router in a long-lived workspace.

Without Helm, the agent may act on partial context, edit too quickly, and leave weak rollback visibility.
With Helm, the runtime is governed through explicit files, execution profiles, checkpoints, audit traces, and visible finalization decisions.

Typical flow:

1. Helm re-hydrates context from notes, memory, command logs, task history, and checkpoints.
2. Helm selects or enforces the right execution profile before work starts.
3. Risky work is paired with checkpoint discipline and visible task/command traces.
4. After execution, Helm assesses what durable state should be captured so later work starts from files, not from remembered chat.
5. The result is easier to inspect, reproduce, recover, and continue.

![Helm explainer cartoon](assets/helm-explainer-cartoon-ko.png)

## What Helm Does

- execution profiles such as `inspect_local`, `workspace_edit`, and `risky_edit`
- file-native context hydration across notes, memory, ontology, tasks, commands, and checkpoints
- task and command audit trails
- checkpoint creation, inspection, and restore guidance
- task finalization with durable state capture planning
- runtime-neutral memory policy for confidence, recency, supersession, crystallization, and audit-first maintenance
- gated skill drafting, review, approval, and rejection
- high-level status and reporting views

## Quick Start

Install Helm and create a workspace:

```bash
curl -fsSL https://raw.githubusercontent.com/JDeun/Helm/main/install.sh | bash
helm init --path ~/.helm/workspace
```

Survey existing systems and apply onboarding:

```bash
helm survey --path ~/.helm/workspace
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
helm checkpoint-recommend --path ~/.helm/workspace
helm checkpoint list --path ~/.helm/workspace
helm checkpoint show --path ~/.helm/workspace <checkpoint-id>
```

Inspect the latest task handoff snapshot:

```bash
helm context --path ~/.helm/workspace state-snapshot
helm context --path ~/.helm/workspace state-snapshot --task-id <task-id> --json
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

## Adaptive Harness

Helm now includes an adaptive harness layer for model-tier-aware execution.

Use it when you want code-enforced preflight and postflight checks instead of relying only on prompt discipline:

```bash
helm harness --path ~/.helm/workspace policy
helm harness --path examples/demo-workspace contract --skill router-context-demo
helm harness --path examples/demo-workspace preflight \
  --skill router-context-demo \
  --profile inspect_local \
  --model gemma4:e4b \
  --task-name "router triage" \
  --request "라우터 변경 전에 필요한 컨텍스트를 먼저 점검해줘" \
  -- python3 -c 'print("ok")'
```

The key design change in the current release is that harness policy is now skill-owned:

- each skill can declare `allowed_profiles` and `default_profile` in `skills/<skill>/contract.json`
- strict runner requirements can be declared in the manifest instead of central code
- browser-heavy workflows can require structured `browser_evidence` in task metadata before the work is treated as complete
- blocked retrieval workflows can require structured `retrieval_evidence` so escalation exits stay inspectable instead of living only in prose
- file-oriented workflows can require structured `file_intake_evidence` so parser routing and mismatch handling stay auditable
- when explicit evidence is missing, the harness can infer minimal browser, retrieval, or file-intake evidence from the task ledger and still force operators to make the escalation path inspectable
- planning, design, comparison, and drafting requests are tagged with `interaction_workflow` so operators can separate divergence from execution
- selected skills are scored with `skill_relevance`; poor matches fail preflight instead of forcing a request through the wrong abstraction
- `python3 scripts/adaptive_harness.py backfill-evidence` can append inferred evidence to prior runs without rewriting the original ledger history
- `python3 scripts/run_with_profile.py validate-manifests --json` audits missing or malformed manifests before release
- `python3 scripts/run_with_profile.py audit-manifest-quality --json` flags contracts that are still too broad, too generic, or missing approval boundaries

## Skill Quality And Policy

If you are improving a Helm workspace over time, the main policy goal is not to accumulate more skills or more rules.
It is to make any skill easier to govern.

Helm should be able to take a new or changing skill and still answer these questions:

- what minimum input must be collected first
- what order of decisions the skill should make
- where the stop or approval boundary lives
- what a good answer should look like
- how failure should be explained without collapsing the workflow

That is why a strong Helm skill is not just a good description plus a manifest.
It is a skill whose `SKILL.md` exposes real operating contracts and whose `contract.json` keeps execution narrow and inspectable.

Good defaults:

- start new skills at `inspect_local`
- widen to `workspace_edit`, `service_ops`, or `risky_edit` only when the workflow truly needs it
- keep irreversible or account-bound actions visible through `approval_keywords`
- add strict runners only where weaker local models should not improvise
- run `validate-manifests` and `audit-manifest-quality` before release or after policy-heavy skill changes
- keep durable memory, workflow artifacts, and promoted skill rules as separate layers

Good `SKILL.md` defaults:

- make `Input contract`, `Decision contract`, `Output contract`, and `Failure contract` explicit
- keep the first clarification questions short and unblock-oriented
- define a default answer shape and a length rule
- state what the skill must not claim, finish, or imply on its own

See [docs/skill-quality-and-policy.md](docs/skill-quality-and-policy.md) for the review checklist and [docs/knowledge-contract.md](docs/knowledge-contract.md) for the memory and promotion boundary.

Operator visibility helpers:

- `helm run-contract --path <workspace> --json` prints the latest run contract snapshot
- `helm capability-diff --path <workspace> --json` compares recent execution capabilities across runs

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

## Task Finalization

Helm treats task finalization as more than "the process exited."

A meaningful task should leave behind:

- an execution trace
- enough rollback or recovery visibility
- an explicit decision about whether durable state now needs to be captured

Current Helm releases implement this as a visible `memory_capture` plan in the task ledger. The planner recommends whether `daily_memory`, `long_term_memory`, `ontology`, or human-readable `notes` should be updated next.
Operators can also record typed follow-up outcomes through `helm memory op ...`, persist run digests with `helm memory crystallize`, and inspect unresolved review items with `helm memory review-queue`.

Finalized `run_with_profile.py` tasks also write a short markdown handoff artifact under `.helm/state-snapshots/` and link it from the task ledger as `state_snapshot`.
The next profiled run receives the previous snapshot path through `HELM_PREVIOUS_STATE_SNAPSHOT` and `OPENCLAW_PREVIOUS_STATE_SNAPSHOT`, so OpenClaw-shaped workflows can opt into the same resume hint without Helm directly editing OpenClaw.

## Installation Notes

Local checkout install:

```bash
python3 -m pip install --user --no-build-isolation .
```

If `helm` is not on your `PATH`, the installer prints the user-level bin directory you should add to your shell profile.

## Docs and Demo

- [`docs/onboarding.md`](docs/onboarding.md)
- [`docs/release-checklist.md`](docs/release-checklist.md)
- [`docs/releases/0.5.11.md`](docs/releases/0.5.11.md)
- [`docs/router-context-hydration.md`](docs/router-context-hydration.md)
- [`docs/adaptive-harness.md`](docs/adaptive-harness.md)
- [`docs/skill-quality-and-policy.md`](docs/skill-quality-and-policy.md)
- [`docs/task-finalization.md`](docs/task-finalization.md)
- [`docs/ops-memory-query.md`](docs/ops-memory-query.md)
- [`examples/demo-workspace`](examples/demo-workspace)
- [`CHANGELOG.md`](CHANGELOG.md)

Try the demo workspace:

```bash
helm survey --path examples/demo-workspace
helm doctor --path examples/demo-workspace
helm validate --path examples/demo-workspace
helm context --path examples/demo-workspace recent-state --limit 5
helm memory --path examples/demo-workspace pending-captures --limit 5
helm memory --path examples/demo-workspace review-queue --limit 5
helm memory --path examples/demo-workspace audit-coherence --json
helm ops --path examples/demo-workspace capture-state --limit 10
helm report --path examples/demo-workspace --format markdown
```

## Current Status

Helm v0.5.11 adds explicit state snapshots, divergence/convergence routing metadata, and skill relevance guardrails for safer long-running agent work.

Included:

- Helm-native CLI packaging
- separate workspace model with read-only adoption
- file-native context hydration
- task finalization with durable capture planning
- operator-facing finalization inspection commands
- typed memory operations and crystallized session artifacts
- review-queue visibility for unresolved capture, supersession, and confidence issues
- manifest-based adaptive harness governance
- adaptive harness contract surfaces for route decisions, result consistency, and downgrade behavior
- manifest auditing for missing or malformed skill contracts
- manifest quality auditing for generic or weak skill contracts
- `SKILL.md` quality guidance and contract-driven drafting templates
- `SKILL.md` structure and manifest-to-document consistency checks in quality audit paths
- generalized demo-only skill contracts instead of repository-root personal skill assets
- checkpoint, report, and skill review flows
- example workspace and release-oriented docs

Not included:

- private memory or ontology data
- personal agent overlays
- credentials or private task history

## Positioning

A short way to think about Helm:

> The agent runtime does the work. Helm governs how the work is prepared, executed, traced, finalized, and recovered.

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
