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
    "database",
    "cloud",
    "package_publish",
    "credential_exposure",
    "process",
    "firewall",
    "cron",
    "heredoc_input",
    "base64_pipe",
    "network_detected",
    "dev_tcp_bypass",
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
        "wc", "file", "stat", "tree", "which", "type",
    }
)
SHELL_WRAPPERS: frozenset[str] = frozenset({"bash", "sh", "zsh", "fish", "cmd", "powershell", "pwsh"})
SHELL_EXEC_FLAGS: frozenset[str] = frozenset({"-c", "-lc", "/c", "-Command"})

DATABASE_COMMANDS: frozenset[str] = frozenset(
    {"psql", "mysql", "mongo", "mongosh", "redis-cli", "dropdb", "mysqladmin", "sqlite3", "cockroach"}
)
DATABASE_DESTRUCTIVE_SUBCMDS: frozenset[str] = frozenset(
    {"drop", "delete", "flush", "flushall", "flushdb", "rm", "destroy"}
)
CLOUD_CLI_COMMANDS: frozenset[str] = frozenset(
    {"aws", "gcloud", "az", "terraform", "kubectl", "pulumi", "cdk", "sam", "serverless"}
)
CLOUD_DESTRUCTIVE_SUBCMDS: frozenset[str] = frozenset(
    {"delete", "destroy", "terminate", "rm", "rb", "-auto-approve"}
)
PACKAGE_PUBLISH_COMMANDS: frozenset[str] = frozenset(
    {"npm", "twine", "cargo", "gem", "docker"}
)
PACKAGE_PUBLISH_SUBCMDS: frozenset[str] = frozenset(
    {"publish", "push", "upload"}
)
CREDENTIAL_EXPOSURE_COMMANDS: frozenset[str] = frozenset(
    {"env", "printenv", "set"}
)
PROCESS_COMMANDS: frozenset[str] = frozenset(
    {"kill", "killall", "pkill", "systemctl", "service", "launchctl"}
)
PROCESS_DESTRUCTIVE_SUBCMDS: frozenset[str] = frozenset(
    {"stop", "disable", "mask"}
)
FIREWALL_COMMANDS: frozenset[str] = frozenset(
    {"iptables", "ufw", "netsh", "firewall-cmd"}
)
CRON_COMMANDS: frozenset[str] = frozenset(
    {"crontab", "at", "schtasks"}
)

# ---------------------------------------------------------------------------
# Flag normalization
# ---------------------------------------------------------------------------

_LONG_TO_SHORT: dict[str, str] = {
    "--recursive": "-r",
    "--force": "-f",
    "--verbose": "-v",
    "--all": "-a",
    "--quiet": "-q",
}


def _normalize_flags(argv: list[str]) -> list[str]:
    """Normalize long flags to short equivalents, then merge consecutive short flags."""
    result: list[str] = []
    for arg in argv:
        result.append(_LONG_TO_SHORT.get(arg, arg))
    # Merge consecutive single-char flags: ["-r", "-f"] -> ["-rf"]
    merged: list[str] = []
    i = 0
    while i < len(result):
        if result[i].startswith("-") and not result[i].startswith("--") and len(result[i]) == 2:
            combo = result[i]
            while i + 1 < len(result) and result[i + 1].startswith("-") and not result[i + 1].startswith("--") and len(result[i + 1]) == 2:
                combo += result[i + 1][1]
                i += 1
            merged.append(combo)
        else:
            merged.append(result[i])
        i += 1
    return merged


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
# Fail-closed policy constant
# ---------------------------------------------------------------------------

_FAIL_CLOSED_POLICY: tuple[list[dict], list[dict]] = (
    _BUILTIN_ABSOLUTE_DENY,  # keep absolute deny even on corruption
    [{"id": "fail_closed.all", "patterns": [""]}],  # matches everything → require_approval
)

# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CommandClassification:
    normalized_command: str
    argv: tuple[str, ...]
    shell_wrapped: bool
    shell_inner_command: str | None
    categories: tuple[RiskCategory, ...]
    matched_rules: tuple[str, ...]
    writes_detected: bool
    network_detected: bool
    destructive_detected: bool
    privilege_detected: bool
    remote_detected: bool
    target_paths: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class GuardDecision:
    action: GuardAction
    risk_score: float
    selected_profile: str
    recommended_profile: str | None
    reasons: tuple[str, ...]
    matched_rules: tuple[str, ...]
    classification: CommandClassification
    approval_required: bool
    approval_hint: str | None = None
    score_breakdown: dict[str, float] = field(default_factory=dict)
    workspace: str | None = None
    task_name: str | None = None
    task_goal: str | None = None
    policy_version: int = 1
    evaluated_at: str = ""


