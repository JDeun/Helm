#!/usr/bin/env python3
"""Deterministic command risk classifier and profile enforcement guard.

Must not execute any command, call any remote API, or import heavy ML libraries.
All classification is pure string matching against a loaded policy.
"""
from __future__ import annotations

import json
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

# ---------------------------------------------------------------------------
# Public type aliases
# ---------------------------------------------------------------------------

GuardAction = Literal["allow", "warn", "require_approval", "deny"]

RiskCategory = Literal[
    "read",
    "write",
    "network",
    "privilege",
    "destructive",
    "remote",
    "unknown",
    "profile_mismatch",
]

# ---------------------------------------------------------------------------
# Detection sets
# ---------------------------------------------------------------------------

WRITE_COMMANDS: frozenset[str] = frozenset(
    {"touch", "mkdir", "cp", "mv", "rm", "tee", "dd", "install", "rsync", "sed", "awk", "patch"}
)
NETWORK_COMMANDS: frozenset[str] = frozenset(
    {"curl", "wget", "ssh", "scp", "rsync", "ftp", "sftp", "nc", "netcat", "ping", "nmap"}
)
DESTRUCTIVE_PATTERNS: frozenset[str] = frozenset(
    {"rm -rf", "rm -r", "git clean", "git reset --hard", "docker system prune"}
)
PRIVILEGE_COMMANDS: frozenset[str] = frozenset({"sudo", "su", "doas", "runas"})
REMOTE_COMMANDS: frozenset[str] = frozenset({"ssh", "scp", "rsync", "kubectl", "docker exec"})
READ_ONLY_COMMANDS: frozenset[str] = frozenset(
    {
        "cat", "head", "tail", "less", "more", "grep", "find", "ls", "pwd",
        "echo", "git status", "git log", "git diff", "git show",
        "wc", "file", "stat", "tree", "which", "type", "env", "printenv",
    }
)
SHELL_WRAPPERS: frozenset[str] = frozenset({"bash", "sh", "zsh", "fish", "cmd", "powershell", "pwsh"})
SHELL_EXEC_FLAGS: frozenset[str] = frozenset({"-c", "-lc", "/c", "-Command"})

# ---------------------------------------------------------------------------
# Built-in policy fallback (mirrors guard_policy.json)
# ---------------------------------------------------------------------------

_BUILTIN_ABSOLUTE_DENY: list[dict[str, Any]] = [
    {"id": "deny.rm_root", "patterns": ["rm -rf /", "rm -rf /*", "sudo rm -rf /", "sudo rm -rf /*"]},
    {"id": "deny.rm_home", "patterns": ["rm -rf ~", "rm -rf $HOME", 'rm -rf "$HOME"', "sudo rm -rf ~"]},
    {"id": "deny.rm_parent_escape", "patterns": ["rm -rf ../..", "rm -rf ../../.."]},
    {"id": "deny.dd_device", "patterns": ["dd if=", "sudo dd if="]},
    {"id": "deny.mkfs", "patterns": ["mkfs", "diskutil eraseDisk", "sudo mkfs"]},
    {"id": "deny.fork_bomb", "patterns": [":(){ :|:& };:", ":(){ :|:& };"]},
    {"id": "deny.chmod_root", "patterns": ["chmod -R 777 /", "sudo chmod -R 777 /"]},
    {"id": "deny.sudo_destructive", "patterns": ["sudo rm -rf", "sudo dd", "sudo mkfs"]},
    {"id": "deny.format_disk_win", "patterns": ["format C:", "format D:", "diskpart"]},
    {"id": "deny.del_system_win", "patterns": [
        "del /s /q C:\\Windows", "rmdir /s /q C:\\Windows", "del /s /q C:\\",
    ]},
]

