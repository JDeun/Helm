# Contributing to Helm

Helm is an operations layer for long-lived AI agent workspaces. Contributions are most useful when they improve safety, recovery, auditability, or durable context for repeated agent work.

## Good First Contributions

- Improve first-run docs, examples, or comparison pages.
- Add tests around guardrail behavior, checkpoint handling, reports, or workspace detection.
- Make CLI output clearer without changing machine-readable formats.
- Add portable integrations for existing local agent workspaces.

## Design Principles

- Keep Helm runtime-agnostic. It should work around agent systems rather than becoming one.
- Prefer files as the source of truth. SQLite and reports are indexes or views.
- Fail closed for risky command handling and policy parsing.
- Keep private memory, credentials, and personal overlays out of the public project.

## Development

```bash
python3 -m pytest -q
git diff --check
```

For packaging changes, also run a target install smoke test:

```bash
python3 -m pip install --target /tmp/helm-install-check .
env PYTHONPATH=/tmp/helm-install-check /tmp/helm-install-check/bin/helm init --path /tmp/helm-installed-test --force
```

## Pull Requests

Please include:

- the user-facing problem being solved
- the affected commands or docs
- tests or smoke checks run
- any compatibility notes for existing workspaces
