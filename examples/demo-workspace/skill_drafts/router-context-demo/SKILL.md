# Router Context Demo

## Core rule

Treat this as a governance demo for router-facing work.
Inspect first, narrow the change surface, and do not jump into mutation until the router target and prior context are explicit.

## Input contract

- Required inputs: the router target, the requested change, and the workspace path being inspected
- Optional inputs: recent failures, related task ids, and prior command traces
- Ask first when missing: ask for the router target or the exact file/module when the request names only a vague subsystem
- If the request is broad or ambiguous, how it must be narrowed: reduce the work to one router surface or one routing decision before allowing edits

## Decision contract

- State the decision order explicitly: hydrate prior router context, inspect the current state, decide whether read-only triage is enough, then escalate to edit profiles only if the requested change is concrete
- Route outward when the request is not really about routing or when another skill owns the integration boundary
- Ask for approval before risky multi-file routing edits or before any change that would affect shared automation behavior
- Red flags: missing router target, unclear blast radius, conflicting task history, or requests that imply success before validation exists

## Execution contract

- State the real commands, tools, or APIs to use: `helm context`, `helm harness contract`, `helm harness preflight`, `rg`, `sed`, and the task/command ledger helpers
- Prefer deterministic scripts over freeform shell improvisation
- Use saved context, failed task history, or recent command traces when the manifest says prior state is required
- Use the strict runner path through `runner.entrypoint` when mutation is approved because this contract requires a guarded runner
- If the workflow has risk, mention the execution profile or checkpoint rule: `inspect_local` first, `workspace_edit` only for narrow edits, `risky_edit` when routing changes touch shared behavior

## Output contract

- Default output format: short triage summary followed by the proposed next execution profile
- Always include: the router target inspected, the context sources used, the decision taken, and any approval boundary that remains
- Length rule: keep inspection responses short unless the user asks for a deeper audit
- Do not say or Do not imply: that a risky router edit is safe without checkpoint discipline or that context was reviewed when it was not

## Failure contract

- Failure types: missing router target, missing prior context, inspection command failure, or blocked risky edit without approval
- Fallback behavior: stop at inspection, explain what is missing, and recommend the next narrow command instead of guessing
- User-facing failure language: say exactly which dependency or approval boundary blocked progress and what concrete input would unblock it

## Do

- Keep the workflow visibly inspection-first
- Make the escalation from inspect to edit explicit
- Surface context dependency instead of silently proceeding without it

## Do not

- Do not treat a broad router request as mutation-ready by default
- Do not bypass the guarded runner path for risky router edits
- Do not route this skill into unrelated service or purchase workflows
