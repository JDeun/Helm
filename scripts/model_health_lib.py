#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from helm_workspace import get_workspace_layout
from scripts.discovery import discover_environment, snapshot_to_json
from scripts.model_provider_probe import probe_api_providers_from_env, probe_local_providers

WORKSPACE = get_workspace_layout().root
STATE_ROOT = get_workspace_layout().state_root
POLICY_FILE = WORKSPACE / "references" / "model_recovery_policy.json"

_DEFAULT_MODEL_REGISTRY: dict[str, dict[str, Any]] = {
    "openai": {"ref": "openai/gpt-4.1-mini", "probe": {"kind": "openai_chat_completion", "model": "gpt-4.1-mini"}},
    "google_gemini": {
        "ref": "google_gemini/gemini-2.5-flash",
        "probe": {"kind": "google_generate_content", "model": "gemini-2.5-flash"},
    },
    "ollama": {"ref": "ollama/llama3.2:latest", "probe": {"kind": "ollama_generate", "model": "llama3.2:latest"}},
}
_OPENAI_COMPATIBLE_PROVIDERS = {
    "openrouter",
    "groq",
    "together",
    "fireworks",
    "deepseek",
    "xai",
    "mistral",
    "perplexity",
    "cerebras",
    "nvidia_nim",
}


@dataclass(frozen=True)
class ModelHealthChoice:
    model: str | None
    reason: str
    source: str


@dataclass(frozen=True)
class ProbeOutcome:
    ok: bool
    detail: str
    status: str | None = None
    auth_status: str | None = None
    generation_status: str | None = None


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_iso(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def load_json(path: Path, default: object) -> object:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"warning: ignoring malformed JSON file {path}: {exc}", file=sys.stderr)
        return default


def load_policy(path: Path = POLICY_FILE) -> dict[str, Any]:
    payload = load_json(path, {})
    return payload if isinstance(payload, dict) else {}


def state_path(policy: dict[str, Any], workspace: Path | None = None) -> Path:
    workspace = workspace or WORKSPACE
    state_root = get_workspace_layout().state_root if workspace == WORKSPACE else workspace / ".helm"
    configured = str(policy.get("state_path") or str(state_root / "model-health-state.json"))
    path = Path(configured)
    return path if path.is_absolute() else workspace / path


def load_state(policy: dict[str, Any] | None = None, workspace: Path | None = None) -> dict[str, Any]:
    policy = policy or load_policy()
    payload = load_json(state_path(policy, workspace=workspace), {"version": 1, "models": {}})
    if not isinstance(payload, dict):
        return {"version": 1, "models": {}}
    payload.setdefault("version", 1)
    payload.setdefault("models", {})
    return payload


