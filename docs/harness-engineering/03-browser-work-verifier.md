# Harness Engineering — Task 13: Browser Work Verifier

**Status:** Design document (no implementation)
**Branch:** feat/harness-engineering-2026-05-22
**Source inputs:**
- `~/Downloads/chromux-openclaw-helm-adoption-plan-2026-05-19.md` §"Helm 반영 방향" items 1, 2, 5
- `docs/harness-engineering/01-inventory.md` §1 (execution profile shapes)

**Document structure choice:** Split into two files (`03-browser-work-verifier.md` and
`04-browser-profile-policy.md`). The verifier schema and the profile policy are related but
distinct: the verifier examines a candidate task and emits a decision dict; the profile policy
defines what that decision enforces. Each has its own YAML/table schema, its own source of
truth, and will have its own implementation unit in Task 14. Keeping them separate keeps each
file under the 200-line-per-section budget and makes cross-referencing explicit.

---

## Out of Scope

This document does NOT specify:

- The pause/resume hard-stop mechanism (Task 14)
- chromux skill content (Task 11)
- Site-note-policy details (Task 12)

---

## 1. Purpose

`browser_recon_verifier` is a pre-flight gate that runs **before any browser task** is
dispatched. It examines the candidate task, consults available context (site notes, task
ledger, profile policy), and emits a `BrowserReconDecision` dict. The runner (`run_with_profile.py`)
checks this decision before acquiring a browser session; a blocking decision prevents the
session from opening.

The verifier is not a guard policy rule — it is a higher-level semantic check that operates on
browser-specific signals that the existing `command_guard` layer does not model (login state,
mutation surface, parallel safety, site history).

---

## 2. Input Fields

The verifier receives a `BrowserTaskSpec` dict. All fields are required unless noted.

| Field | Type | Required | Description |
|---|---|---|---|
| `url_pattern` | `str` | Yes | The URL or URL pattern for the target site (e.g. `https://example.com/*`). Used to look up site notes and profile-policy rules. |
| `intended_action` | `enum` | Yes | One of: `read`, `fillform`, `submit`, `navigate`, `fetch_resource`, `crawl_batch`, `screenshot`, `interact`. See §3. |
| `logged_in_account_required` | `bool` | Yes | Whether the task explicitly needs a logged-in user session. |
| `parallel_requested` | `bool` | Yes | Whether the caller wants multiple concurrent sessions for this task. |
| `existing_site_note_path` | `str \| None` | No | Path to a pre-located site note file, if the caller has already resolved it. `None` triggers verifier-internal lookup by `url_pattern`. |

### `intended_action` Vocabulary

| Value | Mutation? | Notes |
|---|---|---|
| `read` | No | Passive page load, accessibility snapshot, screenshot only |
| `navigate` | No | Navigation between pages; no form interaction |
| `fetch_resource` | No | Downloading a file or resource; no page interaction |
| `screenshot` | No | Visual capture only |
| `crawl_batch` | No | Multi-URL batch traversal; read-only per URL |
| `fillform` | **Maybe** | Fills form fields but does not submit |
| `interact` | **Yes** | Clicks, toggles, UI state changes short of submission |
| `submit` | **Yes** | Form or workflow submission |

---

## 3. Decision Values

The verifier returns a `BrowserReconDecision` dict. Multiple values can be set simultaneously
(e.g. `allow_single_session: true` and `require_confirmation: true`).

| Decision Key | Type | Meaning |
|---|---|---|
| `allow_single_session` | `bool` | One browser session is safe to open. |
| `allow_parallel` | `bool` | Multiple concurrent sessions are safe. Always `false` when `allow_single_session` is `false`. |
| `require_user_login` | `bool` | The logged-in user Chrome profile is needed; agent must not use an anonymous profile. |
| `require_confirmation` | `bool` | A human confirmation gate must fire before the session proceeds. |
| `block_mutation` | `bool` | Any mutation `intended_action` is refused for this URL/profile combination. |
| `pause_profile` | `bool` | The browser profile for this execution profile should enter pause state (no new sessions). Implies `allow_single_session: false`. |

A `BrowserReconDecision` must include at minimum `allow_single_session`. If that is `false`,
the runner aborts before session open.

---

## 4. Verifier Check List

Each check has: what it examines, its source of truth, which decision keys it can set, and the
default outcome when the source of truth is absent.

### Check 1 — Login Required?

**Question:** Does this URL/task require an authenticated session?

| Attribute | Value |
|---|---|
| Source of truth | `logged_in_account_required` input field; site note `auth_required` flag |
| Decision impact | `require_user_login`, `require_confirmation` |
| Default if unknown | `require_user_login: false` — assume public unless stated |

If `logged_in_account_required: true` and the active execution profile has
`allow_logged_in_profile: false`, the verifier emits `allow_single_session: false`
and surfaces a reason string.

---

### Check 2 — Can the Logged-In User Profile Be Used?

**Question:** Is the current execution profile permitted to use the real user Chrome profile?

| Attribute | Value |
|---|---|
| Source of truth | Profile policy field `allow_logged_in_profile` (see `04-browser-profile-policy.md`) |
| Decision impact | `require_user_login`, `block_mutation`, `allow_single_session` |
| Default if unknown | `allow_logged_in_profile: false` — deny logged-in profile by default |

