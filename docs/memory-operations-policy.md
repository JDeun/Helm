# Memory Operations Policy

Helm treats memory operations as runtime policy, not as app-specific storage tricks.

The storage engine can be markdown, jsonl, sqlite, or something else.
The operating contract should remain stable across those choices.

## Core Layers

Helm's preferred ladder is:

1. working context
2. episodic record
3. semantic memory
4. procedural rule

Meaning:

- working context is transient run-local material
- episodic record is an inspectable record of what happened in a bounded task or session
- semantic memory is a reusable fact, entity, relation, or durable state claim
- procedural rule is a reusable operating pattern strong enough to influence later routing or policy

Do not collapse these into one bucket called "memory".

## Session Crystallization

When a run looks durable, Helm should encourage a crystallized episode before broader promotion.

A useful crystallized record keeps these fields visible:

- question
- action
- result
- lesson
- affected entities

This is the minimum structure that lets later work answer:

- what problem was being solved
- what changed
- what was learned
- what parts of the system or domain were touched

Crystallization sits between raw task evidence and semantic promotion.

## Confidence And Recency

Durable claims need visible freshness and support signals.

At minimum, a knowledge operation should be able to carry:

- `last_confirmed_at`
- `source_count`
- `confidence_hint`
- `recency_hint`

Helm does not force one scoring formula.
It does require that runtimes avoid pretending old and new claims are equally trustworthy.

## Supersession

Knowledge systems need a clean way to say that newer state replaced older state.

Helm recommends explicit supersession fields or relations such as:

- `supersedes`
- `superseded_by`
- `replacement_reason`

This matters for:

- repeated workflow retries
- corrected rules
- updated operating status
- durable state changes that should not coexist as equally current truths

Supersession is not deletion.
The older record can still matter for audit and rollback.

## Audit-First Mutations

These operations should be treated as first-class audit events:

- ingest
- promotion
- overwrite
- supersession
- deletion
- rollback

Good audit records answer:

- what changed
- when it changed
- why it changed
- which prior record it replaced or depended on
- whether the mutation was automatic, suggested, or approved by a human

## Review Flags

Helm should distinguish:

- safe automatic maintenance
- low-confidence or contradiction-prone maintenance
- human-review-required changes

Good initial review triggers include:

- contradiction candidates
- low-confidence promotions
- private-to-shared promotion attempts
- overwrite or supersession of durable rules

## Coherence Audits

Durable memory work is not complete just because one file was written.

Runtimes should be able to audit whether the visible layers still agree:

- task-ledger memory decisions
- typed memory operations
- crystallized session artifacts
- supersession references
- review queues

The audit should flag unresolved blockers, operations that point to unknown tasks, crystallized artifacts without source tasks, and supersession links that no longer resolve.

In Helm CLI terms, `helm memory audit-coherence` is the runtime-neutral check for this layer.

## Scope Boundaries

Memory operations should preserve visibility around scope:

- `private`
- `shared`
- `team`
- `public`

This is not only a privacy feature.
It is also a promotion-control feature.

Helm should be able to answer:

- whether a fact may remain private
- whether it may be exported
- whether it may be promoted into team-visible policy
- whether it should be filtered during sync or reporting

## Relationship To Finalization

Finalization should not directly mutate every layer.
It should decide:

- whether a session needs crystallization
- whether a claim should stay episodic
- whether semantic promotion is justified
- whether procedural promotion needs stronger evidence or review

That is how Helm stays runtime-agnostic without becoming vague.
