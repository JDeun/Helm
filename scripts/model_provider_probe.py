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
    "fireworks", "cohere", "deepseek", "xai", "replicate", "perplexity",
    "huggingface", "cerebras", "nvidia_nim",
    "ollama", "lm_studio", "llama_cpp", "vllm", "openai_compatible", "unknown",
]
ProviderLocation = Literal["api", "local", "runtime_config", "unknown"]
ProviderRole = Literal["runtime_llm", "helm_intelligence", "both", "unknown"]
ProviderStatus = Literal["available", "configured", "unavailable", "port_open_unverified", "unknown"]


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
    detected_env_names: tuple[str, ...]
    priority: int | None
    is_primary_candidate: bool
    is_fallback_candidate: bool
    detail: str | None = None
    confidence: str = "high"  # "high" or "low"


# ---------------------------------------------------------------------------
# Policy registry (inline to avoid circular deps; mirrors policy JSON)
# ---------------------------------------------------------------------------

_API_PROVIDER_ENV_REGISTRY: dict[str, dict[str, list[str]]] = {
    "openai": {"required": ["OPENAI_API_KEY"], "optional": ["OPENAI_BASE_URL", "OPENAI_API_BASE"]},
    "anthropic": {"required": ["ANTHROPIC_API_KEY"], "optional": ["CLAUDE_API_KEY"]},
    "google_gemini": {"required": ["GEMINI_API_KEY"], "weak": ["GOOGLE_API_KEY"]},
    "openrouter": {"required": ["OPENROUTER_API_KEY"]},
    "azure_openai": {"required": ["AZURE_OPENAI_API_KEY"], "optional": ["AZURE_OPENAI_ENDPOINT"]},
    "aws_bedrock": {"required": ["AWS_ACCESS_KEY_ID"], "optional": ["AWS_REGION", "AWS_DEFAULT_REGION", "AWS_PROFILE"]},
    "google_vertex": {"required": ["GOOGLE_APPLICATION_CREDENTIALS", "GOOGLE_CLOUD_PROJECT"], "optional": ["GOOGLE_CLOUD_PROJECT_ID"]},
    "mistral": {"required": ["MISTRAL_API_KEY"]},
    "groq": {"required": ["GROQ_API_KEY"]},
    "together": {"required": ["TOGETHER_API_KEY"]},
    "fireworks": {"required": ["FIREWORKS_API_KEY"]},
    "cohere": {"required": ["COHERE_API_KEY"]},
    "deepseek": {"required": ["DEEPSEEK_API_KEY"]},
    "xai": {"required": ["XAI_API_KEY"]},
    "replicate": {"required": ["REPLICATE_API_TOKEN"]},
    "perplexity": {"required": ["PERPLEXITY_API_KEY"]},
    "huggingface": {"required": ["HF_TOKEN"], "weak": ["HUGGINGFACE_TOKEN"]},
    "cerebras": {"required": ["CEREBRAS_API_KEY"]},
    "nvidia_nim": {"required": ["NGC_API_KEY"], "weak": ["NVIDIA_API_KEY"]},
}

_LOCAL_PROVIDER_ENDPOINTS: dict[str, str] = {
    "ollama": "http://localhost:11434/api/tags",
    "lm_studio": "http://localhost:1234/v1/models",
    "llama_cpp": "http://localhost:8080/v1/models",
    "vllm": "http://localhost:8000/v1/models",
}

# Priority order: lower number = higher priority
_API_PRIORITY: dict[str, int] = {
    "anthropic": 1, "openai": 2, "google_gemini": 3, "openrouter": 4,
    "azure_openai": 5, "aws_bedrock": 6, "google_vertex": 7, "mistral": 8,
    "groq": 9, "together": 10, "fireworks": 11, "cohere": 12,
    "deepseek": 13, "xai": 14, "replicate": 15, "perplexity": 16,
    "huggingface": 17, "cerebras": 18, "nvidia_nim": 19,
}

_LOCAL_PRIORITY: dict[str, int] = {
    "ollama": 1,
    "lm_studio": 2,
    "llama_cpp": 3,
    "vllm": 4,
}

_BUILTIN_API_REGISTRY = _API_PROVIDER_ENV_REGISTRY
_BUILTIN_LOCAL_REGISTRY = _LOCAL_PROVIDER_ENDPOINTS


def _load_provider_registry(policy_path: Path | None = None) -> tuple[dict, dict]:
    if policy_path and policy_path.exists():
        try:
            data = json.loads(policy_path.read_text(encoding="utf-8"))
            api_reg = data.get("api_provider_env_registry", {})
            local_reg = data.get("local_endpoint_registry", {})
            if api_reg:
                return api_reg, local_reg or _BUILTIN_LOCAL_REGISTRY
        except Exception:
            pass
    return _BUILTIN_API_REGISTRY, _BUILTIN_LOCAL_REGISTRY


def probe_api_providers_from_env(
    policy_path: Path | None = None,
) -> list[ProviderProbe]:
    """Detect API providers by env var PRESENCE only.

    Never reads secret values. Never calls APIs.
    If policy_path is given, load custom registry from it.
    """
    api_registry, _ = _load_provider_registry(policy_path)
    results: list[ProviderProbe] = []

    for provider, key_spec in api_registry.items():
        required_keys = key_spec.get("required", [])
        optional_keys = key_spec.get("optional", [])
        weak_keys = key_spec.get("weak", [])

        required_present = [k for k in required_keys if os.environ.get(k)]  # truthy check
        optional_present = [k for k in optional_keys if os.environ.get(k)]
        weak_present = [k for k in weak_keys if os.environ.get(k)]

        all_detected = required_present + optional_present + weak_present
        all_required_met = len(required_present) == len(required_keys)
        has_weak_only = not all_required_met and len(weak_present) > 0

        if not all_required_met and not has_weak_only:
            continue

        confidence = "high" if all_required_met else "low"
        priority = _API_PRIORITY.get(provider)

        results.append(ProviderProbe(
            provider=provider,
            kind=provider,  # type: ignore[arg-type]
            location="api",
            role="runtime_llm",
            status="configured",
            source="env",
            auth_detected=all_required_met,
            endpoint_detected=False,
            detected_env_names=tuple(all_detected),  # VAR NAMES only, never values
            priority=priority,
            is_primary_candidate=(all_required_met and priority == 1),
            is_fallback_candidate=(all_required_met and priority is not None and priority > 1),
            detail=None,
            confidence=confidence,
        ))

    results.sort(key=lambda p: (p.priority or 999))
    return results


def _validate_probe_response(provider: str, body: bytes) -> bool:
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return False
    if provider == "ollama":
        return isinstance(data.get("models"), list)
    return isinstance(data.get("data"), list)


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
                    body = resp.read(65536)  # cap at 64 KiB to prevent OOM on misbehaving endpoints
                    if _validate_probe_response(provider, body):
                        endpoint_detected = True
                        status = "available"
                    else:
                        endpoint_detected = True
                        status = "port_open_unverified"
                        detail = "port open but response body invalid"
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
                detected_env_names=(),
                priority=priority,
                is_primary_candidate=(status == "available" and priority == 1),
                is_fallback_candidate=(status == "available" and priority is not None and priority > 1),
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
                detected_env_names=(),
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
    policy_path: Path | None = None,
) -> list[ProviderProbe]:
    """Merge all probe results."""
    api_probes = probe_api_providers_from_env(policy_path=policy_path)
    local_probes = probe_local_providers(timeout_ms=timeout_ms)
    config_probes = probe_runtime_config_providers(workspace)
    return api_probes + local_probes + config_probes
