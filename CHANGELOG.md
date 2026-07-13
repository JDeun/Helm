# Changelog

## Unreleased

### Added

- Guarded OMFM recovery with runtime context checks and canary evidence.
- Verified execution, source-bundle quality gates, memory quality/decay, and parallel worktree review.
- Documentation and README updates for evidence-aware execution and guarded model recovery.

### Verification

- Full test suite: 1,523 passed.

## [0.11.0] — 2026-07-11

### Added

- Add a versioned workflow-unit registry with explicit inputs, live sources, mutation surfaces, verification, reporting, handoff, and stop contracts.
- Add structured completion claims, evidence references, refuter findings, and arbiter decisions to the reply gate.
- Add active-workspace scope, loaded context, planned mutations, pending claims, evidence, and retrieval trace fields to state snapshots.

### Changed

- Make `action_scope` imports work both as a package module and as a directly executed Helm script.

### Verification

- `python3 scripts/workflow_registry.py`
- `python3 scripts/release_version_check.py --version 0.11.0`
- `python3 -m pytest -q` → 1440 passed
- `bash scripts/release_smoke.sh /tmp/helm-release-smoke-0.11.0`

## [0.10.2] — 2026-06-24

### Added

- Add `helm loops validate` and `helm loops inspect` for reusable loop definitions.
- Add completion-evidence and docs-sweep loop examples.
- Add conservative `helm skill-intake classify` / `validate` commands for external skill candidate review.
- Add a coding-task-finalization pipeline reference and loop documentation.

### Changed

- Declare `PyYAML` as the package dependency for YAML loop files.

### Verification

- `python3 -m pytest tests/test_loop_and_skill_intake.py -q`
- `python3 scripts/release_version_check.py --version 0.10.2`
- `python3 -m pytest -q` → 1432 passed
- `bash scripts/release_smoke.sh /tmp/helm-release-smoke-0.10.2`

## [0.10.1] — 2026-06-20

### Added

- Record `experience_attribution` on completed, blocked, and guard-audit task ledger rows so tool/skill selection and missing evidence can be reviewed after the run.

### Fixed

- Keep chat memory capture `queued` / `running` rows free of final-only memory and attribution payloads.

### Verification

- `python3 scripts/release_version_check.py --version 0.10.1`
- `python3 -m pytest -q` → 1426 passed
- `bash scripts/release_smoke.sh /tmp/helm-release-smoke-0.10.1`

## [0.10.0] — 2026-05-22

### Added

- **failure_signature classification**: `scripts/failure_signature.py` produces a structured `{component, tool, profile, error_class, target, fingerprint}` signature from any failure event, covering FS-001..FS-010 patterns observed in the task ledger.
- **build_ledger_entry schema helper**: `scripts/state_io.build_ledger_entry` formalises the optional task-ledger fields (failure_signature, sessions, snapshot_evidence, cleanup_status, browser stubs, policy_transition, browser_recon) with a strict no-null-fillers contract.
- **task-state control container**: `helm_state_model.py` now hosts the Forge-style "Control Flow Is Not Memory" container: required_steps, completed_steps, blockers, external_side_effect_approvals, finalization_state, recovered_messages — separated from transcript content so compaction cannot drop control state.
- **profile → tool-group grants**: `references/tool_groups.json` plus `scripts/tool_groups.py` map each execution profile to an allow / ask / deny tool group, and `run_with_profile.py` records the computed `tool_grant` in every ledger entry while blocking profile-denied tool groups before subprocess execution.
- **policy_transition** module + `adaptive_harness_lib` integration: same-fingerprint / patch_failed / same-skill / credential-invalid-grant rules drive automatic transitions written through `build_ledger_entry`.
- **edit_policy + validation_gate**: patch-first edit policy with checkpoint requirements, plus per-extension validation gate commands.
- **agent reliability eval suite**: `scripts/eval_runner.py` and six `tests/eval/` scenarios cover inspect-only, save-required, recovered-context, external-side-effect approval, compaction-finalize integrity, and partial-completion reporting.
- **trace_recorder + trace_replay**: structured per-task trace JSON files with tool-sequence, validation gates, failure signatures, and a CLI replayer that prints replay plans (no re-execution this release).
- **trace_to_skill candidates**: `scripts/trace_to_skill.py` aggregates traces into scaffold / repair / compound-runner candidates; `scripts/skill_capture_ext.py` adds `draft-from-task` and `assess-draft` subcommands.
- **profile_pause_resume**: `scripts/profile_pause_resume.py` with secret-token gate and `OPENCLAW_PAUSE_GATE` env flag wired into `run_with_profile.py` (`EXIT_PAUSED = 26`).
- **local_model_proxy spike + model_repair orchestrator**: `scripts/local_model_proxy.py` (validate / nudge / retry / record) plus `scripts/model_repair.py` (`evaluate_response`, `repair_loop`, `repair_enabled()` reading `HELM_MODEL_REPAIR`).
- **synthetic_respond_tool spike + respond_tool_wiring**: schema + injection + strip + enforce helpers, gated by `HELM_SYNTHETIC_RESPOND` and the `L3_local_model` tier.
- **browser_work_verifier + browser_gate**: `scripts/browser_work_verifier.py` returns a `BrowserReconDecision` (allow_single_session, allow_parallel, require_user_login, require_confirmation, block_mutation, pause_profile, require_cleanup_evidence). `scripts/browser_gate.py` hosts the runner-side enforcement (session counter, finalization gate). `OPENCLAW_BROWSER_GATE` env flag plus `--browser-action` CLI opts opt-in.
- **skill_promotion pipeline**: `scripts/skill_promotion_{state,digest,approval}.py` + `commands/skill_promotion.py` produce digest payloads, accept Telegram replies (`approve` / `reject` / `details <id>`), and persist promotion state.
- **shadow_mode_report + recommendations**: `scripts/shadow_mode_report.py` aggregates 14-day signals across all feature-flagged surfaces; `scripts/shadow_mode_recommendation.py` emits `ready_to_enforce / needs_more_data / caution / no_signal` per feature; `commands/shadow_report.py` exposes `helm shadow-report --since N --format md|json --with-recommendations`.
- **env_flags shared helper**: `scripts/env_flags.py` centralises the `1 / true / yes` truthy-value contract used by every feature flag.
- **atomic_write_json shared helper**: `scripts/io_utils.py` consolidates the `tempfile + os.replace` pattern.
- **harness-engineering docs**: 13 design / runbook documents in `docs/harness-engineering/` covering inventory, ledger schema, browser verifier, Control Flow Is Not Memory, Helm vs Forge positioning, chromux Phase 1 smoke, local-model-proxy spike, synthetic-respond-tool spike, skill-promotion pipeline, commit attribution, and shadow-mode report runbook.

