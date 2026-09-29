"""The published scrubber must not be weaker than the one it claims to build on.

helm shipped redaction.py with no tests at all: the workspace had them, and they did
not travel with the module. These come from an adversarial review of the published
surface, and every one of them failed when it was written.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "helm" / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "helm" / "scripts"))

import redaction  # noqa: E402


class TestRedactIsASupersetOfRedactSecrets:
    """redact() calls itself the full publishing scrubber "on top of the secret rules".

    It was not. Two rules the secret list carries were missing from the publishing
    list, so the scrubber used on content destined for publication was strictly
    weaker than the one used on internal evidence.
    """

    def test_bearer_token_after_a_key_value_header(self):
        # The module's own comment above SECRET_PATTERNS documents this exact failure:
        # the key-value rule's \S+ eats the word "Bearer", orphaning the token. It was
        # fixed by ordering in that list and left unfixed in the publishing list.
        out = redaction.redact("access_token: Bearer QQQabc12345678")
        assert "QQQabc12345678" not in out, out

    def test_authorization_header_opaque_token(self):
        out = redaction.redact("Authorization: QQQopaque1234567890")
        assert "QQQopaque1234567890" not in out, out

    def test_authorization_header_basic_credentials(self):
        out = redaction.redact("Authorization: Basic ZHVtbXk6cGFzc3dk")
        assert "ZHVtbXk6cGFzc3dk" not in out, out

    def test_redact_masks_everything_redact_secrets_masks(self):
        """The general claim, not just the two known cases."""
        samples = [
            "access_token: Bearer QQQabc12345678",
            "Authorization: QQQopaque1234567890",
            "api_key=sk-abcdefghijklmnopqrst12345678",
            "postgres://appuser:QQQhunter2pw@db.example.test/x",
        ]
        for text in samples:
            secrets_out = redaction.redact_secrets(text)
            publish_out = redaction.redact(text)
            leaked = [
                tok for tok in ("QQQabc12345678", "QQQopaque1234567890",
                                "ZHVtbXk6cGFzc3dk", "QQQhunter2pw")
                if tok in publish_out and tok not in secrets_out
            ]
            assert not leaked, (text, publish_out, leaked)


class TestShimCannotBeHijackedByAToplevelModule:
    """The dual-import guard must not prefer an arbitrary top-level module.

    Ported files carry `try: from redaction import SECRET_REGEXES / except
    ModuleNotFoundError: from helm.scripts.redaction import ...`. Inside helm the
    primary branch is meaningless -- there is no top-level `redaction` -- but it WINS
    whenever anything by that name is importable: an older install, a user module, a
    file in the working directory. `redaction`, `state_io` and `task_state_bundle` are
    generic names.

    Demonstrated before the fix: a two-line decoy on sys.path made
    task_state_bundle._redact return an API key unmasked, with nothing raised and
    nothing logged. A guard that can be silently disabled by a filename is worse than
    no guard, because callers believe it ran.
    """

    def test_ported_modules_import_redaction_from_the_helm_package(self):
        import subprocess
        import tempfile
        import textwrap

        with tempfile.TemporaryDirectory() as tmp:
            decoy = Path(tmp) / "redaction.py"
            decoy.write_text("SECRET_REGEXES = []\n", encoding="utf-8")
            probe = textwrap.dedent(f"""
                import sys
                sys.path.insert(0, {tmp!r})
                sys.path.insert(1, {str(ROOT)!r})
                import helm.scripts.task_state_bundle as t
                print(len(t.SECRET_REGEXES))
            """)
            out = subprocess.run(
                [sys.executable, "-c", probe], capture_output=True, text=True, cwd=tmp
            )
            assert out.returncode == 0, out.stderr
            assert out.stdout.strip() != "0", (
                "a decoy redaction.py on sys.path replaced the real SECRET_REGEXES; "
                "the shim's primary branch must not win over the helm package"
            )


class TestHomePathsOnEveryOs:
    """_PATHS claims to cover "any machine"; it covered macOS and `~/` only.

    pyproject declares "Operating System :: OS Independent". On Linux or Windows
    redact() masked no home path at all, so the generalisation that removed three
    deployment names left a rule that works on one OS.
    """

    def test_linux_home_path(self):
        assert "testuser" not in redaction.redact("/home/testuser/secret/a")

    def test_windows_home_path(self):
        assert "testuser" not in redaction.redact(r"C:\Users\testuser\Desktop\a.txt")

    def test_macos_home_path_still_masked(self):
        assert "testuser" not in redaction.redact("/Users/testuser/x")

    def test_tilde_still_masked(self):
        assert redaction.redact("~/y") == "[path]"

    def test_an_ordinary_word_is_not_a_path(self):
        """The widened rule must not start eating prose."""
        assert redaction.redact("homeward bound") == "homeward bound"
        assert redaction.redact("see users table") == "see users table"
