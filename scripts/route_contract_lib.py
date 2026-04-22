from __future__ import annotations

import re
import shlex
from pathlib import Path


def _command_blob(command: list[str]) -> str:
    return " ".join(str(part or "") for part in command).casefold()


def _tokens(value: object) -> set[str]:
    text = str(value or "").casefold()
    return {token for token in re.split(r"[^a-z0-9가-힣]+", text) if len(token) >= 2}


def _keyword_hit(blob: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in blob for keyword in keywords)


def _strip_env_prefix(command: list[str]) -> list[str]:
    index = 0
    while index < len(command):
        token = str(command[index] or "")
        if "=" not in token:
            break
        name, _, value = token.partition("=")
        if not name or not value:
            break
        index += 1
    return command[index:]


def infer_chosen_tool(command: list[str]) -> str | None:
    normalized_command = _strip_env_prefix(command)
    if not normalized_command:
        return None
    candidate = Path(str(normalized_command[0])).name
    if candidate.startswith("python") and len(normalized_command) >= 3 and str(normalized_command[1]) == "-m":
        return str(normalized_command[2]) or None
    if candidate.startswith("python") and len(normalized_command) >= 2 and str(normalized_command[1]) == "-c":
        return "python-inline"
    if candidate.startswith("python") and len(normalized_command) >= 2:
        return Path(str(normalized_command[1])).name
    if candidate in {"bash", "zsh", "sh"} and len(normalized_command) >= 3 and str(normalized_command[1]) in {"-c", "-lc"}:
        nested = shlex.split(str(normalized_command[2]))
        return infer_chosen_tool(nested) if nested else candidate
    if candidate in {"bash", "zsh", "sh"} and len(normalized_command) >= 2:
        return Path(str(normalized_command[1])).name
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


def infer_interaction_workflow(*, request: str | None, task_name: str | None = None, command: list[str] | None = None) -> dict:
    blob = f"{task_name or ''} {request or ''} {_command_blob(command or [])}".casefold()
    divergence_keywords = (
        "plan",
        "design",
        "architecture",
        "proposal",
        "strategy",
        "compare",
        "draft",
        "기획",
        "설계",
        "방향",
        "비교",
        "초안",
        "제안",
    )
    convergence_keywords = (
        "fix",
        "apply",
        "save",
        "run",
        "execute",
        "implement",
        "patch",
        "update",
        "commit",
        "고쳐",
        "수정",
        "적용",
        "저장",
        "실행",
        "구현",
        "진행",
    )
    divergence = _keyword_hit(blob, divergence_keywords)
    convergence = _keyword_hit(blob, convergence_keywords)
    if divergence and not convergence:
        return {
            "mode": "diverge_then_converge",
            "stage": "diverge",
            "next_action": "present_options",
            "reason": "request looks like planning, design, comparison, or drafting work",
        }
    if divergence and convergence:
        return {
            "mode": "converge_with_tradeoff_note",
            "stage": "execute",
            "next_action": "run",
            "reason": "request contains design/planning terms but also asks for execution",
        }
    return {
        "mode": "converge",
        "stage": "execute",
        "next_action": "run",
        "reason": "request looks execution-oriented or narrow enough to handle directly",
    }


def score_skill_relevance(
    *,
    skill: str | None,
    profile: str,
    contract: dict,
    request: str | None,
    task_name: str | None,
    command: list[str],
) -> dict:
    if not skill:
        return {
            "score": 100,
            "verdict": "direct",
            "reasons": ["no skill selected; direct handling is allowed"],
            "fallback": None,
        }

    blob = f"{task_name or ''} {request or ''} {_command_blob(command)}".casefold()
    reasons: list[str] = []
    score = 0

    skill_tokens = _tokens(str(skill).replace("-", " "))
    request_tokens = _tokens(blob)
    overlap = sorted(skill_tokens & request_tokens)
    if overlap:
        score += 35
        reasons.append(f"skill name overlaps request tokens: {', '.join(overlap[:5])}")

    route_contract = contract.get("route_decision") or {}
    task_type = str(route_contract.get("task_type") or "")
    if task_type and task_type != "generic" and task_type.casefold() in blob:
        score += 25
        reasons.append(f"route task_type `{task_type}` appears in request")

    context = contract.get("context") or {}
    context_terms = _tokens(" ".join([str(context.get("query") or ""), *[str(item) for item in context.get("include", [])]]))
    context_overlap = sorted(context_terms & request_tokens)
    if context_overlap:
        score += 20
        reasons.append(f"context terms overlap request: {', '.join(context_overlap[:5])}")

    runner = contract.get("runner") or {}
    entrypoint = str(runner.get("entrypoint") or "")
    if entrypoint and entrypoint in " ".join(command):
        score += 25
        reasons.append(f"command uses preferred runner `{entrypoint}`")

    allowed_profiles = [str(item) for item in contract.get("allowed_profiles", [])]
    if allowed_profiles and profile in allowed_profiles:
        score += 10
        reasons.append(f"profile `{profile}` is allowed by the skill policy")

    if not reasons:
        reasons.append("no request, context, runner, or profile signal supports the selected skill")

    if score >= 50:
        verdict = "strong"
        fallback = None
    elif score >= 25:
        verdict = "weak"
        fallback = "Proceed only if the operator has external context; otherwise use direct handling or a higher-level orchestration skill."
    else:
        verdict = "poor"
        fallback = "Do not force this request through the selected skill; use direct handling or choose a more relevant orchestration skill."

    return {
        "score": min(score, 100),
        "verdict": verdict,
        "reasons": reasons,
        "fallback": fallback,
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
