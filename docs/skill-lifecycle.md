# Skill Lifecycle Management

Sidecar telemetry and curation tooling for skills installed in a Helm or
OpenClaw workspace. Tracks when skills are used, when they are patched, and
which ones have gone stale — without modifying `SKILL.md` files themselves.

This is a conservative lifecycle layer. It records observations and surfaces
candidates; it never auto-deletes a skill, and the only state mutations it
performs are explicit (`archive`, `pin`, `stale --apply`).

> Status: PRD-complete except for OpenClaw-core-dependent items. Includes
> task-ledger correlation, observer + manual `view`, persisted negative-
> claim metadata with TTL revalidation, four umbrella signals (name token,
> description token, downstream share, execution profile), Pin Candidates
> + Recommended Actions report sections, and the `helm curator` alias.

## Why

Long-running agents accumulate skills. Some get used every day, some get used
once, some encode a workaround that has been outdated for months. Without
usage data the only signal for "should this skill still exist?" is intuition.

This layer answers the basic questions:

- when was this skill last used?
- how many times has it been used?
- which skills have I never actually run?
- which skills look like umbrella consolidation candidates?
- which skills make negative claims ("X does not work") that may be stale?

## Layout

All metadata lives next to the workspace it describes, under
`<workspace>/.openclaw/skill-lifecycle/`:

```
<workspace>/.openclaw/skill-lifecycle/
├── usage.json     central per-skill metadata index
├── events.jsonl   append-only event log
└── config.json    policy / thresholds
```

Archived skills move to `<workspace>/skills/.archive/<skill>/`. The dot
prefix keeps them out of normal skill discovery while preserving the
original directory layout for restore.

## Commands

### `helm skill-lifecycle scan`

Walk `<workspace>/skills/*/SKILL.md` and reconcile `usage.json` with what is
on disk.

```bash
helm skill-lifecycle scan --path ~/.openclaw/workspace
helm skill-lifecycle scan --path ~/.openclaw/workspace --dry-run
helm skill-lifecycle scan --path ~/.openclaw/workspace --json
```

What scan does:

- Registers any newly discovered skill (creating its metadata entry).
- Refreshes `path` and `source` if they changed since the last scan.
- Marks entries `state="missing"` when their `SKILL.md` no longer exists.
- Detects skills only present under `skills/.archive/` and records them as
  `archived` with an `archive_path` pointer.
- Reactivates an archived entry to `active` if a fresh `SKILL.md` reappears
  under `skills/<name>/`.

What scan does not do:

- It never modifies any `SKILL.md`.
- It never moves files. (Use `archive` / `restore` for that — M2.)
- In `--dry-run` mode it writes nothing.

### `helm skill-lifecycle status`

Print a compact lifecycle summary: total skills, state counts, never-used
list, least-recently-used list, archive candidates.

```bash
helm skill-lifecycle status --path ~/.openclaw/workspace
helm skill-lifecycle status --path ~/.openclaw/workspace --json
```

### `helm skill-lifecycle report`

Render a markdown or JSON report.

```bash
helm skill-lifecycle report --path ~/.openclaw/workspace --format markdown --out reports/skill-lifecycle.md
helm skill-lifecycle report --path ~/.openclaw/workspace --format json
```

### `helm skill-lifecycle pin / unpin`

Mark a skill as protected from auto stale/archive transitions.

```bash
helm skill-lifecycle pin   --path ~/.openclaw/workspace household-ledger-ko
helm skill-lifecycle unpin --path ~/.openclaw/workspace household-ledger-ko
```

### `helm skill-lifecycle stale`

Print or apply stale-state transitions per `config.json`. Defaults to dry-run.

```bash
helm skill-lifecycle stale --path ~/.openclaw/workspace            # dry-run
helm skill-lifecycle stale --path ~/.openclaw/workspace --apply    # transition active -> stale
helm skill-lifecycle stale --path ~/.openclaw/workspace --json
```

Pinned skills and skills whose `source` is in `protect_sources` are excluded.

### `helm skill-lifecycle archive / restore`

Move a skill into or out of `<workspace>/skills/.archive/`. Defaults to
dry-run; `--apply` performs the actual move.

```bash
helm skill-lifecycle archive --path ~/.openclaw/workspace old-skill            # dry-run preview
helm skill-lifecycle archive --path ~/.openclaw/workspace old-skill --apply    # move
helm skill-lifecycle restore --path ~/.openclaw/workspace old-skill --apply
```

`archive` refuses to act when:

- the skill is `pinned`
- the skill's `source` is in `protect_sources` (e.g. bundled / hub)
- the archive target directory already exists
- the skill is already archived or marked missing

`restore` refuses to act when the live target directory already exists.

