# Helm / OpenClaw Agent Reliability Eval Suite (Forge後補 D)

**Purpose**: Behavioral regression scenarios for Helm/OpenClaw operational
failure modes.  Tests verify *behavior at the API surface* — not implementation
details — so they remain valid across refactors.

## How to run

```
pytest -q tests/eval
```

Or use the structured eval runner (emits PASS/FAIL JSON):

```
python3 scripts/eval_runner.py --all
python3 scripts/eval_runner.py --scenario 3
```

## Scenarios

| # | File | One-line description |
|---|------|---------------------|
| 1 | `test_scenario_1_inspect_only_no_file_creation.py` | Guard denies write commands under `inspect_local`; no artifact reaches disk |
| 2 | `test_scenario_2_save_request_persists_artifact.py` | `workspace_edit` save action writes artifact; `is_finalized=True` after all steps complete |
| 3 | `test_scenario_3_recovered_context_survives_compaction.py` | `active_unhandled` recovered message survives transcript compaction |
| 4 | `test_scenario_4_approval_log_contract_and_action_scope.py` | Approval-log contract (`record_approval` shape) + real `action_scope` gate for `telegram_outbound`. Scoped as a contract test — no production "send" entrypoint consults the approval log yet; see file docstring. |
| 5 | `test_scenario_5_compaction_no_false_complete.py` | `is_finalized=False` after compaction when a required step is missing (no false completion) |
| 6 | `test_scenario_6_partial_completion_not_reported_as_complete.py` | Partial run (2/3 steps + raise): ledger `outcome!="completed"`, `completed_steps` length 2 |
| 7 | `test_scenario_7_completion_claim_evidence_chain.py` | Typed evidence and claim prerequisites gate completion and postflight |