_BUILTIN_REQUIRE_APPROVAL: list[dict[str, Any]] = [
    {"id": "risk.rm_recursive_workspace", "patterns": ["rm -rf"], "recommended_profile": "risky_edit"},
    {"id": "risk.find_delete", "patterns": ["find . -delete", "find . -exec rm"], "recommended_profile": "risky_edit"},
    {"id": "risk.git_clean", "patterns": ["git clean -fd", "git clean -fdx"], "recommended_profile": "risky_edit"},
    {"id": "risk.git_reset_hard", "patterns": ["git reset --hard"], "recommended_profile": "risky_edit"},
    {"id": "risk.chmod_recursive", "patterns": ["chmod -R"], "recommended_profile": "risky_edit"},
    {"id": "risk.chown_recursive", "patterns": ["chown -R"], "recommended_profile": "risky_edit"},
    {"id": "risk.curl_pipe_shell", "patterns": ["curl|sh", "curl|bash", "wget|sh", "wget|bash"], "recommended_profile": "service_ops"},
    {"id": "risk.sudo", "patterns": ["sudo"], "recommended_profile": "risky_edit"},
    {"id": "risk.remote_shell", "patterns": ["ssh"], "recommended_profile": "remote_handoff"},
    {"id": "risk.docker_prune", "patterns": ["docker system prune", "docker volume prune"], "recommended_profile": "risky_edit"},
    {"id": "risk.package_install", "patterns": ["pip install", "npm install", "brew install", "apt install", "apt-get install"], "recommended_profile": "service_ops"},
    {"id": "risk.del_recursive_win", "patterns": ["del /s", "rmdir /s"], "recommended_profile": "risky_edit"},
]

# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CommandClassification:
    normalized_command: str
    argv: list[str]
    shell_wrapped: bool
    shell_inner_command: str | None
    categories: list[RiskCategory]
    matched_rules: list[str]
    writes_detected: bool
    network_detected: bool
    destructive_detected: bool
    privilege_detected: bool
    remote_detected: bool
    target_paths: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class GuardDecision:
    action: GuardAction
    risk_score: float
    selected_profile: str
    recommended_profile: str | None
    reasons: list[str]
    matched_rules: list[str]
    classification: CommandClassification
    approval_required: bool
    approval_hint: str | None = None


# ---------------------------------------------------------------------------
# Policy loading
# ---------------------------------------------------------------------------

def _load_policy(policy_path: Path | None) -> tuple[list[dict], list[dict]]:
    """Return (absolute_deny_rules, require_approval_rules).

    Falls back to built-in defaults on any error.
    """
    candidates: list[Path] = []
    if policy_path is not None:
        candidates.append(policy_path)

    # Default location relative to this file's grandparent (repo root)
    default = Path(__file__).resolve().parents[1] / "references" / "guard_policy.json"
    candidates.append(default)

    for path in candidates:
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("policy root must be a JSON object")
            deny = data.get("absolute_deny", [])
            approval = data.get("require_approval", [])
            if not isinstance(deny, list) or not isinstance(approval, list):
                raise ValueError("absolute_deny and require_approval must be arrays")
            return deny, approval
        except Exception:
            # Malformed — fall through to built-in defaults
            continue

    return _BUILTIN_ABSOLUTE_DENY, _BUILTIN_REQUIRE_APPROVAL


# ---------------------------------------------------------------------------
# Classification helpers
# ---------------------------------------------------------------------------

def _normalize(argv: list[str]) -> str:
    """Join argv into a single string for pattern matching."""
    return " ".join(argv)


def _extract_shell_inner(argv: list[str]) -> str | None:
    """If this is a shell wrapper invocation, return the inner command string."""
    if not argv:
        return None
    if argv[0].lower() not in SHELL_WRAPPERS:
        return None
    for i, arg in enumerate(argv[1:], start=1):
        if arg in SHELL_EXEC_FLAGS and i + 1 < len(argv):
            return argv[i + 1]
    return None


def _effective_argv(argv: list[str]) -> tuple[list[str], bool, str | None]:
    """Return (effective_argv, shell_wrapped, inner_command_str)."""
    inner_str = _extract_shell_inner(argv)
    if inner_str is not None:
        try:
            inner_argv = shlex.split(inner_str)
        except ValueError:
            inner_argv = inner_str.split()
        return inner_argv, True, inner_str
    return argv, False, None