### `helm skill-lifecycle events`

Print the lifecycle event log, optionally filtered by skill or limited to
the last N entries.

```bash
helm skill-lifecycle events --path ~/.openclaw/workspace
helm skill-lifecycle events --path ~/.openclaw/workspace --skill car
helm skill-lifecycle events --path ~/.openclaw/workspace --limit 20 --json
```

### `helm skill-lifecycle negative-claims`

Scan every active and archived `SKILL.md` for lines that look like negative
claims and may need re-validation. Detects English and Korean phrasing
(`does not work`, `doesn't work`, `unavailable`, `not installed`,
`not supported`, `failed`, `안 됨`, `없음`, `불가`, `실패`, `지원하지 않음`).
Lines inside fenced code blocks are skipped.

```bash
helm skill-lifecycle negative-claims --path ~/.openclaw/workspace
helm skill-lifecycle negative-claims --path ~/.openclaw/workspace --json
```

This produces candidates only — no SKILL.md is modified. The list is
expected to include false positives (e.g. "if X fails, do Y" describes
graceful handling, not a stale claim). Treat the output as a review queue.

### `helm skill-lifecycle umbrella`

Surface umbrella consolidation candidates by clustering active skill ids
across three signals:

- `name_token` — skills sharing a meaningful token in their id (e.g.,
  `*-search`)
- `description_token` — skills sharing a distinctive token in their
  `SKILL.md` frontmatter `description:` (Jaccard-style); tokens that
  appear in more than ~25% of skills are filtered as too generic
- `downstream_share` — skills that reference the same downstream skill
  in backticks (`` `<skill-id>` ``) inside their SKILL.md body
- `execution_profile` — skills declaring the same `default_profile` in
  `<workspace>/references/skill_profile_policies.json`

```bash
helm skill-lifecycle umbrella --path ~/.openclaw/workspace
helm skill-lifecycle umbrella --path ~/.openclaw/workspace --min-cluster-size 4 --json
```

Each cluster carries a `signal` field plus the shared `token` and the
member `skill_ids`. Tokens like `ko`, `ops`, `data`, `info`, `v1`, `v2`
plus an extended English/Korean stopword list of common verbs are
excluded. Archived skills are excluded. Reported as advisory only — the
PRD explicitly rules out automatic merging.

### `helm skill-lifecycle ledger`

Print lifecycle events joined with rows from
`<workspace>/.openclaw/task-ledger.jsonl` by `task_id`. When a runner
event carries a `task_id`, the matching ledger row contributes
`task_name`, `task_status`, `exit_code`, `started_at`, `finished_at`,
and `profile` to the event line.

```bash
helm skill-lifecycle ledger --path ~/.openclaw/workspace
helm skill-lifecycle ledger --path ~/.openclaw/workspace --skill car
helm skill-lifecycle ledger --path ~/.openclaw/workspace --limit 50 --json
```

Useful for tracing a skill's recent runs end-to-end without manually
correlating event timestamps to the task ledger.

### `helm skill-lifecycle observe`

Poll every tracked SKILL.md and emit `skill_patched` (mtime advance) or
`skill_viewed` (atime advance) events. The first invocation per skill
baselines its timestamps silently — events fire only on the second and
subsequent observations.

```bash
helm skill-lifecycle observe --path ~/.openclaw/workspace
helm skill-lifecycle observe --path ~/.openclaw/workspace --dry-run --json
```

Caveat: macOS APFS and many Linux mounts defer or disable atime updates.
Where atime is unreliable, `skill_viewed` events from `observe` will
under-report. The mtime path remains accurate for actual edits. For an
atime-independent view signal, use `helm skill-lifecycle view <skill>`.

### `helm skill-lifecycle view`

Manually record a `skill_viewed` event. Useful for callers that opened a
SKILL.md and want to record the view explicitly, independent of
filesystem atime semantics.

```bash
helm skill-lifecycle view --path ~/.openclaw/workspace car
```

### `helm skill-lifecycle revalidation-due`

Surface persisted negative claims whose TTL window has elapsed and need
re-checking.

```bash
helm skill-lifecycle revalidation-due --path ~/.openclaw/workspace
helm skill-lifecycle revalidation-due --path ~/.openclaw/workspace --json
```

A claim is "due for revalidation" when:

- it has a non-null `detected_at` (or `last_revalidated_at`) and
  positive `ttl_days`
- the TTL anchor (`last_revalidated_at` if present, else `detected_at`)
  + `ttl_days` is in the past
- its `status` is not `resolved`

Each result includes the `skill_id`, the TTL `anchor`, and how many days
overdue the claim is.

### `helm curator <subcommand>`

Short alias. `helm curator` is equivalent to `helm skill-lifecycle` for
every subcommand.