def save_state(state: dict[str, Any], policy: dict[str, Any] | None = None, workspace: Path | None = None) -> Path:
    policy = policy or load_policy()
    path = state_path(policy, workspace=workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = utc_now_iso()
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f"{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        handle.write(json.dumps(state, indent=2, ensure_ascii=False) + "\n")
        tmp = Path(handle.name)
    tmp.replace(path)
    return path


def _default_entry_for_provider(provider: str, priority: int) -> dict[str, Any] | None:
    provider_key = provider.casefold()
    if provider_key in _DEFAULT_MODEL_REGISTRY:
        template = _DEFAULT_MODEL_REGISTRY[provider_key]
        return {
            "ref": template["ref"],
            "provider": provider_key,
            "priority": priority,
            "probe": dict(template["probe"]),
        }
    if provider_key in _OPENAI_COMPATIBLE_PROVIDERS:
        return {
            "ref": f"{provider_key}/{provider_key}-default",
            "provider": provider_key,
            "priority": priority,
            "probe": {"kind": "openai_compatible_chat_completion", "model": f"{provider_key}-default"},
        }
    return None


def policy_models(policy: dict[str, Any]) -> list[dict[str, Any]]:
    models = policy.get("models")
    if isinstance(models, list):
        valid = [item for item in models if isinstance(item, dict) and item.get("ref")]
        if valid:
            return sorted(valid, key=lambda item: int(item.get("priority", 9999)))

    derived: list[dict[str, Any]] = []
    priority = 10
    for probe in probe_local_providers(timeout_ms=100):
        if probe.status == "available":
            entry = _default_entry_for_provider(probe.provider, priority)
            if entry:
                derived.append(entry)
                priority += 10
    for probe in probe_api_providers_from_env():
        entry = _default_entry_for_provider(probe.provider, priority)
        if entry and entry["ref"] not in {item["ref"] for item in derived}:
            derived.append(entry)
            priority += 10
    return derived


def model_entry(policy: dict[str, Any], model: str) -> dict[str, Any] | None:
    for entry in policy_models(policy):
        if entry.get("ref") == model:
            return entry
    return None


def higher_priority_models(policy: dict[str, Any], current_model: str | None) -> list[str]:
    models = policy_models(policy)
    if not current_model:
        return [str(item["ref"]) for item in models]
    current = model_entry(policy, current_model)
    if not current:
        return [str(item["ref"]) for item in models]
    priority = int(current.get("priority", 9999))
    return [str(item["ref"]) for item in models if int(item.get("priority", 9999)) < priority]


def is_fresh(entry: dict[str, Any], *, now: float, ttl_seconds: int) -> bool:
    checked_at = parse_iso(str(entry.get("checked_at") or ""))
    return checked_at is not None and now - checked_at <= ttl_seconds


def provider_aliases(provider: str | None) -> set[str]:
    normalized = str(provider or "").strip().casefold()
    if not normalized:
        return set()
    aliases = {normalized}
    if normalized in _OPENAI_COMPATIBLE_PROVIDERS:
        aliases.add("openai_compatible")
    return aliases


def discovery_available_providers(snapshot_payload: dict[str, Any]) -> set[str]:
    state = snapshot_payload.get("runtime_model_state") if isinstance(snapshot_payload, dict) else {}
    if not isinstance(state, dict):
        return set()
    available: set[str] = set()
    for key in ("api_candidates", "local_candidates"):
        candidates = state.get(key)
        if not isinstance(candidates, list):
            continue
        for item in candidates:
            if not isinstance(item, dict):
                continue
            provider = str(item.get("provider") or "").strip()
            if provider:
                available.add(provider.casefold())
    return available


def choose_model_from_discovery(policy: dict[str, Any] | None = None, workspace: Path | None = None) -> ModelHealthChoice | None:
    policy = policy or load_policy()
    workspace = workspace or WORKSPACE
    try:
        snapshot = snapshot_to_json(discover_environment(workspace=workspace, timeout_ms=200))
    except Exception:
        return None
    providers = discovery_available_providers(snapshot)
    if not providers:
        return None
    for item in policy_models(policy):
        ref = str(item.get("ref") or "")
        provider = str(item.get("provider") or ref.split("/", 1)[0] if "/" in ref else "")
        if provider_aliases(provider).intersection(providers):
            return ModelHealthChoice(ref, f"runtime discovery detected available provider `{provider}`", "runtime-discovery")
    return None


def select_model(policy: dict[str, Any] | None = None, state: dict[str, Any] | None = None, workspace: Path | None = None) -> ModelHealthChoice:
    policy = policy or load_policy()
    workspace = workspace or WORKSPACE
    state = state or load_state(policy, workspace=workspace)
    models = state.get("models") if isinstance(state.get("models"), dict) else {}
    ordered = policy_models(policy)
    primary = str(ordered[0]["ref"]) if ordered else None
    now = time.time()
    ttl = int(policy.get("fresh_after_seconds", 300))
    for item in ordered:
        ref = str(item["ref"])
        health = models.get(ref, {})
        if isinstance(health, dict) and health.get("status") == "healthy" and is_fresh(health, now=now, ttl_seconds=ttl):
            return ModelHealthChoice(ref, "highest fresh healthy model", "model-health-state")
    stale_ttl = int(policy.get("fallback_stale_after_seconds", 1800))
    degraded_seen = False
    for item in ordered:
        ref = str(item["ref"])
        health = models.get(ref, {})
        if not isinstance(health, dict):
            continue
        if health.get("status") in {"down", "degraded"} and is_fresh(health, now=now, ttl_seconds=ttl):
            degraded_seen = True
            continue
        if degraded_seen and health.get("last_ok_at") and is_fresh({"checked_at": health.get("last_ok_at")}, now=now, ttl_seconds=stale_ttl):
            return ModelHealthChoice(ref, "higher-priority model is degraded; using recently healthy fallback", "model-health-state")
    discovery_choice = choose_model_from_discovery(policy, workspace=workspace)
    if discovery_choice is not None:
        return discovery_choice
    return ModelHealthChoice(primary, "no fresh health state; use primary and let runtime fallback handle failures", "default")


def _env(*keys: str) -> str | None:
    for key in keys:
        value = os.environ.get(key)
        if value:
            return value
    return None


def http_error_detail(exc: urllib.error.HTTPError) -> str:
    try:
        body = exc.read().decode("utf-8")
    except Exception:
        body = ""
    if not body:
        return f"HTTP Error {exc.code}: {exc.reason}"
    return f"HTTP Error {exc.code}: {body[:500]}"


def probe_google_generate_content(model: str, probe: dict[str, Any]) -> ProbeOutcome:
    key = _env("GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_GENERATIVE_AI_API_KEY")
    if not key:
        return ProbeOutcome(False, "missing Gemini API key", status="down", auth_status="missing")
    api_model = str(probe.get("model") or model.split("/", 1)[1] if "/" in model else model)
    prompt = str(probe.get("prompt") or "Reply with exactly: ok")
    timeout = int(probe.get("timeout_seconds", 45))
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0, "candidateCount": 1},
    }
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{urllib.parse.quote(api_model, safe='')}:generateContent?key={urllib.parse.quote(key, safe='')}"
    )
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return ProbeOutcome(False, http_error_detail(exc), status="down", auth_status="failed")
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        return ProbeOutcome(False, str(exc), status="down", generation_status="failed")
    candidates = payload.get("candidates") if isinstance(payload, dict) else None
    if not isinstance(candidates, list) or not candidates:
        return ProbeOutcome(False, "empty candidates", status="down", generation_status="empty")
    return ProbeOutcome(True, "generateContent succeeded", status="healthy", auth_status="healthy", generation_status="healthy")


