# Helm Module Split

This document defines what belongs in Helm public core and what should stay in a
private workspace overlay.

## Public Core

### Runtime Governance

- `scripts/run_with_profile.py`
- `references/execution_profiles.json`
- skill `contract.json` files
- `docs/execution-profiles.md`

These define the execution discipline layer.

### Observability and Audit

- `scripts/task_ledger_report.py`
- `scripts/command_log_report.py`
- `scripts/ops_daily_report.py`
- `scripts/ops_db.py`

These make repeated work inspectable.

### Context Hydration

- `scripts/ops_memory_query.py`
- `docs/ops-memory-query.md`
- `docs/router-context-hydration.md`

These define how workflows use memory and operational traces safely.

### Rollback and Recovery

- `scripts/workspace_checkpoint.py`
- `scripts/state_snapshot.py`
- checkpoint documentation

These are core to making repeated agent work recoverable.

### Gated Self-improvement

- `scripts/skill_capture.py`
- `references/skill-capture-template.md`
- `docs/skill-self-improvement.md`

These are the public form of workflow learning.

### Example Upper-layer Skills

Selected skills may be published as examples when they are cleaned of private
state and account assumptions.

## Private Overlay

Keep these outside Helm:

- personal memory and ontology
- runtime state and historical ledgers
- credentials, OAuth files, tokens, and API keys
- assistant identity/persona files
- machine-specific notes and local service assumptions
- runtime patch scripts for one user's installation

## Extraction Rule

If a file expresses a reusable operating principle, safety rule, tracing or
rollback mechanism, or portable orchestration pattern, it can belong in Helm.

If a file expresses who a user is, what they own, what they remember, what
tokens they use, or live operational history, it stays private.
