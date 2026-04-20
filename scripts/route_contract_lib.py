from __future__ import annotations

from pathlib import Path


def _command_blob(command: list[str]) -> str:
    return " ".join(str(part or "") for part in command).casefold()


def infer_chosen_tool(command: list[str]) -> str | None:
    if not command:
        return None
    candidate = Path(str(command[0])).name
    if candidate.startswith("python") and len(command) >= 2:
        return Path(str(command[1])).name
    if candidate in {"bash", "zsh", "sh"} and len(command) >= 2:
        return Path(str(command[1])).name
    return candidate or None


def infer_route_decision(*, command: list[str], request: str | None, contract: dict) -> dict | None:
    route_contract = contract.get("route_decision") or {}
    if not isinstance(route_contract, dict) or not route_contract:
        return None
    request_blob = str(request or "").casefold()
    risk_flags: list[str] = []
    if any(token in request_blob for token in ("book", "buy", "pay", "sign", "approval")):
        risk_flags.append("approval_sensitive")
    return {
        "task_type": str(route_contract.get("task_type") or "generic"),
        "stage": "execute",
        "missing_inputs": [],
        "chosen_tool": infer_chosen_tool(command),
        "risk_flags": risk_flags,
        "hitl_required": "approval_sensitive" in risk_flags,
        "next_action": "run",
    }


def validate_route_decision(payload: dict | None, contract: dict, *, command: list[str]) -> tuple[bool, str]:
    route_contract = contract.get("route_decision") or {}
    if not isinstance(route_contract, dict) or not route_contract:
        return True, "route decision not required"
    explicitly_required = bool(route_contract.get("required"))
    has_rules = bool(route_contract.get("tool_rules"))
    if not explicitly_required and not has_rules:
        return True, "route decision not required"
    if payload is None:
        return False, "route_decision missing"
    required_fields = [str(item) for item in route_contract.get("required_fields", []) if str(item).strip()]
    missing = [field for field in required_fields if payload.get(field) in (None, "", [])]
    if missing:
        return False, f"route_decision missing required fields: {', '.join(missing)}"
    allowed_stages = [str(item) for item in route_contract.get("allowed_stages", []) if str(item).strip()]
    if allowed_stages and str(payload.get("stage") or "") not in allowed_stages:
        return False, f"route_decision.stage must be one of: {', '.join(allowed_stages)}"
    allowed_next = [str(item) for item in route_contract.get("allowed_next_actions", []) if str(item).strip()]
    if allowed_next and str(payload.get("next_action") or "") not in allowed_next:
        return False, f"route_decision.next_action must be one of: {', '.join(allowed_next)}"
    chosen_tool = str(payload.get("chosen_tool") or "")
    tool_rules = route_contract.get("tool_rules") or []
    if tool_rules:
        chosen_blob = chosen_tool.casefold()
        command_blob = _command_blob(command)
        matched = False
        for rule in tool_rules:
            hints = [str(item).casefold() for item in (rule.get("match_any") or []) if str(item).strip()]
            if hints and any(hint in chosen_blob or hint in command_blob for hint in hints):
                matched = True
                break
        if not matched:
            return False, f"route_decision.chosen_tool `{chosen_tool}` does not match the skill routing table"
    return True, "route_decision satisfied"


def applicable_downgrade_steps(contract: dict, trigger: str) -> list[dict]:
    steps = ((contract.get("failure_downgrade") or {}).get("steps") or [])
    return [step for step in steps if isinstance(step, dict) and str(step.get("when") or "") == trigger]
