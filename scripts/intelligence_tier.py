"""Intelligence tier resolution from a discovery snapshot.

Tier definitions:
  L0: Static safety rules (command_guard.py)
  L1: Deterministic scoring (route_contract_lib.py)
  L2: Optional BM25 / lexical retrieval (future)
  L3: Local model / local classifier (Ollama, LM Studio, llama.cpp, vLLM)
  L4: Cloud provider (OpenAI, Anthropic, etc.)

Resolution logic:
  - mode() inspects the discovery_snapshot for provider entries.
    If any local model providers are present it returns "local_model_available".
    If any cloud API providers are present it returns "cloud_available".
    If both are present it returns "local_model_available" (local takes priority label).
    With no snapshot data it returns "deterministic_only".
  - local_model_calls_enabled() is True when the snapshot lists at least one
    recognised local inference provider (Ollama, LM Studio, llama.cpp, vLLM).
  - cloud_calls_enabled() is True when the snapshot lists at least one API
    (cloud) provider.
  - available_tiers() returns the L0/L1 baseline tiers plus L3 and/or L4
    depending on what the snapshot reports.
"""
from __future__ import annotations

_LOCAL_PROVIDERS = {"ollama", "lm studio", "lmstudio", "llama.cpp", "llamacpp", "vllm"}
_CLOUD_PROVIDERS = {"openai", "anthropic", "azure", "cohere", "mistral", "groq", "together", "fireworks"}


def _providers(discovery_snapshot: dict) -> list[dict]:
    """Return the flat list of provider entries from the snapshot."""
    # Accept both {"providers": [...]} and {"local": [...], "cloud": [...]} shapes.
    if "providers" in discovery_snapshot:
        raw = discovery_snapshot["providers"]
        if isinstance(raw, list):
            return raw
    # Flatten local + cloud sub-lists if present.
    combined: list[dict] = []
    for key in ("local", "cloud", "api"):
        sub = discovery_snapshot.get(key)
        if isinstance(sub, list):
            combined.extend(sub)
    return combined


def _has_local(discovery_snapshot: dict) -> bool:
    for provider in _providers(discovery_snapshot):
        name = str(provider.get("name") or provider.get("type") or "").lower()
        if any(local in name for local in _LOCAL_PROVIDERS):
            return True
        if str(provider.get("kind") or "").lower() == "local":
            return True
    return False


def _has_cloud(discovery_snapshot: dict) -> bool:
    for provider in _providers(discovery_snapshot):
        name = str(provider.get("name") or provider.get("type") or "").lower()
        if any(cloud in name for cloud in _CLOUD_PROVIDERS):
            return True
        if str(provider.get("kind") or "").lower() in {"cloud", "api"}:
            return True
    return False


class IntelligenceTier:
    """Resolve the active intelligence tier from a discovery snapshot.

    Parameters
    ----------
    discovery_snapshot:
        A dict produced by the model-provider discovery layer.  Accepted shapes:
        ``{"providers": [{"name": "ollama", ...}, ...]}`` or
        ``{"local": [...], "cloud": [...]}`` or an empty dict for the
        deterministic-only baseline.
    """

    def __init__(self, *, discovery_snapshot: dict) -> None:
        self.discovery_snapshot = discovery_snapshot

    def mode(self) -> str:
        """Return the highest-capability mode indicated by the snapshot.

        Returns one of:
          "deterministic_only"      - no model providers found
          "local_model_available"   - at least one local inference provider
          "cloud_available"         - at least one cloud/API provider (no local)
        """
        if _has_local(self.discovery_snapshot):
            return "local_model_available"
        if _has_cloud(self.discovery_snapshot):
            return "cloud_available"
        return "deterministic_only"

    def cloud_calls_enabled(self) -> bool:
        """Return True if the snapshot shows at least one cloud/API provider."""
        return _has_cloud(self.discovery_snapshot)

    def local_model_calls_enabled(self) -> bool:
        """Return True if the snapshot shows at least one local inference provider."""
        return _has_local(self.discovery_snapshot)

    def available_tiers(self) -> list[str]:
        """Return the list of active intelligence tiers based on the snapshot."""
        tiers = ["L0_static_safety", "L1_deterministic_scoring"]
        if _has_local(self.discovery_snapshot):
            tiers.append("L3_local_model")
        if _has_cloud(self.discovery_snapshot):
            tiers.append("L4_cloud_provider")
        return tiers