### Changed

- **OQ-4 / OQ-8 hard block**: `workspace_edit` and `remote_handoff` profiles now hard-block any browser action via the verifier (previously surfaced as `require_confirmation`). Operator workflows that relied on `--approve-risk` passing browser tasks through these profiles must switch to `service_ops` or `risky_edit`.
- **adaptive_harness_lib ledger reads** tail-sample the task ledger (200-line tail) instead of full-read; protects against unbounded growth.
- **command-line surface**: `run_with_profile.py` adds `--browser-action`, `--browser-url-pattern`, `--browser-logged-in`, `--browser-parallel`, `--browser-site-note`. `helm.py` adds `skill-promotion` and `shadow-report` subcommands.

### Fixed

- **OQ-3 max_sessions enforcement**: runner-side ledger-based counter blocks new browser sessions once a profile's `max_sessions` cap is reached (within a 10-minute window).
- **OQ-7 finalization gate**: tasks emitting `require_cleanup_evidence` cannot mark complete without a `cleanup_status` row; runner returns `EXIT_CLEANUP_REQUIRED = 28`.
- **shadow_mode_report tail-cap visibility**: `window_truncated` flag is set in `data_freshness` and surfaced as a warning line in the markdown render when the tail cap is hit.
- **candidate_id NUL-byte defence**: `skill_promotion_state.candidate_id_for` raises `ValueError` if either input contains `\x00`.
- **respond_tool_schema copy-on-read**: returns a shallow dict copy instead of the live cache reference.
- **`_resolve_site_note_path` cache**: `lru_cache(maxsize=256)` eliminates repeated `Path.exists()` syscalls; documented `cache_clear()` escape hatch.
- **expanduser coverage**: `OPENCLAW_TRACES_DIR`, `OPENCLAW_DRAFTS_DIR`, `OPENCLAW_PAUSE_STATE`, `RESPOND_TOOL_SCHEMA_PATH` env values now resolve `~`.

### Boundary

This release ships only the public Helm operations layer and harness-engineering modules. It does not include OpenClaw workspace contents, private memory, personal connector state, local schedules, credentials, or raw task history.

### Verification

- `python3 scripts/release_version_check.py --version 0.10.0`
- `python3 -m pytest -q` → 1372 passed
- two cross-cutting review cycles (duplication + over-engineering / bug + performance) returned CLEAN before merge

## [0.9.6] — 2026-05-16

### Added

- **CI release gate**: added a GitHub Actions workflow that runs tests, release-version consistency, package build, and package metadata checks on pushes and pull requests.
- **timeout regression coverage**: added tests for Helm script-runner timeout handling and skill promotion timeout handling.

### Changed

- **PyPI publish hardening**: the publish workflow now runs `twine check` before publishing release artifacts.
- **release smoke isolation**: package install verification now uses a smoke-run virtual environment instead of writing to the active user Python environment.
- **adaptive harness timeouts**: auto-hydration and wrapper execution now fail with explicit timeout status instead of waiting indefinitely.
- **CLI script-runner timeout**: Helm command wrappers now enforce a default script timeout, with `HELM_SCRIPT_TIMEOUT_SECONDS=0` available for deliberate unlimited runs.

### Fixed

- **skill promotion hangs**: `skill-lifecycle promote-from-trajectory --apply` now returns `124` when draft generation exceeds its timeout.

### Verification

- `python3 scripts/release_version_check.py --version 0.9.6`
- `python3 -m pytest -q`

## [0.9.5] — 2026-05-15

### Added

- **OpenHuman-inspired operations digest boundary**: documented the reusable pattern from OpenClaw dogfooding: artifact fingerprints, connector freshness, daily digest review queues, and recoverable JSONL retention.
- **OpenClaw integration guidance**: expanded the integration doc with a public-safe boundary for promoting operations metadata without private memory, schedules, credentials, or raw task content.

### Changed

- **README positioning**: added operations digest and connector freshness to the core Helm value proposition in English and Korean.
- **release metadata**: bumped package, citation, README, changelog, and release-note metadata to `0.9.5`.