def _openai_like_request(url: str, key: str, body: dict[str, Any], timeout: int) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def probe_openai_chat_completion(model: str, probe: dict[str, Any]) -> ProbeOutcome:
    key = _env("OPENAI_API_KEY")
    if not key:
        return ProbeOutcome(False, "missing OPENAI_API_KEY", status="down", auth_status="missing")
    api_model = str(probe.get("model") or model.split("/", 1)[1] if "/" in model else model)
    base_url = (_env("OPENAI_BASE_URL", "OPENAI_API_BASE") or "https://api.openai.com/v1").rstrip("/")
    timeout = int(probe.get("timeout_seconds", 45))
    prompt = str(probe.get("prompt") or "Reply with exactly: ok")
    body = {"model": api_model, "messages": [{"role": "user", "content": prompt}], "temperature": 0}
    try:
        payload = _openai_like_request(f"{base_url}/chat/completions", key, body, timeout)
    except urllib.error.HTTPError as exc:
        detail = http_error_detail(exc)
        status = "degraded" if exc.code == 429 else "down"
        generation = "rate_limit" if exc.code == 429 else "failed"
        return ProbeOutcome(False, detail, status=status, auth_status="healthy" if exc.code == 429 else "failed", generation_status=generation)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        return ProbeOutcome(False, str(exc), status="down", generation_status="failed")
    choices = payload.get("choices") if isinstance(payload, dict) else None
    if not isinstance(choices, list) or not choices:
        return ProbeOutcome(False, "chat completion returned no choices", status="down", auth_status="healthy", generation_status="empty")
    return ProbeOutcome(True, "chat completion succeeded", status="healthy", auth_status="healthy", generation_status="healthy")


def _compatible_provider_config(provider: str, probe: dict[str, Any]) -> tuple[str | None, str | None]:
    env_key = str(probe.get("api_key_env") or "")
    env_base = str(probe.get("base_url_env") or "")
    if env_key and env_base:
        return _env(env_key), _env(env_base)
    provider = provider.casefold()
    if provider == "openrouter":
        return _env("OPENROUTER_API_KEY"), "https://openrouter.ai/api/v1"
    if provider == "groq":
        return _env("GROQ_API_KEY"), "https://api.groq.com/openai/v1"
    if provider == "together":
        return _env("TOGETHER_API_KEY"), "https://api.together.xyz/v1"
    if provider == "fireworks":
        return _env("FIREWORKS_API_KEY"), "https://api.fireworks.ai/inference/v1"
    if provider == "deepseek":
        return _env("DEEPSEEK_API_KEY"), "https://api.deepseek.com/v1"
    if provider == "xai":
        return _env("XAI_API_KEY"), "https://api.x.ai/v1"
    if provider == "mistral":
        return _env("MISTRAL_API_KEY"), "https://api.mistral.ai/v1"
    if provider == "perplexity":
        return _env("PERPLEXITY_API_KEY"), "https://api.perplexity.ai"
    if provider == "cerebras":
        return _env("CEREBRAS_API_KEY"), "https://api.cerebras.ai/v1"
    if provider == "nvidia_nim":
        return _env("NGC_API_KEY", "NVIDIA_API_KEY"), "https://integrate.api.nvidia.com/v1"
    return None, None


