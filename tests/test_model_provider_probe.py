from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

import sys
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.model_provider_probe import (
    probe_api_providers_from_env,
    probe_local_providers,
    probe_all_model_providers,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _clear_all_provider_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove every env var that any provider could detect."""
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


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_detects_anthropic_env_without_reading_value(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_all_provider_env(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret-should-never-appear")

    probes = probe_api_providers_from_env()
    anthropic_probes = [p for p in probes if p.provider == "anthropic"]

    assert len(anthropic_probes) == 1
    probe = anthropic_probes[0]
    assert probe.auth_detected is True
    assert probe.status == "configured"
    assert "ANTHROPIC_API_KEY" in probe.detected_env_names

    # Secret value must never appear in serialized form
    serialized = json.dumps([p.__dict__ for p in probes])
    assert "sk-secret-should-never-appear" not in serialized


def test_detects_gemini_env_without_reading_value(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_all_provider_env(monkeypatch)
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-secret-xyz")

    probes = probe_api_providers_from_env()
    gemini_probes = [p for p in probes if p.provider == "google_gemini"]

    assert len(gemini_probes) == 1
    probe = gemini_probes[0]
    assert probe.auth_detected is True
    assert probe.status == "configured"
    assert "GEMINI_API_KEY" in probe.detected_env_names

    serialized = json.dumps([p.__dict__ for p in probes])
    assert "gemini-secret-xyz" not in serialized


def test_detects_openrouter_env(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_all_provider_env(monkeypatch)
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-secret-abc")

    probes = probe_api_providers_from_env()
    or_probes = [p for p in probes if p.provider == "openrouter"]

    assert len(or_probes) == 1
    probe = or_probes[0]
    assert probe.auth_detected is True
    assert probe.status == "configured"
    assert "OPENROUTER_API_KEY" in probe.detected_env_names

    serialized = json.dumps([p.__dict__ for p in probes])
    assert "or-secret-abc" not in serialized


def test_openai_is_not_required_for_api_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """Anthropic alone is sufficient to be an API candidate."""
    _clear_all_provider_env(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-anything")

    probes = probe_api_providers_from_env()
    configured = [p for p in probes if p.status == "configured"]

    assert len(configured) >= 1
    providers = [p.provider for p in configured]
    assert "anthropic" in providers
    assert "openai" not in providers


def test_multiple_api_providers_are_candidates(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_all_provider_env(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-anything")
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-key")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")

    probes = probe_api_providers_from_env()
    configured = [p for p in probes if p.status == "configured"]

    assert len(configured) >= 3
    providers = {p.provider for p in configured}
    assert "anthropic" in providers
    assert "google_gemini" in providers
    assert "openrouter" in providers


def test_local_ollama_probe_timeout_does_not_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    """Even when ollama times out or is unavailable, probe must not raise."""
    import urllib.request
    import urllib.error

    def _fake_urlopen(url, timeout=None):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)

    # Must not raise
    probes = probe_local_providers(timeout_ms=50)
    ollama_probes = [p for p in probes if p.provider == "ollama"]

    assert len(ollama_probes) == 1
    assert ollama_probes[0].status == "unavailable"
    assert ollama_probes[0].endpoint_detected is False


def test_lm_studio_probe_timeout_does_not_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    import urllib.request
    import urllib.error

    def _fake_urlopen(url, timeout=None):
        raise urllib.error.URLError("timed out")

    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)

    probes = probe_local_providers(timeout_ms=50)
    lm_probes = [p for p in probes if p.provider == "lm_studio"]

    assert len(lm_probes) == 1
    assert lm_probes[0].status == "unavailable"
    assert lm_probes[0].endpoint_detected is False


def test_no_provider_detected_returns_empty_candidates(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_all_provider_env(monkeypatch)

    probes = probe_api_providers_from_env()
    configured = [p for p in probes if p.status == "configured"]

    assert len(configured) == 0


def test_secret_values_are_not_serialized(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_all_provider_env(monkeypatch)
    secret = "ultra-secret-key-1234567890"
    monkeypatch.setenv("ANTHROPIC_API_KEY", secret)
    monkeypatch.setenv("GEMINI_API_KEY", secret + "-gemini")
    monkeypatch.setenv("OPENROUTER_API_KEY", secret + "-or")

    probes = probe_api_providers_from_env()
    serialized = json.dumps([p.__dict__ for p in probes])

    assert secret not in serialized
    assert secret + "-gemini" not in serialized
    assert secret + "-or" not in serialized
    # But the env var names (not values) should appear
    assert "ANTHROPIC_API_KEY" in serialized
