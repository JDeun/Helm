<p align="center">
  <img src="assets/helm-icon-v2.png" alt="Helm icon" width="108" />
</p>

<h1 align="center">Helm</h1>

<p align="center"><strong>A stability-first operations layer for long-lived personal agents.</strong></p>

<p align="center">Bring execution discipline, context hydration, audit trails, rollback guidance, and gated improvement to the agent runtime you already use.</p>

<p align="center">
  <a href="README.ko.md">한국어 README</a>
</p>

<p align="center">
  <img alt="License MIT" src="https://img.shields.io/badge/license-MIT-0f172a?style=flat-square">
  <img alt="Python 3.10+" src="https://img.shields.io/badge/python-3.10%2B-1d4ed8?style=flat-square">
  <img alt="Stability first" src="https://img.shields.io/badge/focus-stability--first-334155?style=flat-square">
  <img alt="Runtime agnostic" src="https://img.shields.io/badge/runtime-agnostic-475569?style=flat-square">
  <img alt="Agent ops layer" src="https://img.shields.io/badge/agent--ops-layer-64748b?style=flat-square">
</p>

<p align="center">
  <a href="#quick-start">Quick Start</a> ·
  <a href="#what-helm-provides">What Helm Provides</a> ·
  <a href="#architecture-at-a-glance">Architecture</a> ·
  <a href="#installation">Installation</a> ·
  <a href="#typical-workflow">Workflow</a>
</p>

![Helm social preview](assets/helm-social-preview.png)

Helm adds execution-profile discipline, context hydration, audit trails, rollback guidance, and gated self-improvement on top of agent runtimes.

It is designed for agents that already know how to reason and call tools, but still need a safer and more inspectable way to operate over time.

## Quick Start

```bash
git clone https://github.com/JDeun/Helm.git
cd Helm
python3 scripts/run_with_profile.py list
python3 scripts/ops_memory_query.py --describe-modes
python3 scripts/ops_daily_report.py
```

## Who Helm is for

Helm is most useful for people who already have:

- an existing agent runtime or workspace
- a tool-execution loop they want to make safer
- long-lived workflows, skills, or automations they want to operate more carefully

Helm is runtime-agnostic in principle, but it is easiest to adopt when you already work in an OpenClaw-style or Hermes-style agent workspace.

## Relationship to OpenClaw and Hermes

Helm is not a fork of OpenClaw or Hermes.

Instead, it extracts and packages the reusable operational layer that emerged from running an OpenClaw-based personal agent system while selectively absorbing ideas associated with Hermes-style agent operation, especially:

- execution-backend discipline
- persistent operational context
- safer workflow reuse
- gated self-improvement
- stronger observability and rollback

You do **not** have to use OpenClaw or Hermes specifically.
But you will get the most value from Helm if you already have an agent runtime, skill system, or automation workspace that needs a stronger operational layer.

## Why Helm exists

Most agent stacks are good at flexible tool use, but weak at the parts that matter once the system starts doing real work repeatedly:

- choosing the right execution mode before running commands
- remembering operational context without blindly trusting chat history
- tracing parent tasks and low-level command execution together
- recovering from risky changes with explicit rollback paths
- improving repeated workflows without allowing uncontrolled self-modification

Helm focuses on those layers.

## What Helm provides

- **Execution profiles**
  - Run meaningful work under declared profiles such as `inspect_local`, `workspace_edit`, `risky_edit`, `service_ops`, and `remote_handoff`.
- **Context hydration**
  - Query durable memory, ontology, task history, command failures, and checkpoints before routing.
- **Audit trails**
  - Record task-level and command-level operational traces.
- **Rollback guidance**
  - Link risky edits to checkpoints and suggest recovery candidates.
- **Gated self-improvement**
  - Turn successful work into draft skills, assess them, and require explicit approval before promotion.
- **Operations reporting**
  - Summarize recent task status, failed commands, checkpoints, and draft-assessment state.

## Architecture at a glance

![Helm architecture diagram](assets/helm-architecture-diagram.png)

Helm sits above an existing agent runtime or workspace and standardizes the operational layer around:

- execution profiles
- context hydration
- task and command observability
- rollback guidance
- gated self-improvement

## Repository layout

- [`scripts/`](scripts)
  - core operational utilities
- [`docs/`](docs)
  - execution model and workflow guidance
- [`references/`](references)
  - starter profile, policy, and template files

## Installation

Helm currently ships as a lightweight file-based core rather than a packaged CLI.

## Prerequisites

