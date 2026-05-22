# Harness Engineering — Task 13: Browser Profile Policy

**Status:** Design document (no implementation)
**Branch:** feat/harness-engineering-2026-05-22
**Source inputs:**
- `~/Downloads/chromux-openclaw-helm-adoption-plan-2026-05-19.md` §"Helm 반영 방향" items 1, 2
- `docs/harness-engineering/01-inventory.md` §1 (execution profile shapes)

---

## Out of Scope

This document does NOT specify:

- The pause/resume hard-stop mechanism (Task 14)
- chromux skill content (Task 11)
- Site-note-policy details (Task 12)

---

## 1. Purpose

Helm's five execution profiles (`inspect_local`, `workspace_edit`, `risky_edit`, `service_ops`,
`remote_handoff`) are defined in `execution_profiles.json` and govern general task isolation and
write permissions. Browser work adds a second dimension of policy that the existing profiles do
not model: Chrome profile identity, mutation gates, and session parallelism.

The `browser_profile_policy` YAML extends exactly **three** of the five execution profiles
with browser-specific constraints. The other two are excluded for the reasons documented in §2.

---

## 2. Why Three Profiles, Not Five

| Execution Profile | Browser Policy? | Reason |
|---|---|---|
| `inspect_local` | Yes | Read-only browser tasks are common; needs explicit parallelism cap and logged-in prohibition |
| `workspace_edit` | No | Browser work under `workspace_edit` is not a recognised workflow; any browser action here should be reclassified |
| `risky_edit` | Yes | Large fan-out / bulk crawl tasks that need checkpoint + pause/resume belong here |
| `service_ops` | Yes | Logged-in browser operations (forms, SaaS UI, authenticated reads) are the primary browser use case |
| `remote_handoff` | No | `remote_handoff` targets a remote host or container; the Chrome profile identity and session cap cannot be determined locally. No browser-policy story exists until Task 14 defines a remote browser handoff abstraction |

`workspace_edit` is excluded because browser automation is not part of its purpose (local file
edits, deterministic maintenance). Any task that needs browser access should be reclassified as
`inspect_local`, `service_ops`, or `risky_edit` before dispatch.

---

## 3. Policy YAML Schema

The canonical file will live at:

```
~/Helm/references/browser_profile_policy.yaml
```

Rationale for this path: the existing `execution_profiles.json` lives under
`<workspace>/references/`, and all structured policy documents for the harness live there.
Keeping `browser_profile_policy.yaml` in the same directory makes the policy surface
discoverable and avoids a new directory level.

```yaml
browser_profiles:
  inspect_local:
    allowed_modes: [crawl, default]
    allow_logged_in_profile: false
    allow_mutation: false
    max_sessions: 5

  service_ops:
    allowed_modes: [default]
    allow_logged_in_profile: true
    allow_mutation: gated
    max_sessions: 3

  risky_edit:
    allowed_modes: [default, crawl]
    allow_logged_in_profile: false
    require_checkpoint: true
    require_pause_resume: true
    require_cleanup_evidence: true
    max_sessions: 2
```

### Field Reference

| Field | Type | Applies to | Description |
|---|---|---|---|
| `allowed_modes` | `list[str]` | all | chromux modes the profile may use. `default` = standard interactive Chrome; `crawl` = resource-limited batch mode |
| `allow_logged_in_profile` | `bool` | all | Whether the task may use the real user Chrome profile (with active cookies/sessions) |
| `allow_mutation` | `bool \| "gated"` | `inspect_local`, `service_ops` | `false` = mutations blocked; `true` = allowed freely; `"gated"` = allowed only with human confirmation |
| `max_sessions` | `int` | all | Maximum concurrent browser sessions for this execution profile |
| `require_checkpoint` | `bool` | `risky_edit` | A harness checkpoint must exist before any session opens |
| `require_pause_resume` | `bool` | `risky_edit` | Pause/resume hard-stop must be active for the profile during the task |
| `require_cleanup_evidence` | `bool` | `risky_edit` | Task ledger row must contain `cleanup_status: confirmed` before the task is marked complete |

---

## 4. How `allow_mutation: gated` Works

