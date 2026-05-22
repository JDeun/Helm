# Task Ledger vs Command Log

**Generated:** 2026-05-22
**Branch:** feat/harness-engineering-2026-05-22

---

## What each file captures

### `task-ledger.jsonl`

The **task ledger** is the authoritative record of task-level orchestration.
One JSONL row per lifecycle event (queued → running → final status).  A single
task may produce 2–4 rows as it moves through states.

Key fields: `task_id`, `profile`, `skill`, `command`, `status`, `exit_code`,
`guard`, `failure_stage`, `failure_reason`, `memory_capture`, `state_snapshot`.

Written by: `run_with_profile.py` (via `append_ledger` → `append_jsonl_atomic`)
and `commands/task.py` (`_append_state`).

### `command-log.jsonl`

The **command log** is a low-level audit trail of individual subprocess
invocations.  It records one entry per command execution with fields:
`task_id`, `component`, `label`, `started_at`, `exit_code`.

It is written by any code that calls a `run_command()` / command-log helper
and is consumed by `command_log_report.py`, `skill_capture.py`,
`ops_db.py`, and `ops_memory_query.py` for analytics, skill-capture
discovery, and SQLite indexing.

---

## Which is authoritative for failure-signature inputs

**`task-ledger.jsonl` is authoritative** for `failure_signature` inputs.

Reasons:
1. It carries the complete task context: profile, skill, guard decision,
   failure stage, failure reason, and exit code.
2. `command-log.jsonl` records per-command exit codes but lacks the
   task-level guard state, profile constraints, and skill attribution
   needed to classify FS-001..FS-010 shapes.
3. The `failure_signature` module (`scripts/failure_signature.py`) is
   designed to consume a single ledger event dict, which maps directly
   to a `task-ledger.jsonl` row.

---

## Where the new fields belong

All new optional fields added in Task 2 belong to **`task-ledger.jsonl` only**.

| Field | File | Rationale |
|---|---|---|
| `failure_signature` | task-ledger only | Task-level classification; command log lacks context |
| `retry_count` | task-ledger only | Already present; tracks task-level retry state |
| `sessions` | task-ledger only | Session attribution is a task-level concept |
| `snapshot_evidence` | task-ledger only | Snapshot identity is linked to a task finalization |
| `cleanup_status` | task-ledger only | Post-task cleanup is task-scoped |
| `browser_profile` | task-ledger only | Browser configuration is task-level metadata |
| `browser_mode` | task-ledger only | Same |
| `source_urls` | task-ledger only | Browsed URLs are task-level evidence |
| `screenshot_evidence` | task-ledger only | Screenshot is captured per task run |
| `console_network_signals` | task-ledger only | Network signals observed during a task |
| `site_note_update` | task-ledger only | Note update is an output of the task |

The command log is a low-level execution audit trail and should remain
narrow in scope; enriching it with these fields would duplicate data and
create a divergence risk.

---

*End of note.*
