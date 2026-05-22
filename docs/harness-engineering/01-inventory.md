# Harness Engineering — Task 1: Baseline Inventory

**Generated:** 2026-05-22  
**Branch:** feat/harness-engineering-2026-05-22  
**Ledger sample:** tail-200 + head-50 + full-distinct-task scan (1 180 unique task IDs, 12 MB file)

---

## 1. Execution Profiles

Source: `<workspace>/references/execution_profiles.json`

| Profile | Description | Backend | Isolation | writes_allowed | network_allowed | checkpoint |
|---|---|---|---|---|---|---|
| `inspect_local` | Read-only local inspection: file reads, diagnostics, status checks | local / local-shell | shared-session | false | false | never |
| `workspace_edit` | Normal local workspace edits and deterministic maintenance | local / local-shell | shared-session | true | false | optional |
| `risky_edit` | Multi-file or workflow-affecting local edits; checkpoint first | local / local-shell | checkpointed-session | true | false | required |
| `service_ops` | Live operations touching local scripts plus external APIs/SaaS | local / local-shell (service-gateway) | shared-session | true | true | optional |
| `remote_handoff` | Work that must stay on a remote host, SSH target, or container | manual-remote / manual-handoff (remote-runtime) | target-defined | depends-on-target | true | manual |

### Profile Drift Between Copies

Both copies are **identical** (bit-for-bit). No drift detected between:
- `<workspace>/references/execution_profiles.json`
- `<helm-worktree>/references/execution_profiles.json`

---

## 2. Runner: `run_with_profile.py`

Source: `<workspace>/scripts/run_with_profile.py`

### CLI Surface

The script exposes two surfaces depending on how it is invoked:

**Management subcommands** (dispatched through `argparse` subparsers):

| Subcommand | Purpose | Key flags |
|---|---|---|
| `list` | Print all configured profiles (name, backend, checkpoint, description) | — |
| `show <profile>` | Print one profile as JSON | — |
| `policy` | Print skill-to-profile policy mappings | — |
| `validate-manifests` | Validate all skill contract manifests against profiles | `--json` |
| `audit-manifest-quality` | Flag overly generic or weak manifest policies | `--json` |
| `ledger` | Tail recent task-ledger entries | `--limit N` (default 20) |
| `rollback` | Suggest the checkpoint to restore for a risky task | `--task-id`, `--json` |
| `state-snapshot` | Show the latest task-handoff state snapshot | `--task-id`, `--json` |

**Run mode** (`run <profile> [flags] -- <command>`):

Activated when `sys.argv[1] == "run"`. Key flags:

| Flag | Purpose |
|---|---|
| `--task-name` | Human-readable name stored in the ledger row |
| `--task-id` | Explicit task-id override for harness-controlled runs |
| `--parent-task-id` | Links retry/follow-up tasks to their parent |
| `--idempotency-key` | Stable deduplication key for external orchestrators |
| `--retry-count` | Retry attempt number (default 0; auto-incremented from parent) |
| `--max-retries` | Upper bound on retries |
| `--owner-session-id` | Session/worker responsible for the task |
| `--skill` | Owning skill slug for policy enforcement |
| `--meta-json` | Structured JSON blob embedded in the ledger row under `meta` |
| `--label` | Checkpoint label (used when `checkpoint=required`) |
| `--path` | Checkpoint path override (repeatable) |
| `--runtime-target` | Named runtime (local, `ssh:host`, `container:name`, node label) |
| `--runtime-note` | Short context note for handoff |
| `--delivery-mode` | `inline` / `background` / `announce` / `none` (default `inline`) |
| `--guard-mode` | `enforce` / `audit` / `off` (default: `enforce` or `HELM_GUARD_MODE` env) |
| `--approve-risk` | Approve `require_approval` guard decisions without overriding `deny` |
| `--guard-json` | Print guard decision as JSON and exit without running |
| `--timeout` | Subprocess timeout in seconds (default 1800; 0 = no limit) |

### What the Runner Writes to the Ledger

A ledger row is written in up to four lifecycle moments per task:

1. **On queued** — initial task stub (all metadata, status=`queued`)
2. **On running** — status update to `running` with `started_execution_at`
3. **On guard_audit** — if `--guard-json` is used, terminal entry with status=`guard_audit`
4. **On finalization** — final row with `exit_code`, `status`, `memory_capture`, `state_snapshot`, `touched_paths`; also appended on `blocked`, `timeout`, `failed`, and `handoff_required` outcomes

