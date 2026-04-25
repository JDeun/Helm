# Changelog

## Unreleased

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
