# Loops

Helm loops are small, reusable workflow contracts. They name when a repeated
agent operation should run, what evidence is required, and when the operator
should stop instead of retrying.

Loops are not an agent runtime. They are validation and planning artifacts that
fit the existing task ledger, checkpoint, and completion-evidence model.

## Commands

```bash
helm loops validate examples/loops/completion-evidence.yaml
helm loops inspect completion-evidence
```

Use `--json` when another tool needs a machine-readable result.

## File Shape

Required fields:

- `id`
- `title`
- `use_when`
- `steps`
- `required_evidence`
- `stop_conditions`

Keep loop files short. Add a runner only after a loop has proven useful in more
than one real task.

## Built-In Examples

- `examples/loops/completion-evidence.yaml`
- `examples/loops/docs-sweep.yaml`

## Pipeline Reference

`references/pipelines/coding-task-finalization-pipeline.yaml` shows how the same
contract style can describe a multi-stage coding finalization workflow without
replacing the existing harness.