# ---------------------------------------------------------------------------
# Policy loading
# ---------------------------------------------------------------------------

def _load_policy(policy_path: Path | None) -> tuple[list[dict], list[dict], int]:
    """Return (absolute_deny_rules, require_approval_rules, policy_version).
    Fail-closed on corruption or unknown version. Built-in fallback only when no file found.
    """
    import warnings as _warnings
    candidates: list[Path] = []
    if policy_path is not None:
        candidates.append(policy_path)
    default = Path(__file__).resolve().parents[1] / "references" / "guard_policy.json"
    candidates.append(default)

    for path in candidates:
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("policy root must be a JSON object")
            version = data.get("version")
            if version != 1:
                _warnings.warn(f"Unknown guard policy version {version}, using fail-closed policy")
                return _FAIL_CLOSED_POLICY[0], _FAIL_CLOSED_POLICY[1], 0
            deny = data.get("absolute_deny", [])
            approval = data.get("require_approval", [])
            if not isinstance(deny, list) or not isinstance(approval, list):
                raise ValueError("absolute_deny and require_approval must be arrays")
            return deny, approval, version
        except Exception:
            _warnings.warn("Malformed guard policy, using fail-closed policy")
            return _FAIL_CLOSED_POLICY[0], _FAIL_CLOSED_POLICY[1], 0

    return _BUILTIN_ABSOLUTE_DENY, _BUILTIN_REQUIRE_APPROVAL, 1


# ---------------------------------------------------------------------------
# Classification helpers
# ---------------------------------------------------------------------------

INTERPRETER_WRAPPERS: frozenset[str] = frozenset({"python3", "python", "perl", "ruby", "node"})
INTERPRETER_EXEC_FLAGS: frozenset[str] = frozenset({"-c", "-e"})


def _extract_interpreter_inner(argv: list[str]) -> str | None:
    if not argv or argv[0].lower() not in INTERPRETER_WRAPPERS:
        return None
    for i, arg in enumerate(argv[1:], start=1):
        if arg in INTERPRETER_EXEC_FLAGS and i + 1 < len(argv):
            return argv[i + 1]
    return None