`allow_mutation: gated` (used by `service_ops`) means mutation is structurally permitted but
requires a human confirmation gate before the browser executes the mutating action.

Interaction flow:

1. The `browser_recon_verifier` (see `03-browser-work-verifier.md`) detects a mutation
   `intended_action` (`fillform`, `interact`, `submit`) against a `service_ops` task.
2. The verifier emits `require_confirmation: true` in its decision dict.
3. `run_with_profile.py` checks the decision and, when `require_confirmation: true`:
   - If `--approve-risk` was passed at invocation time: confirmation is pre-approved and logged.
   - Otherwise: the runner exits with `EXIT_GUARD_REQUIRE_APPROVAL` (code 24), prompting the
     caller to re-invoke with `--approve-risk`.
4. The pre-approval is recorded in the ledger row under `browser_recon.confirmation_source`.

`gated` does NOT mean "always ask". If the task was pre-approved at invocation time
(`--approve-risk`), the confirmation is silent but still audited. This preserves the
automated-pipeline use case while maintaining the audit trail.

`gated` also does NOT require a site note. A site note may relax the confirmation (if
`mutation_classes` are documented and the task matches a known-safe class), but absence of a
site note does not block `gated` mutations — it only means the confirmation cannot be
pre-informed by historical context.

---

## 5. Migration: Linking `execution_profiles.json` to Browser Policy

The existing `execution_profiles.json` must not be modified in ways that break current consumers
(the runner, the guard, skill manifests). The link to browser policy is additive.

### Proposed Migration

Add an optional `browser_policy_ref` field to each profile entry in `execution_profiles.json`:

```json
{
  "inspect_local": {
    "description": "...",
    "backend": "local",
    ...
    "browser_policy_ref": "inspect_local"
  },
  "service_ops": {
    ...
    "browser_policy_ref": "service_ops"
  },
  "risky_edit": {
    ...
    "browser_policy_ref": "risky_edit"
  },
  "workspace_edit": {
    ...
    "browser_policy_ref": null
  },
  "remote_handoff": {
    ...
    "browser_policy_ref": null
  }
}
```

Rules for consuming code:

- `browser_policy_ref: null` means no browser policy applies; any attempt to open a browser
  session under this profile is rejected by the verifier.
- `browser_policy_ref: "<name>"` is a key into `browser_profile_policy.yaml`.
- Existing consumers that do not read `browser_policy_ref` are unaffected (the field is new and
  optional).
- The verifier is the only consumer of `browser_policy_ref` at Task 14 scope. Future consumers
  (e.g. a browser session manager) will follow the same lookup pattern.

This migration requires one change to `execution_profiles.json` and zero changes to the runner,
the guard, or any existing skill manifest.

---

## 6. Open Questions

These are unresolved design questions for Kevin to weigh in on. No answers are provided here.

1. **`allow_mutation: gated` — confirmation AND site note, or either?**
   Should `gated` require BOTH a prior site note documenting the mutation class AND a
   user `--approve-risk` flag, or is either condition alone sufficient to allow the mutation
   to proceed? The stricter interpretation would make first-run mutations harder; the looser
   one may allow poorly-understood mutations through.

2. **`risky_edit` and logged-in profile: permanent prohibition or escalation path?**
   The current schema sets `allow_logged_in_profile: false` for `risky_edit`. If a bulk crawl
   task under `risky_edit` legitimately needs a logged-in session (e.g. a batch audit of a
   private dashboard), is there an escalation path, or is that always a misclassification?

3. **`max_sessions` enforcement location: verifier, runner, or profile daemon?**
   The verifier can check the cap at pre-flight, but it cannot enforce it at runtime (sessions
   may already be open from prior tasks). Should runtime enforcement live in the runner's
   session registry, in the chromux profile daemon, or in a new broker layer?

4. **`remote_handoff` browser policy: stub or hard error?**
   When `browser_policy_ref: null` and a task attempts a browser action, should the verifier
   emit a hard block with an error message, or a soft warning that lets the task proceed with
   no policy constraints? A hard block is safer but may be disruptive for ad-hoc remote work.

