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

## Durable Layers

Helm uses these durable targets as planning vocabulary:

- `daily_memory` for short operational facts that belong in the day's log
- `long_term_memory` for durable rules, workflow decisions, and recurring truths
- `ontology` for stable entities and relations
- `notes` for human-readable explanation when logs alone are not enough

Helm does not assume every workspace uses every layer. The point is to make the decision explicit and inspectable.

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
