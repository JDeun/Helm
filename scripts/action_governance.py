#!/usr/bin/env python3
"""Deterministic action governance primitives for Helm.

This module is intentionally library-first. It does not execute mutating
actions; it evaluates whether an action may run, builds the pre-execution
decision record, and validates the evidence required before completion can be
claimed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts.action_scope import ActionScopeKind, evaluate as evaluate_scope  # noqa: E402
from scripts.time_helpers import utc_now_iso  # noqa: E402

REGISTRY_PATH = _ROOT / "references" / "action_governance_registry.json"
DECISION_RECORD_FIELDS = (
    "timestamp",
    "session_id",
    "user_message_hash",
    "user_message_redacted",
    "parsed_scope",
    "attempted_action",
    "resource",
    "target",
    "policy_version",
    "decision",
    "reason",
    "live_source_requirement",
    "approval_status",
    "execution_status",
    "verification_result",
    "evidence_contract",
)

DECISIONS = {"allow", "deny", "require_approval", "inspect_only"}
APPROVAL_STATUSES = {"not_required", "pending", "approved", "rejected"}

__all__ = [
    "ActionDefinition",
    "ActionRegistry",
    "DecisionRecord",
    "EvidenceValidationResult",
    "load_registry",
    "evaluate_governed_action",
    "validate_evidence_contract",
    "append_decision_record",
]


@dataclass(frozen=True)
class ActionDefinition:
    """One governed action entry from the registry."""

    action_id: str
    resource_type: str
    required_scope: ActionScopeKind
    mutates: bool
    needs_live_source: bool
    requires_approval: bool
    default_decision: str
    evidence_contract: dict[str, list[str]]

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ActionDefinition":
        action_id = _require_string(payload, "action_id")
        default_decision = _require_string(payload, "default_decision")
        if default_decision not in DECISIONS:
            raise ValueError(f"{action_id}: unknown default_decision={default_decision!r}")
        contract = payload.get("evidence_contract") or {}
        if not isinstance(contract, dict):
            raise ValueError(f"{action_id}: evidence_contract must be an object")
        return cls(
            action_id=action_id,
            resource_type=_require_string(payload, "resource_type"),
            required_scope=ActionScopeKind(_require_string(payload, "required_scope")),
            mutates=bool(payload.get("mutates")),
            needs_live_source=bool(payload.get("needs_live_source")),
            requires_approval=bool(payload.get("requires_approval")),
            default_decision=default_decision,
            evidence_contract=_normalize_contract(contract),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "resource_type": self.resource_type,
            "required_scope": self.required_scope.value,
            "mutates": self.mutates,
            "needs_live_source": self.needs_live_source,
            "requires_approval": self.requires_approval,
            "default_decision": self.default_decision,
            "evidence_contract": {
                "require_all": list(self.evidence_contract.get("require_all", [])),
                "require_one_of": list(self.evidence_contract.get("require_one_of", [])),
            },
        }


@dataclass(frozen=True)
class ActionRegistry:
    """Loaded action registry with deterministic lookup semantics."""

    schema_version: str
    policy_version: str
    default_action: str
    actions: dict[str, ActionDefinition]

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ActionRegistry":
        schema_version = _require_string(payload, "schema_version")
        policy_version = _require_string(payload, "policy_version")
        default_action = _require_string(payload, "default_action")
        if default_action not in DECISIONS:
            raise ValueError(f"unknown default_action={default_action!r}")
        raw_actions = payload.get("actions")
        if not isinstance(raw_actions, list):
            raise ValueError("registry actions must be a list")
        actions: dict[str, ActionDefinition] = {}
        for item in raw_actions:
            if not isinstance(item, dict):
                raise ValueError("each registry action must be an object")
            action = ActionDefinition.from_dict(item)
            if action.action_id in actions:
                raise ValueError(f"duplicate action_id={action.action_id!r}")
            actions[action.action_id] = action
        return cls(
            schema_version=schema_version,
            policy_version=policy_version,
            default_action=default_action,
            actions=actions,
        )

    def get(self, action_id: str) -> ActionDefinition | None:
        return self.actions.get(action_id)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "policy_version": self.policy_version,
            "default_action": self.default_action,
            "actions": [action.as_dict() for action in self.actions.values()],
        }


@dataclass(frozen=True)
class DecisionRecord:
    """Standard pre-execution decision record for one governed action."""

    timestamp: str
    session_id: str | None
    user_message_hash: str
    user_message_redacted: str | None
    parsed_scope: str | None
    attempted_action: str
    resource: str
    target: str | None
    policy_version: str
    decision: str
    reason: str
    live_source_requirement: bool
    approval_status: str
    execution_status: str | None
    verification_result: dict[str, Any] | None
    evidence_contract: dict[str, list[str]]

    def as_dict(self) -> dict[str, Any]:
        return {field: getattr(self, field) for field in DECISION_RECORD_FIELDS}


@dataclass(frozen=True)
class EvidenceValidationResult:
    """Result of checking an action's post-execution evidence."""

    action_id: str
    ok: bool
    missing_all: list[str]
    missing_one_of: list[str]
    satisfied: list[str]
    evidence: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "ok": self.ok,
            "missing_all": list(self.missing_all),
            "missing_one_of": list(self.missing_one_of),
            "satisfied": list(self.satisfied),
            "evidence": dict(self.evidence),
        }


