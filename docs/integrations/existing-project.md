# Add Helm to an Existing Project

Use this when you do not want a demo workspace. Helm stays in its own workspace and records operations around your existing project.

## Setup

```bash
helm init --path ~/.helm/workspace
helm doctor --path ~/.helm/workspace
```

## Inspect a project

```bash
helm profile --path ~/.helm/workspace run inspect_local \
  --task-name "inspect existing project" \
  -- git -C ~/project status --short
```

## Run a bounded edit or verification

```bash
helm profile --path ~/.helm/workspace run workspace_edit \
  --task-name "run project tests" \
  -- python3 -m pytest ~/project/tests -q
```

## Prepare a risky change

```bash
helm checkpoint create --path ~/.helm/workspace --label before-project-refactor --include ~/project/src
helm profile --path ~/.helm/workspace run risky_edit \
  --task-name "project refactor verification" \
  -- python3 -m pytest ~/project/tests -q
helm checkpoint recommend --path ~/.helm/workspace
```

## Inspect the outcome

```bash
helm status --path ~/.helm/workspace --brief
helm report --path ~/.helm/workspace --format markdown
```
