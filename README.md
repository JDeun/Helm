<p align="center">
  <img src="assets/helm-icon-v2.png" alt="Helm icon" width="108" />
</p>

<h1 align="center">Helm</h1>

<p align="center"><strong>The safety and memory layer for agents you run more than once.</strong></p>

<p align="center">Helm helps long-lived agents keep context, boundaries, rollback visibility, and traceable execution without turning your runtime into a black box.</p>

<p align="center"><strong>Current release: v0.6.3</strong></p>

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
  <a href="#command-guard">Command Guard</a> ·
  <a href="#model-health">Model Health</a> ·
  <a href="#provider-discovery">Provider Discovery</a> ·
  <a href="#operations-database">Operations Database</a> ·
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

- skill execution contracts via `skills/<skill>/contract.json`
- narrower runners and stricter defaults for weaker local models
- manifest quality auditing beyond existence checks
- runtime-neutral memory policy with typed operations and crystallized sessions
- review-queue visibility for unresolved memory follow-ups
- model-health probing and fallback selection that can stay file-driven between runs
- workspace-aware status and reporting for OpenClaw-shaped layouts

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
- policy-driven model health probing and fallback selection
- conversational durable capture without a profiled shell command
- deterministic command guard with risk scoring and approval workflow
- provider-agnostic LLM discovery (API and local, no secrets stored)
- SQLite query index over operational JSONL

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

Probe model health or pick a recovered fallback:

```bash
helm health --path ~/.helm/workspace state --json
helm health --path ~/.helm/workspace select --json
```

Capture a chat-only change into the task ledger and memory plan:

```bash
helm memory --path ~/.helm/workspace capture-chat \
  --task-name "document release state" \
  --path README.md \
  --path CHANGELOG.md \
  --json
```

## Command Guard

Helm includes a deterministic command guard that evaluates every command before execution:

- **Absolute deny**: Catastrophic commands like `rm -rf /` are always blocked
- **Profile enforcement**: `inspect_local` blocks writes and network access; `workspace_edit` blocks network
- **Risk scoring**: Commands receive a risk score (0.0–1.0) based on detected categories
- **Approval workflow**: Risky commands require `--approve-risk` to proceed

```bash
# Guard blocks dangerous commands
helm profile run inspect_local -- rm -rf build
# GUARD DENY: write detected under inspect_local

# Override with approval for known-risky operations
helm profile run risky_edit --approve-risk -- rm -rf build

# Audit mode records but does not block
helm profile run workspace_edit --guard-mode audit -- curl https://example.com

# Check guard decision without running
helm profile run workspace_edit --guard-json -- rm -rf build
```

Guard modes: `enforce` (default), `audit` (record only), `off` (disabled but recorded).

## Model Health

Helm now ships a separate model-health layer for one specific job: remember which candidate model was healthy recently, probe it again when needed, and pick a fallback from files instead of from chat memory.

The default template lives in `references/model_recovery_policy.json`. You can keep it empty and rely on discovery-only fallback selection, or define explicit models and probe kinds when you want active health checks.
The bundled template now starts with a practical local-first chain: `ollama/llama3.2:latest`, then `openai/gpt-4.1-mini`, then `google_gemini/gemini-2.5-flash`.

Typical flow:

```bash
# inspect persisted state
helm health --path ~/.helm/workspace state --json

# actively probe configured candidates
helm health --path ~/.helm/workspace probe --json

# select the best fresh healthy model for the next turn
helm health --path ~/.helm/workspace select --json
```

`helm doctor` now includes the model-health policy path, state path, and the currently selected candidate so the runtime handoff stays inspectable.

The guard also detects shell-level bypass patterns including heredoc injection, base64-piped execution, and `/dev/tcp` network access. Guard exceptions are fail-closed (default to `require_approval`), and guard evaluation runs before manual-remote handoff to prevent bypass.

## Provider Discovery

Helm detects available LLM providers without calling any API:

- **API providers**: 19 providers detected by environment variable presence (Anthropic, OpenAI, Gemini, OpenRouter, Azure, Bedrock, Vertex, Mistral, Groq, Together, Fireworks, Cohere, DeepSeek, xAI, Replicate, Perplexity, HuggingFace, Cerebras, NVIDIA NIM)
- **Local providers**: 4 providers detected by endpoint probe (Ollama, LM Studio, llama.cpp, vLLM)
- **GPU detection**: NVIDIA, Apple Silicon, and AMD (ROCm) with multi-GPU support
- **Custom registry**: User-defined providers via `model_provider_policy.json`
- **No API calls**: Provider detection never sends requests to cloud APIs
- **No secrets stored**: API key values are never logged or persisted

Run `helm doctor` to see the full discovery report.

## Operations Database

Helm maintains a SQLite query index over the JSONL task ledger:

```bash
helm db init              # Create the SQLite index
helm db rebuild           # Rebuild from JSONL source files
helm db verify            # Check for JSONL/SQLite drift
helm db status            # Show index statistics
```

Query the index:

```bash
helm db query --status completed --limit 10
helm db query --guard-action deny --json
```

JSONL remains the append-only source of truth. SQLite failures never block command execution.

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

### Skill-Owned Policy