### Verification

- `python3 scripts/release_version_check.py --version 0.9.5`
- `python3 -m pytest -q`

## [0.9.4] — 2026-05-13

### Added

- **release smoke packaging checks**: expanded `scripts/release_smoke.sh` to include release-version consistency, wheel/sdist build, and package metadata checks.

### Changed

- **single package version source**: removed the legacy `setup.py` packaging shim so `pyproject.toml` is the only package version source.
- **README release readability**: condensed release links into latest/recent entries plus the full release-note directory.

### Tests

- Updated release-version tests to reject legacy `setup.py` reintroduction.

## [0.9.3] — 2026-05-13

### Added

- **release version check**: added `scripts/release_version_check.py` to verify package metadata, citation metadata, README release banners, changelog entries, and release-note files agree before publishing.

### Changed

- **README release list**: shortened the release section to the latest notes and linked the full `docs/releases/` directory for older entries.

### Tests

- Added release-version consistency tests.

## [0.9.2] — 2026-05-13

### Added

- **artifact validation gate**: adaptive harness contracts can now require post-write artifact validation through `artifact_validation`, with `helm harness record-evidence --write-validation-json` persisting `memory_capture.write_validation` for postflight enforcement.
- **skill scaffolding guidance**: new skill templates now include a post-write validation contract, and manifest quality audit flags skills that require artifact validation without documenting that boundary in `SKILL.md`.
- **artifact-specific validation docs**: execution profiles, task finalization, and adaptive harness docs now spell out minimal-diff discipline and Obsidian Markdown/Base/Canvas validation gates.

### Tests

- Full suite: 402 tests passing.

## [0.9.0] — 2026-05-11

### Added

- **task state CLI**: added `helm task list|show|block|complete|retry|doctor` for append-only task inspection, manual blocked/completed transitions with evidence, retry task creation, and stale task detection.
- **task reclaim flow**: added `helm task mark-stale` and `helm task reclaim` to convert stale active work into append-only stale and ready states without rewriting prior ledger rows.
- **task state docs**: added `docs/task-state.md` and README examples for task inspection and doctor checks.
- **completion evidence gate**: adaptive harness postflight now enforces profile-level completion evidence at `balanced` or stricter enforcement. `risky_edit` requires a checkpoint plus test/lint/diff, write validation, or explicit evidence; `service_ops` and `remote_handoff` require operational or handoff evidence.
- **manual evidence recording**: `helm harness record-evidence` now accepts `--completion-evidence` so operators can attach reviewed completion evidence without auto-completing work.
- **needs verification transition**: harness-managed commands that exit successfully but fail completion policy now append a `needs_verification` task state.
- **task doctor hardening**: task doctor now reports dead recorded processes and retry-limit exhaustion as review findings while keeping all remediation human-triggered.
- **skill outcome metadata v2**: lifecycle runner events now include an `outcome` object with evidence quality, retry count, user correction, selection reason, and improvement-candidate metadata.
- **skill outcome reporting**: added `helm skill-lifecycle outcome-report`, `outcome-candidates`, and `selection-stats` for outcome metadata inspection.
- **trajectory draft flow**: added `helm skill-lifecycle promote-from-trajectory` to create review-only skill drafts from outcome candidates.
- **checkpoint pruning**: added `helm checkpoint prune` to plan or apply retention cleanup while preserving recent, pinned, and task-referenced checkpoints. Added `helm checkpoint protect` for retention pins, `helm checkpoint policy` for config inspection, and `--max-total-mb` size pressure.
- **DCI alias**: added `helm dci` as a direct corpus interaction query entry point for common context options.
- **DCI inspection hints**: query results now include direct inspection hints for tasks, commands, checkpoints, and source files when available.
- **HITL decision patterns**: added a Helm-local approval/rejection pattern logger with audit snapshots for approved policy entries. The policy records review state only and does not execute commands.

### Tests

- Full suite: 399 tests passing.

## [0.8.0] — 2026-05-08

### Added

- **privacy boundary primitive**: added `helm privacy scan|tokenize|restore` for local-first reversible tokenization, non-recoverable secret redaction, workspace-local vault storage, and tokenize/restore audit events.
- **privacy boundary docs**: added `docs/privacy-boundary.md` and linked privacy preflight guidance from execution profiles, memory operations, and the OpenClaw integration boundary.
- **context ranking explainability**: added `helm context --explain-ranking` and new retrieval presets (`decisions`, `timeline`, `entity`, `reflect-candidates`) as the first Hindsight-inspired query-layer step.
- **field-aware context ranking**: upgraded `helm context` ranking with title/excerpt/metadata score components, recency boost, and shallow ontology graph expansion for entity-centered queries.
- **memory learning layers**: documented the distinction between raw facts, episodes, observations, and operating rules so future reflection-style reports keep evidence visible.
- **negative-claim revalidation workflow**: added `helm skill-lifecycle revalidate-claim` for manual claim review and allowlisted `probe_command` execution. Safe probes update claim status, persist probe output, and append lifecycle events.

### Changed

- **skill lifecycle docs**: documented `negative_claim_safe_probe_prefixes`, manual claim revalidation, and safe probe execution.

### Tests

- Full suite: 379 tests passing.

## [0.7.3] — 2026-05-03

### Changed

