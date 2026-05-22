"""Tests for :mod:`scripts.failure_signature`.

Covers:
- classify_error() on representative stderr snippets
- normalize_target() on paths, URLs, commands
- signature() on synthetic ledger events matching FS-001..FS-010
- fingerprint stability
- Ledger extension: writing entries with/without new optional fields,
  round-trip read, backward-compat with old entries.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.failure_signature import classify_error, normalize_target, signature


# ---------------------------------------------------------------------------
# classify_error
# ---------------------------------------------------------------------------

class TestClassifyError:
    def test_google_sheets_api_http_error(self):
        stderr = "HttpError 400: Unable to parse range: Sheet1!A1:Z100"
        assert classify_error(stderr) == "google_sheets_api"

    def test_google_sheets_api_quota(self):
        stderr = "googleapiclient.errors.HttpError: <HttpError 429 when requesting sheets.googleapis.com"
        assert classify_error(stderr) == "google_sheets_api"

    def test_google_sheets_api_auth(self):
        stderr = "google.auth.exceptions.TransportError: Failed to retrieve access token"
        assert classify_error(stderr) == "google_sheets_api"

    def test_credential_invalid_grant(self):
        stderr = "invalid_grant: Token has been expired or revoked."
        assert classify_error(stderr) == "credential_invalid_grant"

    def test_credential_invalid_grant_token(self):
        stderr = "oauth2: token invalid: invalid_grant"
        assert classify_error(stderr) == "credential_invalid_grant"

    def test_gemini_video_api_not_found(self):
        stderr = "gemini video file not found: files/abc123"
        assert classify_error(stderr) == "gemini_video_api"

    def test_gemini_video_api_quota(self):
        stderr = "ResourceExhausted: 429 Quota exceeded for quota metric 'generate_content' gemini"
        assert classify_error(stderr) == "gemini_video_api"

    def test_gemini_video_api_content_policy(self):
        stderr = "HARM_CATEGORY_DANGEROUS_CONTENT gemini_video_understand"
        assert classify_error(stderr) == "gemini_video_api"

    def test_obsidian_link_maintenance(self):
        stderr = "Error: Malformed wikilink in vault/path/note.md"
        assert classify_error(stderr) == "obsidian_link_maintenance"

    def test_obsidian_link_maintenance_vault(self):
        stderr = "obsidian vault not found at expected path"
        assert classify_error(stderr) == "obsidian_link_maintenance"

    def test_guard_deny(self):
        stderr = "GUARD DENY: command requires network access"
        assert classify_error(stderr) == "guard_deny"

    def test_guard_require_approval(self):
        stderr = "GUARD APPROVAL REQUIRED: high risk score 0.9"
        assert classify_error(stderr) == "guard_deny"

    def test_patch_failed(self):
        stderr = "patch: **** malformed patch at line 5"
        assert classify_error(stderr) == "patch_failed"

    def test_patch_failed_hunk(self):
        stderr = "Apply patch failed: Hunk #1 FAILED at 23."
        assert classify_error(stderr) == "patch_failed"

    def test_timeout(self):
        stderr = "TIMEOUT: command exceeded 1800s limit"
        assert classify_error(stderr) == "timeout"

    def test_timeout_subprocess(self):
        stderr = "subprocess.TimeoutExpired: Command timed out"
        assert classify_error(stderr) == "timeout"

    def test_exit_nonzero_generic(self):
        stderr = "Traceback (most recent call last): RuntimeError: something went wrong"
        assert classify_error(stderr) == "exit_nonzero"

    def test_empty_returns_unknown(self):
        assert classify_error("") == "unknown"

    def test_none_returns_unknown(self):
        assert classify_error(None) == "unknown"

    def test_whitespace_returns_unknown(self):
        assert classify_error("   ") == "unknown"

    def test_gws_auth(self):
        stderr = "gws: authentication failed: 401 Unauthorized"
        assert classify_error(stderr) == "google_sheets_api"

    def test_sheets_read_error(self):
        stderr = "sheets: Unable to parse range: 'Transactions'!A:Z"
        assert classify_error(stderr) == "google_sheets_api"


# ---------------------------------------------------------------------------
# normalize_target
# ---------------------------------------------------------------------------

class TestNormalizeTarget:
    def test_none_returns_none(self):
        assert normalize_target(None) is None

    def test_empty_returns_none(self):
        assert normalize_target("") is None

    def test_home_path_replaced(self):
        home = os.path.expanduser("~")
        result = normalize_target(f"{home}/documents/foo.txt")
        assert result == "~/documents/foo.txt"
        assert home not in result

    def test_workspace_path_replaced(self):
        result = normalize_target("/Users/kevin/.openclaw/workspace/scripts/foo.py")
        assert result == "<workspace>/scripts/foo.py"

    def test_tmp_path_digits_stripped(self):
        result = normalize_target("/tmp/run12345/output.json")
        assert result is not None
        assert "12345" not in result
        assert "tmp" in result

    def test_tmp_path_another_pattern(self):
        result = normalize_target("/tmp/abc9876def/log.txt")
        assert result is not None
        assert "9876" not in result

    def test_url_scheme_lowercased(self):
        result = normalize_target("HTTPS://example.com/path")
        assert result is not None
        assert result.startswith("https://")

    def test_url_query_params_stripped(self):
        result = normalize_target("https://api.example.com/v1/resource?key=secret&ts=12345")
        assert result is not None
        assert "key=secret" not in result
        assert "ts=12345" not in result

    def test_git_sha_in_path_stripped(self):
        result = normalize_target("/tmp/git-a1b2c3d4e5f6/work")
        assert result is not None
        assert "a1b2c3d4e5f6" not in result

    def test_git_sha_in_url_stripped(self):
        result = normalize_target("https://github.com/user/repo/commit/abc123def456789")
        assert result is not None
        # Long hex string should be stripped
        assert "abc123def456789" not in result

    def test_plain_command_unchanged(self):
        result = normalize_target("python3")
        assert result == "python3"

    def test_stable_across_calls(self):
        val = "/Users/kevin/.openclaw/workspace/scripts/foo.py"
        assert normalize_target(val) == normalize_target(val)


# ---------------------------------------------------------------------------
# signature() — one test per FS shape
# ---------------------------------------------------------------------------

def _make_event(
    command: list[str],
    skill: str | None = None,
    profile: str = "service_ops",
    exit_code: int | None = 1,
    status: str = "failed",
    failure_reason: str | None = None,
    meta_stderr: str | None = None,
) -> dict:
    """Build a minimal synthetic ledger event."""
    event: dict = {
        "task_id": "test-task-id",
        "command": command,
        "command_preview": " ".join(command),
        "profile": profile,
        "status": status,
        "exit_code": exit_code,
    }
    if skill:
        event["skill"] = skill
    if failure_reason:
        event["failure_reason"] = failure_reason
    if meta_stderr:
        event.setdefault("meta", {})
        event["meta"]["stderr"] = meta_stderr
    return event


class TestSignatureFS001:
    """FS-001: google_sheets_append_row.py → google_sheets_api"""

    def test_component(self):
        event = _make_event(
            ["python3", "/workspace/scripts/google_sheets_append_row.py", "--range", "Sheet1!A:Z"],
            skill="household-ledger-ko",
            profile="service_ops",
            exit_code=2,
        )
        sig = signature(event)
        assert sig["error_class"] == "google_sheets_api"
        assert sig["component"] in ("skill", "external_api", "google-workspace")

    def test_tool_normalized(self):
        event = _make_event(
            ["python3", "/workspace/scripts/google_sheets_append_row.py"],
            skill="household-ledger-ko",
        )
        sig = signature(event)
        assert "google_sheets_append_row" in sig["tool"]

    def test_fingerprint_is_string(self):
        event = _make_event(
            ["python3", "/workspace/scripts/google_sheets_append_row.py"],
            skill="household-ledger-ko",
        )
        sig = signature(event)
        assert isinstance(sig["fingerprint"], str)
        assert len(sig["fingerprint"]) > 0


class TestSignatureFS002:
    """FS-002: google_sheets_read_range.py → google_sheets_api"""

    def test_error_class(self):
        event = _make_event(
            ["python3", "/workspace/scripts/google_sheets_read_range.py"],
            skill="household-ledger-ko",
            profile="inspect_local",
            exit_code=2,
        )
        sig = signature(event)
        assert sig["error_class"] == "google_sheets_api"

    def test_tool_contains_read_range(self):
        event = _make_event(
            ["python3", "/workspace/scripts/google_sheets_read_range.py"],
        )
        sig = signature(event)
        assert "google_sheets_read_range" in sig["tool"]


class TestSignatureFS003:
    """FS-003: gws CLI → google_sheets_api / gws auth"""

    def test_error_class_gws(self):
        event = _make_event(
            ["gws", "list", "--format", "json"],
            skill="knowledge-capture-ko",
            profile="inspect_local",
            exit_code=3,
        )
        sig = signature(event)
        assert sig["error_class"] == "google_sheets_api"
        assert sig["tool"] == "gws"


class TestSignatureFS004:
    """FS-004: gemini_video_understand.py → gemini_video_api"""

    def test_error_class(self):
        event = _make_event(
            ["python3", "/workspace/scripts/gemini_video_understand.py", "--file", "video.mp4"],
            skill="knowledge-capture-ko",
            profile="service_ops",
            exit_code=1,
        )
        sig = signature(event)
        assert sig["error_class"] == "gemini_video_api"

    def test_tool_name(self):
        event = _make_event(
            ["python3", "gemini_video_understand.py"],
        )
        sig = signature(event)
        assert "gemini_video_understand" in sig["tool"]


class TestSignatureFS005:
    """FS-005: obsidian_link_maintenance.py → obsidian_link_maintenance"""

    def test_error_class_exit1(self):
        event = _make_event(
            ["python3", "/workspace/scripts/obsidian_link_maintenance.py"],
            skill="knowledge-capture-ko",
            profile="workspace_edit",
            exit_code=1,
        )
        sig = signature(event)
        assert sig["error_class"] == "obsidian_link_maintenance"

    def test_error_class_exit2(self):
        event = _make_event(
            ["python3", "/workspace/scripts/obsidian_link_maintenance.py"],
            skill="knowledge-capture-ko",
            profile="workspace_edit",
            exit_code=2,
        )
        sig = signature(event)
        assert sig["error_class"] == "obsidian_link_maintenance"

    def test_tool_name(self):
        event = _make_event(
            ["python3", "obsidian_link_maintenance.py"],
        )
        sig = signature(event)
        assert "obsidian_link_maintenance" in sig["tool"]


class TestSignatureFS006:
    """FS-006: python3 inline → exit_nonzero"""

    def test_error_class_service_ops(self):
        event = _make_event(
            ["python3", "-c", "import sys; sys.exit(1)"],
            skill="knowledge-capture-ko",
            profile="service_ops",
            exit_code=1,
        )
        sig = signature(event)
        assert sig["error_class"] in ("exit_nonzero", "google_sheets_api", "gemini_video_api")

    def test_error_class_workspace_edit(self):
        event = _make_event(
            ["python3", "-c", "raise ValueError('bad data')"],
            skill="knowledge-capture-ko",
            profile="workspace_edit",
            exit_code=1,
        )
        sig = signature(event)
        assert sig["error_class"] in ("exit_nonzero", "patch_failed")

    def test_tool_python3(self):
        event = _make_event(["python3", "-c", "pass"])
        sig = signature(event)
        assert sig["tool"] == "python3"


class TestSignatureFS007:
    """FS-007: guard deny → guard_deny"""

    def test_error_class(self):
        event = {
            "task_id": "test-guard-deny",
            "command": ["python3", "some_network_script.py"],
            "command_preview": "python3 some_network_script.py",
            "profile": "inspect_local",
            "status": "blocked",
            "exit_code": 25,
            "failure_stage": "guard",
            "failure_reason": "guard deny",
        }
        sig = signature(event)
        assert sig["error_class"] == "guard_deny"
        assert sig["component"] == "guard"

    def test_target_profile(self):
        event = {
            "task_id": "test-guard-deny",
            "command": ["ls", "-la"],
            "command_preview": "ls -la",
            "profile": "inspect_local",
            "status": "blocked",
            "exit_code": 25,
            "failure_stage": "guard",
            "failure_reason": "guard deny",
        }
        sig = signature(event)
        assert sig["target"] == "profile:inspect_local"


class TestSignatureFS008:
    """FS-008: guard require_approval → guard_deny"""

    def test_error_class(self):
        event = {
            "task_id": "test-approval-required",
            "command": ["rm", "-rf", "/tmp/old"],
            "command_preview": "rm -rf /tmp/old",
            "profile": "workspace_edit",
            "status": "blocked",
            "exit_code": 24,
            "failure_stage": "guard",
            "failure_reason": "approval required",
        }
        sig = signature(event)
        assert sig["error_class"] == "guard_deny"
        assert sig["component"] == "guard"

    def test_target_profile_workspace_edit(self):
        event = {
            "task_id": "test-approval-required",
            "command": ["rm", "-rf", "/tmp"],
            "command_preview": "rm -rf /tmp",
            "profile": "workspace_edit",
            "status": "blocked",
            "exit_code": 24,
            "failure_stage": "guard",
            "failure_reason": "approval required",
        }
        sig = signature(event)
        assert sig["target"] == "profile:workspace_edit"


class TestSignatureFS009:
    """FS-009: zsh/bash inline → exit_nonzero"""

    def test_error_class_bash(self):
        event = _make_event(
            ["bash", "-c", "cat /nonexistent"],
            profile="inspect_local",
            exit_code=1,
        )
        sig = signature(event)
        assert sig["error_class"] == "exit_nonzero"

    def test_error_class_zsh(self):
        event = _make_event(
            ["zsh", "-c", "nonexistent_cmd"],
            profile="inspect_local",
            exit_code=127,
        )
        sig = signature(event)
        assert sig["error_class"] == "exit_nonzero"

    def test_tool_shell(self):
        event = _make_event(["zsh", "-c", "echo hello"])
        sig = signature(event)
        assert sig["tool"] in ("zsh", "bash", "sh")


class TestSignatureFS010:
    """FS-010: household_ledger_runner.py → exit_nonzero (composite)"""

    def test_error_class(self):
        event = _make_event(
            ["python3", "/workspace/scripts/household_ledger_runner.py"],
            skill="household-ledger-ko",
            profile="service_ops",
            exit_code=2,
        )
        sig = signature(event)
        assert sig["error_class"] in ("exit_nonzero", "google_sheets_api")

    def test_tool_name(self):
        event = _make_event(
            ["python3", "household_ledger_runner.py"],
        )
        sig = signature(event)
        assert "household_ledger_runner" in sig["tool"]


# ---------------------------------------------------------------------------
# signature() — key dict structure
# ---------------------------------------------------------------------------

class TestSignatureStructure:
    REQUIRED_KEYS = {"component", "tool", "profile", "error_class", "target", "fingerprint"}

    def test_all_keys_present(self):
        event = _make_event(["python3", "foo.py"])
        sig = signature(event)
        assert self.REQUIRED_KEYS.issubset(set(sig.keys()))

    def test_profile_in_signature(self):
        event = _make_event(["python3", "foo.py"], profile="service_ops")
        sig = signature(event)
        assert sig["profile"] == "service_ops"

    def test_profile_none_for_missing(self):
        event = {"task_id": "x", "command": ["ls"], "command_preview": "ls", "status": "failed"}
        sig = signature(event)
        assert sig["profile"] is None

    def test_error_class_is_string(self):
        event = _make_event(["python3", "foo.py"])
        sig = signature(event)
        assert isinstance(sig["error_class"], str)

    def test_component_is_string(self):
        event = _make_event(["python3", "foo.py"])
        sig = signature(event)
        assert isinstance(sig["component"], str)


# ---------------------------------------------------------------------------
# Fingerprint stability
# ---------------------------------------------------------------------------

class TestFingerprintStability:
    def test_same_inputs_same_fingerprint(self):
        event = _make_event(
            ["python3", "google_sheets_append_row.py"],
            skill="household-ledger-ko",
            profile="service_ops",
        )
        sig1 = signature(event)
        sig2 = signature(event)
        assert sig1["fingerprint"] == sig2["fingerprint"]

    def test_different_tool_different_fingerprint(self):
        event1 = _make_event(["python3", "google_sheets_append_row.py"], profile="service_ops")
        event2 = _make_event(["python3", "gemini_video_understand.py"], profile="service_ops")
        sig1 = signature(event1)
        sig2 = signature(event2)
        assert sig1["fingerprint"] != sig2["fingerprint"]

    def test_profile_does_not_affect_fingerprint(self):
        event1 = _make_event(["python3", "foo.py"], profile="service_ops")
        event2 = _make_event(["python3", "foo.py"], profile="workspace_edit")
        sig1 = signature(event1)
        sig2 = signature(event2)
        # Per spec, profile is NOT part of the fingerprint hash input.
        assert sig1["fingerprint"] == sig2["fingerprint"]

    def test_different_error_class_different_fingerprint(self):
        # guard deny vs non-guard
        event1 = {
            "task_id": "t1",
            "command": ["ls"],
            "command_preview": "ls",
            "profile": "inspect_local",
            "status": "blocked",
            "exit_code": 25,
            "failure_stage": "guard",
            "failure_reason": "guard deny",
        }
        event2 = _make_event(["ls"], profile="inspect_local", exit_code=1)
        sig1 = signature(event1)
        sig2 = signature(event2)
        assert sig1["fingerprint"] != sig2["fingerprint"]

    def test_fingerprint_short(self):
        event = _make_event(["python3", "foo.py"])
        sig = signature(event)
        # Should be a short hash, not a full SHA-256
        assert len(sig["fingerprint"]) <= 16


# ---------------------------------------------------------------------------
# Ledger extension — new optional fields
# ---------------------------------------------------------------------------

class TestLedgerExtension:
    """Verify that the ledger writer (append_jsonl_atomic + build_ledger_entry)
    persists new optional fields and that old entries without them still parse.
    """

    def _write_and_read(self, path: Path, entries: list[dict]) -> list[dict]:
        from scripts.state_io import append_jsonl_atomic
        for entry in entries:
            append_jsonl_atomic(path, entry)
        from scripts.jsonl_io import read_jsonl
        return read_jsonl(path)

    def test_failure_signature_persisted(self, tmp_path: Path):
        path = tmp_path / "ledger.jsonl"
        entry = {
            "task_id": "abc",
            "status": "failed",
            "failure_signature": {
                "component": "skill",
                "tool": "google_sheets_append_row",
                "profile": "service_ops",
                "error_class": "google_sheets_api",
                "target": None,
                "fingerprint": "abcd1234",
            },
        }
        rows = self._write_and_read(path, [entry])
        assert len(rows) == 1
        assert rows[0]["failure_signature"]["error_class"] == "google_sheets_api"
        assert rows[0]["failure_signature"]["fingerprint"] == "abcd1234"

    def test_sessions_persisted(self, tmp_path: Path):
        path = tmp_path / "ledger.jsonl"
        entry = {
            "task_id": "abc",
            "status": "completed",
            "sessions": ["session-001", "session-002"],
        }
        rows = self._write_and_read(path, [entry])
        assert rows[0]["sessions"] == ["session-001", "session-002"]

    def test_snapshot_evidence_persisted(self, tmp_path: Path):
        path = tmp_path / "ledger.jsonl"
        entry = {
            "task_id": "abc",
            "status": "completed",
            "snapshot_evidence": "~/.openclaw/workspace/.openclaw/snapshots/snap123.json",
        }
        rows = self._write_and_read(path, [entry])
        assert rows[0]["snapshot_evidence"] == "~/.openclaw/workspace/.openclaw/snapshots/snap123.json"

    def test_cleanup_status_persisted(self, tmp_path: Path):
        path = tmp_path / "ledger.jsonl"
        for value in ("ok", "partial", "failed", "not_required"):
            entry = {"task_id": f"task-{value}", "status": "completed", "cleanup_status": value}
            path2 = tmp_path / f"ledger_{value}.jsonl"
            rows = self._write_and_read(path2, [entry])
            assert rows[0]["cleanup_status"] == value

    def test_browser_fields_persisted(self, tmp_path: Path):
        path = tmp_path / "ledger.jsonl"
        entry = {
            "task_id": "browser-task",
            "status": "completed",
            "browser_profile": "default",
            "browser_mode": "headless",
            "source_urls": ["https://example.com/page1"],
            "screenshot_evidence": "~/screenshots/task123.png",
            "console_network_signals": {"xhr_count": 5},
            "site_note_update": "Updated landing page note.",
        }
        rows = self._write_and_read(path, [entry])
        r = rows[0]
        assert r["browser_profile"] == "default"
        assert r["browser_mode"] == "headless"
        assert r["source_urls"] == ["https://example.com/page1"]
        assert r["screenshot_evidence"] == "~/screenshots/task123.png"
        assert r["console_network_signals"]["xhr_count"] == 5
        assert r["site_note_update"] == "Updated landing page note."

    def test_retry_count_unchanged(self, tmp_path: Path):
        path = tmp_path / "ledger.jsonl"
        entry = {"task_id": "abc", "status": "failed", "retry_count": 3}
        rows = self._write_and_read(path, [entry])
        assert rows[0]["retry_count"] == 3

    def test_old_entry_without_new_fields_parses_cleanly(self, tmp_path: Path):
        """Legacy entries (no failure_signature, no sessions, etc.) still round-trip."""
        path = tmp_path / "ledger.jsonl"
        legacy_entry = {
            "task_id": "legacy-001",
            "task_name": "old task",
            "profile": "service_ops",
            "status": "completed",
            "exit_code": 0,
            "retry_count": 0,
        }
        rows = self._write_and_read(path, [legacy_entry])
        assert rows[0]["task_id"] == "legacy-001"
        # New fields must NOT appear on old entries
        assert "failure_signature" not in rows[0]
        assert "sessions" not in rows[0]
        assert "cleanup_status" not in rows[0]
        assert "snapshot_evidence" not in rows[0]

    def test_mixed_old_and_new_entries(self, tmp_path: Path):
        """Mix of legacy and new-field entries all parse correctly."""
        path = tmp_path / "ledger.jsonl"
        legacy = {"task_id": "leg-001", "status": "completed", "exit_code": 0}
        new_entry = {
            "task_id": "new-001",
            "status": "failed",
            "failure_signature": {"component": "guard", "tool": "run_with_profile", "profile": None,
                                   "error_class": "guard_deny", "target": "profile:inspect_local",
                                   "fingerprint": "deadbeef"},
        }
        rows = self._write_and_read(path, [legacy, new_entry])
        assert len(rows) == 2
        assert "failure_signature" not in rows[0]
        assert rows[1]["failure_signature"]["error_class"] == "guard_deny"

    def test_no_null_fillers_on_legacy_roundtrip(self, tmp_path: Path):
        """Round-tripping a legacy entry must not inject null values for new fields."""
        path = tmp_path / "ledger.jsonl"
        legacy = {
            "task_id": "leg-002",
            "status": "failed",
            "exit_code": 1,
        }
        rows = self._write_and_read(path, [legacy])
        row = rows[0]
        for field in ("failure_signature", "sessions", "snapshot_evidence", "cleanup_status",
                      "browser_profile", "browser_mode", "source_urls", "screenshot_evidence",
                      "console_network_signals", "site_note_update"):
            assert field not in row, f"New field {field!r} should not appear on legacy entry"


class TestBuildLedgerEntry:
    """Unit tests for scripts.state_io.build_ledger_entry."""

    def test_returns_copy_not_mutation(self):
        from scripts.state_io import build_ledger_entry
        base = {"task_id": "abc", "status": "failed"}
        result = build_ledger_entry(base, sessions=["s1"])
        assert "sessions" not in base  # original unchanged

    def test_base_fields_preserved(self):
        from scripts.state_io import build_ledger_entry
        base = {"task_id": "abc", "status": "failed", "exit_code": 1, "retry_count": 2}
        result = build_ledger_entry(base)
        assert result["retry_count"] == 2
        assert result["exit_code"] == 1

    def test_failure_signature_included(self):
        from scripts.state_io import build_ledger_entry
        sig = {"component": "guard", "tool": "run_with_profile", "profile": "inspect_local",
               "error_class": "guard_deny", "target": "profile:inspect_local", "fingerprint": "abc12345"}
        result = build_ledger_entry({"task_id": "t1"}, failure_signature=sig)
        assert result["failure_signature"]["error_class"] == "guard_deny"

    def test_sessions_included(self):
        from scripts.state_io import build_ledger_entry
        result = build_ledger_entry({"task_id": "t1"}, sessions=["sess-001"])
        assert result["sessions"] == ["sess-001"]

    def test_cleanup_status_valid_values(self):
        from scripts.state_io import build_ledger_entry
        for val in ("ok", "partial", "failed", "not_required"):
            result = build_ledger_entry({"task_id": "t1"}, cleanup_status=val)
            assert result["cleanup_status"] == val

    def test_cleanup_status_invalid_raises(self):
        from scripts.state_io import build_ledger_entry
        with pytest.raises(ValueError, match="cleanup_status"):
            build_ledger_entry({"task_id": "t1"}, cleanup_status="bad_value")

    def test_none_fields_not_included(self):
        from scripts.state_io import build_ledger_entry
        result = build_ledger_entry({"task_id": "t1"})
        for field in ("failure_signature", "sessions", "snapshot_evidence", "cleanup_status",
                      "browser_profile", "browser_mode", "source_urls", "screenshot_evidence",
                      "console_network_signals", "site_note_update"):
            assert field not in result

    def test_browser_fields_included(self):
        from scripts.state_io import build_ledger_entry
        result = build_ledger_entry(
            {"task_id": "t1"},
            browser_profile="default",
            browser_mode="headless",
            source_urls=["https://example.com"],
            screenshot_evidence="~/screenshots/s.png",
            console_network_signals={"xhr_count": 3},
            site_note_update="note text",
        )
        assert result["browser_profile"] == "default"
        assert result["browser_mode"] == "headless"
        assert result["source_urls"] == ["https://example.com"]
        assert result["screenshot_evidence"] == "~/screenshots/s.png"
        assert result["console_network_signals"]["xhr_count"] == 3
        assert result["site_note_update"] == "note text"

    def test_snapshot_evidence_included(self):
        from scripts.state_io import build_ledger_entry
        result = build_ledger_entry({"task_id": "t1"}, snapshot_evidence="~/snaps/s.json")
        assert result["snapshot_evidence"] == "~/snaps/s.json"

    def test_round_trip_via_append(self, tmp_path: Path):
        """build_ledger_entry result persists cleanly via append_jsonl_atomic."""
        from scripts.state_io import build_ledger_entry, append_jsonl_atomic
        from scripts.jsonl_io import read_jsonl
        path = tmp_path / "ledger.jsonl"
        sig = {"component": "skill", "tool": "gemini_video_understand", "profile": "service_ops",
               "error_class": "gemini_video_api", "target": None, "fingerprint": "feedcafe"}
        entry = build_ledger_entry(
            {"task_id": "rt-001", "status": "failed"},
            failure_signature=sig,
            sessions=["s1", "s2"],
            cleanup_status="not_required",
        )
        append_jsonl_atomic(path, entry)
        rows = read_jsonl(path)
        assert len(rows) == 1
        assert rows[0]["failure_signature"]["error_class"] == "gemini_video_api"
        assert rows[0]["sessions"] == ["s1", "s2"]
        assert rows[0]["cleanup_status"] == "not_required"
