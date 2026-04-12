# Skill Self-Improvement Draft Flow

Use `scripts/skill_capture.py draft-from-task` to turn a successful task into a draft skill package without enabling it automatically.

## Goal

Preserve a repeated workflow with less manual scaffolding while keeping an explicit approval gate before the draft becomes a live skill.

## Workflow

1. Run or inspect work through `run_with_profile.py` so the task ledger and command log capture `task_id`, `skill`, `profile`, and command metadata.
2. Pick a completed task with a useful pattern.
3. Generate a draft:

```bash
python3 ~/.openclaw/workspace/scripts/skill_capture.py draft-from-task \
  --task-id <task-id> \
  --name <new-skill-slug> \
  --description "One-line outcome description"
```

4. Review the generated draft under:
   - `skill_drafts/<new-skill-slug>/SKILL.md`
   - `skill_drafts/<new-skill-slug>/references/`
   - `skill_drafts/<new-skill-slug>/checks/`
   - `skill_drafts/<new-skill-slug>/meta/task-summary.json`
5. Assess promotability:

```bash
python3 ~/.openclaw/workspace/scripts/skill_capture.py assess-draft \
  --name <new-skill-slug> \
  --json
```

6. Promote only after the assessment passes and you explicitly approve it:

```bash
python3 ~/.openclaw/workspace/scripts/skill_capture.py promote-draft \
  --name <new-skill-slug> \
  --approve
```

## Guardrails

- Draft generation is allowed only from `completed` tasks.
- It does not auto-register the skill in `SKILLS_REGISTRY.md`.
- It does not change routing rules or profile policy automatically.
- It is a drafting aid, not autonomous self-modification.
- Promotion is blocked when placeholders remain, no execution profile is declared, or no substantive artifact exists beyond the scaffold.
- Promotion also requires an explicit `--approve` flag even when the draft passes assessment.
- Assessment also checks likely duplicate-skill conflicts and policy conflicts when they can be inferred.
- Promotion prints follow-up steps for registry, policy, and validation work instead of silently ending.
