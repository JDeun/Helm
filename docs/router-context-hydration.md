# Router Context Hydration

Upper-layer router skills should not rely only on the current user message when durable context or recent operational state may matter.

Before routing, use `helm context` for a focused read. For command examples and query flags, see [Ops/Memory Unified Query](./ops-memory-query.md).

Hydration quality depends on source durability. If previous tasks only reported success in chat and did not leave durable traces behind, routing quality will degrade even if the query tool itself is correct.

## Rule

Do a focused read before tool selection whenever one of these is true:

- the task may depend on an existing plan, directive, or prior preference
- the task may continue or correct a recent failed workflow
- the task may touch reminders, recurring operations, or existing records
- the task may conflict with previously saved constraints

Skip the query only when the user asks a clearly stand-alone one-shot question and there is no realistic value in historical state.

## Query style

Prefer narrow, domain-biased reads instead of one giant search. Keep routing evidence small enough that it can change the decision without becoming the answer.

## What to look for

- active directives or saved preferences
- recent facts in the same domain
- the latest successful or failed task in the same workflow
- command failures that explain why a provider or wrapper should be avoided
- recovery context when the user is continuing or undoing risky work
- unresolved durable-capture recommendations

## Output discipline

Do not dump the whole retrieved context back to the user.
Use it to improve routing, avoid repeated mistakes, and surface only the few facts that materially affect the answer.
