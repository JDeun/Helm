# chromux Phase 1 — Local Install + Isolated Smoke Test

- Task: #10 of the harness-engineering initiative
- Profile: `chromux-openclaw-test` (NOT default; cleaned up post-run)
- Target URL: `https://example.com` only

## STATUS: DONE

The smoke sequence succeeded on the first attempt with no manual intervention. The isolated profile was created, used for a snapshot, closed, and killed; `chromux ps` confirmed the profile no longer runs.

## 1. Preconditions

| Check | Result |
| --- | --- |
| Node version | v24.14.1 (spec requires ≥ 22) |
| Chrome.app present | yes |
| chromux installed (before) | no |
| `~/team-attention/` exists | no (created fresh) |
| chromux installed (after) | yes — `<home>/.npm-global/bin/chromux` |

## 2. Install path taken

`npm install -g .` from `~/team-attention/chromux` (clone of `team-attention/chromux` at version 0.7.0). Single npm package, no transitive deps; took ~291 ms.

## 3. Smoke command sequence

```
chromux launch chromux-openclaw-test
chromux --profile chromux-openclaw-test open smoke https://example.com
chromux ps
chromux --profile chromux-openclaw-test snapshot smoke
chromux --profile chromux-openclaw-test close smoke
chromux kill chromux-openclaw-test
chromux ps
```

## 4. Step results

| Step | Result | Sanitized evidence |
| --- | --- | --- |
| `launch chromux-openclaw-test` | PASS | userDataDir under `<home>/.chromux/profiles/chromux-openclaw-test`, headed, mode default |
| `open smoke https://example.com` | PASS | session=smoke, url=https://example.com/, title=Example Domain |
| `ps` (during) | PASS | one profile, port 9300, status running, daemon ok, tabs=1 |
| `snapshot smoke` | PASS | 229 bytes |
| `close smoke` | PASS | hint emitted for non-secret site notes (chromux-work workflow) |
| `kill chromux-openclaw-test` | PASS | profile and daemon stopped |
| `ps` (after) | PASS | "No running profiles." |

## 5. Snapshot evidence

229-byte ARIA-tree snapshot. First five lines (verbatim, no sanitization needed — only public example.com text):

```
# Example Domain
# https://example.com/

heading "Example Domain"
p "This domain is for use in documentation examples without needing permission. Avoid use in operations"
```

## 6. Cleanup confirmation

`chromux ps` post-kill: `No running profiles.`

No leftover process; the daemon and Chrome instance for the isolated profile both terminated. The profile's `userDataDir` directory remains under `<home>/.chromux/profiles/chromux-openclaw-test` (chromux design — re-launching reuses it). This is acceptable for the test profile; if a future task wants a pristine baseline, the directory can be removed manually.

## 7. Notes for Task #11 / #12

- `chromux close` emits a `knowledgeHint` reminder pointing at non-secret site notes — this is the upstream knowledge-capture gate hook. The Task #11 chromux-work skill and Task #12 `browser_site_note_gate` should treat the hint as the trigger for the gated promotion flow.
- chromux's `ps` output is human-readable but column-aligned with spaces, not tabs. A future runner integration that parses `ps` should treat it as fixed-width fields, not whitespace-split.
- The `--profile` and `--port` flags work as documented; default port is auto-assigned (9300 in this run).
- No interaction with the user's main Chrome profile occurred. The isolated profile launched in a separate Chrome process with its own userDataDir.

## Boundary statement

This smoke test deliberately did NOT exercise:

- Logged-in sites (no auth, no cookies, no profile bridge)
- Mutation operations (no form submit, no purchase, no message send)
- Parallel worker-tab pools (`crawl` mode untested in Phase 1)
- Helm `profile_pause_resume` integration (a future task)

Phase 1 simply verifies that chromux can be installed and exercised on an isolated profile without disturbing the user's environment.
