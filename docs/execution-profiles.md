# Execution Profiles

This workspace uses a small execution-profile layer so shell actions are chosen deliberately instead of ad hoc.

## Profiles

- `inspect_local`
  - Use for read-only inspection, diagnostics, and state review.
  - Default for `rg`, `sed`, `git status`, config reads, and log checks.

- `workspace_edit`
  - Use for normal local edits inside the workspace.
  - Good default for single-file skill updates, docs, and maintenance scripts.

- `risky_edit`
  - Use when a change touches multiple files, automation wiring, routers, tasks, cron jobs, or reusable skills.
  - A checkpoint is required before the command runs.

- `service_ops`
  - Use for local scripts that also touch live services or APIs.
  - Examples: calendar writes, Sheets writes, meeting-recording pipeline, outbound delivery hooks.

- `remote_handoff`
  - Use when the operation belongs on a remote host, SSH target, node-specific runtime, or container.
  - Do not pretend this is a normal local shell step. State the target and handoff explicitly.

## Decision Rule

Pick the narrowest profile that matches the real risk:

1. Read-only? `inspect_local`
2. Local edit, low blast radius? `workspace_edit`
3. Local edit, high blast radius? `risky_edit`
4. Local script plus external side effects? `service_ops`
5. Needs another machine/runtime? `remote_handoff`

## Operational Bias

- Prefer `inspect_local` before `workspace_edit`.
- Prefer `workspace_edit` before `risky_edit`.
- Escalate to `risky_edit` when changing shared skills, scripts, routing layers, task automation, or environment-wide behavior.
- If the right answer is `remote_handoff`, say so early instead of silently faking local execution.
- Name the real runtime target with `--runtime-target` whenever the backend is not just the local workspace shell.

## Finalization Rule

Execution is not the whole task boundary.

After the command or handoff path ends, Helm should still decide whether the result needs durable state capture.

Examples:

- repo docs, workflow rules, release actions, or reusable scripts changed
- live service or integration behavior changed
- note, memory, ontology, or other durable knowledge sources changed

The profiled runner now writes a `memory_capture` plan into the final task-ledger state so this decision is visible instead of implicit.

## Helpers

- List or inspect profiles:
  - `python3 ~/Helm/scripts/run_with_profile.py list`
  - `python3 ~/Helm/scripts/run_with_profile.py show risky_edit`
  - `python3 ~/Helm/scripts/run_with_profile.py policy`
  - `python3 ~/Helm/scripts/run_with_profile.py validate-manifests --json`
  - `python3 ~/Helm/scripts/run_with_profile.py audit-manifest-quality --json`

- Run a command with a declared profile:
  - `python3 ~/Helm/scripts/run_with_profile.py run workspace_edit -- git -C ~/Helm status --short`
  - `python3 ~/Helm/scripts/run_with_profile.py run service_ops --task-name "meeting pipeline" -- python3 /path/to/helper.py`
  - `python3 ~/Helm/scripts/run_with_profile.py run remote_handoff --runtime-target ssh:gpu-box --runtime-note "Docker build belongs on remote builder" -- docker build .`

- Create a checkpoint directly:
  - `python3 ~/Helm/scripts/workspace_checkpoint.py create --label risky-router-edit --path examples/demo-workspace/skill_drafts/router-context-demo --path scripts`
  - `python3 ~/Helm/scripts/workspace_checkpoint.py preview <checkpoint-id>`

- Inspect the task ledger:
  - `python3 ~/Helm/scripts/run_with_profile.py ledger --limit 20`
  - `python3 ~/Helm/scripts/run_with_profile.py rollback --task-id <task-id> --json`
  - `python3 ~/Helm/scripts/task_ledger_report.py --summary`
  - `python3 ~/Helm/scripts/task_ledger_report.py --failed-only --limit 20`
  - `python3 ~/Helm/scripts/task_ledger_report.py --skill router-context-demo --summary`
  - `python3 ~/Helm/scripts/task_ledger_report.py --latest --summary`

- Inspect low-level command execution:
  - `python3 ~/Helm/scripts/command_log_report.py --summary`
  - `python3 ~/Helm/scripts/command_log_report.py --component router-context-demo --failed-only`

## Enforcement

- `risky_edit` automatically creates a checkpoint before execution.
- `risky_edit` stores the created `checkpoint_id` in later task-ledger states when checkpoint creation succeeds.
- `remote_handoff` records a handoff task instead of pretending to execute locally, and requires `--runtime-target`.
- If `--skill` is provided, the runner checks the skill-local `contract.json` manifest first and rejects disallowed profile/skill combinations.
- `service_ops` runs are appended to `.helm/task-ledger.jsonl` so detached or side-effectful work is auditable later.
- Final task-ledger states include a visible `memory_capture` assessment so operational completion is inspectable.
