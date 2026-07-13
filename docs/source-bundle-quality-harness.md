# SourceBundle Quality Harness

`scripts/source_bundle.py` stores one canonical bundle per canonical HTTP(S) URL in `.helm/source-bundles.json`. Tracking parameters and fragments do not create duplicate sources; non-tracking query order is preserved because repeated parameters can be order-sensitive. A `partial` or `blocked` source is rejected unless it records at least one uncertainty.

## Contract

- `references/source_bundle.schema.json` is the serialized shape.
- `claims` contain source-backed statements and evidence plus a normalized `cluster_key` and `positive|negative` polarity. Opposing cross-bundle claims in one semantic cluster are downgraded to `conflicted` before publication. Every non-empty `interpretation` is separate and must point back to at least one known claim ID.
- Claim clusters are `verified`, `single_source`, `conflicted`, `official_unread`, or `stale_or_unclear`. Only `verified` claims enter script, video-manifest, or publishable content artifacts.
- Every downstream artifact retains `bundle_id`, canonical `source_url`, and the registry path. Registry entries retain file hashes and readback evidence. Caller-supplied `derived_artifacts` are rejected; only the readback-registering API may add them.
- Claim IDs must be unique across any multi-bundle derivation. Ambiguous local IDs fail before files are written.
- `completion_sip`, `ssot_check`, `contextless_review`, and `fidelity_check` run before success is returned. Humanized prose must preserve claim IDs, URLs, numbers, protected names, polarity, and causality, and may not introduce new fact tokens, significant vocabulary, or non-structural prose assertions.
- Materialization snapshots every destination plus the registry, writes all artifact records in one locked registry update, verifies readback, and restores the exact prior file/registry bytes on failure.
- `retro` writes a deduplicated review candidate after a failure; it never edits skills or promotes memory automatically.

Create or update a bundle from JSON:

```bash
python3 -m scripts.source_bundle --registry .helm/source-bundles.json create --input /path/to/bundle.json
```

Generate separate source captures plus aggregate insight, PRD, briefing, script YAML, video manifest, and final content:

```bash
python3 -m scripts.source_bundle --registry .helm/source-bundles.json derive --id <bundle-id> --output-dir /path/to/output
```

The video artifact is a renderer-neutral manifest. It deliberately contains only verified claim text; a renderer may consume it without gaining authority to invent claims.

## Memory and capability boundaries

New task-memory plans carry `quality_label` with `raw`, `candidate`, `promoted`, `stale`, or `deprecated`, visible freshness/confidence, and `requires_live_source`. Failed executions remain `raw` error-prevention memory even when confirmation is missing. Evidence-backed candidate/promotion transitions refresh `last_confirmed_at`. `scripts/memory_quality.py` only down-ranks stale memory; it never auto-deletes. Task capture invokes decay, and the memory review queue reapplies it when stored memories are read. Promotion and deprecation require evidence and explicit approval.

`references/capability_boundaries.json` maps the PRD's semantic lanes to the existing execution profiles and action-scope gate. It does not create a competing profile system. SourceBundle upsert/materialize writes consult the `local_write` lane; inspect verbs cannot cross `read_only`, and high-risk control is removable and disabled by default.

## Parallel risky-edit review

`scripts/parallel_worktree_review.py` accepts one or two JSON candidate specs and runs two candidates concurrently in separate detached worktrees. A dirty original worktree is rejected rather than silently reviewing only `HEAD`. The output directory must be outside the reviewed repository. Candidate and test commands are argv lists executed with `shell=False`, a minimal environment, an empty per-candidate HOME/XDG/TMP tree, credential redaction, and no direct shell/wrapper/Git executable or original-worktree path. The final patch is captured after tests, and a test-caused source mutation makes the candidate ineligible. Each candidate produces a patch plus diff size, touched files, tests, completion evidence, policy risk, HOME/worktree cleanup evidence, and rollback metadata in JSON/Markdown matrices.

The runner compares original HEAD, status, refs, staged diff, and unstaged diff before and after. It has no apply or merge operation; `automatic_merge` is always false and human review is required.

The detached worktree and isolated environment are containment against accidental cross-talk, not an OS security sandbox. Do not run hostile code with this script alone; invoke the runner inside the existing `risky_edit` sandbox/profile when the candidate is not trusted.

```json
{"profile":"risky_edit","candidates":[{"name":"candidate-a","command":["agent-cli","--apply"],"test_command":["python3","-m","pytest","-q"]}]}
```

```bash
python3 -m scripts.parallel_worktree_review --repo /path/to/repo --spec /path/to/candidates.json --output-dir /tmp/review-run
```
