# Control Flow Is Not Memory

**Task 17** | Branch: `feat/harness-engineering-2026-05-22`

---

## 1. Principle

Source: Forge reliability layer design (reviewed 2026-05-20; see
`~/Downloads/forge-openclaw-helm-development-direction-2026-05-20.md`).

> A model's message history is memory and is subject to compaction.
> Workflow completion state, iteration count, and terminal conditions
> are control state and must live outside the transcript.

Every long-running agent session is at risk of one bug class:

1. A task is in progress. The conversation grows long. The runtime
   compacts the transcript — older messages are summarized or dropped.
2. After compaction, the model no longer has direct access to the
   messages that established "we already sent the email" or "the
   Telegram context was recovered and is awaiting action."
3. A completion check that asks the model "did we finish step X?" now
   returns an incorrect answer, because the model's only evidence was
   in the compacted region.

**Concrete example — recovered Telegram context regression:**
A user sends a Telegram message while the session is inactive. On
session resume, the message is recovered and injected into the
transcript. If the runner does not record that recovery event in
structured state, compaction can drop the injection context entirely.
The model then has no record of the pending request, and the task is
silently abandoned — not failed, not acknowledged, just lost.

This is not a model quality problem. It is an architectural problem:
control state was placed in memory.

---

## 2. What Lives Where

### Memory (transcript, summaries, compaction targets)

- User messages and assistant replies.
- Tool outputs returned inline to the conversation.
- Prior-session summaries injected as context.
- Any prose reasoning the model produced about the task.

Memory is useful but unreliable after compaction. It should be used
for reasoning input, not for completion checks.

### Control state (task-state container — Task 6, commit `25d1983`)

Structured fields in `helm_state_model.py` that survive compaction:

| Field | Purpose |
|---|---|
| `required_steps` | The full set of steps the task must complete. |
| `completed_steps` | Steps confirmed done; authoritative after compaction. |
| `blockers` | Reasons the task cannot proceed. |
| `external_side_effect_approvals` | Timestamped records of user-approved side effects. |
| `finalization_state` | Lifecycle position: `pending`, `in_progress`, `finalized`, `abandoned`. |
| `recovered_messages` | Messages injected from an external source (e.g. Telegram recovery), with per-message status. |

These fields answer "did we do X?" with system authority, not model
recollection.

---

## 3. How Helm Enforces It

### Persistence: state_io ledger

The task-state container is a plain dict serialized and written by
`save_task_state()` and read by `load_task_state()`. At task
finalization, `run_with_profile.py` writes this as `state_snapshot`
inside the task ledger row (see `01-inventory.md` Section 3 for the
ledger field inventory). On task resume, the runner calls
`load_task_state(previous_state_snapshot)` before any LLM call,
restoring control state independently of what the transcript contains.

### Dual-condition finalization gate

`is_finalized(state)` returns `True` only when both conditions hold:

1. `finalization_state == "finalized"` — the runner explicitly closed
   the task, AND
2. every entry in `required_steps` appears in `completed_steps`.

Either condition alone is insufficient. This design catches two
distinct failure modes:

- A task with all steps recorded as done but `finalization_state ==
  "pending"` is still mid-flight; the runner has not yet confirmed
  closure. Premature completion is blocked.
- A task flagged `finalized` with missing steps is malformed; the
  runner set the flag without completing the work. `is_finalized`
  raises `ValueError` rather than silently returning `False`, surfacing
  the bug immediately.

The dirty-data guard in `is_finalized` also raises `ValueError` if
`completed_steps` contains an entry not in `required_steps`, catching
callers who bypass `mark_step_completed()` and write directly to the
list.

### Recovered-context regression scenario

Before the task-state container was introduced (Task 6), the recovered
Telegram message path worked like this:

1. Recovery event happens; message injected into transcript.
2. Session continues; transcript grows; compaction fires.
3. Injection context is dropped. Model has no record.
4. Completion check returns "done" — incorrectly.

After Task 6, the path is:

1. Recovery event happens; `record_recovered_message()` is called with
   `status="active_unhandled"`.
2. Session continues; transcript may be compacted.
3. Completion check calls `unhandled_recovered_messages(state)`.
4. The list is non-empty; the runner knows the request is still open.
5. Only after `mark_recovered_message(state, id, "handled")` will the
   check clear.

The transcript may have been compacted. The control state was not.

---

## 4. Compaction Safety Contract

The compactor MUST NOT touch:

- Any JSONL row in the task ledger (append-only; never modified in place).
- Any `state_snapshot` dict stored inside a ledger row.
- Any entry in `external_side_effect_approvals` (durable approval log).
- Any `recovered_messages` entry whose `status` is `active_unhandled`.
- The `finalization_state` field itself.

Authorities that answer "did we do X?" after compaction:

- `completed_steps` in the loaded task-state — not the transcript.
- `external_side_effect_approvals` list — not a model assertion.
- `unhandled_recovered_messages(state)` — not a search of prior messages.
- The task ledger `status` field — not the model's recollection of the
  last assistant turn.

The compactor MAY summarize:

- User and assistant message turns.
- Inline tool output that was purely informational.
- Prior-session context injections once the relevant step is in
  `completed_steps`.

---

## 5. Anti-Patterns

The following patterns violate the principle. Each is a real failure
mode observed in long-running agent sessions.

**AP-1. Ask the model to recall whether an email was sent.**
Prompt: "Did we send the weekly report email yet?"
Problem: If the send-email tool call was compacted, the model answers
"I don't have that in context" or, worse, "yes" based on a summary
that omitted the send failure. The authoritative check is
`completed_steps` containing `"send_weekly_report"`, not the model.

**AP-2. Store a Telegram recovery flag in the system prompt.**
A recovered message is injected as a system prompt line:
`"[RECOVERED] User asked: schedule meeting for 3pm."` After
compaction, the line is gone. Nothing records that the request is
unhandled. Use `record_recovered_message()` instead.

**AP-3. Use `finalization_state == "finalized"` as the sole
completion check.**
If a runner sets `finalization_state = "finalized"` before all steps
finish (e.g., on timeout), `is_finalized` will raise `ValueError`
when `completed_steps` is a subset of `required_steps`. Callers that
swallow the exception and return `True` silently report a partial task
as complete. The dual-condition gate exists to prevent this.

**AP-4. Write completion evidence as an assistant message.**
The model says "I have finished the data export." This is a memory
artifact. If the session is compacted and resumed, the next runner
invocation has no structured evidence. Completion evidence must be
written to `completed_steps` by the runner, not narrated by the model.

**AP-5. Rely on iteration count from the transcript.**
"This is attempt 3 of 5" in an assistant turn is lost to compaction.
If the retry loop reads iteration count from the message history rather
than from a structured field, compaction resets the count. Track
iteration count in the task-state container or the ledger.

---

See also:
- `helm_state_model.py` — task-state container implementation (Task 6, `25d1983`)
- `docs/harness-engineering/01-inventory.md` Section 3 — ledger field inventory
- `docs/harness-engineering/06-helm-vs-forge.md` — Helm vs Forge positioning
