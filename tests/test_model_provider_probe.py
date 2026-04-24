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
from scripts.discovery import _detect_gpu, HardwareProfile


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
        "AWS_PROFILE", "AWS_ACCESS_KEY_ID", "AWS_REGION", "AWS_DEFAULT_REGION",
        "GOOGLE_APPLICATION_CREDENTIALS", "GOOGLE_CLOUD_PROJECT", "GOOGLE_CLOUD_PROJECT_ID",
        "MISTRAL_API_KEY", "GROQ_API_KEY", "TOGETHER_API_KEY",
        "FIREWORKS_API_KEY", "COHERE_API_KEY", "DEEPSEEK_API_KEY", "XAI_API_KEY",
        "REPLICATE_API_TOKEN", "PERPLEXITY_API_KEY",
        "HF_TOKEN", "HUGGINGFACE_TOKEN",
        "CEREBRAS_API_KEY", "NGC_API_KEY", "NVIDIA_API_KEY",
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


def test_empty_string_env_var_not_detected(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_all_provider_env(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    probes = probe_api_providers_from_env()
    configured = [p for p in probes if p.status == "configured"]
    assert len(configured) == 0


def test_aws_region_alone_not_detected(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_all_provider_env(monkeypatch)
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    probes = probe_api_providers_from_env()
    bedrock = [p for p in probes if p.provider == "aws_bedrock"]
    assert len(bedrock) == 0


def test_google_api_key_alone_low_confidence(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_all_provider_env(monkeypatch)
    monkeypatch.setenv("GOOGLE_API_KEY", "some-key")
    probes = probe_api_providers_from_env()
    gemini = [p for p in probes if p.provider == "google_gemini"]
    assert len(gemini) == 1
    assert gemini[0].confidence == "low"


def test_aws_bedrock_requires_access_key(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_all_provider_env(monkeypatch)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIAIOSFODNN7EXAMPLE")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    probes = probe_api_providers_from_env()
    bedrock = [p for p in probes if p.provider == "aws_bedrock"]
    assert len(bedrock) == 1
    assert bedrock[0].confidence == "high"


def test_local_probe_invalid_json_body_is_unverified(monkeypatch: pytest.MonkeyPatch) -> None:
    import urllib.request

    class FakeResponse:
        status = 200
        def read(self): return b"not json"
        def __enter__(self): return self
        def __exit__(self, *a): pass

    monkeypatch.setattr(urllib.request, "urlopen", lambda url, timeout=None: FakeResponse())
    probes = probe_local_providers(timeout_ms=50)
    ollama = [p for p in probes if p.provider == "ollama"][0]
    assert ollama.status == "port_open_unverified"


def test_local_probe_valid_ollama_response(monkeypatch: pytest.MonkeyPatch) -> None:
    import urllib.request

    class FakeResponse:
        status = 200
        def read(self): return json.dumps({"models": [{"name": "llama3"}]}).encode()
        def __enter__(self): return self
        def __exit__(self, *a): pass

    monkeypatch.setattr(urllib.request, "urlopen", lambda url, timeout=None: FakeResponse())
    probes = probe_local_providers(timeout_ms=50)
    ollama = [p for p in probes if p.provider == "ollama"][0]
    assert ollama.status == "available"


def test_local_probe_valid_openai_compat(monkeypatch: pytest.MonkeyPatch) -> None:
    import urllib.request

    class FakeResponse:
        status = 200
        def read(self): return json.dumps({"data": [{"id": "m1"}]}).encode()
        def __enter__(self): return self
        def __exit__(self, *a): pass

    monkeypatch.setattr(urllib.request, "urlopen", lambda url, timeout=None: FakeResponse())
    probes = probe_local_providers(timeout_ms=50)
    lm = [p for p in probes if p.provider == "lm_studio"][0]
    assert lm.status == "available"


def test_policy_json_loading_custom_provider(tmp_path: Path) -> None:
    from scripts.model_provider_probe import _load_provider_registry
    policy = tmp_path / "custom.json"
    policy.write_text(json.dumps({
        "version": 1,
        "api_provider_env_registry": {"custom_provider": {"required": ["CUSTOM_KEY"]}},
        "local_endpoint_registry": {}
    }), encoding="utf-8")
    api_reg, local_reg = _load_provider_registry(policy)
    assert "custom_provider" in api_reg


def test_policy_json_fallback_on_missing() -> None:
    from scripts.model_provider_probe import _load_provider_registry
    api_reg, local_reg = _load_provider_registry(Path("/nonexistent/path.json"))
    assert "openai" in api_reg


def test_gpu_detection_nvidia_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess

    class FakeResult:
        returncode = 0
        stdout = "GPU 0: NVIDIA RTX 4090 (UUID: ...)\n"

    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: FakeResult())
    detected, name, vram = _detect_gpu()
    assert detected is True
    assert "NVIDIA" in (name or "")


def test_gpu_detection_no_nvidia_smi(monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess

    def _fail(*a, **kw):
        raise FileNotFoundError("nvidia-smi not found")

    monkeypatch.setattr(subprocess, "run", _fail)
    detected, name, vram = _detect_gpu()
    # On non-Apple-Silicon, should be False
    if not (sys.platform == "darwin"):
        assert detected is False


def test_skip_discovery_flag_skips_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    """--skip-discovery should result in no discovery section."""
    import helm
    parser = helm.build_parser()
    args = parser.parse_args(["doctor", "--skip-discovery"])
    assert args.skip_discovery is True
