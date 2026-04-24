from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.discovery import discover_environment, snapshot_to_json


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _clear_all_provider_env(monkeypatch: pytest.MonkeyPatch) -> None:
    keys = [
        "OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_API_BASE",
        "ANTHROPIC_API_KEY", "CLAUDE_API_KEY",
        "GEMINI_API_KEY", "GOOGLE_API_KEY",
        "OPENROUTER_API_KEY",
        "AZURE_OPENAI_API_KEY", "AZURE_OPENAI_ENDPOINT",
        "AWS_PROFILE", "AWS_ACCESS_KEY_ID", "AWS_REGION",
        "GOOGLE_APPLICATION_CREDENTIALS", "GOOGLE_CLOUD_PROJECT", "GOOGLE_CLOUD_PROJECT_ID",
        "MISTRAL_API_KEY",
        "GROQ_API_KEY",
        "TOGETHER_API_KEY",
        "FIREWORKS_API_KEY",
        "COHERE_API_KEY",
        "DEEPSEEK_API_KEY",
        "XAI_API_KEY",
    ]
    for key in keys:
        monkeypatch.delenv(key, raising=False)


def _make_helm_workspace(tmp_path: Path) -> Path:
    (tmp_path / ".helm").mkdir()
    return tmp_path


def _make_openclaw_workspace(tmp_path: Path) -> Path:
    oc_dir = tmp_path / ".openclaw"
    oc_dir.mkdir()
    # Need state artifact for openclaw detection
    (oc_dir / "task-ledger.jsonl").write_text('{"task_id": "t1"}\n', encoding="utf-8")
    return tmp_path


def _make_hermes_workspace(tmp_path: Path) -> Path:
    (tmp_path / ".hermes").mkdir()
    return tmp_path


# ---------------------------------------------------------------------------
# Runtime fingerprint tests
# ---------------------------------------------------------------------------

def test_detects_helm_workspace_marker(tmp_path: Path) -> None:
    workspace = _make_helm_workspace(tmp_path)
    snapshot = discover_environment(workspace=workspace, timeout_ms=50)

    assert snapshot.runtime.kind == "helm"
    assert snapshot.runtime.confidence >= 0.9


def test_detects_openclaw_marker(tmp_path: Path) -> None:
    workspace = _make_openclaw_workspace(tmp_path)
    snapshot = discover_environment(workspace=workspace, timeout_ms=50)

    assert snapshot.runtime.kind == "openclaw"
    assert snapshot.runtime.confidence >= 0.8


def test_detects_hermes_marker(tmp_path: Path) -> None:
    workspace = _make_hermes_workspace(tmp_path)
    snapshot = discover_environment(workspace=workspace, timeout_ms=50)

    assert snapshot.runtime.kind == "hermes"
    assert snapshot.runtime.confidence >= 0.7


def test_unknown_runtime_falls_back_conservatively(tmp_path: Path) -> None:
    # tmp_path has no markers at all
    snapshot = discover_environment(workspace=tmp_path, timeout_ms=50)

    assert snapshot.runtime.kind == "unknown"
    assert snapshot.runtime.confidence <= 0.2


# ---------------------------------------------------------------------------
# Runtime model state tests
# ---------------------------------------------------------------------------

def test_api_only_runtime_model_state(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _clear_all_provider_env(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

    workspace = _make_helm_workspace(tmp_path)

    import urllib.request, urllib.error
    def _fail_urlopen(url, timeout=None):
        raise urllib.error.URLError("refused")
    monkeypatch.setattr(urllib.request, "urlopen", _fail_urlopen)

    snapshot = discover_environment(workspace=workspace, timeout_ms=50)

    assert snapshot.runtime_model_state.mode == "api_only"
    assert len(snapshot.runtime_model_state.api_candidates) >= 1


def test_provider_unavailable_sets_runtime_degraded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _clear_all_provider_env(monkeypatch)

    workspace = _make_helm_workspace(tmp_path)

    import urllib.request, urllib.error
    def _fail_urlopen(url, timeout=None):
        raise urllib.error.URLError("refused")
    monkeypatch.setattr(urllib.request, "urlopen", _fail_urlopen)

    snapshot = discover_environment(workspace=workspace, timeout_ms=50)

    assert snapshot.runtime_model_state.readiness == "degraded"
    assert snapshot.runtime_model_state.mode == "unavailable"


# ---------------------------------------------------------------------------
# Helm intelligence state tests
# ---------------------------------------------------------------------------

def test_helm_intelligence_defaults_to_deterministic_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _clear_all_provider_env(monkeypatch)

    workspace = _make_helm_workspace(tmp_path)

    import urllib.request, urllib.error
    def _fail_urlopen(url, timeout=None):
        raise urllib.error.URLError("refused")
    monkeypatch.setattr(urllib.request, "urlopen", _fail_urlopen)

    snapshot = discover_environment(workspace=workspace, timeout_ms=50)

    assert snapshot.helm_intelligence_state.cloud_calls_enabled is False
    assert snapshot.helm_intelligence_state.local_model_calls_enabled is False
    assert snapshot.helm_intelligence_state.mode == "deterministic_only"


def test_low_ram_strategy_disables_local_model_calls(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _clear_all_provider_env(monkeypatch)
    workspace = _make_helm_workspace(tmp_path)

    import urllib.request, urllib.error
    def _fail_urlopen(url, timeout=None):
        raise urllib.error.URLError("refused")
    monkeypatch.setattr(urllib.request, "urlopen", _fail_urlopen)

    # Patch hardware detection to return low-RAM profile
    from scripts import discovery as disc_mod
    from scripts.discovery import HardwareProfile
    fake_hw = HardwareProfile(
        os_name="Linux",
        machine="x86_64",
        processor=None,
        is_macos=False,
        is_apple_silicon=False,
        memory_total_gb=8.0,
        low_ram=True,
        python_version="3.11.0",
    )
    monkeypatch.setattr(disc_mod, "_detect_hardware", lambda: fake_hw)

    snapshot = discover_environment(workspace=workspace, timeout_ms=50)

    assert snapshot.helm_intelligence_state.local_model_calls_enabled is False


# ---------------------------------------------------------------------------
# Hardware profile
# ---------------------------------------------------------------------------

def test_hardware_profile_populated(tmp_path: Path) -> None:
    workspace = _make_helm_workspace(tmp_path)
    snapshot = discover_environment(workspace=workspace, timeout_ms=50)

    assert snapshot.hardware.os_name != ""
    assert snapshot.hardware.python_version != ""


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------

def test_snapshot_to_json_serializable(tmp_path: Path) -> None:
    workspace = _make_helm_workspace(tmp_path)
    snapshot = discover_environment(workspace=workspace, timeout_ms=50)

    result = snapshot_to_json(snapshot)
    # Must not raise
    serialized = json.dumps(result)
    assert isinstance(serialized, str)
    assert len(serialized) > 0
