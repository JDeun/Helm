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