```bash
helm curator status --path ~/.openclaw/workspace
helm curator scan --path ~/.openclaw/workspace
helm curator report --path ~/.openclaw/workspace --format markdown
```

### Persisting negative claims

`helm skill-lifecycle negative-claims --persist` writes detected claims
into per-skill metadata using the PRD-specified shape:

```json
{
  "claim_id": "sha256:...",
  "text": "...",
  "keyword": "...",
  "skill_md": "skills/<name>/SKILL.md",
  "line_no": 42,
  "detected_at": "2026-05-03T...",
  "last_revalidated_at": null,
  "ttl_days": 30,
  "confidence": 0.6,
  "status": "needs_review"
}
```

Re-runs are idempotent — they preserve manually edited `status` /
`last_revalidated_at` / `confidence` fields by keying on `claim_id`.

## Configuration

`config.json` is created on the first non-dry-run scan with these defaults:

```json
{
  "enabled": true,
  "stale_after_days": 45,
  "archive_after_days": 120,
  "never_used_stale_after_days": 30,
  "auto_archive": false,
  "auto_stale": false,
  "hide_archived_from_registry": true,
  "hide_stale_from_prompt": false,
  "protect_sources": ["bundled", "hub"],
  "negative_claim_ttl_days": 30,
  "report_top_n": 20
}
```

Edit the file directly to tune thresholds. `auto_stale` and `auto_archive`
default to false — state changes always require an explicit command.

## Source classification

Each skill is classified as one of:

- `workspace` — locally authored (default)
- `bundled` — marker file `.bundled` exists in the skill directory, or the
  `SKILL.md` frontmatter declares `source: bundled`
- `hub` — same pattern with `.hub` / `source: hub`

`protect_sources` in config governs which sources are excluded from
auto-stale and auto-archive decisions.

## Event log

Every state-changing operation appends one JSONL line to `events.jsonl`:

```json
{"ts":"2026-05-03T05:48:31+00:00","event":"skill_registered","skill_id":"car","source":"workspace"}
{"ts":"2026-05-03T06:26:32+00:00","event":"skill_used","skill_id":"car","profile":"inspect_local","task_id":"..."}
{"ts":"2026-05-03T06:26:32+00:00","event":"skill_success","skill_id":"car","exit_code":0,"task_id":"..."}
```

The log is append-only.

### Recorded events

| Event | Emitted by | Counter / timestamp updated |
|-------|------------|------------------------------|
| `skill_registered` | `scan` (first time) | none |
| `skill_missing` | `scan` (skill vanished) | `last_reviewed_at` |
| `skill_used` | `helm profile run --skill <name>` start | `use_count`, `last_used_at` |
| `skill_success` | `run_with_profile` exit code 0 | `last_successful_apply_at` |
| `skill_failure` | `run_with_profile` non-zero exit or timeout | none |
| `skill_viewed` | `view` (manual) or `observe` (atime advance) | `view_count`, `last_viewed_at` |
| `skill_patched` | `observe` (mtime advance) | `patch_count`, `last_patched_at` |
| `skill_promoted` | `helm skill-approve` (skill_capture promote) | `patch_count`, `last_patched_at` |
| `skill_rejected` | `helm skill-reject` | none |
| `skill_pinned` / `skill_unpinned` | `pin` / `unpin` | none |
| `skill_stale` | `stale --apply` | `last_reviewed_at` |
| `skill_archived` | `archive --apply` | `archived_at` |
| `skill_restored` | `restore --apply` | `reactivated_at` |

Runner integration is fail-soft: if `usage.json` does not exist for the
workspace yet, runners skip emitting events. Initialize the lifecycle layer
once with `helm skill-lifecycle scan --path <workspace>` to start collecting
data.

## What this does not change

This layer is a sidecar. It deliberately:

- never rewrites `SKILL.md`
- never auto-deletes any skill
- never touches OpenClaw core source

Discovery filtering is achieved by physically moving skills under
`skills/.archive/` (a dot-prefixed directory standard skill discovery
typically skips). If the runtime later wants to consume lifecycle metadata
directly, it can read `usage.json` — the schema is stable from M1.

## Roadmap

- M5+ (out of band): runtime-side hooks if the agent runtime adopts the
  schema natively; automatic negative-claim re-validation guarded by an
  allowlist of safe probe commands.

## Known gaps

- A workspace may carry its own copy of `scripts/skill_capture.py` predating
  Helm. Direct `python3 .../skill_capture.py promote-draft ...` invocations
  on that local copy do not emit `skill_promoted`. The packaged path
  (`helm skill-approve`) does emit. Migrate workflows to the packaged
  command if you want full lifecycle coverage.
