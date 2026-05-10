# Task State

Helm task state is append-only. Commands such as `helm task block` and
`helm task complete` do not rewrite old ledger rows; they append a newer row
for the same `task_id`.

## Commands

List latest task states.

```bash
helm task list --path ~/.helm/workspace
helm task list --path ~/.helm/workspace --status running
```

Show one task.

```bash
helm task show <task-id> --path ~/.helm/workspace
```

Append a blocked state.

```bash
helm task block <task-id> \
  --path ~/.helm/workspace \
  --reason "missing approval" \
  --next-action "ask the user before deploy"
```

Append a completed state with explicit evidence.

```bash
helm task complete <task-id> \
  --path ~/.helm/workspace \
  --evidence test:pytest \
  --evidence diff:reviewed
```

Create a ready retry task linked to the original task.

```bash
helm task retry <task-id> \
  --path ~/.helm/workspace \
  --reason "rerun after credentials were fixed"
```

Mark a stuck active task stale, then reclaim it as ready work.

```bash
helm task mark-stale <task-id> \
  --path ~/.helm/workspace \
  --reason "heartbeat expired"

helm task reclaim <task-id> \
  --path ~/.helm/workspace \
  --reason "operator resumed work" \
  --owner-session-id "session-1"
```

Inspect stale or inconsistent task states.

```bash
helm task doctor --path ~/.helm/workspace
helm task doctor --path ~/.helm/workspace --stale-minutes 30 --json
```

## State Model

Runner-owned states currently include:

- `queued`
- `running`
- `blocked`
- `needs_verification`
- `completed`
- `failed`
- `timeout`
- `handoff_required`
- `guard_audit`

Manual task-state commands add:

- `ready` for retry tasks waiting to be picked up
- `stale` for active tasks that exceeded an operational liveness window
- `needs_verification` when completion policy fails after a command exits successfully
- explicit `blocked` rows with `blocked_reason`
- explicit `completed` rows with `completion_evidence`

## Human Review Boundary

Task state changes are human-in-the-loop by design. Doctor checks and harness
postflight can propose or append review states such as `needs_verification`,
but they do not decide that work is operationally complete without explicit
evidence. Risky follow-up actions such as retrying, reclaiming, pruning, or
marking work complete remain explicit operator commands.

## Evidence

`helm task show` summarizes evidence from several existing Helm fields:

- `completion_evidence`
- `exit_code`
- `checkpoint_id`
- `memory_capture.finalization_status`
- `memory_capture.write_validation`
- harness evidence for browser, retrieval, and file intake work

Explicit completion evidence is still preferred for manual state changes.
Use a compact `kind:value` shape such as `test:pytest`,
`diff:reviewed`, `provider:response-id`, or `healthcheck:ok`.
For harness-managed tasks, use `helm harness record-evidence --completion-evidence`
to append reviewed evidence without marking the task complete automatically.

The adaptive harness also uses these fields for its profile-level completion
gate. At `balanced` enforcement or higher, high-risk profiles must carry
profile-appropriate evidence before postflight succeeds. See
[`docs/adaptive-harness.md`](adaptive-harness.md) for the exact policy.
When a harness-managed command exits successfully but fails this completion
policy, Helm appends a `needs_verification` row instead of silently treating
the terminal command exit as operational completion.

## Doctor Rules

`helm task doctor` currently checks:

- `running` or `queued` tasks older than the stale threshold
- active tasks whose recorded `pid` or `process_id` is no longer alive
- active tasks that already have `finished_at`
- completed tasks without any detectable evidence bucket
- failed, timed-out, or blocked tasks whose `retry_count` reached `max_retries`

The command reports suggested follow-up commands. It does not mutate state by
itself; use `helm task mark-stale` or `helm task reclaim` when you want an
append-only state transition.

## Checkpoint Retention

Use `helm checkpoint prune` to plan checkpoint cleanup. By default it keeps the
newest checkpoints, checkpoints newer than the retention window, checkpoints
referenced by tasks, and pinned checkpoints. It only deletes archives and
updates the checkpoint index when `--apply` is provided.

Treat `--apply` as the human approval boundary for retention changes. The
policy file can shape the plan, but it does not delete archives by itself.

Use `helm checkpoint protect <checkpoint-id>` to pin a checkpoint, and
`helm checkpoint protect <checkpoint-id> --unprotect` to remove that pin. The
prune command also accepts `--max-total-mb` to add size-pressure pruning after
the protected set is calculated.

Use `helm checkpoint policy` to inspect the default policy or a workspace-local
`references/checkpoint_policy.json` override. When prune flags are omitted,
`helm checkpoint prune` uses that policy for `keep_recent`, `keep_days`, and
`max_total_mb`.