- **umbrella summary fidelity**: `compute_summary(..., paths=...)` now preserves each umbrella candidate's `signal` (`name_token`, `description_token`, `downstream_share`, `execution_profile`) instead of returning only `token` and `skill_ids`. This removes ambiguity when the same token is surfaced by multiple signals.
- **markdown report labels**: the `## Umbrella Candidates` section now prints the signal type in each cluster heading, matching JSON output and the standalone `helm skill-lifecycle umbrella --json` command.
- **workspace hook validation**: verified the OpenClaw workspace runner path records `skill_used` and `skill_success` events against the same lifecycle sidecar files used by Helm.
- **dedup gate noise reduction**: tightened the workspace briefing dedup checker so URL and filename-slug matches act as high-specificity Obsidian signals, reducing hub/index note noise while preserving the Gemini Embedding 2 duplicate catch.

### Packaging

- bumped package metadata to `0.7.3`.
- smoke-tested the installed console script via `helm skill-lifecycle scan --path ~/.openclaw/workspace --dry-run --json`.

### Tests

- 60 lifecycle test cases passing.
- Full suite: 369 tests passing.

## [0.7.2] — 2026-05-03

### Added

- **`helm curator` alias**: every `helm skill-lifecycle <subcommand>` is now also reachable as `helm curator <subcommand>` (PRD 6.3 optional alias).
- **umbrella execution-profile signal**: new `execution_profile` cluster type groups active skills by their `default_profile` declared in `<workspace>/references/skill_profile_policies.json`. Closes the last unimplemented signal from PRD 6.6.
- **`helm skill-lifecycle revalidation-due`**: surfaces persisted negative claims whose TTL has elapsed (`detected_at` or `last_revalidated_at` + `ttl_days` < now) and `status` is not `resolved`. Reports per-claim overdue days and the TTL anchor. Subset of PRD Phase 5 that does not require LLM automation.
- **archive dry-run information**: `helm skill-lifecycle archive --dry-run` now shows the file count, total bytes, and a sample of files inside the directory that would move. Concrete preview before applying.

### Tests

- 60 lifecycle test cases (5 added since v0.7.1): execution-profile signal positive/negative, TTL-based revalidation due (anchored on `detected_at` and `last_revalidated_at`), `status="resolved"` exclusion, and archive plan file summary.
- Full suite: 369 tests passing.

## [0.7.1] — 2026-05-03

### Added

- **skill-lifecycle ledger**: new `helm skill-lifecycle ledger` joins lifecycle events with `task-ledger.jsonl` rows by `task_id`, surfacing `task_name` / `task_status` / `exit_code` per event. Useful for tracing a skill's recent runs end-to-end.
- **skill-lifecycle observe**: new `helm skill-lifecycle observe` polls SKILL.md `mtime`/`atime` and records `skill_patched` / `skill_viewed` events when timestamps advance. First run baselines silently. macOS APFS atime caveat documented.
- **skill-lifecycle view**: new `helm skill-lifecycle view <skill>` records a `skill_viewed` event manually — atime-independent, useful when filesystem atime tracking is unreliable.
- **skill-lifecycle negative-claims --persist**: detected claims are now written into per-skill `negative_claims` metadata using the PRD-specified shape (`claim_id` / `text` / `keyword` / `detected_at` / `last_revalidated_at` / `ttl_days` / `confidence` / `status`). Idempotent: re-running keeps existing entries by `claim_id`, so manually-edited `status` fields ("still_valid", "resolved") survive future runs.
- **skill-lifecycle umbrella**: now emits three signal types — `name_token` (existing), `description_token` (Jaccard-style on SKILL.md frontmatter description), and `downstream_share` (skills referencing the same downstream skill in backticks). Each cluster carries a `signal` field. Description-token clusters cap at ~25% of skills to filter generic words; expanded English/Korean stopword list filters common verbs.
- **report**: now includes `## Pin Candidates` (active unpinned skills with `use_count >= 3`) and `## Recommended Actions` (action items derived from never-used / archive / pin / umbrella / negative-claim findings).

### Changed

- **briefing dedup gate** (workspace `scripts/briefing_dedup_check.py`): also searches `~/.openclaw/memory/main.sqlite` (`chunks_fts` FTS5) and `~/.openclaw/workspace/.openclaw/task-ledger.jsonl` (last 7 days). New flags `--ledger-lookback-days N`, `--no-memory`, `--no-ledger`. Verdicts now include a `sources` breakdown (`obsidian_notes` / `obsidian_web` / `memory` / `task_ledger`); `existing_notes` retained for back-compat. Placeholder hosts (`example.com`, `localhost`, `0.0.0.0`, etc.) are filtered out of URL needles to prevent false positives.
- **eligibility report** (workspace `scripts/skill_eligibility_report.py`): now reads lifecycle metadata and shows `state` / `pinned` / `use_count` / `last_used_at` / `source` per skill, plus an aggregate summary. New `--json` mode. Falls back gracefully when the lifecycle layer is not initialized.

### Tests

- 55 lifecycle test cases (12 added since 0.7.0) covering pin candidates, recommended actions, persisted negative claims (idempotency + status preservation), umbrella signal richness (description / downstream / generic-token filtering), task-ledger correlation, observer (baseline / mtime advance / dry-run / uninitialized), and the new manual `view` event path.
- Full suite: 364 tests passing.

## [0.7.0] — 2026-05-03

### Added

