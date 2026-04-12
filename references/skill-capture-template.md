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

## Purpose

Describe the workflow this skill owns and the outcome it should produce.

## When To Use

- List the user intents or trigger phrases that should activate this skill.
- Mention adjacent skills or routers that should yield to this one.

## Inputs

- Required inputs
- Optional context
- Clarifying questions to ask only when necessary

## Execution

- State the real commands, tools, or APIs to use.
- Prefer deterministic scripts over freeform shell improvisation.
- If the workflow has risk, mention the execution profile or checkpoint rule.

## Output

- State what a successful answer must include.
- State how partial success and failure should be reported.

## Boundaries

- List what this skill should not do.
- Point to downstream tools or sibling skills when the request belongs elsewhere.
