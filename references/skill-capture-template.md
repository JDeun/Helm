---
name: __SKILL_NAME__
description: __SKILL_DESCRIPTION__
metadata:
  openclaw:
    emoji: "__EMOJI__"
    requires:
      bins: []
      env: []
---

# __SKILL_NAME__

## Core rule

State the narrow rule that governs this skill.
This should explain how the skill stays safe, focused, and predictable.

## Input contract

- Required inputs
- Optional inputs
- Ask first when missing
- If the request is broad or ambiguous, how it must be narrowed
- If multiple source types can drive the skill, state the source priority explicitly

## Decision contract

- State the decision order explicitly.
- Explain when the skill should route outward, stop, ask for approval, or escalate.
- List the red flags weaker models are likely to miss.
- If the skill depends on evidence quality, say what counts as stronger versus weaker source material.

## Execution contract

- State the real commands, tools, or APIs to use.
- Prefer deterministic scripts over freeform shell improvisation.
- If the workflow has risk, mention the execution profile or checkpoint rule.

## Output contract

- Default output format
- Always include
- Length rule
- Do not say or Do not imply

## Failure contract

- Failure types
- Fallback behavior
- User-facing failure language

## Do

- List the most important positive behaviors to preserve.

## Do not

- List what this skill must not do.
- Point to downstream tools or sibling skills when the request belongs elsewhere.