If the profile policy says `allow_logged_in_profile: false` but login is required, the
verifier blocks the task and surfaces an upgrade path (e.g. use `service_ops`).

---

### Check 3 — Read-Only or Mutation?

**Question:** Does the `intended_action` write, submit, or alter state on the target site?

| Attribute | Value |
|---|---|
| Source of truth | `intended_action` field; mutation vocabulary in §2 |
| Decision impact | `block_mutation`, `require_confirmation` |
| Default if unknown | Treat as mutation if `intended_action` is not in the read-only set |

If the action is in `{fillform, interact, submit}` and the profile policy has
`allow_mutation: false`, the verifier emits `block_mutation: true`.

---

### Check 4 — Dangerous Mutation Surface Present?

**Question:** Is this a form submission, message send, purchase, or settings mutation?

| Attribute | Value |
|---|---|
| Source of truth | `intended_action` (specifically `submit`); site note `mutation_classes` list |
| Decision impact | `require_confirmation`, `block_mutation` |
| Default if unknown | If `intended_action == submit` and no site note: `require_confirmation: true` |

When `intended_action == submit` and `allow_mutation: gated` in the profile policy, the
verifier emits `require_confirmation: true`. If `allow_mutation: false`, it emits
`block_mutation: true` regardless.

---

### Check 5 — Captcha / Auth Wall / Rate-Limit History?

**Question:** Has this site previously returned a captcha, auth challenge, or rate limit?

| Attribute | Value |
|---|---|
| Source of truth | Site note `captcha_observed`, `auth_wall_observed`, `rate_limit_observed` flags; prior task ledger entries for this `url_pattern` |
| Decision impact | `require_confirmation`, `pause_profile` |
| Default if unknown | No flag set — proceed without extra gate |

If a site note records any of these, the verifier emits `require_confirmation: true`. If the
prior task ledger shows repeated rate-limit hits (≥3 in last 24 h, heuristic), it also emits
`pause_profile: true`.

---

### Check 6 — Parallel-Safe?

**Question:** Can multiple sessions run against this URL simultaneously?

| Attribute | Value |
|---|---|
| Source of truth | `parallel_requested` input field; site note `parallel_safe` flag; profile policy `max_sessions` |
| Decision impact | `allow_parallel`, `require_confirmation` |
| Default if unknown | `allow_parallel: false` — single session unless explicitly safe |

`allow_parallel: true` requires ALL of: `parallel_requested: true`, site note
`parallel_safe: true` (or no site note + read-only action), and profile policy
`max_sessions > 1`.

---

### Check 7 — Existing Site Note Present?

**Question:** Is there a durable site note for this URL pattern?

| Attribute | Value |
|---|---|
| Source of truth | `existing_site_note_path` input field; fallback: file lookup under `references/browser-site-notes/<host>.md` |
| Decision impact | Enriches all other checks; absence does NOT itself block. Absence + mutation → `require_confirmation: true` |
| Default if unknown | No site note — treat as first-time visit; conservative defaults apply to checks 1–6 |

A site note's presence does not by itself grant permission; it is a data source that can
either relax or tighten each other check.

---

## 5. Decision Summary Matrix

This matrix shows the most common `intended_action` × profile combinations and the resulting
decision. "Profile" refers to the Helm execution profile, not the Chrome profile.

| Execution Profile | `intended_action` | `logged_in_required` | Typical Decision |
|---|---|---|---|
| `inspect_local` | `read`, `crawl_batch` | false | `allow_single_session: true`, `allow_parallel: true` |
| `inspect_local` | `read` | true | `allow_single_session: false` (profile cannot use logged-in Chrome) |
| `inspect_local` | `submit` | any | `block_mutation: true` |
| `service_ops` | `read` | true | `allow_single_session: true`, `require_user_login: true` |
| `service_ops` | `submit` | true | `allow_single_session: true`, `require_confirmation: true` |
| `service_ops` | `crawl_batch` | false | `allow_single_session: true`, `allow_parallel: true` (up to `max_sessions: 3`) |
| `risky_edit` | `submit` | any | `allow_single_session: true`, `require_confirmation: true`, `pause_profile` on repeated failure |
| `risky_edit` | `crawl_batch` | false | `allow_single_session: true`, `allow_parallel: true` |

---

## 6. Integration Points

- The verifier runs inside `run_with_profile.py` after guard evaluation and before subprocess
  launch, when the command targets a browser skill (`chromux`, `chromux-work`, or any skill
  whose `contract.json` declares `browser: true`).
- The decision dict is written into the ledger row under a new `browser_recon` key (alongside
  the existing `guard` key). This feeds the evidence-first task ledger described in the
  chromux adoption plan §"Helm 반영 방향" item 5.
- `browser_profile`, `browser_mode`, `sessions`, `snapshot_evidence`, `cleanup_status`, and
  `site_note_update` ledger fields (currently absent per `01-inventory.md` §3) are populated
  by the browser skill post-run, not by the verifier.

---

*End of browser work verifier design. Implementation: Task 14.*
