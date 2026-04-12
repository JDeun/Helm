# Helm

**A stability-first operations layer for long-lived personal agents.**

[한국어 README](README.ko.md)

Helm adds execution-profile discipline, context hydration, audit trails, rollback guidance, and gated self-improvement on top of agent runtimes.

It is designed for agents that already know how to reason and call tools, but still need a safer and more inspectable way to operate over time.

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

## Repository layout

- [`scripts/`](scripts)
  - core operational utilities
- [`docs/`](docs)
  - execution model and workflow guidance
- [`references/`](references)
  - starter profile, policy, and template files

## Installation

Helm currently ships as a lightweight file-based core rather than a packaged CLI.

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

## License

MIT