Fields always present in a completed row: `task_id`, `task_state_schema_version`, `task_name`, `skill`, `profile`, `backend`, `runtime_backend`, `runtime_target_kind`, `isolation`, `handoff_required`, `command`, `command_preview`, `started_at`, `finished_at`, `heartbeat_at`, `status`, `exit_code`, `retry_count`, `delivery_mode`, `guard`, `discovery`, `memory_capture`, `state_snapshot`, `previous_state_snapshot`.

### Guard Hooks Called

1. `evaluate_command_guard()` from `command_guard` — classifies the command, matches rules from `<workspace>/references/guard_policy.json`, and returns a `GuardDecision` with `action` ∈ {`allow`, `require_approval`, `deny`} and a `risk_score`.
2. If `guard_mode=enforce` and action is `deny` → exits with code 25 (`EXIT_GUARD_DENY`).
3. If `guard_mode=enforce` and action is `require_approval` and `--approve-risk` is absent → exits with code 24 (`EXIT_GUARD_REQUIRE_APPROVAL`).
4. `record_runner_event()` from `skill_lifecycle_lib` is called on `skill_used`, `skill_success`, and `skill_failure` events.
5. `apply_memory_capture()` from `task_memory_capture` runs at finalization.
6. `write_state_snapshot()` from `state_snapshot` runs at finalization.
7. `index_task_entry()` from `ops_db` indexes every ledger append into the SQLite ops-index.

---

## 3. Task Ledger Schema

Source: `<workspace>/.openclaw/task-ledger.jsonl`  
File size: ~12 MB, 3 587 lines.  
Sample: `tail -200` (200 entries) + `head -50` (50 entries) + full distinct-task scan (1 180 unique task IDs).

### Field Inventory (Sampled)

All values for string/path fields are described abstractly; no raw content is reproduced.

| Field | Type | Populated (tail-200) | Notes |
|---|---|---|---|
| `task_id` | string (UUID) | 200/200 | Primary key |
| `task_state_schema_version` | int | 200/200 | Always `1` in current sample |
| `task_name` | string | 200/200 | Human-readable; set from `--task-name` or auto-derived from command |
| `skill` | string / null | 200/200 (some null in older entries) | Skill slug |
| `profile` | string | 200/200 | One of the 5 profiles |
| `backend` | string | 200/200 | Mirrors profile backend |
| `runtime_backend` | string | 200/200 | Mirrors profile runtime_backend |
| `runtime_target_kind` | string | 200/200 | Mirrors profile target kind |
| `isolation` | string | 200/200 | Mirrors profile isolation |
| `handoff_required` | bool | 200/200 | From profile |
| `runtime_target` | null | 0/200 (null throughout sample) | Populated only for `remote_handoff` tasks |
| `runtime_note` | null | 0/200 (null throughout sample) | Populated only when `--runtime-note` passed |
| `command` | list of strings | 200/200 | Full argv |
| `command_preview` | string | 200/200 | `shlex.join()` of command |
| `started_at` | ISO datetime | 200/200 | Task creation time |
| `started_execution_at` | ISO datetime | 134/200 | Set when subprocess actually starts; absent on `queued`/`blocked` entries |
| `finished_at` | ISO datetime | 68/200 | Present only on terminal entries |
| `heartbeat_at` | ISO datetime | 200/200 | Updated at each lifecycle write |
| `status` | string | 200/200 | `queued` / `running` / `completed` / `failed` / `blocked` / `timeout` / `handoff_required` / `guard_audit` |
| `exit_code` | int | 68/200 | Exit code of subprocess; absent until process completes |
| `blocked_reason` | string / null | 0 non-null in tail-200 (populated in blocked entries in full scan) | Free-text reason for guard block |
| `failure_stage` | string | Sparse | `guard` / `checkpoint` / `execution` / `handoff` |
| `failure_reason` | string | Sparse | Normalized reason text (no raw error messages in ledger itself) |
| `next_action` | string / null | 7/200 | Advisory string for what to do next |
| `checkpoint_id` | string / null | 0/200 in tail (non-null in older risky_edit entries) | ID of checkpoint created before run |
| `checkpoint_label` | string / null | 0/200 in tail | Human label for checkpoint |
| `checkpoint_paths` | list | 0/200 non-empty in tail | Paths included in checkpoint |
| `guard` | dict | 134/200 | Guard decision: `action`, `risk_score`, `score_breakdown`, `reasons`, `source`, `approved` |
| `delivery_mode` | string | 200/200 | `inline` / `background` / `announce` / `none` |
| `discovery` | dict | 200/200 | Env snapshot: `hardware`, `openclaw_intelligence_state`, `runtime`, `runtime_model_state`, `strategy`, `warnings` |
| `memory_capture` | dict | 68/200 | Post-run memory capture result (keys: `artifact_registry`, `claim_state`, `crystallization`, `touched_paths`, etc.) |
| `state_snapshot` | dict | 68/200 | Keys: `created_at`, `format`, `path`, `summary` |
| `previous_state_snapshot` | string (path) | 68/200 | Path to prior snapshot |
| `owner_session_id` | null | 0/200 | Not yet populated in any sampled entry |
| `parent_task_id` | null | 0/200 | Not populated in sample |
| `idempotency_key` | null | 0/200 | Not populated in sample |
| `retry_count` | int | 200/200 | Always `0` in current sample |
| `max_retries` | null | 0/200 | Not populated in sample |
| `meta` | dict (empty) | 0/200 non-empty | `{}` in all sampled entries |

