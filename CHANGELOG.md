# Changelog

## Unreleased

- documented a runtime-neutral memory operations policy covering crystallization, confidence/recency metadata, supersession, review flags, and scope boundaries
- expanded knowledge-contract and task-finalization guidance so durable capture policy is explicitly treated as a first-class runtime contract

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
