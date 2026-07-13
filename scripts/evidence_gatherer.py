#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


SHELL_META_RE = re.compile(r"[;&|`<>\r\n]|\$\(|\$\{")
SECRET_KEY_RE = re.compile(r"(?:secret|token|password|passwd|api[_-]?key|credential)", re.I)
SECRET_TEXT_PATTERNS = (
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+\-/]+=*"),
    re.compile(r"\b(?:sk|ghp|github_pat|xox[baprs])-[-A-Za-z0-9_]{8,}\b", re.I),
)
SAFE_ENV_KEYS = {
    "PATH", "HOME", "LANG", "LC_ALL", "TERM", "TMPDIR", "TMP", "TEMP",
    "SYSTEMROOT", "COMSPEC", "CI", "PYTHONPATH", "VIRTUAL_ENV", "CONDA_PREFIX",
}


@dataclass(frozen=True)
class TrustedServiceReadback:
    source: str
    provenance: str


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_config_path() -> Path:
    return Path(__file__).resolve().parents[1] / "references" / "evidence_commands.json"


def load_config(path: Path | None = None) -> dict:
    target = path or default_config_path()
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"evidence command config not found: {target}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid evidence command config: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("evidence command config must be a JSON object")
    limits = payload.get("limits")
    prefixes = payload.get("allowed_prefixes")
    if not isinstance(limits, dict) or not isinstance(prefixes, list):
        raise ValueError("evidence command config requires limits and allowed_prefixes")
    for prefix in prefixes:
        if not isinstance(prefix, list) or not prefix or not all(isinstance(token, str) and token for token in prefix):
            raise ValueError("each allowed command prefix must be a non-empty string array")
    for prefix in payload.get("service_readback_prefixes") or []:
        if not isinstance(prefix, list) or not prefix or not all(isinstance(token, str) and token for token in prefix):
            raise ValueError("each service readback prefix must be a non-empty string array")
    return payload


def _normalized_argv(argv: Iterable[object]) -> list[str]:
    command = [str(token) for token in argv]
    if command:
        executable = Path(command[0]).name
        if re.fullmatch(r"python(?:3(?:\.\d+)?)?", executable):
            command[0] = "python3"
        else:
            command[0] = executable
    return command


def _configured_prefixes(config: dict, cwd: Path) -> list[list[str]]:
    result: list[list[str]] = []
    rows = config.get("repository_prefixes") or []
    for row in rows:
        if not isinstance(row, dict):
            continue
        root = Path(str(row.get("root") or "")).expanduser()
        try:
            matches = cwd.resolve().is_relative_to(root.resolve())
        except (OSError, ValueError):
            matches = False
        if not matches:
            continue
        for prefix in row.get("prefixes") or []:
            if isinstance(prefix, list) and prefix and all(isinstance(token, str) and token for token in prefix):
                result.append(prefix)
    return result


def validate_command(argv: list[str], *, config: dict, cwd: Path) -> tuple[bool, str, list[str] | None]:
    if not argv or not all(isinstance(token, str) and token for token in argv):
        return False, "command must be a non-empty string array", None
    if any("\x00" in token or SHELL_META_RE.search(token) for token in argv):
        return False, "shell metacharacters and command chaining are forbidden", None
    normalized = _normalized_argv(argv)
    prefixes = [*config.get("allowed_prefixes", []), *_configured_prefixes(config, cwd)]
    for raw_prefix in prefixes:
        prefix = _normalized_argv(raw_prefix)
        if normalized[: len(prefix)] == prefix:
            return True, "allowlisted token prefix", raw_prefix
    return False, "command does not match an allowlisted token prefix", None


def validate_service_readback_command(argv: list[str], *, config: dict) -> tuple[bool, str]:
    if not argv or not all(isinstance(token, str) and token for token in argv):
        return False, "service readback command must be a non-empty string array"
    if any("\x00" in token or SHELL_META_RE.search(token) for token in argv):
        return False, "shell metacharacters and command chaining are forbidden"
    normalized = _normalized_argv(argv)
    for raw_prefix in config.get("service_readback_prefixes") or []:
        prefix = _normalized_argv(raw_prefix)
        if normalized[: len(prefix)] == prefix:
            return True, "service readback token prefix"
    return False, "command is not in the dedicated service readback allowlist"


def _redact_text(text: str, *, env: dict[str, str] | None = None) -> str:
    redacted = text
    for pattern in SECRET_TEXT_PATTERNS:
        redacted = pattern.sub(lambda match: (match.group(1) if match.lastindex else "") + "[REDACTED]", redacted)
    for key, value in (env or os.environ).items():
        if SECRET_KEY_RE.search(key) and value and len(value) >= 6:
            redacted = redacted.replace(value, "[REDACTED]")
    return redacted