### Upcoming-Need Field Status

| Field | Status | Notes |
|---|---|---|
| `failure_signature` | **ABSENT** | No such field exists; `failure_reason` + `failure_stage` are sparse, unstructured |
| `retry_count` | **PRESENT** | Field exists and is written; always `0` in sample (no retries observed yet) |
| `tool_grant` | **ABSENT** | No such field exists anywhere in schema |
| `browser_profile` | **ABSENT** | No such field exists |
| `browser_mode` | **ABSENT** | No such field exists |
| `sessions` | **ABSENT** | No such field exists |
| `snapshot_evidence` | **ABSENT** | No such field; `state_snapshot.path` is the closest analog |
| `cleanup_status` | **ABSENT** | No such field exists |
| `site_note_update` | **ABSENT** | No such field exists |
| `replay_hint` | **ABSENT** | No such field exists |
| `skill_candidate` | **ABSENT** | No such field exists; `skill` is the populated slug field |

---

## 4. Recent Failure Analysis

Sample: full distinct-task scan (1 180 tasks). Non-completed statuses: 98 `failed`, 11 `blocked`, 17 `running` (in-flight), 2 `handoff_required`.

Failures grouped by heuristic signature `(script / skill / profile / exit_code)`:

| Count | Script | Skill | Profile | Exit Code | Failure Class |
|---|---|---|---|---|---|
| 13 | `google_sheets_append_row.py` | `household-ledger-ko` | `service_ops` | 2 | Google Sheets write error (API / auth / range validation) |
| 10 | `gemini_video_understand.py` | `knowledge-capture-ko` | `service_ops` | 1 | Gemini video API error (file not found, quota, or content policy) |
| 5 | `obsidian_link_maintenance.py` | `knowledge-capture-ko` | `workspace_edit` | 1 | Obsidian vault link maintenance — malformed links or missing vault |
| 5 | `python3` (inline) | `knowledge-capture-ko` | `workspace_edit` | 1 | Generic Python error during knowledge capture maintenance |
| 4 | `python3` (inline) | `<none>` | `service_ops` | 1 | Ad-hoc service_ops invocation failure |
| 4 | `python3` (inline) | `knowledge-capture-ko` | `service_ops` | 1 | Knowledge capture network/API error |
| 3 | `google_sheets_read_range.py` | `household-ledger-ko` | `inspect_local` | 2 | Google Sheets read error (auth or range not found) |
| 3 | `google_sheets_append_row.py` | `household-ledger-ko` | `service_ops` | 1 | Google Sheets write error (auth or rate limit) |
| 3 | `household_ledger_runner.py` | `household-ledger-ko` | `service_ops` | 2 | Ledger runner composite failure |
| 3 | `google_sheets_read_range.py` | `household-ledger-ko` | `inspect_local` | 1 | Google Sheets read error |
| 3 | `gws` (CLI wrapper) | `knowledge-capture-ko` | `inspect_local` | 3 | GWS CLI error (workspace auth or network) |
| 3 | `zsh`/`bash` (inline shell) | `<none>` | `inspect_local` | 1/3 | Ad-hoc shell command failure |
| 3 | `workout_ops.py` | `workout-ops-ko` | `workspace_edit` | 1 | Workout ops data/logic error |
| 2 | `obsidian_link_maintenance.py` | `knowledge-capture-ko` | `workspace_edit` | 2 | Obsidian link maintenance — validation failure |

