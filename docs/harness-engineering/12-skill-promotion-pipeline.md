# Skill Promotion Pipeline — Wave 4

**Branch:** `feat/wave4-digest-2026-05-22`
**Date:** 2026-05-22

---

## 1. Why Telegram Digest

The OQ-4 open question from the Wave 0 conversation asked: *how does Kevin review
and approve skill scaffold candidates without needing to run CLI commands each day?*

Telegram was chosen as the daily UX because Kevin already uses it for ops alerts.
A short daily (or weekly) message lists the top candidates, each with a short ID.
Kevin replies with `approve <id>` or `reject <id> [reason]` inline; the workspace
Telegram transport forwards those replies back to `handle_reply()`, which updates
the state file and optionally kicks off a draft promotion.

This keeps the review loop under 60 seconds and surfaces candidates without
requiring an active terminal session.

---

## 2. Data Flow

```
traces/*.json
     |
     v
trace_to_skill.load_recent_traces()
     |
     v
trace_to_skill.skill_scaffold_candidates()
     |
     v
skill_promotion_digest.build_digest()   ← filters via skill_promotion_state
     |                                    records newly-notified candidates
     v
delivery payload dict
     |
     v  (workspace-side Telegram transport — NOT this module)
Telegram message → Kevin
     |
     v
Kevin replies: "approve abc12345"
     |
     v  (workspace-side transport receives reply text)
skill_promotion_approval.handle_reply()
     |
     v
skill_promotion_state.mark_approved()
     +------→ optional approve_callback(cid, trace_id)
                  |
                  v
              skill_capture_ext.draft_from_trace()  ← caller decides
```

The modules in this document handle everything from `build_digest()` through
`handle_reply()`. Telegram I/O and `draft_from_trace()` calls are workspace-side
concerns.

---

## 3. Module Roles

### `scripts/skill_promotion_state.py`

Owns the on-disk ledger (`skill-promotion-state.json`). Provides the core
CRUD-like API: `load_state`, `save_state`, `record_notified`, `mark_approved`,
`mark_rejected`, `pending_approvals`, `is_processed`. All writes go through
`scripts.io_utils.atomic_write_json` to prevent partial writes on crash. The
`candidate_id` is a stable 8-hex SHA-256 digest of `(skill, task_name)`, so the
same candidate produces the same ID across runs.

### `scripts/skill_promotion_digest.py`

Builds the Telegram-ready payload by loading traces, calling
`skill_scaffold_candidates`, and classifying each qualifying (skill, task_name)
pair as `"new"` (not yet notified) or `"reminder"` (notified but not approved or
rejected). The payload is capped at `max_candidates` (default 5); extras generate
a `+ N more` line. `summary_text` is guaranteed to fit in 800 UTF-8 bytes. New
candidates are persisted via `record_notified` before returning.

### `scripts/skill_promotion_approval.py`

Parses raw Telegram reply text with a single regex and applies the matching state
transition. `parse_reply()` is a pure function — no I/O. `handle_reply()` loads
state, checks for unknown or already-processed IDs, applies the transition, saves
state, and calls optional callbacks. The `approve_callback(cid, trace_id)` hook
lets callers invoke `draft_from_trace()` without coupling this module to
`skill_capture_ext`.

### `commands/skill_promotion.py`

CLI shim that exposes five `helm skill-promotion` subcommands: `digest`, `approve`,
`reject`, `pending`, `state-path`. Follows the existing `commands/` pattern
(argparse Namespace dispatch, `--state-path` override on every subcommand). Wired
into `helm.py` as the `skill-promotion` top-level command.

---

## 4. State File Shape

Default path: `~/.openclaw/workspace/.openclaw/skill-promotion-state.json`
Override: `OPENCLAW_SKILL_PROMOTION_STATE` env var.

```json
{
  "entries": [
    {
      "candidate_id": "a1b2c3d4",
      "fingerprint": {
        "skill": "my-skill",
        "task_name": "deploy the staging service",
        "count": 7
      },
      "notified_at": "2026-05-22T08:00:00.123456+00:00",
      "status": "notified"
    },
    {
      "candidate_id": "cafebabe",
      "fingerprint": {
        "skill": "audit-runner",
        "task_name": "run daily audit",
        "count": 5
      },
      "notified_at": "2026-05-21T08:00:00.000000+00:00",
      "status": "approved",
      "approved_by": "kevin",
      "approved_at": "2026-05-21T09:15:03.000000+00:00"
    },
    {
      "candidate_id": "deadbeef",
      "fingerprint": {
        "skill": null,
        "task_name": "ad-hoc fetch",
        "count": 4
      },
      "notified_at": "2026-05-20T08:00:00.000000+00:00",
      "status": "rejected",
      "rejected_at": "2026-05-20T10:00:00.000000+00:00",
      "reason": "too narrow a use-case"
    }
  ]
}
```

---

## 5. Reply Vocabulary

| Message form | Effect |
|---|---|
| `approve <id>` | Marks the candidate approved; triggers optional `approve_callback`. |
| `reject <id>` | Marks the candidate rejected with no reason stored. |
| `reject <id> <reason text>` | Same, with a free-text reason stored in state. |
| `details <id>` | Returns `outcome: "ok"` with no state change; caller can look up trace details. |

Rules:
- The action verb is case-insensitive.
- `<id>` must be exactly 8 lowercase hexadecimal characters.
- Any other message returns `None` from `parse_reply()` and `"not_an_approval"` from `handle_reply()`.

---

## 6. Operator Runbook

### Preview the digest

```bash
helm skill-promotion digest \
  --cadence daily \
  --max 5
```

Prints the full JSON payload to stdout. Check `summary_text` to see what would be
sent to Telegram.

### Inspect pending candidates

```bash
helm skill-promotion pending
# or machine-readable:
helm skill-promotion pending --json
```

### Manual approve / reject (bypass Telegram)

```bash
helm skill-promotion approve a1b2c3d4
helm skill-promotion reject deadbeef --reason "too narrow"
```

### Check where the state file lives

```bash
helm skill-promotion state-path
# Override via env:
OPENCLAW_SKILL_PROMOTION_STATE=/tmp/test-state.json helm skill-promotion state-path
```

### Override traces or state paths per-run

All subcommands accept `--state-path` and `digest` additionally accepts
`--traces-dir`. This is useful for testing or for running against a secondary
workspace.

---

## 7. Open Questions for Follow-up

1. **Cadence (daily vs weekly):** The `cadence` field is informational only. The
   scheduling of actual Telegram delivery is workspace-side. Should a cron trigger
   call `build_digest(cadence="daily")` each morning, or should it be weekly with
   a burst of reminders? The answer depends on trace volume per week.

2. **Retention of approved entries:** Currently approved entries remain in the
   state file indefinitely. A retention policy (e.g. delete after 30 days, or
   after the corresponding skill draft is promoted) has not been defined.

3. **Auto-expire long-pending candidates:** Candidates in `"notified"` status
   accumulate over time if Kevin never replies. Should there be a TTL (e.g. 14
   days) after which a candidate is auto-rejected or removed from the pending
   list?

4. **Multiple workspaces:** The state file path is a single global path. If
   multiple OpenClaw workspaces run traces, their candidates will share one ledger.
   Workspace-scoped state paths (or a namespace key in the state) may be needed.

5. **Fingerprint evolution:** The `fingerprint` stored in state captures `skill`,
   `task_name`, and `count` at notification time. If the candidate accumulates more
   successes between notification and approval, the count is stale. This is
   cosmetic today but may matter if count is used as a promotion threshold guard.
