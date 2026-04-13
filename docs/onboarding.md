# Helm Onboarding

Helm should normally start as its own workspace, then adopt existing runtimes and note stores explicitly.

The intended sequence is:

```bash
helm init --path ~/.helm/workspace
helm survey --path ~/.helm/workspace
helm onboard --path ~/.helm/workspace --use-detected --dry-run
helm onboard --path ~/.helm/workspace --use-detected
```

By default, `helm onboard` applies the plan and then runs:

- `helm doctor`
- `helm validate`
- `helm status --verbose`

If you want to apply the plan without the follow-up checks:

```bash
helm onboard --path ~/.helm/workspace --use-detected --skip-checks
```

## What `helm survey` looks for

- existing OpenClaw-style runtime trees
- existing Hermes-style runtime trees
- Obsidian vaults or other Markdown-first note roots in common locations
- already adopted external sources

`helm onboard` turns those signals into an actual adoption plan and can apply it.

## Adoption model

Treat existing systems as read-only context sources first.

Examples:

```bash
helm adopt --path ~/.helm/workspace --from-path ~/.openclaw/workspace --name openclaw-main
helm adopt --path ~/.helm/workspace --from-path ~/.hermes --name hermes-main
helm adopt --path ~/.helm/workspace --from-path ~/Documents/Obsidian/MyVault --kind generic --name obsidian-main
```

## Obsidian guidance

Obsidian is optional, not required.

Helm cares about explicit file state, not a specific notes application. But if you already keep durable operational notes in Obsidian, adopting the vault as a read-only source is a strong default because it makes context hydration inspectable and portable.

If you do not use Obsidian, keep durable notes in Markdown under the Helm workspace anyway.
