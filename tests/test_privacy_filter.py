from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from scripts.privacy_filter import detect, load_vault, restore_text, tokenize_text


REPO_ROOT = Path(__file__).resolve().parents[1]


def create_workspace(root: Path) -> None:
    (root / ".helm").mkdir(parents=True)


def run_cli(workspace: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["HELM_WORKSPACE"] = str(workspace)
    env["PYTHONPATH"] = str(REPO_ROOT)
    return subprocess.run(
        [sys.executable, str(REPO_ROOT / "helm.py"), *args],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def test_detect_finds_recoverable_pii_and_nonrecoverable_secret() -> None:
    findings = detect("Email me at alice@example.com. api_key=sk-test-secret")
    labels = [item.label for item in findings]
    assert "EMAIL" in labels
    assert "SECRET" in labels
    assert [item for item in findings if item.label == "SECRET"][0].recoverable is False


def test_tokenize_uses_stable_tokens_and_restore_round_trips() -> None:
    vault = load_vault(Path("/tmp/nonexistent-helm-privacy-vault.json"))
    text = "Alice alice@example.com emailed alice@example.com."

    tokenized, replacements = tokenize_text(text, vault=vault, scope="task-1")
    assert tokenized.count("<PRIVATE_EMAIL_1>") == 2
    assert len(replacements) == 2

    restored, count = restore_text(tokenized, vault=vault, scope="task-1")
    assert restored == text
    assert count == 2


def test_secret_values_are_redacted_not_stored_in_vault() -> None:
    vault = load_vault(Path("/tmp/nonexistent-helm-privacy-vault.json"))
    tokenized, replacements = tokenize_text("password=hunter2", vault=vault, scope="task-1")

    assert "<SECRET_REDACTED_1>" in tokenized
    assert replacements[0].recoverable is False
    assert vault["scopes"]["task-1"]["tokens"] == {}


def test_privacy_cli_tokenize_restore_writes_vault_and_audit() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        create_workspace(root)

        tokenized = run_cli(
            root,
            "privacy",
            "tokenize",
            "--scope",
            "task-1",
            "--text",
            "Contact alice@example.com",
            "--json",
        )
        assert tokenized.returncode == 0, tokenized.stderr
        payload = json.loads(tokenized.stdout)
        assert payload["text"] == "Contact <PRIVATE_EMAIL_1>"
        assert Path(payload["vault"]).exists()

        restored = run_cli(
            root,
            "privacy",
            "restore",
            "--scope",
            "task-1",
            "--text",
            payload["text"],
            "--json",
        )
        assert restored.returncode == 0, restored.stderr
        restore_payload = json.loads(restored.stdout)
        assert restore_payload["text"] == "Contact alice@example.com"

        audit_path = root / ".helm" / "privacy-audit.jsonl"
        assert audit_path.exists()
        events = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]
        assert [event["operation"] for event in events] == ["tokenize", "restore"]