- Python 3.10+
- a local agent workspace or automation workspace
- basic familiarity with running Python scripts from the shell

Optional but strongly recommended:

- an existing skill or workflow structure
- durable memory or note files
- a place to store runtime state such as logs, task ledgers, and checkpoints

### 1. Clone the repository

```bash
git clone https://github.com/JDeun/Helm.git
cd Helm
```

### 2. Use Python 3.10+ and make scripts executable if needed

```bash
python3 --version
chmod +x scripts/*.py scripts/*.sh 2>/dev/null || true
```

### 3. Start with the profile and query tools

```bash
python3 scripts/run_with_profile.py list
python3 scripts/ops_memory_query.py --describe-modes
python3 scripts/ops_daily_report.py
```

### 4. Adapt the reference files to your own environment

Review and customize:

- `references/execution_profiles.json`
- `references/skill_profile_policies.json`
- `references/skill-capture-template.md`

### Optional workspace state

Some commands expect a workspace-local `.openclaw/` directory to exist once you start running tracked tasks. It will be created automatically by the runner as needed.

If you are not using OpenClaw itself, you can still adopt Helm by reusing the same conventions:

- keep a workspace root
- keep runtime state in a dedicated hidden directory
- treat profiles, memory, and skill rules as explicit files rather than hidden prompt state

## Core scripts

- [`run_with_profile.py`](scripts/run_with_profile.py)
- [`ops_memory_query.py`](scripts/ops_memory_query.py)
- [`workspace_checkpoint.py`](scripts/workspace_checkpoint.py)
- [`task_ledger_report.py`](scripts/task_ledger_report.py)
- [`command_log_report.py`](scripts/command_log_report.py)
- [`ops_daily_report.py`](scripts/ops_daily_report.py)
- [`skill_capture.py`](scripts/skill_capture.py)

## Current status

This repository is the first public extraction of the reusable Helm core from a larger private operating stack.

What is already here:

- the core safety and observability scripts
- the execution-profile model
- context-hydration guidance
- rollback and reporting utilities
- the gated skill-improvement flow

What is intentionally not here:

- private memory and ontology data
- personal agent overlays
- task history, checkpoints, and credentials
- packaging polish for every runtime and workflow

## Typical workflow

1. Inspect context with `ops_memory_query.py`
2. Choose the narrowest execution profile that fits the work
3. Run the task with `run_with_profile.py`
4. Audit outcomes through ledger and command reports
5. Use checkpoints and rollback advice for risky changes
6. Convert repeated success into a draft skill through `skill_capture.py`

## Example commands

List available execution profiles:

```bash
python3 scripts/run_with_profile.py list
```

Inspect router-friendly context presets:

```bash
python3 scripts/ops_memory_query.py --describe-modes
python3 scripts/ops_memory_query.py --mode failures --limit 5
```

Create a checkpoint-backed risky task:

```bash
python3 scripts/run_with_profile.py run risky_edit \
  --task-name "router refactor" \
  -- python3 -c 'print("hello")'
```

Generate and assess a skill draft from a completed task:

```bash
python3 scripts/skill_capture.py draft-from-task \
  --task-id <task-id> \
  --name example-skill \
  --description "Example reusable workflow"

python3 scripts/skill_capture.py assess-draft --name example-skill --json
```

## Positioning

Helm is **not**:

- a new foundation model
- a generic chat UI
- a fully autonomous agent platform
- a replacement for every runtime

Helm **is**:

- an operations layer
- a governance and observability layer
- a stability-first orchestration layer for local and personal agents

## Acknowledgements and influences

Helm was shaped by practical iteration inside a real OpenClaw-based personal agent workspace and by selectively learning from a broader set of ideas around long-lived agent operation, reusable workflows, and externalized knowledge.

Important influences include:

- **OpenClaw**
  - the underlying personal-agent workspace where many of these operational patterns were first exercised in practice
- **Hermes Agent**
  - structural ideas around safer workflow reuse, persistent operational context, and runtime discipline
- **Wiki-style knowledge management and externalized working context**
  - the use of explicit notes, linked context, and externalized working memory as part of the operating environment
- **Skills-based workflow design**
  - reusable, inspectable workflow units instead of purely hidden prompt behavior
- **Checkpoint, audit, and rollback-oriented local operations practices**
  - treating observability and recovery as first-class parts of agent work rather than afterthoughts

Helm is not an official extension, endorsement, or collaboration with any of the projects or people referenced above. They are acknowledged here as influences that helped shape the design direction.

## License

MIT