- **skill-lifecycle**: new sidecar telemetry and curation layer for skills installed in a Helm or OpenClaw workspace. State, usage counters, and event log live under `<workspace>/.openclaw/skill-lifecycle/` (`usage.json`, `events.jsonl`, `config.json`); archived skills move to `<workspace>/skills/.archive/<skill>/`. `SKILL.md` is never modified by lifecycle operations.
- **skill-lifecycle CLI**: 11 new subcommands under `helm skill-lifecycle`:
  - read-only — `scan`, `status`, `report`
  - mutating — `pin`, `unpin`, `stale`, `archive`, `restore` (all dry-run by default; `--apply` to act)
  - inspection — `events`, `negative-claims`, `umbrella`
- **runner integration**: `run_with_profile.py` emits `skill_used` (start), `skill_success` (exit 0), `skill_failure` (non-zero / timeout) when invoked with `--skill <name>`, updating `use_count`, `last_used_at`, and `last_successful_apply_at`. `skill_capture.promote-draft` emits `skill_promoted` (increments `patch_count`); `helm skill-reject` emits `skill_rejected`. All hooks are fail-soft — if the lifecycle layer has not been initialized for a workspace, runners skip event emission silently.
- **candidate detection**: `negative-claims` scans every SKILL.md for English and Korean negative-claim keywords (`does not work`, `unavailable`, `not installed`, `not supported`, `failed`, `안 됨`, `없음`, `불가`, `실패`, `지원하지 않음`), skipping fenced code blocks, and emits stable `claim_id` hashes. `umbrella` clusters active skill ids by shared name token (with stop-token filtering for `ko`, `ops`, `data`, `info`, `v1`, `v2`, etc.). Both feeds flow into the markdown / JSON `report` output. Detection is advisory only — no SKILL.md is modified, no merges are applied.
- **safety**: archive refuses pinned skills, protected sources (bundled/hub by default via `protect_sources` config), already-archived/missing skills, and target collisions. Restore refuses live-target collisions. Each transition appends one JSONL line to `events.jsonl`.

### Docs

- new `docs/skill-lifecycle.md` covering layout, commands, configuration, source classification, runner integration, and the event log schema.
- new `docs/releases/0.7.0.md` release note.

### Tests

- added `tests/test_skill_lifecycle.py` with 43 cases covering scan registration / idempotency / dry-run, missing and archived detection, source classification, report rendering, pin / unpin, stale candidate selection (with pinned and protected-source exclusion), archive → restore roundtrip, archive guards, runner-event recording (`use_count`, `last_used_at`, `last_successful_apply_at`, `patch_count`, fail-soft on uninitialized workspace), negative-claim keyword detection (English + Korean, with code-fence skipping), and umbrella token clustering.

## [0.6.7] — 2026-04-27

### Added

- **community**: added contributing guidance, security policy, issue templates, and a pull request template for external contributors
- **docs**: added a three-minute demo focused on profiles, checkpoints, reports, and durable local task history
- **docs**: added a public launch checklist for repository, PyPI, demo, announcement, and landing-page readiness
- **demo**: added an animated three-minute demo GIF and terminal capture asset for README and demo docs

### Changed

- **README**: sharpened the public landing copy around concrete coding-agent operating problems and added a quick comparison table
- **README**: added PyPI install-first quickstart copy, PyPI/version badges, publish workflow badge, and landing-page links
- **packaging**: added Landing, Documentation, and Security project URLs for PyPI metadata

### Validation

- generated demo GIF locally with Pillow
- package build passed: `python3 -m build`
- `git diff --check` passed

## [0.6.6] — 2026-04-27

### Added

- **docs**: added Helm product definition, module split, and dogfooding-boundary docs to clarify what should be promoted from private OpenClaw-style workspaces into public Helm
- **state_snapshot**: added a portable snapshot inspection CLI and `snapshot_payload(...)` helper for reading the latest task state snapshot from a Helm workspace

### Changed

- **packaging**: bundled `references/*.json` and `references/*.md` as package data so installed `helm init` can copy required reference files
- **docs**: reduced internal duplication across onboarding, integration, context hydration, finalization, and knowledge-contract docs
- **README**: linked the new positioning docs and updated release/status copy for v0.6.6
- **README**: wrapped the custom installer command so GitHub rendering does not clip the workspace flag
- **ops_db**: reused JSONL parsing across task and command logs, added a command task-id index, accepted `guard.evaluated_at`, and made drift checks compare latest task state instead of raw JSONL line count
- **run_with_profile**: centralized guard fallback and blocked-task ledger recording, and made ledger append trigger best-effort SQLite indexing once
- **state_snapshot**: includes touched paths from memory-capture metadata when present

### Validation

- full local pytest suite passed after the release update: 309 passed
- target install smoke passed: installed package includes `references/*`, `helm init` succeeds from the installed wheel, and `scripts.state_snapshot.snapshot_payload` imports from the installed target
- `git diff --check` passed

## [0.6.5] — 2026-04-26

### Added

- **docs**: added first-run, demo, OpenClaw/Hermes-style integration, comparison, and profile-template guidance for external adopters
- **status/report**: added `helm status --brief`, `helm dashboard`, and `helm report --format html` for faster operational visibility

### Changed

- **README**: shortened README and README.ko into landing-style guides, sharpened the product copy, added plain-language explanations for core layers, kept Quickstart installer-first, and moved detailed feature explanation to linked docs

### Validation

- full local pytest suite passed after the fix: 309 passed
- compileall and whitespace diff checks passed

## [0.6.4] — 2026-04-26

### Fixed

