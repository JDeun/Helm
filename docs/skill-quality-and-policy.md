# Skill Quality And Policy

This document is a practical checklist for improving skill quality and operational policy in Helm workspaces.

Helm is not primarily a catalog of skills.
It is the operating layer that makes changing skills easier to run safely, inspect, tighten, and recover.

That means the real question is not:

- "does this workspace contain many impressive skills"

The real questions are:

- "can any skill in this workspace be routed predictably"
- "does each skill expose its risk boundary clearly"
- "will a weaker model know when to stop, ask, or hand off"
- "can an operator audit why a skill was allowed to run the way it did"

## Skill Quality Baseline

- every promoted skill should have `SKILL.md` and `contract.json`
- `SKILL.md` should read like an operating procedure, not a marketing description
- `SKILL.md` should make input, decision, output, and failure rules explicit
- `contract.json` should keep `allowed_profiles` as narrow as possible
- `default_profile` should point to the least risky path that still works
- `context.required` should be true only when the skill actually depends on prior state
- `approval_keywords` should cover irreversible, account-bound, or payment-adjacent actions
- `runner.strict_required` should be used only when freeform execution is too risky for weaker models

## SKILL.md Baseline

Every promoted skill should make these contracts visible inside `SKILL.md`:

- `Input contract`
  - required inputs
  - optional inputs
  - the first questions to ask when required inputs are missing
  - how broad or ambiguous inputs must be narrowed
  - the source priority when multiple input or evidence sources are possible
- `Decision contract`
  - the order of decisions the skill makes
  - the routing or handoff boundary
  - the approval or stop boundary
  - the red flags weaker models are likely to miss
  - what counts as stronger versus weaker source material when quality is uneven
- `Output contract`
  - the default answer shape
  - the required fields
  - the length rule
  - the claims the skill must not make
- `Failure contract`
  - common failure types
  - fallback behavior
  - how the failure should be explained to the user

These sections matter because Helm should be able to govern unknown or newly added skills without relying on prompt folklore or operator memory.

## Why This Matters

A skill with only a good description is still operationally weak.

Typical weak-skill failure modes:

- the model does not know what minimum input must be collected first
- the model answers before classifying the task
- the model does not know which failure should trigger clarification vs escalation
- the model produces long answers even when the right output is a short shortlist
- the skill can technically run, but its stop boundary is invisible

Helm should push skills away from those patterns.

## Operator Policy Baseline

- start new skills at `inspect_local`
- widen to `workspace_edit` only when local file mutation is truly part of the skill
- widen to `service_ops` only when live service or API side effects are expected
- use `risky_edit` for shared scripts, routing changes, automation changes, and reusable infrastructure edits
- use `remote_handoff` explicitly instead of pretending remote execution is local
- document intentional fallback exceptions as policy decisions with clear scope, owner, and acceptance rationale

## Review Questions

- does the skill mutate durable state, or only inspect it
- is the default profile narrower than necessary, equal to necessary, or broader than necessary
- does the skill need prior context to avoid repeated mistakes
- would a weak local model benefit from a strict runner instead of open-ended command execution
- does reply-gate failure for this skill mean the manifest is too weak, or the workflow is missing a real finalization step
- does `SKILL.md` expose a real stop boundary, or only describe the workflow at a high level
- if this skill were swapped out for a new one tomorrow, would the same Helm quality bar still catch weak inputs, weak outputs, and weak failure handling
- when this skill fails, can Helm later distinguish an unresolved failure from a failure that was already superseded by a successful retry

## Review Heuristics For SKILL.md

Good signs:

- the skill asks only for the missing minimum input
- the decision order is explicit
- source priority is visible when the workflow can pull from multiple materials
- the output shape is narrow and repeatable
- time-sensitive or risky claims are visibly constrained
- failure behavior keeps the workflow moving instead of collapsing into generic apology text

Warning signs:

- large prose sections with few rules
- no default answer shape
- no explicit user-facing failure behavior
- no mention of what the skill must not do
- broad execution claims with no approval or handoff boundary

## Suggested Maintenance Loop

1. Run `python3 scripts/run_with_profile.py validate-manifests --json`
2. Run `python3 scripts/run_with_profile.py audit-manifest-quality --json`
3. Review skills whose `allowed_profiles` are broader than their actual workflow needs
4. Review `SKILL.md` for missing input, decision, output, or failure contracts
5. Tighten `approval_keywords` for account-bound or irreversible actions
6. Add `runner.entrypoint` and `runner.strict_required` where weaker models should not improvise
7. Re-run `helm validate --path <workspace>` after policy changes

## Release Bar

Before a Helm release:

- `validate-manifests` must report `ok: true`
- `audit-manifest-quality` must report `ok: true`
- README and release notes should describe the current governance model accurately
- representative skills should be tightened enough that the release does not rely on generic backfill contracts
- released skill examples should demonstrate narrow input discipline, explicit stop boundaries, and predictable output shapes
- operating docs should explain how failure review distinguishes unresolved vs superseded failures, and how intentional fallback exceptions are tracked
