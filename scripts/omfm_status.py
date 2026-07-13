#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Mapping


DEFAULT_PORT = 4567
GROUP_MIN_CONTEXT = {"fast": 32_768, "balanced": 131_072, "capable": 1_000_000}
_MODEL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+\-]{0,255}")
_SECRET_MARKERS = ("sk-", "nvapi-", "aiza", "api_key", "apikey", "token=")
_ROUTE_REASONS = {"lowest-latency", "model-group", "fallback-order", "requested-selected"}


def _safe_model_id(value: object) -> str | None:
    model = str(value or "").strip()
    lowered = model.casefold()
    return model if _MODEL_ID.fullmatch(model) and not any(marker in lowered for marker in _SECRET_MARKERS) else None


def _safe_best_route(value: object) -> dict[str, Any] | None:
    if not isinstance(value, dict) or not (model := _safe_model_id(value.get("model"))):
        return None
    try:
        latency = max(0, int(value.get("latency_ms") or 0))
    except (TypeError, ValueError):
        latency = 0
    reason = str(value.get("reason") or "unknown")
    return {"model": model, "latency_ms": latency, "reason": reason if reason in _ROUTE_REASONS else "unknown"}


def _json_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def omfm_home(env: Mapping[str, str] | None = None) -> Path:
    env = env or os.environ
    return Path(env.get("OMFM_HOME") or Path.home() / ".oh-my-free-models").expanduser()


def _parse_status(text: str) -> dict[str, Any]:
    parsed: dict[str, Any] = {}
    for line in map(str.strip, text.splitlines()):
        if line in {"omfm running", "omfm stopped"}:
            parsed["running"] = line.endswith("running")
        elif match := re.fullmatch(r"port:\s*(\d+)", line):
            parsed["port"] = int(match.group(1))
        elif match := re.fullmatch(r"selected models:\s*(\d+)", line):
            parsed["selected_model_count"] = int(match.group(1))
        elif match := re.fullmatch(r"best route:\s*(.+?)\s+\((\d+)ms,\s*([^)]+)\)", line):
            if model := _safe_model_id(match.group(1)):
                parsed["best_route"] = {"model": model, "latency_ms": int(match.group(2)), "reason": match.group(3)[:80]}
    return parsed


