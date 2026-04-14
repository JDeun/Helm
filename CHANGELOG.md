# Changelog

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