- Each skill declares `allowed_profiles` and `default_profile` in `contract.json`
- Strict runner requirements live in the manifest, not central code
- Selected skills are scored with `skill_relevance`; thresholds are tunable in `references/adaptive_harness_policy.json`
- Planning, design, and drafting requests are tagged with `interaction_workflow`

### Evidence Requirements

- Browser-heavy workflows can require `browser_evidence` before completion
- Blocked retrieval workflows require `retrieval_evidence` for inspectable escalation
- File-oriented workflows require `file_intake_evidence` for auditable parser routing
- Missing evidence can be inferred from the task ledger as a fallback

### Operator Tooling

- `python3 scripts/adaptive_harness.py backfill-evidence` — append inferred evidence to prior runs
- `python3 scripts/run_with_profile.py validate-manifests --json` — audit missing or malformed manifests
- `python3 scripts/run_with_profile.py audit-manifest-quality --json` — flag weak or overly broad contracts

## Skill Quality And Policy

If you are improving a Helm workspace over time, the main policy goal is not to accumulate more skills or more rules.
It is to make any skill easier to govern.

### Governance Questions

Helm should be able to take a new or changing skill and still answer these questions:

- what minimum input must be collected first
- what order of decisions the skill should make
- where the stop or approval boundary lives
- what a good answer should look like
- how failure should be explained without collapsing the workflow

That is why a strong Helm skill is not just a good description plus a manifest.
It is a skill whose `SKILL.md` exposes real operating contracts and whose `contract.json` keeps execution narrow and inspectable.

### Good Defaults

Good defaults:

- start new skills at `inspect_local`
- widen to `workspace_edit`, `service_ops`, or `risky_edit` only when the workflow truly needs it
- keep irreversible or account-bound actions visible through `approval_keywords`
- add strict runners only where weaker local models should not improvise
- run `validate-manifests` and `audit-manifest-quality` before release or after policy-heavy skill changes
- keep durable memory, workflow artifacts, and promoted skill rules as separate layers

### SKILL.md Defaults

Good `SKILL.md` defaults:

- make `Input contract`, `Decision contract`, `Output contract`, and `Failure contract` explicit
- keep the first clarification questions short and unblock-oriented
- define a default answer shape and a length rule
- state what the skill must not claim, finish, or imply on its own

See [docs/skill-quality-and-policy.md](docs/skill-quality-and-policy.md) for the review checklist and [docs/knowledge-contract.md](docs/knowledge-contract.md) for the memory and promotion boundary.

### Visibility Helpers

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
Snapshots include the task status, memory-capture summary, harness routing metadata, skill relevance, route decision, and evidence presence.
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
- [`docs/releases/0.6.3.md`](docs/releases/0.6.3.md)
- [`docs/releases/0.6.2.md`](docs/releases/0.6.2.md)
- [`docs/releases/0.6.1.md`](docs/releases/0.6.1.md)
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
helm memory --path examples/demo-workspace capture-chat --task-name "demo memory capture" --path README.md
helm health --path examples/demo-workspace state --json
helm ops --path examples/demo-workspace capture-state --limit 10
helm report --path examples/demo-workspace --format markdown
```

## Current Status

Helm v0.6.3 keeps the v0.6.2 model-health and conversational capture additions, and ships the verified hotfix for `capture-chat` finalization planning after a full local pytest pass.

### Core

- Helm-native CLI packaging
- Separate workspace model with read-only adoption
- File-native context hydration
- Task finalization with durable capture planning
- Typed memory operations and crystallized session artifacts
- Policy-driven model health selection and probing
- Conversational memory capture without a profiled shell run

### Security

- Deterministic command guard with risk scoring and approval workflow
- `SemanticResult` structured return type for bypass-resistant semantic analysis
- Recursive shell unwrapping (max depth 5) and interpreter detection
- Heredoc, base64, and `/dev/tcp` bypass detection
- Fail-closed guard policy on exceptions (tuple-based, immutable)
- Guard evaluation before manual-remote handoff
- `--guard-json` flag for machine-readable guard decisions
- Minimal environment isolation for restricted profiles

### Reliability

- Provider discovery: 19 API providers, 4 local providers (no API calls, no secrets stored)
- Model health state persisted under `.helm/model-health-state.json` with policy-driven fallback selection
- GPU/VRAM detection: NVIDIA, Apple Silicon, AMD (ROCm), multi-GPU with `lru_cache`
- Custom provider registry via policy JSON
- SQLite query index over JSONL (init, rebuild, verify, query) with thread-safe caching
- Atomic JSONL append with cross-platform sentinel-region file locking
- Schema versioning and streaming JSONL reads (no OOM on large files)
- Subprocess timeout control (`--timeout`, default 1800s)
- Extended `helm doctor` with discovery, hardware, and guard sections

### Governance

- Manifest-based adaptive harness with skill-owned policy
- Deep merge for skill contract resolution
- Manifest auditing and quality auditing for skill contracts
- `SKILL.md` quality guidance and contract-driven drafting templates
- Checkpoint, report, and skill review flows
- Review-queue visibility for unresolved memory issues

### CLI

- `helm db init/rebuild/verify/status/query`
- `helm doctor --skip-discovery`
- State-snapshot inspection and handoff artifacts
- Snapshot-driven intelligence tier resolution (L0-L4)
- 298 tests (pytest, cross-platform)

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