def load_registry(path: Path = REGISTRY_PATH) -> ActionRegistry:
    """Load the action governance registry from JSON."""
    with Path(path).open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError("action governance registry must be a JSON object")
    return ActionRegistry.from_dict(payload)


def evaluate_governed_action(
    *,
    user_message: str,
    action_id: str,
    resource: str | None = None,
    target: str | None = None,
    target_explicit: bool | None = None,
    live_source_confirmed: bool = False,
    approval_status: str | None = None,
    session_id: str | None = None,
    registry: ActionRegistry | None = None,
    include_redacted_message: bool = False,
) -> DecisionRecord:
    """Evaluate a governed action before execution.

    The function fails closed for unknown actions and missing mutation targets.
    It records a hash of the raw user message by default; callers may opt into
    a redacted text preview for local diagnostics.
    """
    registry = registry or load_registry()
    action = registry.get(action_id)
    message_hash = _message_hash(user_message)
    redacted = _redact_message(user_message) if include_redacted_message else None
    now = utc_now_iso()

    if action is None:
        return DecisionRecord(
            timestamp=now,
            session_id=session_id,
            user_message_hash=message_hash,
            user_message_redacted=redacted,
            parsed_scope=None,
            attempted_action=action_id,
            resource=resource or "unknown",
            target=target,
            policy_version=registry.policy_version,
            decision=registry.default_action,
            reason="unregistered_action",
            live_source_requirement=False,
            approval_status="not_required",
            execution_status=None,
            verification_result=None,
            evidence_contract={},
        )

    explicit = bool(target) if target_explicit is None else target_explicit
    explicit_targets: Iterable[str] | None = (target,) if target else None
    scope_decision = evaluate_scope(user_message, explicit_targets=explicit_targets)
    parsed_scope = scope_decision.locked_scope.value if scope_decision.locked_scope else None
    normalized_approval = _approval_status(action, approval_status)

    decision = action.default_decision
    reason = "default_decision"

    if scope_decision.refusal_reason:
        decision = "deny"
        reason = scope_decision.refusal_reason
    elif action.mutates and scope_decision.locked_scope == ActionScopeKind.INSPECT:
        decision = "inspect_only"
        reason = "inspect_scope_for_mutation"
    elif action.mutates and not explicit:
        decision = "deny"
        reason = "missing_explicit_target"
    elif _scope_rank(scope_decision.locked_scope) < _scope_rank(action.required_scope):
        decision = "deny"
        reason = f"scope={parsed_scope} below required_scope={action.required_scope.value}"
    elif action.needs_live_source and not live_source_confirmed:
        decision = "deny"
        reason = "live_source_required"
    elif action.requires_approval and normalized_approval == "rejected":
        decision = "deny"
        reason = "approval_rejected"
    elif action.requires_approval and normalized_approval != "approved":
        decision = "require_approval"
        reason = "approval_required"
    else:
        decision = "allow"
        reason = "policy_satisfied"

    return DecisionRecord(
        timestamp=now,
        session_id=session_id,
        user_message_hash=message_hash,
        user_message_redacted=redacted,
        parsed_scope=parsed_scope,
        attempted_action=action.action_id,
        resource=resource or action.resource_type,
        target=target,
        policy_version=registry.policy_version,
        decision=decision,
        reason=reason,
        live_source_requirement=action.needs_live_source,
        approval_status=normalized_approval,
        execution_status=None,
        verification_result=None,
        evidence_contract=action.evidence_contract,
    )