- **command_guard**: shell write redirection without whitespace, such as `echo x>file`, is now classified as a write and blocked under read-only profiles
- **run_with_profile**: `--guard-json` now records a final `guard_audit` task-ledger event before exiting without running the command

### Validation

- full local pytest suite passed after the fix: 306 passed
- compileall and whitespace diff checks passed

## [0.6.3] — 2026-04-25

### Fixed

- **memory capture**: `helm memory capture-chat` now sets task `status` before planning durable capture, so completed chat-driven work correctly yields `capture_planned` instead of `no_capture_needed`

### Validation

- full local pytest suite passed after the fix: 304 passed
  warnings remained unchanged and were limited to existing guard/SQLite warning-path coverage

## [0.6.2] — 2026-04-25

### Added

- **model health**: added policy-driven runtime health probing and fallback selection via `scripts/model_health_lib.py` and `scripts/model_health_probe.py`
- **CLI**: added `helm health {probe,watch,select,state,launch}` passthrough for model-health operations
- **memory**: added `helm memory capture-chat` for durable memory capture without a profiled shell run
- **references**: added `references/model_recovery_policy.json` and bundled it into `helm init`

### Changed

- **doctor**: now surfaces model-health policy/state paths and the currently selected fallback candidate
- **memory_capture**: extracted planning logic into `scripts/task_capture_core.py` and kept `scripts/memory_capture.py` as a thinner compatibility layer
- **helm.py**: passthrough `--path` parsing now preserves nested subcommand `--path` flags instead of swallowing them
- **release smoke**: now exercises `helm health state` and `helm memory capture-chat`

### Docs

- updated `README.md` and `README.ko.md` for the new health and conversational capture workflows
- refreshed demo workspace references to include the model recovery policy template

## [0.6.1] — 2026-04-25

### Hardening

- **command_guard**: `SemanticResult` NamedTuple replaces stringly-typed `"approve."/"deny."` prefix convention
- **command_guard**: Correct return type annotation (`SemanticResult | None`)
- **command_guard**: `dd` read vs write distinction (if= → require_approval, of= → deny)
- **command_guard**: `shred`/`wipefs`/`blkdiscard` semantic deny rules
- **command_guard**: Explicit parentheses on `/dev/zero` check
- **command_guard**: Recursive shell unwrapping (max depth 5)
- **command_guard**: Pipe pattern detection via `left in before_words`
- **command_guard**: All `list[str]` → `tuple[str, ...]` in frozen dataclasses
- **run_with_profile**: `--timeout` CLI arg (default 1800s) with `TimeoutExpired` handling
- **run_with_profile**: `_minimal_env()` for restricted profiles (inspect_local/workspace_edit)
- **run_with_profile**: Lazy profile loading (no `load_profiles()` at parser build time)
- **run_with_profile**: Fail-closed fallback uses `tuple()` not list literals
- **run_with_profile**: Negative timeout clamped to 0
- **run_with_profile**: `--guard-json` test coverage
- **discovery**: `StrategyConfig` frozen dataclass replaces mutable `dict[str, object]`
- **discovery**: All mutable fields in frozen dataclasses → tuple
- **discovery**: `gpus` field serialized in `snapshot_to_json`
- **discovery**: `@functools.lru_cache(maxsize=1)` on `_detect_gpu()`
- **model_provider_probe**: `ProviderProbe.detected_env_names` → `tuple[str, ...]`
- **model_provider_probe**: `policy_path` forwarded in `probe_all_model_providers`
- **model_provider_probe**: Response body 64KB limit
- **ops_db**: `_INITIALIZED_DBS` protected by `threading.Lock`
- **ops_db**: `verify_index` streaming (line-by-line instead of `read_text()`)
- **ops_db**: `_check_schema_version(conn)` from `_connect()`
- **state_io**: Windows sentinel-region lock (bytes 0–1) instead of past-EOF lock
- **state_io**: `threading.Event` for thread-safe lock warning
- **state_io**: Documented `"ab"` mode seek behavior
- **adaptive_harness**: `python3` → `sys.executable` for Windows compatibility
- **adaptive_harness_lib**: `_deep_merge(base, overlay)` for skill contract resolution
- **adaptive_harness_lib**: JSONL functions consolidated from `state_io`/`commands`
- **intelligence_tier**: Complete rewrite from stub to snapshot-driven provider resolution (L0-L4)
- **intelligence_tier**: `available_tiers()` returns `tuple[str, ...]`
- **reply_gate**: `TASK_LEDGER` → `_get_task_ledger()` lazy initialization
- **reply_gate**: `load_entries(path=None)` injectable for testing
- **memory_capture**: `_recent_final_tasks(task, state_root=None)` parameter injection
- **helm.py**: Duplicate `build_status_payload`/`build_state_snapshot_payload` removed
- **commands/__init__.py**: `run_script` consolidated, `read_jsonl` streaming
- **commands/checkpoint.py**: `_parse_timestamp` handles both ISO-8601 and compact formats

### Tests

- 118 new tests (298 total, was 180)

## [0.6.0] — 2026-04-24

### Added — Runtime Guard & Provider-Agnostic Memory Index

- **Command Guard**: deterministic command classification and risk scoring before execution (`scripts/command_guard.py`)
  - Absolute deny rules for catastrophic commands (`rm -rf /`, `dd` to device, `mkfs`, fork bombs)
  - Profile compatibility enforcement: `inspect_local` blocks writes/network, `workspace_edit` blocks network
  - Risk score calculation with configurable thresholds
  - `--guard-mode {enforce,audit,off}` and `--approve-risk` CLI flags
  - Guard policy file at `references/guard_policy.json`
