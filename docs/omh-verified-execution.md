# OMH-style verified execution

OpenClaw and Helm use a small, profile-aware subset of the OMH patterns. The existing execution profiles, task ledger, checkpoints, and external-action governance remain authoritative.

## Activation

- `risky_edit` and `service_ops` create a Planner/Architect/Critic consensus plan before the command runs.
- `workspace_edit` uses the gate only for shared cron, job, skill, router, workflow, briefing, memory, ledger, or release work.
- Simple inspection and ordinary low-risk edits do not pay the consensus cost.
- AI briefing, memory, skill-router, workflow, and release changes under `risky_edit` require verified execution automatically. Any run can opt in with `--verified-execution`.

The plan is approved only when all three reviews agree. It contains scope, non-goals, atomic tasks, acceptance criteria, verification, and rollback. Review is capped at two rounds. Unsafe paths or unresolved disagreement block execution.

## Evidence gathering

`scripts/evidence_gatherer.py` executes commands with `shell=False` and an exact token-prefix allowlist from `references/evidence_commands.json`. It rejects shell metacharacters, command chaining, path escape, excessive command counts, timeouts, and nonzero exits. Output is truncated and common credential forms plus secret-bearing environment values are redacted.

Example:

```bash
python3 scripts/evidence_gatherer.py \
  --command-json '["python3", "-m", "pytest", "-q", "tests/test_omh_patterns.py"]' \
  --file scripts/evidence_gatherer.py \
  --output .openclaw/task-bundles/demo/verification-evidence.json
```

Repository-specific commands may be added only as token arrays scoped to a repository root. Never allow `sh -c`, `bash -c`, or a broad interpreter prefix.

For a live service readback, pass a non-secret `source` plus a `readback_command` whose token prefix is explicitly listed in `service_readback_prefixes`. The dedicated list is empty by default; the general test/build allowlist does not grant service-readback authority. Caller-supplied `verified: true` assertions are rejected; only a dedicated evidence-gatherer readback or a runner-performed remote/provider readback is trusted. A primary command exit is not a substitute for service readback.

## Readable task state

Each finalized task has one bundle under the configured state root:

```text
task-bundles/<task-id>/
  plan.md
  state.md
  evidence.json
  blockers.md
  manifest.json
  verification-evidence.json  # when independent verification ran
  files/                      # frozen touched files
```

`state.md` records phase, current task, next action, touched paths, blockers, and external surfaces. It cross-references the append-only task ledger. It is a resume view, not a replacement for the ledger or checkpoint.

## Central roles

Use exactly one marker in a specialist goal: `[role:planner]`, `[role:architect]`, `[role:critic]`, `[role:executor]`, `[role:verifier]`, `[role:security-reviewer]`, or `[role:researcher]`. The consensus and verified-execution loops expand the prompt and output contract from `references/role_catalog.json` into each live role input. Unknown markers fail before execution. Helm still records a warning for old free-form registry roles so existing agents can migrate without losing state.

## Ralph-style loop

`scripts/verified_execution.py` reads a JSON plan whose atomic tasks each contain:

- a string-array `command`;
- explicit `scope`;
- one or more `acceptance_criteria`;
- one or more allowlisted `evidence_commands`;
- `max_attempts` from 1 to 3.

The executor runs through `run_with_profile.py`. The verifier independently reads the task ledger's finalization, scope, and evidence records. Executor prose is never accepted as proof. A failure may retry within the declared budget; the same normalized failure fingerprint three times becomes `blocked`. All passing tasks still require a final Architect review.

```bash
python3 scripts/verified_execution.py \
  --plan /path/to/verified-plan.json \
  --workspace "$PWD"
```

## Completion and rollback

A successful command can still be `needs_verification`. Completion requires the applicable evidence types:

- process exit for command execution;
- SHA-256 file readback for touched files;
- allowlisted test/build/lint evidence for verified code changes;
- live readback for external service changes;
- a passing scope gate for consensus-controlled work.

Missing evidence, scope drift, rejected verification commands, or failed readback prevents a verified completion claim. Use the plan rollback note and task checkpoint; never perform an automatic destructive rollback.