def validate_evidence_contract(
    action_id: str,
    evidence: dict[str, Any] | None,
    *,
    registry: ActionRegistry | None = None,
) -> EvidenceValidationResult:
    """Validate evidence for an action's completion claim."""
    registry = registry or load_registry()
    action = registry.get(action_id)
    payload = evidence if isinstance(evidence, dict) else {}
    if action is None:
        return EvidenceValidationResult(
            action_id=action_id,
            ok=False,
            missing_all=["registered_action"],
            missing_one_of=[],
            satisfied=[],
            evidence=payload,
        )

    contract = action.evidence_contract
    require_all = contract.get("require_all", [])
    require_one_of = contract.get("require_one_of", [])
    satisfied = [key for key in require_all + require_one_of if _truthy_evidence(payload.get(key))]
    missing_all = [key for key in require_all if not _truthy_evidence(payload.get(key))]
    missing_one_of: list[str] = []
    if require_one_of and not any(_truthy_evidence(payload.get(key)) for key in require_one_of):
        missing_one_of = list(require_one_of)
    ok = not missing_all and not missing_one_of
    return EvidenceValidationResult(
        action_id=action.action_id,
        ok=ok,
        missing_all=missing_all,
        missing_one_of=missing_one_of,
        satisfied=satisfied,
        evidence=payload,
    )


def append_decision_record(path: Path, record: DecisionRecord | dict[str, Any]) -> None:
    """Append one standard decision record as JSONL."""
    payload = record.as_dict() if isinstance(record, DecisionRecord) else dict(record)
    missing = [field for field in DECISION_RECORD_FIELDS if field not in payload]
    if missing:
        raise ValueError(f"decision record missing fields: {', '.join(missing)}")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


def _require_string(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"missing required string field: {key}")
    return value


def _normalize_contract(contract: dict[str, Any]) -> dict[str, list[str]]:
    normalized: dict[str, list[str]] = {"require_all": [], "require_one_of": []}
    for key in normalized:
        raw = contract.get(key, [])
        if raw is None:
            raw = []
        if not isinstance(raw, list) or not all(isinstance(item, str) and item for item in raw):
            raise ValueError(f"evidence_contract.{key} must be a list of strings")
        normalized[key] = list(dict.fromkeys(raw))
    return normalized


def _approval_status(action: ActionDefinition, status: str | None) -> str:
    if not action.requires_approval:
        return "not_required"
    resolved = status or "pending"
    if resolved not in APPROVAL_STATUSES:
        raise ValueError(f"unknown approval_status={resolved!r}")
    return resolved


def _message_hash(message: str) -> str:
    return hashlib.sha256(message.encode("utf-8")).hexdigest()


def _redact_message(message: str) -> str:
    compact = " ".join(message.split())
    return compact[:160]


def _scope_rank(scope: ActionScopeKind | None) -> int:
    order = {
        ActionScopeKind.INSPECT: 0,
        ActionScopeKind.SAVE: 1,
        ActionScopeKind.EDIT: 2,
        ActionScopeKind.DELETE: 3,
        ActionScopeKind.EXTERNAL_SEND: 4,
    }
    if scope is None:
        return -1
    return order[scope]


def _truthy_evidence(value: Any) -> bool:
    if value is None or value is False:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return True


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Evaluate a governed Helm action.")
    parser.add_argument("--message", required=True)
    parser.add_argument("--action-id", required=True)
    parser.add_argument("--resource")
    parser.add_argument("--target")
    parser.add_argument("--target-explicit", action="store_true")
    parser.add_argument("--live-source-confirmed", action="store_true")
    parser.add_argument("--approval-status", choices=sorted(APPROVAL_STATUSES))
    parser.add_argument("--session-id")
    parser.add_argument("--include-redacted-message", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    record = evaluate_governed_action(
        user_message=args.message,
        action_id=args.action_id,
        resource=args.resource,
        target=args.target,
        target_explicit=args.target_explicit if args.target_explicit else None,
        live_source_confirmed=args.live_source_confirmed,
        approval_status=args.approval_status,
        session_id=args.session_id,
        include_redacted_message=args.include_redacted_message,
    )
    print(json.dumps(record.as_dict(), indent=2, ensure_ascii=False))
    return 0 if record.decision == "allow" else 3


if __name__ == "__main__":
    raise SystemExit(main())
