# Action Governance

Helm treats prompt safety as advisory and execution governance as the control
surface. Mutable actions are checked by a deterministic registry before a tool
or mutator script is allowed to run.

## Registry

The default registry is `references/action_governance_registry.json`.

Each action declares:

- `action_id`
- `resource_type`
- `required_scope`
- `mutates`
- `needs_live_source`
- `requires_approval`
- `default_decision`
- `evidence_contract`

The initial registry covers Telegram outbound messages, Google Calendar create,
update, and delete, Google Sheets ledger append and update, file writes, git
commit and push, cron add, update, and remove, and high-risk smart-home control.
Unknown actions fail closed with `deny`.

## Policy Evaluation

`scripts/action_governance.py` combines the action registry with
`scripts/action_scope.py`.

The evaluator records one of four decisions:

- `allow`: policy, scope, target, live-source, and approval requirements are met
- `deny`: the action is unregistered, below required scope, missing a target,
  missing live-source confirmation, or explicitly rejected
- `require_approval`: the action is known and scoped but needs human approval
- `inspect_only`: the current message is read-only but the attempted action
  mutates state

The user message is stored as a SHA-256 hash by default. A redacted preview is
optional for local diagnostics.

## Decision Record

Every governed action can be serialized as a standard decision record with:

- timestamp
- session id
- user message hash or redacted preview
- parsed scope
- attempted action
- resource and target
- policy version
- decision and reason
- live-source requirement
- approval status
- execution status
- verification result
- evidence contract

Use `append_decision_record(path, record)` to append the record to JSONL.
`scripts.trace_recorder` also exposes `record_governance_decision` so a task
trace can keep the same record beside tool calls and validation gates.

`scripts/run_with_profile.py run ...` evaluates command guard first, then
enforces the profile's `tool_grant`, then evaluates this action-governance
registry before the subprocess is started. Governance records are appended to
`.helm/action-governance-decisions.jsonl`. `deny` and `inspect_only` block the
run, and `require_approval` records a resumable approval pause instead of
executing the command.

## Evidence Contract

Completion is separate from execution. `validate_evidence_contract(action_id,
evidence)` checks the registry's `require_all` and `require_one_of` keys before
a completion claim is considered grounded.

Examples:

- `file_write` requires at least one of `filesystem_stat`, `git_diff`, or
  `write_validation`
- `git_push` requires both `local_head` and `remote_head`
- `telegram_outbound` requires `provider_result` and either `message_id` or
  `send_result`

Evidence validation results can be attached to a trace with
`record_evidence_contract`.