**Blocked tasks (11 total):** All blocked at the `guard` stage. 8 were `guard deny` on `inspect_local` profile (network/write-disallowed commands attempted under a read-only profile), and 3 were `require_approval` on `workspace_edit`/`risky_edit` profiles without `--approve-risk`.

**Summary of error classes:**
1. **Google Sheets API / auth failure** — affects `google_sheets_append_row.py` and `google_sheets_read_range.py` under both `service_ops` and `inspect_local`
2. **Gemini video API failure** — affects `gemini_video_understand.py` under `service_ops`
3. **Obsidian link-maintenance failure** — affects `obsidian_link_maintenance.py` under `workspace_edit`
4. **Generic Python script failure** — inline `python3` invocations across `service_ops` and `workspace_edit`
5. **GWS CLI / workspace auth failure** — `gws` wrapper returning exit 3 under `inspect_local`
6. **Guard deny / profile mismatch** — commands violating profile constraints blocked at guard stage
7. **Guard require_approval** — commands needing approval not pre-approved

---

## 5. Repeated Workflow / Compound Runner Candidates

From 1 052 completed tasks (full distinct-task scan). Threshold: ≥3 completions. Seven skills qualify; table is not padded below threshold.

| Rank | Skill | Completions | Candidate Type |
|---|---|---|---|
| 1 | `knowledge-capture-ko` | 578 | High-frequency skill — compound runner candidate |
| 2 | `<none>` (ad-hoc) | 302 | Untagged ad-hoc invocations — skill tagging opportunity |
| 3 | `workout-ops-ko` | 78 | Periodic workflow — compound runner candidate |
| 4 | `household-ledger-ko` | 59 | Periodic workflow — compound runner candidate |
| 5 | `linkedin-ghostwriter-ko` | 17 | Periodic workflow |
| 6 | `local-discovery-ko` | 4 | Utility workflow |
| 7 | `travel-ops-ko` | 4 | Periodic workflow |

**Key observation:** ~26% of completions (302/1 052) carry no `skill` tag. Tagging these would improve routing fidelity and compound-runner attribution in Phase 2.

**Top 3 compound-runner candidates for SmallCode Phase 2:**
- `knowledge-capture-ko` (knowledge ingestion loop — Gemini video + Obsidian + GWS)
- `workout-ops-ko` (workout logging + projection pipeline)
- `household-ledger-ko` (Sheets read + ledger append + validation loop)

---

## 6. Failure Signature Draft List

These candidate `failure_signature` shapes are distilled from Section 4 and are ready for Task #2 implementation.

Each shape is: `(component, runner/tool, normalized_error_class, target_normalization_rule)`.

| ID | Component | Runner / Tool | Normalized Error Class | Target Normalization Rule |
|---|---|---|---|---|
| `FS-001` | `google-workspace` | `google_sheets_append_row.py` | `sheets_write_error` | Strip spreadsheet ID; normalize to `sheets://append/<range-name>` |
| `FS-002` | `google-workspace` | `google_sheets_read_range.py` | `sheets_read_error` | Strip spreadsheet ID; normalize to `sheets://read/<range-name>` |
| `FS-003` | `google-workspace` | `gws` CLI | `gws_auth_or_network_error` | Classify by exit code: 3 = network/auth, 1 = logic |
| `FS-004` | `gemini-api` | `gemini_video_understand.py` | `gemini_video_api_error` | Normalize target to `gemini://video/<content-class>` (strip URL/filename) |
| `FS-005` | `obsidian` | `obsidian_link_maintenance.py` | `obsidian_link_maintenance_error` | exit=1 → malformed links; exit=2 → validation failure |
| `FS-006` | `python-script` | `python3` (inline) | `python_generic_error` | Sub-classify by skill + profile: `service_ops` = likely network/API; `workspace_edit` = likely logic |
| `FS-007` | `guard` | `run_with_profile.py` | `guard_deny` | Target = `profile:<profile_name>` |
| `FS-008` | `guard` | `run_with_profile.py` | `guard_require_approval` | Target = `profile:<profile_name>` |
| `FS-009` | `shell` | `zsh`/`bash` (inline) | `shell_command_error` | exit=1 = generic error; exit=3 = command not found class; exit=127 = command not found |
| `FS-010` | `household-ledger` | `household_ledger_runner.py` | `ledger_runner_composite_error` | Sub-classify by exit: 2 = data/range error |

