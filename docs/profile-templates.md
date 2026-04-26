# Profile and Policy Templates

Helm ships conservative defaults. Use these templates as design guidance when adapting a workspace policy.

## Personal local agent

Use when one developer runs a local assistant against personal projects.

- Default profile: `inspect_local`
- Common edit profile: `workspace_edit`
- Require checkpoints for broad file rewrites
- Keep network under `service_ops`

Recommended first commands:

```bash
helm profile --path ~/.helm/workspace run inspect_local --task-name "inspect project" -- git status --short
helm status --path ~/.helm/workspace --brief
```

## Team internal agent

Use when multiple people inspect or reuse the same agent workspace.

- Require task names for non-read-only profiles
- Prefer `workspace_edit` over `risky_edit`
- Keep report artifacts for review
- Use explicit skill contracts before promotion

## Strict local-only

Use when the agent must not perform network or service operations.

- Disable or avoid `service_ops`
- Run model health checks only against local providers
- Treat dependency installation as manual handoff
- Keep command guard in enforce mode

## Service-heavy automation

Use when workflows legitimately call local services or APIs.

- Use `service_ops` for networked commands
- Require explicit task names
- Keep credentials outside Helm files
- Prefer audit-first commands before mutation

## Rule

Templates should guide policy choices. They should not silently change user behavior. Make policy changes explicit in version control.
