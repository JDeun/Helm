# Task Finalization

Helm distinguishes execution completion from operational completion.

A command can finish and still leave the workspace in a weak state if the important result only lives in chat or in the operator's memory.

The operating principle is: the model proposes actions, while the harness
validates, authorizes, executes, records, and returns observations. A model
message can suggest that work is done, but Helm should only treat the task as
operationally complete when the ledger contains evidence for the claim.

## Rule

Treat a task as operationally complete only after Helm has done all three:

1. executed the command or handoff path
2. recorded the result in the task ledger
3. assessed whether durable state capture should happen next

Every governed action must also return an observation to the loop. Denial,
approval-required, timeout, error, abort, handoff-required, and validation
failure are all valid tool results. A missing tool result is not a successful
step; it is a broken execution boundary that should remain visible in the
ledger or trace.

Current Helm releases add the assessment boundary first. The task ledger now stores a `memory_capture` plan with:

- whether the task looks memory-relevant
- which event types were detected
- which durable layers should probably be updated next
- why Helm made that recommendation
- whether the run should stay episodic or be crystallized further
- whether confidence, recency, supersession, or review flags should gate promotion

## Durable Layers

Helm uses these durable targets as planning vocabulary:

- `daily_memory` for short operational facts that belong in the day's log
- `long_term_memory` for durable rules, workflow decisions, and recurring truths
- `ontology` for stable entities and relations
- `notes` for human-readable explanation when logs alone are not enough

Helm does not assume every workspace uses every layer. The point is to make the decision explicit and inspectable.

For the full runtime-neutral ladder and promotion policy, see [Memory Operations Policy](./memory-operations-policy.md). This document only defines where the finalization decision happens.

## Support Artifacts Versus Breakage

Not every durable file that looks disconnected should be treated as a defect.

Some workspaces intentionally produce support artifacts such as:

- projected notes generated from another source of truth
- task-capture or audit records kept mainly for inspection
- alias stubs or redirect notes that exist for lookup stability

These should not be collapsed into the same bucket as real breakage such as unresolved links, missing required hub edges, or missing durable traces after a meaningful task.

The operating rule is:

- distinguish real issues from intentional support artifacts
- keep that distinction visible in diagnostics and operator summaries
- only promote support artifacts into first-class navigation when the inspection value clearly outweighs the noise

## Post-Write Validation

Some tasks are not complete when the process exits. They are complete only after
the written artifact has been checked against the workspace's expected
structure.

Helm keeps this runtime-neutral. A workspace-specific validator can inspect
whatever it owns, then attach the result to the task ledger as
`memory_capture.write_validation`.

Example:

```bash
python scripts/adaptive_harness.py record-evidence \
  --task-id <task-id> \
  --write-validation-json '{"ok": true, "checked_paths": ["notes/generated.md"], "validator": "obsidian-structure-audit"}'
```

Skills can require this boundary with an `artifact_validation` section in
`contract.json`:

```json
{
  "artifact_validation": {
    "required": true,
    "required_fields": ["ok", "checked_paths"]
  }
}
```

When that contract is present, `adaptive_harness.py postflight` does not treat a
task as operationally clean until the ledger contains a successful
post-write validation record. This is the general Helm pattern behind
workspace-specific checks such as Obsidian note structure audits.

Artifact-specific validators should name the syntax and integrity boundary they
checked. For Obsidian-backed workspaces, useful gates include:

- Markdown notes: frontmatter YAML, wikilink syntax, empty template sections,
  and source fields when the note is a first-pass capture
- Bases: YAML parse, `views` shape, and filter/formula sanity checks
- Canvas files: JSON parse, unique node and edge IDs, edge references, and
  required node fields

Those checks should be recorded as `write_validation` evidence with the
validator name and the exact artifact paths inspected.

## SmallCode-Inspired Edit Safety

Helm has partial coverage for the file-edit safety patterns described in the
SmallCode PRD:

- Patch-first editing is implemented as policy plus helper functions in
  `references/edit_policy.json` and `scripts/edit_policy.py`.
- Read-before-write enforcement is implemented by `scripts/edit_policy.py`:
  missing evidence, path mismatches, stale mtime, and stale size block a
  planned write before mutation.
- Repeated patch failure is tracked per file and escalates to
  `reload_context_then_decompose`.
- Whole-file rewrite limits are validated against the policy allowlist.
- Checkpoint requirements are implemented for selected target kinds and
  `risky_edit` creates a workspace checkpoint before execution.
- Verification gates are implemented by `scripts/validation_gate.py` and
  profile-level completion evidence is enforced by adaptive harness postflight
  when the configured enforcement threshold applies.
- Rollback is available for local workspace checkpoints through checkpoint
  preview, restore, recommendation, and task-linked rollback inspection.

Known gaps:

- Validation gate selection is still extension/profile driven and does not
  automatically infer every project-specific lint, test, or build command.
- Hard validation failure does not automatically roll back local changes; Helm
  records rollback guidance and leaves restore/apply decisions to an operator.
- External systems such as calendars, sheets, messages, or remote hosts need
  compensating-action guidance rather than true checkpoint restore.

## Why This Matters

- Verification answers "did the task actually run?"
- Finalization answers "what durable state should remain after it ran?"
- Context hydration is only as strong as the files earlier tasks left behind
- Compaction should preserve structured state, not prose. Task state,
  approvals, checkpoint ids, validation evidence, and finalization status must
  survive even when chat text is summarized or dropped.

If the durable traces are weak, later routing and recovery will also be weak.

## Current Scope

The current implementation adds planning and observability:

- `scripts/run_with_profile.py` writes a `memory_capture` section into the final task-ledger state
- `scripts/long_running_runtime.py` stores resumable task runs, phase
  checkpoints, approval pauses, idempotency keys, and specialist registry
  entries in `.helm/long-running-runtime.json`
- `scripts/adaptive_harness.py postflight` can enforce task evidence, finalization, and post-write artifact validation contracts
- `task_ledger_report.py`, `ops_daily_report.py`, `helm status`, and `helm report` surface finalization counts

Actual mutation of workspace-specific memory files stays intentionally separate because each runtime has different write rules.

Even when mutation stays runtime-specific, Helm should still make the finalization decision inspectable. Confidence, recency, supersession, scope, and review flags are defined in [Memory Operations Policy](./memory-operations-policy.md).

## Operator Commands

The second expansion adds direct inspection commands so operators do not need to infer finalization state from raw ledger JSON.

- `helm context recent-state --limit 10`
  shows recently finalized tasks with their finalization status and suggested durable layers
- `helm memory pending-captures`
  shows only tasks that still look like they need durable capture follow-up
- `helm ops capture-state`
  summarizes finalization counts and the current pending capture queue
- `helm checkpoint finalize --task-id <id>`
  combines the task's finalization plan with the checkpoint Helm would use for rollback or inspection

## Audit And Maintenance Direction

Helm's direction is audit-first: finalization should expose what durable state may need follow-up, while [Memory Operations Policy](./memory-operations-policy.md) defines how later ingest, promotion, overwrite, supersession, deletion, rollback, and review should be governed.