- **Provider-Agnostic Discovery**: detect any LLM provider without calling APIs (`scripts/model_provider_probe.py`, `scripts/discovery.py`)
  - API provider detection via environment variable presence (OpenAI, Anthropic, Gemini, OpenRouter, Azure, Bedrock, Vertex, Mistral, Groq, Together, Fireworks, Cohere, DeepSeek, xAI)
  - Local provider detection via short-timeout endpoint probes (Ollama, LM Studio, llama.cpp, vLLM)
  - Separate `runtime_model_state` and `helm_intelligence_state` concepts
  - Hardware profile detection (OS, architecture, memory, Apple Silicon)
- **SQLite Query Index**: read-only index over JSONL source of truth (`scripts/ops_db.py`)
  - `helm db init/rebuild/verify/status` subcommands
  - Best-effort index updates after task execution
  - JSONL remains the append-only source of truth
- **Atomic JSONL Append**: cross-platform file-locking JSONL writer (`scripts/state_io.py`)
- **Extended `helm doctor`**: Discovery, Hardware, Runtime model state, Helm intelligence state, Guard, and Ops DB sections
- **Intelligence Tier Skeleton**: documented L0-L4 extension points (`scripts/intelligence_tier.py`)

### Security & Hardening
- **command_guard**: 7 new command categories (database, cloud, package, credential, process, firewall, cron)
- **command_guard**: Flag normalization (`--recursive --force` → `-rf`)
- **command_guard**: Interpreter unwrapping (python3/perl/ruby/node `-c`/`-e`)
- **command_guard**: Heredoc, base64 pipe, /dev/tcp bypass detection
- **command_guard**: Fail-closed policy on malformed/unknown-version JSON
- **command_guard**: Regex pattern support in guard_policy.json
- **command_guard**: score_breakdown, evaluated_at, policy_version in audit output
- **run_with_profile**: Guard evaluation before manual-remote (closes bypass)
- **run_with_profile**: HELM_GUARD_MODE=off environment warning
- **run_with_profile**: Fail-closed guard exception handling (require_approval on error)

### Reliability
- **model_provider_probe**: Empty string env var false positive fix
- **model_provider_probe**: Required/optional/weak key combinations (AWS, GCP)
- **model_provider_probe**: Response body validation (port_open_unverified status)
- **model_provider_probe**: Runtime policy JSON loading with fallback
- **model_provider_probe**: 5 new API providers (Replicate, Perplexity, HuggingFace, Cerebras, NVIDIA NIM)
- **model_provider_probe**: Confidence field (high/low)
- **discovery**: GPU/VRAM detection (NVIDIA + Apple Silicon)
- **ops_db**: Streaming JSONL read (no OOM on large files)
- **ops_db**: query_tasks() and query_guard_decisions() functions
- **ops_db**: _connect helper with standard pragmas
- **ops_db**: Auto-UUID for null task_id (prevents PK collision)
- **ops_db**: Indexing failure warning (once, then silent)
- **state_io**: Windows LK_LOCK with actual write size
- **state_io**: Lock failure warnings

### CLI
- `helm doctor --skip-discovery` flag
- `helm db query` subcommand with --status, --profile, --guard-action filters
- Discovery failure diagnostics in doctor output

### Tests
- 65 new tests (180 total, was 115)

### Changed

- Migrated all tests from `unittest.TestCase` to pytest function style
- Split `helm.py` into `commands/` package for single-responsibility modules
- `run_with_profile.py` now evaluates command guard before `subprocess.run()`
- Task ledger entries now include `guard` and `discovery` payloads

## 0.5.12

- made skill relevance blocking policy-tunable through adaptive harness validation settings
- enriched state snapshots with harness routing, skill relevance, route decision, and evidence presence details

## 0.5.11

- added markdown state snapshot artifacts for finalized profiled tasks, linked from the task ledger and inspectable through `helm context state-snapshot`
- added previous-snapshot environment hints for subsequent Helm/OpenClaw-shaped runs via `HELM_PREVIOUS_STATE_SNAPSHOT` and `OPENCLAW_PREVIOUS_STATE_SNAPSHOT`
- added adaptive harness divergence/convergence routing metadata for planning, design, comparison, and drafting requests
- added skill relevance scoring so poorly matched explicit skills fail preflight instead of being forced onto unrelated requests
- promoted OpenClaw's file-intake evidence probing into Helm so required local-file evidence can be inferred from existing command paths during backfill

## 0.5.10

- hardened context source loading and query readers so malformed local state degrades instead of aborting operator inspection
- tightened local context and memory query paths around corrupted workspace artifacts
- expanded regression coverage for malformed context and query-state handling

## 0.5.9

- hardened report and ledger readers so malformed JSONL lines no longer break command-log, task-ledger, or daily-report inspection flows
- hardened checkpoint and assessment report loading so malformed JSON artifacts degrade to empty report sections instead of aborting operator views
- expanded regression coverage for report resilience and malformed-state handling across Helm inspection commands

## 0.5.8

