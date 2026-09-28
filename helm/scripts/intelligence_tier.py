from __future__ import annotations

_LOCAL_PROVIDERS = {"ollama", "lm studio", "lmstudio", "llama.cpp", "llamacpp", "vllm"}
_CLOUD_PROVIDERS = {
    "openai",
    "anthropic",
    "azure",
    "cohere",
    "mistral",
    "groq",
    "together",
    "fireworks",
    "google_gemini",
    "openrouter",
}


def _provider_name(provider: dict) -> str:
    return str(provider.get("provider") or provider.get("name") or provider.get("type") or "").lower()


def _provider_kind(provider: dict) -> str:
    return str(provider.get("kind") or provider.get("location") or "").lower()


def _providers(discovery_snapshot: dict) -> list[dict]:
    runtime_state = discovery_snapshot.get("runtime_model_state")
    if isinstance(runtime_state, dict):
        providers: list[dict] = []
        for key in ("local_candidates", "api_candidates"):
            values = runtime_state.get(key)
            if isinstance(values, list):
                providers.extend(item for item in values if isinstance(item, dict))
        if providers:
            return providers
    if "providers" in discovery_snapshot and isinstance(discovery_snapshot["providers"], list):
        return [item for item in discovery_snapshot["providers"] if isinstance(item, dict)]
    combined: list[dict] = []
    for key in ("local", "cloud", "api"):
        values = discovery_snapshot.get(key)
        if isinstance(values, list):
            combined.extend(item for item in values if isinstance(item, dict))
    return combined


def _has_local(discovery_snapshot: dict) -> bool:
    for provider in _providers(discovery_snapshot):
        name = _provider_name(provider)
        if any(local in name for local in _LOCAL_PROVIDERS):
            return True
        if _provider_kind(provider) == "local":
            return True
    return False


def _has_cloud(discovery_snapshot: dict) -> bool:
    for provider in _providers(discovery_snapshot):
        name = _provider_name(provider)
        if any(cloud in name for cloud in _CLOUD_PROVIDERS):
            return True
        if _provider_kind(provider) in {"cloud", "api"}:
            return True
    return False


class IntelligenceTier:
    def __init__(self, *, discovery_snapshot: dict) -> None:
        self.discovery_snapshot = discovery_snapshot

    def mode(self) -> str:
        if _has_local(self.discovery_snapshot):
            return "local_model_available"
        if _has_cloud(self.discovery_snapshot):
            return "cloud_available"
        return "deterministic_only"

    def cloud_calls_enabled(self) -> bool:
        return _has_cloud(self.discovery_snapshot)

    def local_model_calls_enabled(self) -> bool:
        return _has_local(self.discovery_snapshot)

    def available_tiers(self) -> tuple[str, ...]:
        tiers = ["L0_static_safety", "L1_deterministic_scoring"]
        if _has_local(self.discovery_snapshot):
            tiers.append("L3_local_model")
        if _has_cloud(self.discovery_snapshot):
            tiers.append("L4_cloud_provider")
        return tuple(tiers)
