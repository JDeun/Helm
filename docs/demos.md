# Helm Demo Scenarios

These scenarios are designed for README examples, release notes, and local smoke checks.

## Risky refactor with checkpoint

Situation: an agent is about to modify a broad source tree.

```bash
helm checkpoint create --path ~/.helm/workspace --label before-router-refactor --include ~/project/src
helm profile --path ~/.helm/workspace run risky_edit --task-name "router refactor verification" -- python3 -m pytest ~/project/tests -q
helm checkpoint recommend --path ~/.helm/workspace
```

Helm records the risky task and leaves a rollback candidate tied to the operation.

## Blocked destructive command

Situation: a read-only profile receives a write command.

```bash
helm profile --path ~/.helm/workspace run inspect_local --task-name "guard demo" -- bash -c 'echo x>blocked.txt'
```

Helm classifies shell redirection as a write and blocks it under `inspect_local`.

## Guard-only audit run

Situation: check how Helm would classify a command without executing it.

```bash
python3 scripts/run_with_profile.py run inspect_local --guard-json -- bash -c 'echo x>blocked.txt'
```

Helm prints the guard decision and records a `guard_audit` task-ledger event.

## Memory capture after completion

Situation: preserve a chat-driven decision without running a shell command.

```bash
helm memory --path ~/.helm/workspace capture-chat --task-name "document release decision" --path README.md
helm memory --path ~/.helm/workspace pending-captures --limit 5
```

Helm writes a conversational task entry and runs the same durable capture planning logic used by profiled runs.

## Model health fallback selection

Situation: pick the healthiest configured model from file-backed probe state.

```bash
helm health --path ~/.helm/workspace state --json
helm health --path ~/.helm/workspace select --json
```

Helm uses the configured recovery policy and current health state without storing secrets.

## Local dashboard

Situation: quickly inspect whether the workspace needs attention.

```bash
helm dashboard --path ~/.helm/workspace
helm status --path ~/.helm/workspace --brief
```

Helm shows recent task state, checkpoints, memory review pressure, and next actions without requiring a hosted UI.

## Adopted OpenClaw workspace context

Situation: inspect prior OpenClaw work from a dedicated Helm workspace.

```bash
helm adopt --path ~/.helm/workspace --from-path ~/.openclaw/workspace --name openclaw-main
helm context --path ~/.helm/workspace --adapter openclaw-main --include notes tasks commands --limit 8
```

Helm treats the OpenClaw workspace as a read-only context source.