def _pipe_pattern_matches(text_lower: str, pattern_lower: str) -> bool:
    """Check patterns like 'curl|sh' against text that may have args between pipe sides.

    'curl|sh' matches 'curl https://example.com/script.sh | sh' because
    curl appears before the pipe and sh appears after it.
    """
    if "|" not in pattern_lower:
        return False
    left, _, right = pattern_lower.partition("|")
    left = left.strip()
    right = right.strip()
    # Split text on pipe character and check each side
    pipe_parts = text_lower.split("|")
    if len(pipe_parts) < 2:
        return False
    for i in range(len(pipe_parts) - 1):
        before = pipe_parts[i].strip()
        after = pipe_parts[i + 1].strip()
        # left command must appear at start of the before-pipe segment (as first word)
        before_words = before.split()
        after_words = after.split()
        if before_words and before_words[0] == left and after_words and after_words[0] == right:
            return True
    return False


def _match_patterns(text: str, rules: list[dict]) -> list[tuple[str, str | None]]:
    """Return list of (rule_id, recommended_profile | None) for each matching rule."""
    matches: list[tuple[str, str | None]] = []
    text_lower = text.lower()
    for rule in rules:
        rule_id: str = rule.get("id", "")
        recommended: str | None = rule.get("recommended_profile")
        for pattern in rule.get("patterns", []):
            pat_lower = pattern.lower()
            if pat_lower in text_lower:
                matches.append((rule_id, recommended))
                break
            if "|" in pat_lower and _pipe_pattern_matches(text_lower, pat_lower):
                matches.append((rule_id, recommended))
                break
    return matches


def _classify_argv(
    effective_argv: list[str],
    normalized: str,
    shell_wrapped: bool,
    shell_inner: str | None,
    original_argv: list[str],
) -> CommandClassification:
    """Classify an (already unwrapped) command."""
    cmd0 = effective_argv[0].lower() if effective_argv else ""

    writes_detected = cmd0 in WRITE_COMMANDS
    network_detected = cmd0 in NETWORK_COMMANDS
    privilege_detected = cmd0 in PRIVILEGE_COMMANDS
    remote_detected = any(rc.split()[0] == cmd0 for rc in REMOTE_COMMANDS)

    # Check destructive patterns against full normalized string
    destructive_detected = any(pat.lower() in normalized.lower() for pat in DESTRUCTIVE_PATTERNS)

    # Also check for privilege/write/network in multi-word effective_argv
    full_norm = normalized.lower()
    if not privilege_detected:
        privilege_detected = any(pc in full_norm.split() for pc in PRIVILEGE_COMMANDS)
    if not network_detected:
        network_detected = any(nc in full_norm.split() for nc in NETWORK_COMMANDS)
    if not writes_detected:
        writes_detected = any(wc in full_norm.split() for wc in WRITE_COMMANDS)

    # Extract target paths: everything in argv that looks like a path (heuristic)
    target_paths: list[str] = [
        arg for arg in effective_argv[1:]
        if not arg.startswith("-") and ("/" in arg or "\\" in arg or arg in ("~", "..", "."))
    ]

    # Build categories
    categories: list[RiskCategory] = []

    is_read_only_cmd = (
        cmd0 in {r.split()[0] for r in READ_ONLY_COMMANDS}
        or any(full_norm.startswith(ro.lower()) for ro in READ_ONLY_COMMANDS)
    )

    if is_read_only_cmd and not writes_detected and not network_detected and not privilege_detected:
        categories.append("read")
    if writes_detected:
        categories.append("write")
    if network_detected:
        categories.append("network")
    if privilege_detected:
        categories.append("privilege")
    if destructive_detected:
        categories.append("destructive")
    if remote_detected:
        categories.append("remote")
    if not categories:
        categories.append("unknown")

    return CommandClassification(
        normalized_command=normalized,
        argv=original_argv,
        shell_wrapped=shell_wrapped,
        shell_inner_command=shell_inner,
        categories=categories,
        matched_rules=[],  # filled in by caller
        writes_detected=writes_detected,
        network_detected=network_detected,
        destructive_detected=destructive_detected,
        privilege_detected=privilege_detected,
        remote_detected=remote_detected,
        target_paths=target_paths,
    )


