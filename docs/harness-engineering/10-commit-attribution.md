# Commit Attribution Notes — harness-engineering branch

## Why this file exists

The harness-engineering feature branch was implemented by multiple parallel
subagents working in the same git worktree. A small number of commits picked
up uncommitted edits from a sibling agent's work-in-progress because targeted
`git add <path>` discipline lapsed in a few places. The code in the final
merge is correct; only the per-commit subject line does not fully describe
every file it carries.

This document is the authoritative attribution of the muddied commits, so
future readers using `git show <sha>` are not confused by the title.

## Commits with mixed file content

### `e2f551a` — subject: `test(harness-eng): cover tool_groups data … (task 3)`

Actually contains:
- `tests/test_tool_groups.py` — Task 3 (the subject's true scope)
- `scripts/run_with_profile.py` — Task 3 (tool_grant wiring)
- `helm_state_model.py` (+70 net) — **Task 6** (control-state helpers fix round)
- `tests/test_task_state_control.py` (+87 net) — **Task 6** (regression test additions)

### `8127e9d` — subject: `fix(harness-eng): trim 06 to length cap + cite regression test name (task 17)`

Actually contains:
- `docs/harness-engineering/05-control-flow-is-not-memory.md` — Task 17 (the subject's true scope)
- `docs/harness-engineering/06-helm-vs-forge.md` — Task 17
- `tests/eval/test_scenario_4_*.py` rename — **Task 7** (scenario 4 honesty fix; renamed to `…approval_log_contract_and_action_scope.py`)

### `b6e6d50` — subject: `fix(harness-eng): correct tilde-expansion test in synthetic_respond_tool (task 16 follow-up)`

Actually contains:
- `tests/test_synthetic_respond_tool.py` — Task 16 (the subject's true scope)
- `helm_state_model.py` — **Consolidation A** (`utc_now_iso` migration)
- `tests/test_profile_pause_resume.py` — **Consolidation A** (pause_session_summary removal)

### `35dcf95` — subject: `refactor(harness-eng): extract atomic_write_json helper; migrate 4 sites (cleanup)`

Actually contains:
- `scripts/io_utils.py` (new) — Consolidation A (the subject's true scope)
- `scripts/profile_pause_resume.py` — Consolidation A (migrated to helper)
- `scripts/trace_recorder.py` — Consolidation A (migrated to helper)
- `scripts/skill_lifecycle_lib.py` — Consolidation A (rationale comment added)
- `tests/test_io_utils.py` (new) — Consolidation A

The 35dcf95 commit is actually clean — its title and content match. It is
listed here only because earlier reviews noted that the `helm_state_model.py`
UTC change was *also* expected to land in this commit; that change actually
landed in `b6e6d50` instead. So the only thing to know about `35dcf95` is
**what it does NOT contain**: it does not carry the helm_state_model.py
UTC migration. See `b6e6d50` for that.

## Why no history rewrite

The branch was already merged to local `main` (merge commits
`be1faec` for Helm and `556cd3c` for OpenClaw workspace). Rewriting the
merged history with `git rebase -i` or `git filter-branch` would force the
merge commits to be re-created and would invalidate any local clones or
backups already taken from `main`. Since no force-push has occurred and the
code state is correct, this attribution note is preferred over destructive
rewrites.

If you need precise file-level attribution for a specific line, use
`git blame -- <path>` — blame works correctly because the files themselves
are in the right state; only the per-commit `git show` title is misleading.

## Process change for future multi-agent work

The root cause: subagents running `git add` with a broad path (or just
`git add .`) inside a shared worktree where another agent had staged files.

Going forward:
- Subagents must use `git add <explicit-path>` with paths spelled out.
- The controller (or a guard hook) should refuse `git add .` or
  `git add -A` in shared-worktree work.
- For parallel work that genuinely needs many files, give each agent its
  own worktree on its own branch, then merge separately.
