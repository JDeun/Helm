# Proactive Discovery

Proactive discovery is a read-only layer for finding likely operational
problems before a user notices them.

The OpenClaw runner emits candidates to:

```text
~/.openclaw/state/proactive-discovery/runs.jsonl
```

Each candidate uses this shape:

- `candidate_id`
- `problem_class`
- `summary`
- `evidence`
- `suggested_action`
- `action_scope`
- `approval_required`
- `risk_level`
- `confidence`

## Helm Contract

Helm consumers should treat proactive discovery candidates as task intake, not
as permission to act. Any candidate with `approval_required: true` must enter
the normal HITL decision path before edits, network actions, account-bound
actions, or credential changes.

Recommended mapping:

- `silent_failure` -> inspect logs and source-health state
- `stale_connector` -> run live freshness probes before re-auth
- `timeout_or_incomplete_run` -> inspect task ledger and delivery artifacts
- `empty_or_low_value_note` -> improve capture source/evidence or supersede note

## Safety

The discovery runner must remain read-only. It may read state, task ledgers,
source-health files, and Obsidian capture notes, but it must not mutate those
inputs. Writes are limited to its own JSONL run log.