def probe_openai_compatible_chat_completion(model: str, probe: dict[str, Any], provider: str) -> ProbeOutcome:
    api_key, base_url = _compatible_provider_config(provider, probe)
    if not api_key:
        return ProbeOutcome(False, f"missing API key for {provider}", status="down", auth_status="missing")
    if not base_url:
        return ProbeOutcome(False, f"missing base URL for {provider}", status="down", auth_status="failed")
    api_model = str(probe.get("model") or model.split("/", 1)[1] if "/" in model else model)
    timeout = int(probe.get("timeout_seconds", 45))
    prompt = str(probe.get("prompt") or "Reply with exactly: ok")
    body = {"model": api_model, "messages": [{"role": "user", "content": prompt}], "temperature": 0}
    try:
        payload = _openai_like_request(f"{base_url.rstrip('/')}/chat/completions", api_key, body, timeout)
    except urllib.error.HTTPError as exc:
        detail = http_error_detail(exc)
        status = "degraded" if exc.code == 429 else "down"
        generation = "rate_limit" if exc.code == 429 else "failed"
        return ProbeOutcome(False, detail, status=status, auth_status="healthy" if exc.code == 429 else "failed", generation_status=generation)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        return ProbeOutcome(False, str(exc), status="down", generation_status="failed")
    choices = payload.get("choices") if isinstance(payload, dict) else None
    if not isinstance(choices, list) or not choices:
        return ProbeOutcome(False, "chat completion returned no choices", status="down", auth_status="healthy", generation_status="empty")
    return ProbeOutcome(True, f"{provider} chat completion succeeded", status="healthy", auth_status="healthy", generation_status="healthy")


def probe_ollama_generate(model: str, probe: dict[str, Any]) -> ProbeOutcome:
    api_model = str(probe.get("model") or model.split("/", 1)[1] if "/" in model else model)
    timeout = int(probe.get("timeout_seconds", 30))
    url = str(probe.get("url") or "http://localhost:11434/api/generate")
    body = {
        "model": api_model,
        "prompt": str(probe.get("prompt") or "Reply with exactly: ok"),
        "stream": False,
    }
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return ProbeOutcome(False, http_error_detail(exc), status="down", generation_status="failed")
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        return ProbeOutcome(False, str(exc), status="down", generation_status="failed")
    if str(payload.get("response") or "").strip():
        return ProbeOutcome(True, "ollama generate succeeded", status="healthy", auth_status="local", generation_status="healthy")
    return ProbeOutcome(False, "ollama response missing text", status="down", generation_status="empty")


def update_state_with_probe(model: str, policy: dict[str, Any] | None = None, workspace: Path | None = None) -> dict[str, Any]:
    policy = policy or load_policy()
    workspace = workspace or WORKSPACE
    state = load_state(policy, workspace=workspace)
    entry = model_entry(policy, model)
    if not entry:
        return {"model": model, "status": "unknown", "checked_at": utc_now_iso(), "error": "model is not present in recovery policy"}

    provider = str(entry.get("provider") or model.split("/", 1)[0] if "/" in model else "")
    probe = dict(entry.get("probe") or {})
    kind = str(probe.get("kind") or "")
    try:
        if kind == "google_generate_content":
            outcome = probe_google_generate_content(model, probe)
        elif kind == "openai_chat_completion":
            outcome = probe_openai_chat_completion(model, probe)
        elif kind == "ollama_generate":
            outcome = probe_ollama_generate(model, probe)
        elif kind == "openai_compatible_chat_completion":
            outcome = probe_openai_compatible_chat_completion(model, probe, provider)
        else:
            outcome = ProbeOutcome(False, f"unsupported probe kind: {kind or 'missing'}", status="unknown")
    except TimeoutError:
        outcome = ProbeOutcome(False, "probe timed out", status="degraded", generation_status="timeout")
    except Exception as exc:
        outcome = ProbeOutcome(False, str(exc), status="down")

    payload = {
        "model": model,
        "provider": provider,
        "status": outcome.status or ("healthy" if outcome.ok else "down"),
        "checked_at": utc_now_iso(),
        "detail": outcome.detail,
    }
    if outcome.auth_status:
        payload["auth_status"] = outcome.auth_status
    if outcome.generation_status:
        payload["generation_status"] = outcome.generation_status

    previous = state.get("models", {}).get(model) if isinstance(state.get("models"), dict) else None
    if payload["status"] == "healthy":
        payload["last_ok_at"] = payload["checked_at"]
    elif isinstance(previous, dict) and previous.get("last_ok_at"):
        payload["last_ok_at"] = previous.get("last_ok_at")

    state.setdefault("models", {})
    state["models"][model] = payload
    save_state(state, policy, workspace=workspace)
    return payload


def launch_background_recovery_probe(current_model: str | None) -> subprocess.Popen[str] | None:
    targets = higher_priority_models(load_policy(), current_model)
    if not targets:
        return None
    command = [sys.executable, str(ROOT / "scripts" / "model_health_probe.py"), "watch", "--duration-seconds", "300"]
    if current_model:
        command.extend(["--current-model", current_model])
    return subprocess.Popen(command, cwd=str(WORKSPACE), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True)
