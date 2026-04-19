# Task Finalization

Helm distinguishes execution completion from operational completion.

A command can finish and still leave the workspace in a weak state if the important result only lives in chat or in the operator's memory.

## Rule

Treat a task as operationally complete only after Helm has done all three:

1. executed the command or handoff path
2. recorded the result in the task ledger
3. assessed whether durable state capture should happen next

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

For richer workspaces, the stronger ladder is:

1. working context
2. episodic or crystallized session record
3. semantic memory
4. procedural rule

Finalization is where Helm decides whether the run should stay at layer 2 or move further.

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

## Why This Matters

- Verification answers "did the task actually run?"
- Finalization answers "what durable state should remain after it ran?"
- Context hydration is only as strong as the files earlier tasks left behind

If the durable traces are weak, later routing and recovery will also be weak.

## Current Scope

The current implementation adds planning and observability:

- `scripts/run_with_profile.py` writes a `memory_capture` section into the final task-ledger state
- `task_ledger_report.py`, `ops_daily_report.py`, `helm status`, and `helm report` surface finalization counts

Actual mutation of workspace-specific memory files stays intentionally separate because each runtime has different write rules.

Even when mutation stays runtime-specific, Helm should still make these decisions inspectable:

- whether the run produced a crystallized session digest
- whether the resulting claim is high, medium, or low confidence
- whether the run appears to supersede older task state
- whether contradiction or scope review should block automatic promotion

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

Helm's direction is audit-first:

- ingest, promotion, overwrite, supersession, deletion, and rollback should all stay inspectable
- maintenance loops should distinguish automatic repair from human-review-required changes
- stale or low-confidence state should be visible without forcing destructive automatic cleanup

See [Memory Operations Policy](./memory-operations-policy.md) for the runtime-neutral contract Helm should apply across runtimes.
