# Review & quality gate

A two-layer gate keeps changes correct:

## 1. Automated (enforced by `.git/hooks/pre-push`)

`git push` runs `python3 -m pytest -q`. A red suite blocks the push. Keep the
suite green and add a regression test with every fix.

## 2. Adversarial review (manual, for non-trivial changes)

Tests catch what they assert; they miss what no one thought to assert. Before
pushing non-trivial changes (new command/module, security-relevant code, edits
to core paths like `run_with_profile`/`command_guard`/`long_running_runtime`),
run an adversarial review:

1. **Fan out** independent reviewers over the change (by module or dimension).
   Each is told to *refute* correctness — hunt for the failing input, the edge
   case, the security hole (injection, auth gap, path traversal), the
   backward-compat regression — not to confirm the happy path.
2. **Verify** each finding yourself (reproduce it) before acting; separate real
   defects from false positives.
3. **Fix** confirmed defects and add a regression test that would have failed.
4. **Converge**: run a second round scoped to the *fix diffs* (a fix can
   regress). **Stop when a round yields no new critical/important finding** —
   further rounds hit diminishing returns.

### Why

The 0.13.0 operations-layer work (imported from live OpenClaw operation) passed
1,604 tests yet a first adversarial round found 7 real defects the tests missed
(a symlink-escape write, an order-dependent template substitution, a
crash-the-whole-router path, …). Round two converged clean. Tests lock what was
found; this review finds what tests don't.
