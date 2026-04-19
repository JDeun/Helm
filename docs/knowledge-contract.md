# Knowledge Contract

Helm treats model output as replaceable, but operating knowledge as durable.

This is the practical contract that follows from that stance.

## Core rule

Do not treat chat transcript as the primary system of record.

Treat these as separate layers:

- session transcript
- durable memory
- workflow artifacts
- promoted skill knowledge

## Why this matters

Runtime, model, and provider can change.

What should remain inspectable across those changes is:

- role and rule context
- skill-specific operating procedure
- memory worth carrying forward
- workflow evidence that explains why a run succeeded or failed

## Minimal questions

Before promoting any information into durable state, ask:

- is this a one-off conversational detail or a repeated operating fact
- is this a workflow artifact or reusable rule
- should this remain in task history only, or become memory
- should this remain memory, or be promoted into a skill or policy

## Promotion path

Use a narrow promotion ladder:

1. Transcript or temporary context
2. Task artifact or audit record
3. Durable memory
4. Skill contract or operating document

Do not skip directly from one successful run to a broad permanent rule.

## Operator checks

Good knowledge contracts answer:

- what becomes durable memory
- what remains an artifact
- what counts as a failure pattern worth preserving
- what conditions justify promoting a workflow into a reusable skill

## Anti-patterns

- treating all chat history as memory
- hiding workflow evidence inside prose-only summaries
- promoting weak or unverified patterns into durable rules
- coupling durable knowledge too tightly to one model or runtime

## Relationship to Helm

In Helm terms:

- manifests describe execution boundaries
- task ledgers preserve run evidence
- finalization decides whether durable capture is required
- skill quality review decides when workflow knowledge is strong enough to promote

The goal is not more memory by default.
The goal is memory, artifacts, and rules that remain coherent when the runtime changes.