def _command_status(binary: str, timeout: float) -> tuple[dict[str, Any], str | None]:
    try:
        result = subprocess.run([binary, "status", "--json"], capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return {}, "status command failed"
    if result.returncode:
        return {}, f"status command exited {result.returncode}"
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        payload = _parse_status(result.stdout)
    return (payload if isinstance(payload, dict) else {}), None


def _probe(base_url: str, timeout: float) -> tuple[bool, int | None]:
    parsed = urllib.parse.urlparse(base_url)
    if parsed.scheme != "http" or (parsed.hostname or "").casefold() not in {"127.0.0.1", "localhost", "::1"}:
        return False, None
    request = urllib.request.Request(f"{base_url}/models", headers={"Authorization": "Bearer omfm-local"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - loopback checked above
            payload = json.loads(response.read(65_536).decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False, None
    models = payload.get("data") if isinstance(payload, dict) else None
    return (True, len(models)) if isinstance(models, list) else (False, None)


def _local_config(root: Path) -> tuple[int, list[str], dict[str, list[str]]]:
    payload = _json_object(root / "config.json")
    port = payload.get("port") if isinstance(payload.get("port"), int) and 0 < payload["port"] <= 65_535 else DEFAULT_PORT
    selected = list(dict.fromkeys(model for item in payload.get("selectedModelIds", []) if (model := _safe_model_id(item))))[:256]
    source = payload.get("modelGroups") if isinstance(payload.get("modelGroups"), dict) else {}
    groups = {
        group: [model for item in source.get(group, []) if (model := _safe_model_id(item)) and model in selected][:256]
        for group in GROUP_MIN_CONTEXT
    }
    return port, selected, groups


def _context_guards(root: Path, groups: dict[str, list[str]]) -> dict[str, dict[str, Any]]:
    cache = _json_object(root / "models-cache.json")
    rows = cache.get("models") if isinstance(cache.get("models"), list) else []
    contexts: dict[str, int] = {}
    for row in rows:
        if not isinstance(row, dict) or not (model_id := _safe_model_id(row.get("id"))):
            continue
        raw_context = row.get("contextLength")
        if isinstance(raw_context, bool) or not isinstance(raw_context, (int, float)):
            continue
        try:
            context = int(raw_context)
        except (OverflowError, ValueError):
            continue
        if context > 0 and context == raw_context:
            contexts[model_id] = context
    result = {}
    for group, required in GROUP_MIN_CONTEXT.items():
        ids = groups[group]
        unknown = [item for item in ids if item not in contexts]
        known = [contexts[item] for item in ids if item in contexts]
        result[group] = {
            "required_min_context": required,
            "selected_model_count": len(ids),
            "minimum_selected_context": min(known) if known else None,
            "unknown_context_models": unknown,
            "compliant": bool(ids) and not unknown and bool(known) and min(known) >= required,
        }
    return result


def build_omfm_status(*, timeout: float = 1.5, env: Mapping[str, str] | None = None) -> dict[str, Any]:
    env = env or os.environ
    root = omfm_home(env)
    binary = shutil.which("omfm", path=env.get("PATH"))
    empty_groups = {group: [] for group in GROUP_MIN_CONTEXT}
    if not binary:
        return {
            "version": 1,
            "installed": False,
            "status": "not_installed",
            "daemon_state": "not_installed",
            "endpoint_reachable": False,
            "selected_model_count": 0,
            "model_groups": empty_groups,
            "context_guards": _context_guards(root, empty_groups),
        }
    command, error = _command_status(binary, timeout)
    config_port, selected, groups = _local_config(root)
    port = command.get("port") if isinstance(command.get("port"), int) and 0 < command["port"] <= 65_535 else config_port
    base_url = f"http://127.0.0.1:{port}/v1"
    endpoint_ok, endpoint_count = _probe(base_url, timeout)
    claimed = command.get("running") is True
    status = "ready" if endpoint_ok and selected else "unconfigured" if endpoint_ok else "degraded" if claimed else "error" if error else "stopped"
    return {
        "version": 1,
        "installed": True,
        "status": status,
        "daemon_state": "running" if endpoint_ok or claimed else "stopped",
        "daemon_process_claimed": claimed,
        "endpoint": base_url,
        "endpoint_reachable": endpoint_ok,
        "endpoint_model_count": endpoint_count,
        "selected_model_count": len(selected) if selected else int(command.get("selected_model_count") or 0),
        "selected_models": selected,
        "model_groups": groups,
        "context_guards": _context_guards(root, groups),
        "best_route": _safe_best_route(command.get("best_route")),
        **({"detail": error} if error and not endpoint_ok else {}),
    }


def context_guard_allows(status: dict[str, Any], model: str, context_tokens: int, *, reserve_tokens: int = 16_384) -> bool:
    group = model.rsplit("/", 1)[-1].casefold()
    guards = status.get("context_guards")
    if group not in GROUP_MIN_CONTEXT or not isinstance(guards, dict):
        return False
    guard = guards.get(group)
    if not isinstance(guard, dict):
        return False
    minimum = guard.get("minimum_selected_context")
    return bool(
        status.get("status") == "ready"
        and guard.get("compliant")
        and isinstance(minimum, int)
        and not isinstance(minimum, bool)
        and isinstance(context_tokens, int)
        and not isinstance(context_tokens, bool)
        and context_tokens >= 0
        and isinstance(reserve_tokens, int)
        and not isinstance(reserve_tokens, bool)
        and reserve_tokens >= 0
        and context_tokens <= max(0, minimum - reserve_tokens)
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Report redacted omfm readiness.")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--require-ready", action="store_true")
    args = parser.parse_args(argv)
    payload = build_omfm_status()
    print(json.dumps(payload, indent=2, ensure_ascii=False) if args.json else f"omfm={payload['status']}")
    return 1 if args.require_ready and payload["status"] != "ready" else 0


if __name__ == "__main__":
    raise SystemExit(main())
