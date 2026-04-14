# Adaptive Harness

Helm now exposes an adaptive harness layer above profiled execution.

This is a model-tier-aware guardrail system. It keeps the normal execution-profile layer for strong models, but increases enforcement when the active model is more brittle or when the task profile carries more operational risk.

## Building Blocks

- `references/adaptive_harness_policy.json`
- `references/skill_contracts.json`
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

## Typical Commands

```bash
helm harness --path ~/.helm/workspace policy
helm harness --path ~/.helm/workspace contract --skill travel-ops-ko
helm harness --path ~/.helm/workspace preflight --skill travel-ops-ko --profile inspect_local --model gemma4:e4b --task-name "travel triage" --request "출장 항공편 옵션을 먼저 정리해줘" -- python3 -c 'print("ok")'
python3 /Users/kevin/Helm/scripts/reply_gate.py --json
```