**Target normalization rules (general):**
- Strip user home paths; replace with `<workspace>/...`
- Strip spreadsheet IDs, doc IDs, and URL query parameters
- Normalize URL targets to `<scheme>://<domain-class>/<path-class>`
- Normalize Obsidian vault paths to `obsidian://<vault-name>/...`

---

## 7. Profile → Tool-Group Mapping Draft

This draft feeds Task #3 (tool-group permission matrix). Tool groups are drawn from SmallCode suggestions: `read_file`, `apply_patch`, `focused_test`, `git_diff`, `broad_shell`, `external_network`, `secrets_read`, `destructive_git`.

Notation: **allow** = permitted without prompt, **ask** = human-in-the-loop required, **deny** = blocked.

### `inspect_local`
Constraints: `writes_allowed=false`, `network_allowed=false`, `checkpoint=never`

| Tool Group | Grant | Rationale |
|---|---|---|
| `read_file` | **allow** | Core purpose of the profile |
| `git_diff` | **allow** | Read-only git operation |
| `focused_test` | **allow** | Read + run tests without mutation |
| `apply_patch` | **deny** | No writes allowed |
| `broad_shell` | **ask** | Shell commands may implicitly write; guard classifies on invocation |
| `external_network` | **deny** | Network explicitly disabled |
| `secrets_read` | **ask** | Read-only but sensitive; require explicit approval |
| `destructive_git` | **deny** | No writes; destructive operations are never read-only |

### `workspace_edit`
Constraints: `writes_allowed=true`, `network_allowed=false`, `checkpoint=optional`

| Tool Group | Grant | Rationale |
|---|---|---|
| `read_file` | **allow** | Always safe |
| `apply_patch` | **allow** | Primary purpose |
| `git_diff` | **allow** | Safe read; supports review before commit |
| `focused_test` | **allow** | Tests expected as part of edit workflow |
| `broad_shell` | **ask** | Shell may reach outside workspace scope; guard review required |
| `external_network` | **deny** | Network explicitly disabled |
| `secrets_read` | **ask** | Allowed only with explicit approval |
| `destructive_git` | **ask** | Allowed but requires approval; recommend `risky_edit` for these |

### `risky_edit`
Constraints: `writes_allowed=true`, `network_allowed=false`, `checkpoint=required`

| Tool Group | Grant | Rationale |
|---|---|---|
| `read_file` | **allow** | Always safe |
| `apply_patch` | **allow** | Multi-file edits are the core purpose; checkpoint runs first |
| `git_diff` | **allow** | Safe read |
| `focused_test` | **allow** | Required for validating risky changes |
| `broad_shell` | **ask** | Higher-risk profile; all shell commands need guard review |
| `destructive_git` | **ask** | Allowed with approval; checkpoint exists as safety net |
| `external_network` | **deny** | Network explicitly disabled |
| `secrets_read` | **ask** | Sensitive; require explicit approval even with checkpoint |

### `service_ops`
Constraints: `writes_allowed=true`, `network_allowed=true`, `checkpoint=optional`

| Tool Group | Grant | Rationale |
|---|---|---|
| `read_file` | **allow** | Always safe |
| `apply_patch` | **allow** | Scripts may need local edits as part of service operations |
| `git_diff` | **allow** | Safe read |
| `external_network` | **allow** | Core purpose — external APIs and SaaS |
| `focused_test` | **allow** | Validation before API calls |
| `broad_shell` | **ask** | Network-capable shell commands need guard review |
| `secrets_read` | **ask** | Tokens and credentials likely needed but require per-invocation approval |
| `destructive_git` | **deny** | Service operations should not involve destructive git; escalate to `risky_edit` |

### `remote_handoff`
Constraints: `writes_allowed=depends-on-target`, `network_allowed=true`, `checkpoint=manual`

| Tool Group | Grant | Rationale |
|---|---|---|
| `read_file` | **allow** | Always safe locally; remote reads pass through handoff |
| `git_diff` | **allow** | Safe read |
| `external_network` | **allow** | Network is the defining capability of this profile |
| `broad_shell` | **ask** | All shell on remote target needs explicit approval |
| `apply_patch` | **ask** | Writes depend on target; require approval |
| `focused_test` | **ask** | Target environment may differ; require approval |
| `secrets_read` | **ask** | Remote credential scope is undefined; always ask |
| `destructive_git` | **deny** | Destructive git on remote host is too high risk without explicit per-task override |

---

*End of inventory baseline. All 7 sections complete.*