# ---------------------------------------------------------------------------
# Risk score
# ---------------------------------------------------------------------------

def _compute_risk_score(
    classification: CommandClassification,
    absolute_deny: bool,
    profile_mismatch: bool,
    profile: str,
) -> float:
    if absolute_deny:
        return 1.0

    score = 0.0
    c = classification

    if c.writes_detected:
        score += 0.35
    if c.network_detected:
        score += 0.30
    if c.remote_detected:
        score += 0.45
    if c.privilege_detected:
        score += 0.65
    if c.destructive_detected:
        score += 0.75

    # Recursive destructive (already covered by destructive, add extra)
    norm_lower = c.normalized_command.lower()
    if "rm -rf" in norm_lower or "rm -r " in norm_lower:
        score += 0.10  # total 0.85 with destructive

    # Workspace escape
    if any(p in norm_lower for p in ["../", "..\\", "~", "$home"]):
        score += 0.30

    if profile_mismatch:
        score += 0.25

    # Unknown script under inspect_local
    if profile == "inspect_local" and "unknown" in classification.categories:
        score += 0.35

    return min(score, 1.0)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def evaluate_command_guard(
    *,
    command: list[str],
    selected_profile: str,
    profiles: dict[str, dict[str, Any]],
    workspace: Path,
    task_name: str | None = None,
    task_goal: str | None = None,
    metadata: dict[str, Any] | None = None,
    policy_path: Path | None = None,
) -> GuardDecision:
    """Evaluate a command against the guard policy.

    Must not execute the command. Must not call any remote API.
    Must not import heavy ML libraries.
    """
    absolute_deny_rules, require_approval_rules = _load_policy(policy_path)

    # Unwrap shell wrappers
    effective_argv, shell_wrapped, shell_inner = _effective_argv(command)

    # Build normalized string from effective argv for pattern matching
    # Also include the raw inner command string for pipe-pattern detection
    normalized = _normalize(effective_argv)
    match_text = normalized
    if shell_inner is not None:
        match_text = shell_inner  # use original inner string to catch pipe patterns

    # Check absolute deny first
    deny_matches = _match_patterns(match_text, absolute_deny_rules)
    # Also check against full original normalized in case sudo prefix is in outer argv
    original_norm = _normalize(command)
    if not deny_matches:
        deny_matches = _match_patterns(original_norm, absolute_deny_rules)

    is_absolute_deny = len(deny_matches) > 0

    # Check require_approval rules
    approval_matches = _match_patterns(match_text, require_approval_rules)
    if not approval_matches:
        approval_matches = _match_patterns(original_norm, require_approval_rules)

    # Classify using effective argv
    classification = _classify_argv(
        effective_argv=effective_argv,
        normalized=match_text,
        shell_wrapped=shell_wrapped,
        shell_inner=shell_inner,
        original_argv=command,
    )

    # Profile mismatch detection
    profile_cfg = profiles.get(selected_profile, {})
    writes_allowed = profile_cfg.get("writes_allowed", True)
    network_allowed = profile_cfg.get("network_allowed", True)

    profile_mismatch = (
        (classification.writes_detected and not writes_allowed)
        or (classification.network_detected and not network_allowed)
    )

    # Compute risk score
    risk_score = _compute_risk_score(
        classification=classification,
        absolute_deny=is_absolute_deny,
        profile_mismatch=profile_mismatch,
        profile=selected_profile,
    )

    # Collect all matched rule ids
    all_matched_rules: list[str] = (
        [r[0] for r in deny_matches] + [r[0] for r in approval_matches]
    )

    # Determine recommended profile from first approval match
    recommended_profile: str | None = None
    for _, rec in approval_matches:
        if rec:
            recommended_profile = rec
            break

    # Build reasons
    reasons: list[str] = []
    if is_absolute_deny:
        reasons.append(f"absolute_deny matched: {[r[0] for r in deny_matches]}")
    if classification.writes_detected and not writes_allowed:
        reasons.append(f"writes not allowed under profile '{selected_profile}'")
    if classification.network_detected and not network_allowed:
        reasons.append(f"network not allowed under profile '{selected_profile}'")
    if classification.destructive_detected:
        reasons.append("destructive pattern detected")
    if classification.privilege_detected:
        reasons.append("privilege escalation detected")
    if approval_matches:
        reasons.append(f"require_approval rules matched: {[r[0] for r in approval_matches]}")

    # ---------------------------------------------------------------------------
    # Action decision (PRD Section 8.8)
    # ---------------------------------------------------------------------------
    action: GuardAction
    approval_required = False
    approval_hint: str | None = None

    if is_absolute_deny:
        action = "deny"

    elif selected_profile == "inspect_local" and (
        classification.writes_detected or classification.network_detected
    ):
        action = "deny"

    elif selected_profile == "workspace_edit" and classification.network_detected:
        action = "deny"

    elif selected_profile == "workspace_edit" and classification.destructive_detected:
        action = "require_approval"
        recommended_profile = recommended_profile or "risky_edit"
        approval_required = True
        approval_hint = "--approve-risk"

    elif selected_profile == "risky_edit" and classification.destructive_detected:
        action = "require_approval"
        approval_required = True
        approval_hint = "--approve-risk"

    elif selected_profile == "service_ops" and classification.destructive_detected:
        action = "require_approval"
        recommended_profile = recommended_profile or "risky_edit"
        approval_required = True
        approval_hint = "--approve-risk"

    elif selected_profile == "remote_handoff":
        action = "require_approval"
        approval_required = True
        approval_hint = "--approve-risk"

    elif approval_matches:
        # A require_approval policy rule matched but no profile rule caught it above.
        # Conservative default: require_approval rather than warn/allow.
        action = "require_approval"
        approval_required = True
        approval_hint = "--approve-risk"

    elif risk_score >= 0.95:
        action = "deny"

    elif risk_score >= 0.70:
        action = "require_approval"
        approval_required = True
        approval_hint = "--approve-risk"

    elif risk_score >= 0.35:
        action = "warn"

    else:
        action = "allow"

    # Rebuild classification with matched_rules populated
    classification = CommandClassification(
        normalized_command=classification.normalized_command,
        argv=classification.argv,
        shell_wrapped=classification.shell_wrapped,
        shell_inner_command=classification.shell_inner_command,
        categories=classification.categories,
        matched_rules=all_matched_rules,
        writes_detected=classification.writes_detected,
        network_detected=classification.network_detected,
        destructive_detected=classification.destructive_detected,
        privilege_detected=classification.privilege_detected,
        remote_detected=classification.remote_detected,
        target_paths=classification.target_paths,
    )

    return GuardDecision(
        action=action,
        risk_score=risk_score,
        selected_profile=selected_profile,
        recommended_profile=recommended_profile,
        reasons=reasons,
        matched_rules=all_matched_rules,
        classification=classification,
        approval_required=approval_required,
        approval_hint=approval_hint,
    )


def decision_to_json(decision: GuardDecision) -> dict[str, Any]:
    """Convert GuardDecision to JSON-serializable dict."""
    c = decision.classification
    return {
        "action": decision.action,
        "risk_score": decision.risk_score,
        "selected_profile": decision.selected_profile,
        "recommended_profile": decision.recommended_profile,
        "reasons": decision.reasons,
        "matched_rules": decision.matched_rules,
        "approval_required": decision.approval_required,
        "approval_hint": decision.approval_hint,
        "classification": {
            "normalized_command": c.normalized_command,
            "argv": c.argv,
            "shell_wrapped": c.shell_wrapped,
            "shell_inner_command": c.shell_inner_command,
            "categories": c.categories,
            "matched_rules": c.matched_rules,
            "writes_detected": c.writes_detected,
            "network_detected": c.network_detected,
            "destructive_detected": c.destructive_detected,
            "privilege_detected": c.privilege_detected,
            "remote_detected": c.remote_detected,
            "target_paths": c.target_paths,
        },
    }
