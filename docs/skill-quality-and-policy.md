# Skill Quality And Policy

This document is a practical checklist for improving skill quality and operational policy in Helm workspaces.

## Skill Quality Baseline

- every promoted skill should have `SKILL.md` and `contract.json`
- `contract.json` should keep `allowed_profiles` as narrow as possible
- `default_profile` should point to the least risky path that still works
- `context.required` should be true only when the skill actually depends on prior state
- `approval_keywords` should cover irreversible, account-bound, or payment-adjacent actions
- `runner.strict_required` should be used only when freeform execution is too risky for weaker models

## Operator Policy Baseline

- start new skills at `inspect_local`
- widen to `workspace_edit` only when local file mutation is truly part of the skill
- widen to `service_ops` only when live service or API side effects are expected
- use `risky_edit` for shared scripts, routing changes, automation changes, and reusable infrastructure edits
- use `remote_handoff` explicitly instead of pretending remote execution is local

## Review Questions

- does the skill mutate durable state, or only inspect it
- is the default profile narrower than necessary, equal to necessary, or broader than necessary
- does the skill need prior context to avoid repeated mistakes
- would a weak local model benefit from a strict runner instead of open-ended command execution
- does reply-gate failure for this skill mean the manifest is too weak, or the workflow is missing a real finalization step

## Suggested Maintenance Loop

1. Run `python3 scripts/run_with_profile.py validate-manifests --json`
2. Review skills whose `allowed_profiles` are broader than their actual workflow needs
3. Tighten `approval_keywords` for account-bound or irreversible actions
4. Add `runner.entrypoint` and `runner.strict_required` where weaker models should not improvise
5. Re-run `helm validate --path <workspace>` after policy changes
