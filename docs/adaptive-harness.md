# Adaptive Harness

Helm now exposes an adaptive harness layer above profiled execution.

This is a model-tier-aware guardrail system. It keeps the normal execution-profile layer for strong models, but increases enforcement when the active model is more brittle or when the task profile carries more operational risk.

## Building Blocks

- `references/adaptive_harness_policy.json`
- `skills/<skill>/contract.json`
- `references/adaptive_harness.d/*.json`
- `scripts/adaptive_harness.py`
- `helm harness ...`
- `scripts/reply_gate.py`

## What It Enforces

- skill/profile compatibility
- required task names for higher-risk profiles
- required runtime targets for remote handoff
- context hydration for contracts that depend on prior state
- preferred narrow runners in strict mode
- postflight finalization checks so a task is not treated as complete while durable capture is still pending
- profile-level completion evidence checks for higher-risk postflight paths
- skill-level artifact validation checks for workflows that write generated files, notes, reports, or reusable state

## Completion Evidence Gate

`references/adaptive_harness_policy.json` includes a `completion_policy`
section. When the current enforcement level is at or above the configured
threshold, postflight requires profile-appropriate evidence before a task can
be considered complete.

Default profile signals:

- `workspace_edit`: file diff, diagnostics, direct inspection, write validation, or explicit completion evidence
- `risky_edit`: checkpoint id plus test/lint/diff, write validation, or explicit completion evidence
- `service_ops`: process exit, healthcheck, provider result, or explicit completion evidence
- `remote_handoff`: handoff target or explicit completion evidence

Completion evidence can come from existing runner fields such as `exit_code`,
`checkpoint_id`, `memory_capture.write_validation`, checkpoint paths, touched
paths, and `completion_evidence` entries. Explicit entries should use compact
`kind:value` strings, for example `test:pytest`, `diff:reviewed`,
`healthcheck:ok`, or `provider:request-id`.
See [Evidence Label Convention](evidence-label-convention.md) for the standard
label vocabulary.

Operators can append explicit evidence after review:

```bash
helm harness --path ~/.helm/workspace record-evidence \
  --task-id <task-id> \
  --completion-evidence test:pytest \
  --completion-evidence diff:reviewed
```

## Artifact Validation Gate

Skills that create durable artifacts can declare an `artifact_validation`
section in `contract.json`. This makes postflight require a successful
workspace-specific validator result before the task is treated as complete.

```json
{
  "artifact_validation": {
    "required": true,
    "required_fields": ["ok", "checked_paths"]
  }
}
```

After the workspace validator runs, record the result on the task ledger:

```bash
helm harness --path ~/.helm/workspace record-evidence \
  --task-id <task-id> \
  --write-validation-json '{"ok": true, "checked_paths": ["notes/generated.md"], "validator": "workspace-structure-audit"}'
```

`checked_paths` should name the artifacts that were actually inspected. If the
validator reports `ok: false`, or if no checked path is recorded, postflight
fails the artifact validation check.

For Obsidian-backed workflows, one validator can branch by extension while still
reporting the same Helm evidence shape:

```bash
python3 ~/.openclaw/workspace/scripts/obsidian_artifact_validate.py \
  --path ~/Documents/ObsidianVault/04-Resources/Notes/Maps/project-candidates.canvas \
  --json
```

The resulting `write_validation` should preserve the artifact type, validator
name, status, and checked path so postflight can distinguish Markdown,
Base, and Canvas failures.

If a harness-managed command exits with code 0 but fails `completion_policy`,
the harness appends a `needs_verification` task state so the ledger reflects
that command execution finished but operational verification did not.

## Audit Interpretation

Harness audit output should be read as an operator signal, not just a raw counter dump.

- distinguish unresolved failures from failures that were later superseded by a successful retry or corrected follow-up
- treat repeated raw `failed` rows without that distinction as an incomplete audit view
- keep intentional fallback exceptions visible as policy exceptions instead of hiding them behind silent code relaxations

This matters because the operating layer should tell an operator whether the system is still broken, already recovered, or intentionally running with a bounded exception.

New skills do not need central harness edits anymore if they ship their own `contract.json`.

- `scripts/skill_capture.py create` scaffolds `contract.json`
- `scripts/skill_capture.py draft-from-task` scaffolds `contract.json`
- `scripts/adaptive_harness_lib.py` loads skill-local contracts directly
- `allowed_profiles` and `default_profile` now live in the skill contract instead of a required central registry
- `scripts/run_with_profile.py validate-manifests` audits malformed or missing manifests

## Typical Commands

```bash
helm harness --path ~/.helm/workspace policy
helm harness --path examples/demo-workspace contract --skill router-context-demo
helm harness --path examples/demo-workspace preflight --skill router-context-demo --profile inspect_local --model gemma4:e4b --task-name "router triage" --request "라우터 변경 전에 필요한 컨텍스트를 먼저 점검해줘" -- python3 -c 'print("ok")'
python3 /Users/kevin/Helm/scripts/reply_gate.py --json
```
