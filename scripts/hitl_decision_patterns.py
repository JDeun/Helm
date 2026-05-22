#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.jsonl_io import iter_jsonl_silent
from scripts.time_helpers import utc_now_iso


WORKSPACE = Path.home() / ".helm" / "workspace"
DECISION_LOG = ".helm/hitl-decisions.jsonl"
AUTOMATION_POLICY = ".helm/hitl-automation-policy.json"
DEFAULT_MIN_APPROVALS = 3
DEFAULT_MAX_REJECTS = 0
SIGNATURE_MODES = {"simple", "contextual"}


def read_jsonl(path: Path) -> list[dict]:
    """Read a JSONL decision log file.

    Uses the silent variant of the shared helper to preserve the
    historical behavior of treating decision-log corruption as a
    best-effort skip rather than a fatal stderr warning.
    """
    return list(iter_jsonl_silent(path))


def append_jsonl(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def read_json(path: Path, default: dict | None = None) -> dict:
    if not path.exists():
        return dict(default or {})
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return dict(default or {})
    return payload if isinstance(payload, dict) else dict(default or {})


def write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def action_signature(action: dict, *, mode: str = "simple") -> str:
    if mode not in SIGNATURE_MODES:
        raise ValueError(f"signature mode must be one of: {', '.join(sorted(SIGNATURE_MODES))}")
    kind = str(action.get("kind") or "unknown").strip()
    reason = str(action.get("reason") or "unknown").strip()
    if mode == "simple":
        return f"{kind}|{reason}"
    parts = [kind, reason]
    for key in ("profile", "skill", "failure_stage", "exit_code"):
        value = action.get(key)
        if value not in (None, ""):
            parts.append(f"{key}={value}")
    return "|".join(str(part) for part in parts)


def record_decision(
    *,
    workspace: Path,
    action: dict,
    decision: str,
    note: str | None = None,
    source: str = "manual",
    signature_mode: str = "simple",
) -> dict:
    if decision not in {"approve", "reject"}:
        raise ValueError("decision must be approve or reject")
    entry = {
        "ts": utc_now_iso(),
        "decision": decision,
        "action_signature": action_signature(action, mode=signature_mode),
        "signature_mode": signature_mode,
        "action_kind": action.get("kind"),
        "reason": action.get("reason"),
        "command": action.get("command"),
        "task_id": action.get("task_id"),
        "goal_id": action.get("goal_id"),
        "profile": action.get("profile"),
        "skill": action.get("skill"),
        "failure_stage": action.get("failure_stage"),
        "exit_code": action.get("exit_code"),
        "source": source,
    }
    if note:
        entry["note"] = note
    append_jsonl(workspace / DECISION_LOG, entry)
    return entry


def summarize_decisions(rows: list[dict]) -> dict:
    patterns: dict[str, dict] = {}
    for row in rows:
        signature = str(row.get("action_signature") or "")
        if not signature:
            continue
        pattern = patterns.setdefault(
            signature,
            {
                "action_signature": signature,
                "signature_mode": row.get("signature_mode") or "simple",
                "action_kind": row.get("action_kind"),
                "reason": row.get("reason"),
                "approve_count": 0,
                "reject_count": 0,
                "latest_decision_at": None,
                "examples": [],
            },
        )
        decision = row.get("decision")
        if decision == "approve":
            pattern["approve_count"] += 1
        elif decision == "reject":
            pattern["reject_count"] += 1
        pattern["latest_decision_at"] = row.get("ts") or pattern["latest_decision_at"]
        if len(pattern["examples"]) < 3:
            pattern["examples"].append(
                {
                    "decision": decision,
                    "task_id": row.get("task_id"),
                    "goal_id": row.get("goal_id"),
                    "command": row.get("command"),
                    "note": row.get("note"),
                }
            )
    return {"patterns": sorted(patterns.values(), key=lambda item: item["action_signature"])}


def automation_recommendation(
    pattern: dict | None,
    *,
    min_approvals: int = DEFAULT_MIN_APPROVALS,
    max_rejects: int = DEFAULT_MAX_REJECTS,
) -> dict:
    if not pattern:
        return {
            "status": "insufficient_history",
            "approve_count": 0,
            "reject_count": 0,
            "required_approvals": min_approvals,
            "max_rejects": max_rejects,
        }
    approve_count = int(pattern.get("approve_count") or 0)
    reject_count = int(pattern.get("reject_count") or 0)
    if approve_count >= min_approvals and reject_count <= max_rejects:
        status = "automation_candidate"
    else:
        status = "keep_hitl"
    return {
        "status": status,
        "approve_count": approve_count,
        "reject_count": reject_count,
        "required_approvals": min_approvals,
        "max_rejects": max_rejects,
    }


def default_policy() -> dict:
    return {"schema_version": 1, "approved_patterns": []}


def load_policy(workspace: Path) -> dict:
    payload = read_json(workspace / AUTOMATION_POLICY, default_policy())
    payload.setdefault("schema_version", 1)
    payload.setdefault("approved_patterns", [])
    if not isinstance(payload["approved_patterns"], list):
        payload["approved_patterns"] = []
    return payload


def approved_pattern_for(policy: dict, signature: str) -> dict | None:
    for item in policy.get("approved_patterns", []):
        if isinstance(item, dict) and item.get("action_signature") == signature and item.get("enabled", True):
            return item
    return None


def approve_pattern(
    *,
    workspace: Path,
    action_signature_value: str,
    note: str | None = None,
    approved_by: str = "human",
) -> dict:
    policy = load_policy(workspace)
    now = utc_now_iso()
    decision_summary = summarize_decisions(read_jsonl(workspace / DECISION_LOG))
    decision_pattern = next(
        (item for item in decision_summary["patterns"] if item.get("action_signature") == action_signature_value),
        None,
    )
    patterns = policy["approved_patterns"]
    existing = next(
        (item for item in patterns if isinstance(item, dict) and item.get("action_signature") == action_signature_value),
        None,
    )
    if existing is None:
        existing = {
            "action_signature": action_signature_value,
            "enabled": True,
            "approved_at": now,
            "approved_by": approved_by,
        }
        patterns.append(existing)
    else:
        existing["enabled"] = True
        existing["approved_at"] = now
        existing["approved_by"] = approved_by
    if note:
        existing["note"] = note
    existing["decision_history_snapshot"] = {
        "captured_at": now,
        "pattern": decision_pattern,
        "recommendation": automation_recommendation(decision_pattern),
    }
    write_json_atomic(workspace / AUTOMATION_POLICY, policy)
    return existing


def action_from_payload_file(path: Path, index: int) -> dict:
    payload = read_json(path)
    actions = payload.get("actions")
    if not isinstance(actions, list):
        raise ValueError("actions file must contain an actions list")
    if index < 0 or index >= len(actions):
        raise IndexError(f"action index out of range: {index}")
    action = actions[index]
    if not isinstance(action, dict):
        raise ValueError(f"actions[{index}] must be an object")
    return action


def annotate_actions_with_patterns(
    *,
    workspace: Path,
    actions: list[dict],
    min_approvals: int = DEFAULT_MIN_APPROVALS,
    max_rejects: int = DEFAULT_MAX_REJECTS,
    signature_mode: str = "simple",
) -> list[dict]:
    summary = summarize_decisions(read_jsonl(workspace / DECISION_LOG))
    by_signature = {item["action_signature"]: item for item in summary["patterns"]}
    policy = load_policy(workspace)
    annotated: list[dict] = []
    for action in actions:
        row = dict(action)
        signature = action_signature(row, mode=signature_mode)
        pattern = by_signature.get(signature)
        approved_policy = approved_pattern_for(policy, signature)
        row["decision_pattern"] = {
            "action_signature": signature,
            "signature_mode": signature_mode,
            **automation_recommendation(pattern, min_approvals=min_approvals, max_rejects=max_rejects),
        }
        row["automation_policy"] = {
            "status": "approved_for_auto" if approved_policy else "not_approved",
            "approved_pattern": approved_policy,
        }
        annotated.append(row)
    return annotated


def build_report(
    *,
    workspace: Path,
    min_approvals: int = DEFAULT_MIN_APPROVALS,
    max_rejects: int = DEFAULT_MAX_REJECTS,
) -> dict:
    summary = summarize_decisions(read_jsonl(workspace / DECISION_LOG))
    for pattern in summary["patterns"]:
        pattern["automation_recommendation"] = automation_recommendation(
            pattern,
            min_approvals=min_approvals,
            max_rejects=max_rejects,
        )
    return {
        "workspace": str(workspace),
        "decision_log": str(workspace / DECISION_LOG),
        "automation_policy": load_policy(workspace),
        "min_approvals": min_approvals,
        "max_rejects": max_rejects,
        **summary,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Record and summarize Helm human-in-the-loop decision patterns.")
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    record = subparsers.add_parser("record", help="Record one human decision for a proposed action.")
    record.add_argument("--path", default=str(WORKSPACE), help="Helm workspace path.")
    record.add_argument("--kind", required=True, help="Action kind, e.g. retry_candidate or mark_stale.")
    record.add_argument("--reason", required=True, help="Action reason used for pattern matching.")
    record.add_argument("--decision", choices=["approve", "reject"], required=True)
    record.add_argument("--signature-mode", choices=sorted(SIGNATURE_MODES), default="simple")
    record.add_argument("--command", help="Suggested command that was approved or rejected.")
    record.add_argument("--task-id")
    record.add_argument("--goal-id")
    record.add_argument("--profile")
    record.add_argument("--skill")
    record.add_argument("--failure-stage")
    record.add_argument("--exit-code")
    record.add_argument("--note")
    record.add_argument("--json", action="store_true")

    record_action = subparsers.add_parser("record-action", help="Record a decision from a JSON action index.")
    record_action.add_argument("--path", default=str(WORKSPACE), help="Helm workspace path.")
    record_action.add_argument("--actions-json", required=True, help="JSON payload containing an actions list.")
    record_action.add_argument("--index", type=int, required=True, help="Zero-based action index to record.")
    record_action.add_argument("--decision", choices=["approve", "reject"], required=True)
    record_action.add_argument("--signature-mode", choices=sorted(SIGNATURE_MODES), default="simple")
    record_action.add_argument("--note")
    record_action.add_argument("--json", action="store_true")

    approve = subparsers.add_parser("approve-policy", help="Approve an automation candidate pattern into policy.")
    approve.add_argument("--path", default=str(WORKSPACE), help="Helm workspace path.")
    approve.add_argument("--action-signature", required=True)
    approve.add_argument("--note")
    approve.add_argument("--json", action="store_true")

    report = subparsers.add_parser("report", help="Show approval/rejection patterns and automation candidates.")
    report.add_argument("--path", default=str(WORKSPACE), help="Helm workspace path.")
    report.add_argument("--min-approvals", type=int, default=DEFAULT_MIN_APPROVALS)
    report.add_argument("--max-rejects", type=int, default=DEFAULT_MAX_REJECTS)
    report.add_argument("--json", action="store_true")

    args = parser.parse_args()
    workspace = Path(args.path).expanduser()
    if args.subcommand == "record":
        payload = record_decision(
            workspace=workspace,
            action={
                "kind": args.kind,
                "reason": args.reason,
                "command": args.command,
                "task_id": args.task_id,
                "goal_id": args.goal_id,
                "profile": args.profile,
                "skill": args.skill,
                "failure_stage": args.failure_stage,
                "exit_code": args.exit_code,
            },
            decision=args.decision,
            note=args.note,
            signature_mode=args.signature_mode,
        )
    elif args.subcommand == "record-action":
        action = action_from_payload_file(Path(args.actions_json).expanduser(), args.index)
        payload = record_decision(
            workspace=workspace,
            action=action,
            decision=args.decision,
            note=args.note,
            source=f"standing_goal_action_index:{args.index}",
            signature_mode=args.signature_mode,
        )
    elif args.subcommand == "approve-policy":
        payload = approve_pattern(
            workspace=workspace,
            action_signature_value=args.action_signature,
            note=args.note,
        )
    else:
        payload = build_report(
            workspace=workspace,
            min_approvals=args.min_approvals,
            max_rejects=args.max_rejects,
        )
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        if args.subcommand in {"record", "record-action"}:
            print(f"recorded={payload['action_signature']} decision={payload['decision']}")
        elif args.subcommand == "approve-policy":
            print(f"approved_pattern={payload['action_signature']}")
        else:
            print(f"workspace={payload['workspace']}")
            for pattern in payload["patterns"]:
                recommendation = pattern["automation_recommendation"]
                print(
                    f"{pattern['action_signature']} "
                    f"approve={pattern['approve_count']} reject={pattern['reject_count']} "
                    f"status={recommendation['status']}"
                )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
