# HITL Decision Patterns

`scripts/hitl_decision_patterns.py` records approval and rejection history for
repeated Helm operational actions. It does not execute commands.

The default candidate threshold is:

- at least 3 approvals for the same signature
- 0 rejections

Commands:

```bash
python3 scripts/hitl_decision_patterns.py record \
  --path ~/.helm/workspace \
  --kind mark_stale \
  --reason "heartbeat expired" \
  --decision approve

python3 scripts/hitl_decision_patterns.py report --path ~/.helm/workspace

python3 scripts/hitl_decision_patterns.py approve-policy \
  --path ~/.helm/workspace \
  --action-signature "mark_stale|heartbeat expired"
```

Approved policy entries are stored in `.helm/hitl-automation-policy.json` with
a decision history snapshot. They are audit metadata only; they do not trigger
automatic execution.

Signature modes:

- `simple`: `kind|reason`
- `contextual`: `kind|reason|profile=...|skill=...|failure_stage=...|exit_code=...`