def _extract_commands_from_interpreter(code: str) -> list[str]:
    """Extract shell command strings from interpreter code."""
    import re
    commands: list[str] = []
    for pattern in [r"system\(['\"]([^'\"]+)['\"]\)",
                    r"subprocess\.\w+\(\[?['\"]([^'\"]+)['\"]\]?",
                    r"exec\(['\"]([^'\"]+)['\"]\)",
                    r"child_process[^'\"]*['\"]([^'\"]+)['\"]"]:
        for match in re.finditer(pattern, code):
            commands.append(match.group(1))
    return commands


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
    # Shell wrappers first
    inner_str = _extract_shell_inner(argv)
    if inner_str is not None:
        try:
            inner_argv = shlex.split(inner_str)
        except ValueError:
            inner_argv = inner_str.split()
        return inner_argv, True, inner_str
    # Interpreter wrappers
    interp_inner = _extract_interpreter_inner(argv)
    if interp_inner is not None:
        extracted = _extract_commands_from_interpreter(interp_inner)
        if extracted:
            try:
                inner_argv = shlex.split(extracted[0])
            except ValueError:
                inner_argv = extracted[0].split()
            return inner_argv, True, extracted[0]
        return argv, True, interp_inner
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
    import re
    matches: list[tuple[str, str | None]] = []
    text_lower = text.lower()
    for rule in rules:
        rule_id: str = rule.get("id", "")
        recommended: str | None = rule.get("recommended_profile")
        rule_type: str = rule.get("type", "substring")
        for pattern in rule.get("patterns", []):
            if rule_type == "regex":
                try:
                    if re.search(pattern, text, re.IGNORECASE):
                        matches.append((rule_id, recommended))
                        break
                except re.error:
                    continue
            else:
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
    target_paths_list: list[str] = [
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

    # --- v2.1 new category detection ---
    subcmds_lower = [a.lower() for a in effective_argv[1:]]

    if cmd0 in DATABASE_COMMANDS:
        # Also tokenize each arg so "-c DROP DATABASE mydb" is checked word-by-word
        subcmds_words = [w for s in subcmds_lower for w in s.split()]
        has_destructive_sub = any(s in DATABASE_DESTRUCTIVE_SUBCMDS for s in subcmds_words)
        if has_destructive_sub:
            categories.append("database")
            destructive_detected = True

    if cmd0 in CLOUD_CLI_COMMANDS:
        has_destructive_sub = any(s in CLOUD_DESTRUCTIVE_SUBCMDS for s in subcmds_lower)
        if has_destructive_sub:
            categories.append("cloud")
            destructive_detected = True

    if cmd0 in PACKAGE_PUBLISH_COMMANDS:
        has_publish_sub = any(s in PACKAGE_PUBLISH_SUBCMDS for s in subcmds_lower)
        if has_publish_sub:
            categories.append("package_publish")
            network_detected = True

    if cmd0 in CREDENTIAL_EXPOSURE_COMMANDS:
        categories.append("credential_exposure")

    if cmd0 in PROCESS_COMMANDS:
        categories.append("process")
        if cmd0 in {"systemctl", "service", "launchctl"}:
            if any(s in PROCESS_DESTRUCTIVE_SUBCMDS for s in subcmds_lower):
                destructive_detected = True

    if cmd0 in FIREWALL_COMMANDS:
        categories.append("firewall")

    if cmd0 in CRON_COMMANDS:
        categories.append("cron")
        if "-r" in effective_argv[1:]:
            destructive_detected = True

    # Heredoc detection
    if "<<" in effective_argv:
        shell_wrapped = True
        if "heredoc_input" not in categories:
            categories.append("heredoc_input")

    # base64 pipe detection
    if shell_inner:
        inner_lower = shell_inner.lower()
        if "base64" in inner_lower and "|" in inner_lower:
            pipe_parts = inner_lower.split("|")
            if len(pipe_parts) >= 2:
                last_cmd = pipe_parts[-1].strip().split()[0] if pipe_parts[-1].strip() else ""
                if last_cmd in ("bash", "sh", "zsh"):
                    if "base64_pipe" not in categories:
                        categories.append("base64_pipe")

        if "/dev/tcp/" in inner_lower or "/dev/udp/" in inner_lower:
            if "dev_tcp_bypass" not in categories:
                categories.append("dev_tcp_bypass")
            network_detected = True

    if not categories:
        categories.append("unknown")

    return CommandClassification(
        normalized_command=normalized,
        argv=tuple(original_argv),
        shell_wrapped=shell_wrapped,
        shell_inner_command=shell_inner,
        categories=tuple(categories),
        matched_rules=(),  # filled in by caller
        writes_detected=writes_detected,
        network_detected=network_detected,
        destructive_detected=destructive_detected,
        privilege_detected=privilege_detected,
        remote_detected=remote_detected,
        target_paths=tuple(target_paths_list),
    )


# ---------------------------------------------------------------------------
# Risk score
# ---------------------------------------------------------------------------

def _compute_risk_score(
    classification: CommandClassification,
    absolute_deny: bool,
    profile_mismatch: bool,
    profile: str,
) -> tuple[float, dict[str, float]]:
    if absolute_deny:
        return 1.0, {"absolute_deny": 1.0}

    breakdown: dict[str, float] = {}
    score = 0.0
    c = classification

    if c.writes_detected:
        breakdown["write_detected"] = 0.35; score += 0.35
    if c.network_detected:
        breakdown["network_detected"] = 0.30; score += 0.30
    if c.remote_detected:
        breakdown["remote_detected"] = 0.45; score += 0.45
    if c.privilege_detected:
        breakdown["privilege_detected"] = 0.65; score += 0.65
    if c.destructive_detected:
        breakdown["destructive_detected"] = 0.75; score += 0.75

    norm_lower = c.normalized_command.lower()
    if "rm -rf" in norm_lower or "rm -r " in norm_lower:
        breakdown["recursive_destructive"] = 0.10; score += 0.10

    if any(p in norm_lower for p in ["../", "..\\", "~", "$home"]):
        breakdown["workspace_escape"] = 0.30; score += 0.30

    if profile_mismatch:
        breakdown["profile_mismatch"] = 0.25; score += 0.25

    if profile == "inspect_local" and "unknown" in classification.categories:
        breakdown["unknown_under_inspect"] = 0.35; score += 0.35

    return min(score, 1.0), breakdown


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
    absolute_deny_rules, require_approval_rules, policy_version = _load_policy(policy_path)

    # Unwrap shell wrappers
    effective_argv, shell_wrapped, shell_inner = _effective_argv(command)

    # Apply flag normalization
    normalized_argv = _normalize_flags(effective_argv)
    normalized = _normalize(normalized_argv)

    # Build normalized string from effective argv for pattern matching
    # Also include the raw inner command string for pipe-pattern detection
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

    # Classify using effective argv (normalized flags)
    classification = _classify_argv(
        effective_argv=normalized_argv,
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
    risk_score, score_breakdown = _compute_risk_score(
        classification=classification,
        absolute_deny=is_absolute_deny,
        profile_mismatch=profile_mismatch,
        profile=selected_profile,
    )

    # Collect all matched rule ids
    all_matched_rules: tuple[str, ...] = tuple(
        r[0] for r in deny_matches
    ) + tuple(
        r[0] for r in approval_matches
    )

    # Determine recommended profile from first approval match
    recommended_profile: str | None = None
    for _, rec in approval_matches:
        if rec:
            recommended_profile = rec
            break

    # Build reasons
    reasons: list[str] = []  # built as list, converted to tuple at GuardDecision construction
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

    elif "cron" in classification.categories and classification.destructive_detected:
        action = "deny"

    elif "credential_exposure" in classification.categories and selected_profile == "inspect_local":
        action = "warn"

    elif "cron" in classification.categories:
        action = "require_approval"
        approval_required = True
        approval_hint = "--approve-risk"

    elif "firewall" in classification.categories:
        action = "require_approval"
        approval_required = True
        approval_hint = "--approve-risk"

    elif "process" in classification.categories:
        action = "require_approval"
        approval_required = True
        approval_hint = "--approve-risk"

    elif "package_publish" in classification.categories:
        action = "require_approval"
        approval_required = True
        approval_hint = "--approve-risk"

    elif "database" in classification.categories and classification.destructive_detected:
        action = "require_approval"
        approval_required = True
        approval_hint = "--approve-risk"

    elif "cloud" in classification.categories and classification.destructive_detected:
        action = "require_approval"
        approval_required = True
        approval_hint = "--approve-risk"

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

    elif "base64_pipe" in classification.categories:
        action = "require_approval"
        approval_required = True
        approval_hint = "--approve-risk"
        reasons.append("base64 pipe to shell detected")

    elif "heredoc_input" in classification.categories:
        action = "require_approval"
        approval_required = True
        approval_hint = "--approve-risk"
        reasons.append("heredoc input detected")

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

    from datetime import datetime, timezone

    return GuardDecision(
        action=action,
        risk_score=risk_score,
        selected_profile=selected_profile,
        recommended_profile=recommended_profile,
        reasons=tuple(reasons),
        matched_rules=all_matched_rules,
        classification=classification,
        approval_required=approval_required,
        approval_hint=approval_hint,
        score_breakdown=score_breakdown,
        workspace=str(workspace) if workspace else None,
        task_name=task_name,
        task_goal=task_goal,
        policy_version=policy_version,
        evaluated_at=datetime.now(timezone.utc).isoformat(),
    )


def decision_to_json(decision: GuardDecision) -> dict[str, Any]:
    """Convert GuardDecision to JSON-serializable dict."""
    c = decision.classification
    return {
        "action": decision.action,
        "risk_score": decision.risk_score,
        "score_breakdown": decision.score_breakdown,
        "selected_profile": decision.selected_profile,
        "recommended_profile": decision.recommended_profile,
        "reasons": list(decision.reasons),
        "matched_rules": list(decision.matched_rules),
        "approval_required": decision.approval_required,
        "approval_hint": decision.approval_hint,
        "evaluated_at": decision.evaluated_at,
        "policy_version": decision.policy_version,
        "workspace": decision.workspace,
        "task_name": decision.task_name,
        "task_goal": decision.task_goal,
        "classification": {
            "normalized_command": c.normalized_command,
            "argv": list(c.argv),
            "shell_wrapped": c.shell_wrapped,
            "shell_inner_command": c.shell_inner_command,
            "categories": list(c.categories),
            "matched_rules": list(c.matched_rules),
            "writes_detected": c.writes_detected,
            "network_detected": c.network_detected,
            "destructive_detected": c.destructive_detected,
            "privilege_detected": c.privilege_detected,
            "remote_detected": c.remote_detected,
            "target_paths": list(c.target_paths),
        },
    }