def _redact_object(value: object) -> object:
    if isinstance(value, dict):
        return {
            str(key): "[REDACTED]" if SECRET_KEY_RE.search(str(key)) else _redact_object(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_object(item) for item in value]
    if isinstance(value, str):
        return _redact_text(value)
    return value


def _truncate(text: str, limit: int) -> tuple[str, bool, int]:
    cleaned = _redact_text(text)
    if len(cleaned) <= limit:
        return cleaned, False, len(cleaned)
    return cleaned[:limit] + "\n...[truncated]", True, len(cleaned)


def _child_env() -> dict[str, str]:
    return {
        key: value
        for key, value in os.environ.items()
        if key in SAFE_ENV_KEYS or key.startswith("PYTEST_")
    }


def _command_kind(prefix: list[str] | None) -> str:
    normalized = _normalized_argv(prefix or [])
    joined = " ".join(normalized)
    if any(token in joined for token in ("test", "pytest", "unittest")):
        return "test"
    if "build" in normalized:
        return "build"
    if any(token in normalized for token in ("lint", "typecheck", "mypy", "ruff", "black")):
        return "static_check"
    return "verification"


def run_command(argv: list[str], *, cwd: Path, config: dict) -> dict:
    allowed, reason, prefix = validate_command(argv, config=config, cwd=cwd)
    record = {
        "argv": argv,
        "allowlist_prefix": prefix,
        "kind": _command_kind(prefix),
        "started_at": utc_now_iso(),
    }
    if not allowed:
        return {**record, "status": "rejected", "ok": False, "exit_code": None, "reason": reason}
    limits = config["limits"]
    timeout = max(1, int(limits.get("timeout_seconds", 300)))
    output_limit = max(256, int(limits.get("output_chars", 12000)))
    start = time.monotonic()
    try:
        result = subprocess.run(
            argv,
            cwd=str(cwd),
            env=_child_env(),
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False,
        )
    except subprocess.TimeoutExpired as exc:
        stdout, stdout_truncated, stdout_chars = _truncate(str(exc.stdout or ""), output_limit)
        stderr, stderr_truncated, stderr_chars = _truncate(str(exc.stderr or ""), output_limit)
        return {
            **record,
            "finished_at": utc_now_iso(),
            "duration_ms": round((time.monotonic() - start) * 1000),
            "status": "timeout",
            "ok": False,
            "exit_code": 124,
            "reason": f"command exceeded {timeout}s timeout",
            "stdout": stdout,
            "stderr": stderr,
            "stdout_truncated": stdout_truncated,
            "stderr_truncated": stderr_truncated,
            "stdout_chars": stdout_chars,
            "stderr_chars": stderr_chars,
        }
    stdout, stdout_truncated, stdout_chars = _truncate(result.stdout or "", output_limit)
    stderr, stderr_truncated, stderr_chars = _truncate(result.stderr or "", output_limit)
    return {
        **record,
        "finished_at": utc_now_iso(),
        "duration_ms": round((time.monotonic() - start) * 1000),
        "status": "passed" if result.returncode == 0 else "failed",
        "ok": result.returncode == 0,
        "exit_code": result.returncode,
        "reason": "process exited successfully" if result.returncode == 0 else "process exited nonzero",
        "stdout": stdout,
        "stderr": stderr,
        "stdout_truncated": stdout_truncated,
        "stderr_truncated": stderr_truncated,
        "stdout_chars": stdout_chars,
        "stderr_chars": stderr_chars,
    }


def read_file_evidence(path_value: str, *, cwd: Path) -> dict:
    candidate = Path(path_value).expanduser()
    candidate = candidate if candidate.is_absolute() else cwd / candidate
    try:
        resolved = candidate.resolve(strict=False)
        resolved.relative_to(cwd.resolve())
    except (OSError, ValueError):
        return {"path": path_value, "kind": "file_readback", "ok": False, "reason": "path escapes evidence workspace"}
    if not resolved.is_file():
        return {"path": path_value, "kind": "file_readback", "ok": False, "reason": "file is missing"}
    digest = hashlib.sha256(resolved.read_bytes()).hexdigest()
    return {
        "path": str(resolved.relative_to(cwd.resolve())),
        "kind": "file_readback",
        "ok": True,
        "bytes": resolved.stat().st_size,
        "sha256": digest,
    }


def _atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def gather_evidence(
    commands: list[list[str]],
    *,
    cwd: Path,
    config: dict,
    files: list[str] | None = None,
    service_evidence: list[dict] | None = None,
    trusted_service_evidence: list[TrustedServiceReadback] | None = None,
    output_path: Path | None = None,
) -> dict:
    workspace = cwd.expanduser().resolve()
    if not workspace.is_dir():
        raise ValueError(f"evidence cwd is not a directory: {workspace}")
    max_commands = max(0, int((config.get("limits") or {}).get("max_commands", 8)))
    overflow = len(commands) > max_commands
    selected = commands[:max_commands]
    command_results = [run_command(command, cwd=workspace, config=config) for command in selected]
    file_results = [read_file_evidence(path, cwd=workspace) for path in files or []]
    service_results = []
    for raw in service_evidence or []:
        row = _redact_object(raw)
        if not isinstance(row, dict):
            row = {"value": row}
        source = str(row.get("source") or row.get("reference") or "").strip()
        command = row.get("readback_command")
        if isinstance(command, list) and command and all(isinstance(token, str) and token for token in command):
            trusted, trust_reason = validate_service_readback_command(command, config=config)
            if not trusted:
                service_results.append({
                    "kind": "service_readback",
                    "source": source,
                    "provenance": "untrusted_command",
                    "ok": False,
                    "reason": trust_reason,
                })
                continue
            readback = run_command(command, cwd=workspace, config=config)
            service_results.append({
                "kind": "service_readback",
                "source": source,
                "provenance": "evidence_gatherer_command",
                "readback": readback,
                "ok": bool(source) and readback.get("ok") is True,
                "reason": "allowlisted readback passed" if source and readback.get("ok") is True else "allowlisted readback failed",
            })
        else:
            service_results.append({
                "kind": "service_readback",
                "source": source,
                "provenance": "caller_supplied",
                "ok": False,
                "reason": "caller assertions require an allowlisted readback_command",
            })
    for readback in trusted_service_evidence or []:
        if not isinstance(readback, TrustedServiceReadback):
            continue
        service_results.append({
            "kind": "service_readback",
            "source": readback.source,
            "provenance": readback.provenance,
            "ok": bool(readback.source) and readback.provenance in {"actual_remote_readback", "actual_provider_readback"},
            "reason": "runner performed live readback",
        })
    has_evidence = bool(command_results or file_results or service_results)
    ok = has_evidence and not overflow and all(
        row.get("ok") is True for row in [*command_results, *file_results, *service_results]
    )
    payload = {
        "schema_version": 1,
        "created_at": utc_now_iso(),
        "workspace": str(workspace),
        "ok": ok,
        "status": "passed" if ok else "failed",
        "reason": (
            "all evidence passed" if ok else
            "max command count exceeded" if overflow else
            "no evidence supplied" if not has_evidence else
            "one or more evidence checks failed"
        ),
        "limits": dict(config.get("limits") or {}),
        "requested_command_count": len(commands),
        "command_results": command_results,
        "file_results": file_results,
        "service_results": service_results,
    }
    if output_path:
        _atomic_write_json(output_path, payload)
        payload["output_path"] = str(output_path)
    return payload


def _parse_command_json(value: str) -> list[str]:
    try:
        payload = json.loads(value)
    except json.JSONDecodeError as exc:
        raise argparse.ArgumentTypeError(f"invalid command JSON: {exc}") from exc
    if not isinstance(payload, list) or not payload or not all(isinstance(token, str) and token for token in payload):
        raise argparse.ArgumentTypeError("command JSON must be a non-empty string array")
    return payload


def _parse_service_json(value: str) -> dict:
    try:
        payload = json.loads(value)
    except json.JSONDecodeError as exc:
        raise argparse.ArgumentTypeError(f"invalid service evidence JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise argparse.ArgumentTypeError("service evidence JSON must be an object")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Run allowlisted verification commands and save structured evidence.")
    parser.add_argument("--cwd", type=Path, default=Path.cwd())
    parser.add_argument("--config", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--command-json", action="append", type=_parse_command_json, default=[])
    parser.add_argument("--command", action="append", default=[], help="Command parsed with shlex; JSON arrays are safer.")
    parser.add_argument("--file", action="append", default=[])
    parser.add_argument("--service-evidence-json", action="append", type=_parse_service_json, default=[])
    args = parser.parse_args()
    try:
        commands = [*args.command_json, *(shlex.split(value) for value in args.command)]
        payload = gather_evidence(
            commands,
            cwd=args.cwd,
            config=load_config(args.config),
            files=args.file,
            service_evidence=args.service_evidence_json,
            output_path=args.output,
        )
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0 if payload["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
