"""Provider-agnostic model provider probe.

CRITICAL SECURITY RULES:
- API env var values are NEVER stored, logged, or serialized.
- Only os.environ.get(key) is not None is checked (presence only).
- detected_env_names stores ENV VAR NAMES only, never values.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

ProviderKind = Literal[
    "openai", "anthropic", "google_gemini", "openrouter", "azure_openai",
    "aws_bedrock", "google_vertex", "mistral", "groq", "together",
    "fireworks", "cohere", "deepseek", "xai", "ollama", "lm_studio",
    "llama_cpp", "vllm", "openai_compatible", "unknown",
]
ProviderLocation = Literal["api", "local", "runtime_config", "unknown"]
ProviderRole = Literal["runtime_llm", "helm_intelligence", "both", "unknown"]
ProviderStatus = Literal["available", "configured", "unavailable", "unknown"]


@dataclass(frozen=True)
class ProviderProbe:
    provider: str
    kind: ProviderKind
    location: ProviderLocation
    role: ProviderRole
    status: ProviderStatus
    source: str
    auth_detected: bool
    endpoint_detected: bool
    detected_env_names: list[str]
    priority: int | None
    is_primary_candidate: bool
    is_fallback_candidate: bool
    detail: str | None = None


# ---------------------------------------------------------------------------
# Policy registry (inline to avoid circular deps; mirrors policy JSON)
# ---------------------------------------------------------------------------

_API_PROVIDER_ENV_REGISTRY: dict[str, list[str]] = {
    "openai": ["OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_API_BASE"],
    "anthropic": ["ANTHROPIC_API_KEY", "CLAUDE_API_KEY"],
    "google_gemini": ["GEMINI_API_KEY", "GOOGLE_API_KEY"],
    "openrouter": ["OPENROUTER_API_KEY"],
    "azure_openai": ["AZURE_OPENAI_API_KEY", "AZURE_OPENAI_ENDPOINT"],
    "aws_bedrock": ["AWS_PROFILE", "AWS_ACCESS_KEY_ID", "AWS_REGION"],
    "google_vertex": [
        "GOOGLE_APPLICATION_CREDENTIALS",
        "GOOGLE_CLOUD_PROJECT",
        "GOOGLE_CLOUD_PROJECT_ID",
    ],
    "mistral": ["MISTRAL_API_KEY"],
    "groq": ["GROQ_API_KEY"],
    "together": ["TOGETHER_API_KEY"],
    "fireworks": ["FIREWORKS_API_KEY"],
    "cohere": ["COHERE_API_KEY"],
    "deepseek": ["DEEPSEEK_API_KEY"],
    "xai": ["XAI_API_KEY"],
}

_LOCAL_PROVIDER_ENDPOINTS: dict[str, str] = {
    "ollama": "http://localhost:11434/api/tags",
    "lm_studio": "http://localhost:1234/v1/models",
    "llama_cpp": "http://localhost:8080/v1/models",
    "vllm": "http://localhost:8000/v1/models",
}

# Priority order: lower number = higher priority
_API_PRIORITY: dict[str, int] = {
    "anthropic": 1,
    "openai": 2,
    "google_gemini": 3,
    "openrouter": 4,
    "azure_openai": 5,
    "aws_bedrock": 6,
    "google_vertex": 7,
    "mistral": 8,
    "groq": 9,
    "together": 10,
    "fireworks": 11,
    "cohere": 12,
    "deepseek": 13,
    "xai": 14,
}

_LOCAL_PRIORITY: dict[str, int] = {
    "ollama": 1,
    "lm_studio": 2,
    "llama_cpp": 3,
    "vllm": 4,
}


def probe_api_providers_from_env() -> list[ProviderProbe]:
    """Detect API providers by env var PRESENCE only.

    Never reads secret values. Never calls APIs.
    """
    results: list[ProviderProbe] = []

    for provider, env_keys in _API_PROVIDER_ENV_REGISTRY.items():
        # Only check presence — never store the value
        detected_names: list[str] = [k for k in env_keys if os.environ.get(k) is not None]
        auth_detected = len(detected_names) > 0

        if not auth_detected:
            continue

        priority = _API_PRIORITY.get(provider)
        results.append(
            ProviderProbe(
                provider=provider,
                kind=provider,  # type: ignore[arg-type]
                location="api",
                role="runtime_llm",
                status="configured",  # Env present but not called → configured, not available
                source="env",
                auth_detected=True,
                endpoint_detected=False,
                detected_env_names=detected_names,  # VAR NAMES only, never values
                priority=priority,
                is_primary_candidate=(priority == 1),
                is_fallback_candidate=(priority is not None and priority > 1),
                detail=None,
            )
        )

    results.sort(key=lambda p: (p.priority or 999))
    return results


def probe_local_providers(timeout_ms: int = 200) -> list[ProviderProbe]:
    """Probe local endpoints with short timeouts.

    Uses urllib.request only. Never raises on failure.
    """
    timeout_sec = timeout_ms / 1000.0
    results: list[ProviderProbe] = []

    for provider, endpoint in _LOCAL_PROVIDER_ENDPOINTS.items():
        priority = _LOCAL_PRIORITY.get(provider)
        endpoint_detected = False
        status: ProviderStatus = "unavailable"
        detail: str | None = None

        try:
            with urllib.request.urlopen(endpoint, timeout=timeout_sec) as resp:  # noqa: S310
                if resp.status < 400:
                    endpoint_detected = True
                    status = "available"
        except urllib.error.URLError as exc:
            detail = str(exc.reason) if exc.reason else str(exc)
        except OSError as exc:
            detail = str(exc)
        except Exception as exc:  # noqa: BLE001
            detail = f"unexpected: {type(exc).__name__}"

        results.append(
            ProviderProbe(
                provider=provider,
                kind=provider,  # type: ignore[arg-type]
                location="local",
                role="runtime_llm",
                status=status,
                source="endpoint_probe",
                auth_detected=False,
                endpoint_detected=endpoint_detected,
                detected_env_names=[],
                priority=priority,
                is_primary_candidate=(endpoint_detected and priority == 1),
                is_fallback_candidate=(endpoint_detected and priority is not None and priority > 1),
                detail=detail,
            )
        )

    return results


def probe_runtime_config_providers(workspace: Path) -> list[ProviderProbe]:
    """Best-effort scan of runtime config files.

    Must not mutate configs or read secrets.
    """
    results: list[ProviderProbe] = []

    # Look for common runtime config indicators (no secrets read)
    config_candidates = [
        workspace / ".helm" / "runtime_config.json",
        workspace / "runtime_config.json",
    ]

    for config_path in config_candidates:
        if not config_path.exists():
            continue
        try:
            raw = config_path.read_text(encoding="utf-8")
            data = json.loads(raw)
        except (OSError, json.JSONDecodeError):
            continue

        provider = data.get("provider")
        if not isinstance(provider, str) or not provider:
            continue

        results.append(
            ProviderProbe(
                provider=provider,
                kind="openai_compatible",
                location="runtime_config",
                role="runtime_llm",
                status="configured",
                source="runtime_config",
                auth_detected=False,
                endpoint_detected=False,
                detected_env_names=[],
                priority=None,
                is_primary_candidate=False,
                is_fallback_candidate=False,
                detail=f"from {config_path.name}",
            )
        )

    return results


def probe_all_model_providers(
    workspace: Path,
    timeout_ms: int = 500,
) -> list[ProviderProbe]:
    """Merge all probe results."""
    api_probes = probe_api_providers_from_env()
    local_probes = probe_local_providers(timeout_ms=timeout_ms)
    config_probes = probe_runtime_config_providers(workspace)
    return api_probes + local_probes + config_probes
