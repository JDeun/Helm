# Evidence Label Convention

Use compact `kind:value` labels when recording reviewed task evidence. Labels
should describe what a human or deterministic check verified, not just what
command happened to run.

Recommended labels:

- `test:<name>`: automated test passed, e.g. `test:pytest`
- `lint:<name>`: lint/static check passed
- `diff:reviewed`: human reviewed the diff
- `healthcheck:<name>`: service or runtime health check passed
- `provider:<request-or-response-id>`: external provider result was checked
- `artifact:<path>`: durable artifact was produced and inspected
- `rollback:<checkpoint-id>`: rollback path was verified
- `direct:<path-or-id>`: direct corpus/file/ledger inspection was performed

Avoid vague labels such as `done`, `ok`, or `checked` because they do not say
what evidence was reviewed.

`helm harness record-evidence --completion-evidence` and `helm task complete
--evidence` both accept these labels. Recording evidence does not by itself
approve risky follow-up actions.
