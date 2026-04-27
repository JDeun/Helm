# Public Launch Checklist

Use this checklist before announcing a Helm release.

## Repository

- Confirm README install commands work from a clean Python environment.
- Confirm `README.md`, `README.ko.md`, `CHANGELOG.md`, and `docs/releases/` mention the same version.
- Add or review GitHub topics:
  - `ai-agents`
  - `agent-ops`
  - `coding-agents`
  - `developer-tools`
  - `cli`
  - `python`
  - `workflow`
  - `guardrails`
  - `checkpoints`
  - `local-first`
- Create a GitHub Release with the changelog entry.
- Verify the release triggered `.github/workflows/publish.yml`.

## PyPI

- Confirm the project page opens: https://pypi.org/project/helm-agent-ops/
- Confirm the latest version is visible.
- Confirm project links include Homepage, Documentation, Repository, Issues, Changelog, and Security.
- Confirm files show Trusted Publishing provenance.

## Demo Assets

- Record a short terminal demo:
  - install Helm
  - run `helm doctor`
  - run one profiled command
  - create one checkpoint
  - show `helm dashboard`
- Add the demo to README or link it near the Quickstart.

## Announcement

- Lead with the operational problem, not the implementation.
- Include the PyPI install command.
- Include the GitHub repo link.
- Include one concrete example command.
- Mention that Helm is local-first and runtime-agnostic.

## Landing Page

- Generate or update the page from `docs/landing-page-brief.md`.
- Keep the first viewport focused on the install command and GitHub/PyPI links.
- Avoid generic AI visuals; show profiles, checkpoints, task ledgers, or dashboard state.