- hardened adaptive harness hydration commands so empty include lists do not generate invalid `ops_memory_query` invocations
- hardened route-decision tool inference for nested shell commands that prepend environment variables before the real runner
- hardened context source loading so corrupted or partial `.helm/context_sources.json` data does not break local context inspection
- hardened checkpoint restore so archive symlink and hardlink members are rejected instead of being restored into the workspace
- tightened Obsidian and file-intake audit handling so relative path evidence, Office OOXML attachments, and capture-index sync failures are classified more safely

## 0.5.7

- fixed workspace detection so Helm prefers the real nested OpenClaw workspace over parent directories with misleading markers
- fixed `checkpoint list` and `checkpoint show` so layout-aware state roots are used consistently, including OpenClaw-shaped workspaces
- fixed `checkpoint` CLI routing so argparse subcommands are not shadowed by legacy passthrough behavior
- fixed `survey` to stay read-only and avoid creating Helm state as a side effect of inspection
- tightened onboarding suggestions so Helm does not recommend self-adoption of the workspace already being inspected

## 0.5.6

- added typed memory operations for `write`, `promote`, `supersede`, `archive`, and `rollback`, plus crystallized session artifacts for task-level memory outcomes
- added `helm memory review-queue` and surfaced memory-operation / crystallization / review-queue visibility in `status` and `report`
- extended the adaptive harness and manifest validation with `route_decision`, `result_contract`, and `failure_downgrade` policy surfaces
- hardened memory-capture truth resolution and write validation so durable-state contradictions are easier to detect before promotion
- fixed Helm status and reporting to read the layout-aware state root instead of assuming `.helm/` in every workspace

## 0.5.5

- documented a runtime-neutral memory operations policy covering crystallization, confidence/recency metadata, supersession, review flags, and scope boundaries
- expanded knowledge-contract and task-finalization guidance so durable capture policy is explicitly treated as a first-class runtime contract
- surfaced claim-state confidence, retention tier, review flags, and supersession hints directly in Helm CLI inspection output
- refreshed package metadata, README release references, and release notes for the `0.5.5` cut

## 0.5.4

- added `file_intake` evidence contracts to the adaptive harness, manifest validation, and ledger reporting so local file workflows leave inspectable type-routing evidence
- added operator-facing knowledge contract guidance and run-contract / capability-diff inspection commands for recent task state
- tightened release docs and README guidance around file-oriented workflows, visible evidence gaps, and session-card style status output

## 0.5.3

- added explicit `browser_work` and `retrieval_policy` evidence contracts to the adaptive harness so browser-dependent and blocked-retrieval workflows leave inspectable execution records
- added retrieval escalation planning plus task-ledger backfill tooling so older runs can infer missing evidence instead of leaving the escalation path only in prose
- expanded task-ledger reporting and manifest-quality auditing around evidence coverage, next-stage visibility, and conditional `when_any` trigger hygiene

## 0.5.2

- clarified the operator guidance that diagnostics should distinguish real breakage from intentional support artifacts such as projections, capture records, and alias stubs
- updated the skill-capture template and draft checklist to encourage explicit source priority when a skill can be driven by multiple evidence sources

## 0.5.1

- removed repository-root personal skill contracts so Helm ships as a public governance layer rather than a private skill bundle
- replaced personal skill examples in docs with the generalized `router-context-demo` demo draft under `examples/demo-workspace`
- tightened `.gitignore` and demo asset tracking so public demo drafts keep only portable contract artifacts
- expanded test coverage for manifest-quality auditing and CLI validation paths

## 0.5.0

- expanded Helm's skill quality model so `SKILL.md` is treated as an operator-facing contract, not just descriptive prose
- refreshed the skill-quality docs and skill-capture template around explicit input, decision, output, and failure contracts
- extended `audit-manifest-quality` to inspect `SKILL.md` structure and basic manifest-to-document consistency when a skill document exists
- repositioned README and release guidance so Helm is framed as a skill-governance and operating layer rather than a skill catalog

## 0.4.0

- added `audit-manifest-quality` so skill contracts can be checked for generic backfills, weak defaults, and missing approval or runner policy
- tightened the default contracts for core and representative skills so profile scope, context hydration, and approval boundaries are skill-specific
- refreshed README and release docs to position Helm as a stability-first harness for smaller local models as well as stronger hosted models
- clarified the maintenance loop for skill quality so new skills can be added without central harness edits or per-skill hardcoding

## 0.3.0

- replaced the central skill harness registry with skill-local `contract.json` manifests
- moved allowed/default profile policy into skill manifests instead of requiring central policy edits
- added manifest auditing to detect missing or malformed skill contracts before release or runtime use
- expanded README, release docs, and release checklist around manifest-based harness governance

## 0.2.0

- added explicit finalization inspection commands for recent state, pending durable captures, capture-state summaries, and checkpoint-linked finalization review
- expanded Helm task finalization from passive planning visibility into an operator-facing inspection workflow
- refreshed README and release docs to reflect the durable capture and finalization model introduced after the initial public release

## 0.1.0

- added a packaged `helm` CLI with `pyproject.toml`, `setup.py`, and one-line install script support
- introduced Helm-native workspace separation using `.helm/` instead of mutating OpenClaw directly
- added read-only adoption of external OpenClaw, Hermes, and generic note workspaces
- added file-native context hydration across notes, memory, ontology, tasks, commands, and checkpoints
- added `status`, `report`, `validate`, `sources`, and checkpoint recommendation flows
- added draft-skill diff, review, approve, and reject flows
- added onboarding survey guidance for existing runtimes and Obsidian vault detection
- added example demo workspace and runnable reference state
