# Privacy Boundary

Helm treats privacy filtering as an agent/tool boundary concern.

The goal is not to make private data public-safe after it has already been written into logs. The goal is to intercept sensitive spans before external tools, remote handoffs, shared reports, or durable public artifacts receive them.

## Pattern

Use recoverable pseudonymization for private values that must keep stable identity across a task.

```text
alice@example.com asked Bob to call 010-1234-5678
-> <PRIVATE_EMAIL_1> asked Bob to call <PRIVATE_PHONE_1>
```

The tokenized text may cross a tool boundary. The raw mapping must stay in a local vault owned by the runtime.

Secrets are different. API keys, passwords, refresh tokens, and similar values should be redacted by default, not stored as recoverable labels.

## CLI

Scan without writing a vault:

```bash
helm privacy scan --text "Contact alice@example.com" --json
```

Tokenize into the workspace-local vault:

```bash
helm privacy tokenize --scope task-123 --text "Contact alice@example.com"
```

Restore only at an authorized user-facing boundary:

```bash
helm privacy restore --scope task-123 --text "Contact <PRIVATE_EMAIL_1>"
```

The default vault is `.helm/privacy-vault.json` under the target workspace. The default audit log is `.helm/privacy-audit.jsonl`.

## Boundary Rules

- Run privacy scan/tokenization before `service_ops`, `remote_handoff`, external API calls, shared reports, or subagent handoffs that include user/private context.
- Keep tokenized text in task ledgers, command logs, checkpoints, and exported documentation when the raw value is not needed.
- Keep raw mappings only in the local vault. Do not copy vault contents into memory notes, public fixtures, release docs, or examples.
- Restore only for an authorized user-facing response or a local operation that genuinely needs the raw value.
- Audit every tokenize and restore event with scope, operation, vault path, labels, and counts.

## Public Helm Versus Private Runtimes

Helm ships the local-first primitive: scan, tokenize, restore, and audit.

Private runtimes such as OpenClaw may wrap this primitive with stronger detectors, keychain-backed or encrypted vaults, user/session authorization, restore approvals, and anomaly monitoring.

Do not promote private vault data, private detector corpora, personal memory, or workspace-specific restore policy into public Helm.