5. **Site note lookup: where does the verifier search?**
   The verifier must resolve `url_pattern` to a site note file. Should it look only under
   `references/browser-site-notes/<host>.md` (a fixed path convention), or also accept a
   registry file (e.g. `references/browser-site-notes/index.yaml`) that maps patterns to
   paths? The registry approach handles subpath specificity but adds maintenance overhead.

6. **Ledger field placement: `browser_recon` as a top-level key or nested under `guard`?**
   The verifier decision dict is structurally parallel to the existing `guard` dict in the
   ledger row. Should `browser_recon` be a sibling key (clean separation) or nested under
   `guard` as a `browser_recon_detail` subfield (single guard object)? Nesting reduces schema
   surface but conflates two different evaluation layers.

7. **`require_cleanup_evidence` for `risky_edit`: who writes `cleanup_status`?**
   The skill is expected to write `cleanup_status: confirmed` to the ledger before the task
   closes. Should the runner enforce this at finalization (exit with error if absent), or
   should it be advisory (logged as a warning but not blocking)? Enforcement is safer but
   requires skills to be updated to emit the field.

8. **`workspace_edit` exclusion: should it produce a verifier warning or a hard block?**
   If an agent attempts a browser task under `workspace_edit` (which has no browser policy),
   should the verifier produce a hard block with a reclassification suggestion, or a warning
   that allows the task to proceed unconstrained? A hard block enforces clean classification;
   a warning allows transitional workflows while the agent learns to reclassify.

---

## 7. Resolution

Wave 3b (`feat/wave3b-policy-resolve-2026-05-22`) encoded the following decisions for all open
questions from §6.

| OQ | Decision | Code location |
|---|---|---|
| OQ-1 | `gated` mutation: `--approve-risk` OR `existing_site_note_path` satisfies the gate. Verifier always emits `require_confirmation=True`; runner checks both paths. | `scripts/run_with_profile.py` `_evaluate_browser_gate` |
| OQ-2 | `risky_edit` + `logged_in_required`: permanent block (`allow_logged_in_profile=false`). Reason string names `service_ops` upgrade path. | `scripts/browser_work_verifier.py` `_check_login_compat` |
| OQ-3 | `max_sessions` enforced runner-side via ledger counter. Helper `_count_active_browser_sessions` reads last 2000 lines of `task-ledger.jsonl`, counts rows with `browser_recon` set and no `cleanup_status` within a 10-minute window. Caps: `inspect_local=5`, `service_ops=3`, `risky_edit=2`. | `scripts/run_with_profile.py` `_count_active_browser_sessions` |
| OQ-4 | `remote_handoff` + any browser action: hard block (`allow_single_session=False`). NOT a soft confirmation gate. | `scripts/browser_work_verifier.py` `_HARD_BLOCK_PROFILES` |
| OQ-5 | Site note fixed path: `<workspace>/skills/browser-site-notes/<host>.md`. Verifier auto-resolves when `existing_site_note_path` is absent. `workspace_root` overrideable via kwarg / `OPENCLAW_WORKSPACE` env / `~/.openclaw/workspace`. | `scripts/browser_work_verifier.py` `_resolve_site_note_path` |
| OQ-6 | (Wave 3a) `browser_recon` is a top-level sibling key alongside `guard`. | `scripts/run_with_profile.py` `_evaluate_browser_gate` |
| OQ-7 | `require_cleanup_evidence` → finalization gate. Verifier emits `require_cleanup_evidence=True` for `risky_edit`. Runner checks at completion (exit 0 path) via `_check_cleanup_required_satisfied`; blocks with `EXIT_CLEANUP_REQUIRED=28` if no `cleanup_status` row exists. | `scripts/run_with_profile.py` `_check_cleanup_required_satisfied` |
| OQ-8 | `workspace_edit` + any browser action: hard block (`allow_single_session=False`). NOT a soft confirmation gate. | `scripts/browser_work_verifier.py` `_HARD_BLOCK_PROFILES` |

All enforcement is gated by `OPENCLAW_BROWSER_GATE`. When the flag is off the verifier still
runs and logs in shadow mode (`browser_recon_shadow`), but no enforcement exits fire.

---

*End of browser profile policy design. Implementation: Task 14.*
