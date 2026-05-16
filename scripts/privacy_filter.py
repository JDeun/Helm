#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from helm_workspace import get_workspace_layout
from scripts.state_io import append_jsonl_atomic


SECRET_LABEL = "SECRET"
DEFAULT_SCOPE = "default"


@dataclass(frozen=True)
class Finding:
    label: str
    value: str
    start: int
    end: int
    recoverable: bool


@dataclass(frozen=True)
class Replacement:
    label: str
    token: str
    value_hash: str
    recoverable: bool


PATTERNS: tuple[tuple[str, re.Pattern[str], bool], ...] = (
    ("EMAIL", re.compile(r"(?<![\w.+-])[\w.+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}\b"), True),
    ("PHONE", re.compile(r"(?<!\d)(?:\+?\d{1,3}[-.\s]?)?(?:\(?\d{2,4}\)?[-.\s]?)?\d{3,4}[-.\s]?\d{4}(?!\d)"), True),
    ("KOREAN_RRN", re.compile(r"(?<!\d)\d{6}-[1-4]\d{6}(?!\d)"), True),
    ("CREDIT_CARD", re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)"), True),
    (
        SECRET_LABEL,
        re.compile(
            r"(?i)\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|passwd|secret)\b"
            r"\s*[:=]\s*['\"]?[^'\"\s]+"
        ),
        False,
    ),
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _state_root() -> Path:
    return get_workspace_layout().state_root


def default_vault_path() -> Path:
    return _state_root() / "privacy-vault.json"


def default_audit_path() -> Path:
    return _state_root() / "privacy-audit.jsonl"


def value_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _looks_like_credit_card(value: str) -> bool:
    digits = [int(ch) for ch in value if ch.isdigit()]
    if not 13 <= len(digits) <= 19:
        return False
    checksum = 0
    parity = len(digits) % 2
    for idx, digit in enumerate(digits):
        if idx % 2 == parity:
            digit *= 2
            if digit > 9:
                digit -= 9
        checksum += digit
    return checksum % 10 == 0


def detect(text: str) -> list[Finding]:
    findings: list[Finding] = []
    for label, pattern, recoverable in PATTERNS:
        for match in pattern.finditer(text):
            value = match.group(0)
            if label == "CREDIT_CARD" and not _looks_like_credit_card(value):
                continue
            findings.append(Finding(label, value, match.start(), match.end(), recoverable))
    findings.sort(key=lambda item: (item.start, -(item.end - item.start)))

    selected: list[Finding] = []
    occupied_until = -1
    for finding in findings:
        if finding.start < occupied_until:
            continue
        selected.append(finding)
        occupied_until = finding.end
    return selected


def _empty_vault() -> dict:
    return {"version": 1, "scopes": {}}


def load_vault(path: Path) -> dict:
    if not path.exists():
        return _empty_vault()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid privacy vault {path}: {exc}")
    if not isinstance(payload, dict):
        raise SystemExit(f"Invalid privacy vault {path}: expected JSON object")
    payload.setdefault("version", 1)
    payload.setdefault("scopes", {})
    if not isinstance(payload["scopes"], dict):
        raise SystemExit(f"Invalid privacy vault {path}: scopes must be an object")
    return payload


def save_vault(path: Path, vault: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(vault, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def _scope_record(vault: dict, scope: str) -> dict:
    scopes = vault.setdefault("scopes", {})
    record = scopes.setdefault(scope, {"created_at": _now(), "tokens": {}, "labels": {}, "hashes": {}})
    record.setdefault("tokens", {})
    record.setdefault("labels", {})
    record.setdefault("hashes", {})
    return record


def _token_for(record: dict, finding: Finding, counters: dict[str, int]) -> str:
    hashed = value_hash(finding.value)
    existing = record["hashes"].get(hashed)
    if existing:
        return str(existing)
    counters[finding.label] = counters.get(finding.label, 0) + 1
    token = f"<PRIVATE_{finding.label}_{counters[finding.label]}>"
    while token in record["tokens"]:
        counters[finding.label] += 1
        token = f"<PRIVATE_{finding.label}_{counters[finding.label]}>"
    record["hashes"][hashed] = token
    record["tokens"][token] = finding.value
    record["labels"][token] = finding.label
    return token


def tokenize_text(text: str, *, vault: dict | None = None, scope: str = DEFAULT_SCOPE) -> tuple[str, list[Replacement]]:
    findings = detect(text)
    if not findings:
        return text, []

    record = _scope_record(vault, scope) if vault is not None else None
    counters: dict[str, int] = {}
    if record:
        for token, label in record.get("labels", {}).items():
            match = re.fullmatch(rf"<PRIVATE_{re.escape(str(label))}_(\d+)>", str(token))
            if match:
                counters[str(label)] = max(counters.get(str(label), 0), int(match.group(1)))

    replacements: list[Replacement] = []
    chunks: list[str] = []
    cursor = 0
    ephemeral: dict[tuple[str, str], str] = {}
    ephemeral_counters: dict[str, int] = {}

    for finding in findings:
        chunks.append(text[cursor:finding.start])
        if finding.recoverable and record is not None:
            token = _token_for(record, finding, counters)
        elif finding.recoverable:
            key = (finding.label, finding.value)
            if key not in ephemeral:
                ephemeral_counters[finding.label] = ephemeral_counters.get(finding.label, 0) + 1
                ephemeral[key] = f"<PRIVATE_{finding.label}_{ephemeral_counters[finding.label]}>"
            token = ephemeral[key]
        else:
            token = f"<{SECRET_LABEL}_REDACTED_{len([r for r in replacements if r.label == SECRET_LABEL]) + 1}>"
        chunks.append(token)
        replacements.append(
            Replacement(
                label=finding.label,
                token=token,
                value_hash=value_hash(finding.value),
                recoverable=finding.recoverable and record is not None,
            )
        )
        cursor = finding.end
    chunks.append(text[cursor:])
    return "".join(chunks), replacements


def restore_text(text: str, *, vault: dict, scope: str = DEFAULT_SCOPE) -> tuple[str, int]:
    record = _scope_record(vault, scope)
    restored = text
    count = 0
    for token, value in sorted(record["tokens"].items(), key=lambda item: len(item[0]), reverse=True):
        token_count = restored.count(token)
        restored = restored.replace(token, value)
        count += token_count
    return restored, count


def read_input(args: argparse.Namespace) -> str:
    if args.text is not None:
        return args.text
    if args.input:
        return Path(args.input).expanduser().read_text(encoding="utf-8")
    return sys.stdin.read()


def write_audit(event: dict, audit_path: Path | None) -> None:
    if audit_path is None:
        return
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    append_jsonl_atomic(audit_path, event)


def cmd_scan(args: argparse.Namespace) -> int:
    text = read_input(args)
    findings = detect(text)
    payload = {
        "count": len(findings),
        "findings": [
            {
                "label": item.label,
                "start": item.start,
                "end": item.end,
                "recoverable": item.recoverable,
                "value_hash": value_hash(item.value),
            }
            for item in findings
        ],
    }
    print(json.dumps(payload, indent=2, ensure_ascii=False) if args.json else f"privacy_findings={payload['count']}")
    return 1 if findings and args.fail_on_findings else 0


def cmd_tokenize(args: argparse.Namespace) -> int:
    text = read_input(args)
    vault_path = Path(args.vault).expanduser() if args.vault else default_vault_path()
    vault = load_vault(vault_path) if not args.no_vault else None
    tokenized, replacements = tokenize_text(text, vault=vault, scope=args.scope)
    if vault is not None and replacements:
        save_vault(vault_path, vault)
    audit_path = Path(args.audit).expanduser() if args.audit else default_audit_path()
    write_audit(
        {
            "timestamp": _now(),
            "operation": "tokenize",
            "scope": args.scope,
            "vault": None if args.no_vault else str(vault_path),
            "replacement_count": len(replacements),
            "labels": sorted({item.label for item in replacements}),
        },
        None if args.no_audit else audit_path,
    )
    payload = {
        "text": tokenized,
        "replacement_count": len(replacements),
        "replacements": [asdict(item) for item in replacements],
        "vault": None if args.no_vault else str(vault_path),
    }
    print(json.dumps(payload, indent=2, ensure_ascii=False) if args.json else tokenized)
    return 0


def cmd_restore(args: argparse.Namespace) -> int:
    text = read_input(args)
    vault_path = Path(args.vault).expanduser() if args.vault else default_vault_path()
    vault = load_vault(vault_path)
    restored, count = restore_text(text, vault=vault, scope=args.scope)
    audit_path = Path(args.audit).expanduser() if args.audit else default_audit_path()
    write_audit(
        {
            "timestamp": _now(),
            "operation": "restore",
            "scope": args.scope,
            "vault": str(vault_path),
            "replacement_count": count,
        },
        None if args.no_audit else audit_path,
    )
    payload = {"text": restored, "replacement_count": count, "vault": str(vault_path)}
    print(json.dumps(payload, indent=2, ensure_ascii=False) if args.json else restored)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Scan, tokenize, and restore private text at agent/tool boundaries.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    scan = subparsers.add_parser("scan", help="Detect private spans without writing a vault.")
    scan.add_argument("--text")
    scan.add_argument("--input")
    scan.add_argument("--json", action="store_true")
    scan.add_argument("--fail-on-findings", action="store_true")
    scan.set_defaults(func=cmd_scan)

    tokenize = subparsers.add_parser("tokenize", help="Replace private spans with stable tokens.")
    tokenize.add_argument("--text")
    tokenize.add_argument("--input")
    tokenize.add_argument("--scope", default=DEFAULT_SCOPE)
    tokenize.add_argument("--vault", help="Vault path. Defaults to .helm/privacy-vault.json in the target workspace.")
    tokenize.add_argument("--audit", help="Audit JSONL path. Defaults to .helm/privacy-audit.jsonl in the target workspace.")
    tokenize.add_argument("--no-vault", action="store_true", help="Do not persist reversible mappings.")
    tokenize.add_argument("--no-audit", action="store_true")
    tokenize.add_argument("--json", action="store_true")
    tokenize.set_defaults(func=cmd_tokenize)

    restore = subparsers.add_parser("restore", help="Restore tokens from an authorized local vault.")
    restore.add_argument("--text")
    restore.add_argument("--input")
    restore.add_argument("--scope", default=DEFAULT_SCOPE)
    restore.add_argument("--vault", help="Vault path. Defaults to .helm/privacy-vault.json in the target workspace.")
    restore.add_argument("--audit", help="Audit JSONL path. Defaults to .helm/privacy-audit.jsonl in the target workspace.")
    restore.add_argument("--no-audit", action="store_true")
    restore.add_argument("--json", action="store_true")
    restore.set_defaults(func=cmd_restore)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
