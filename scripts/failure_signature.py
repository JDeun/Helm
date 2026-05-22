"""Structured failure-signature module for the Helm task ledger.

This module provides three public functions:

* :func:`signature` — build a structured failure signature dict from a ledger event
* :func:`classify_error` — classify a stderr/message string to a normalized error_class
* :func:`normalize_target` — normalize paths / URLs / commands to a stable form

All error_class strings are string constants (not Enum) to keep the module
dependency-free and easily importable from both Helm and workspace scripts.

Error classes grounded in inventory Section 6 (FS-001..FS-010):
  google_sheets_api        — FS-001, FS-002, FS-003
  gemini_video_api         — FS-004
  obsidian_link_maintenance — FS-005
  guard_deny               — FS-007, FS-008
  patch_failed             — (patch apply failures)
  credential_invalid_grant — OAuth token revocation / expiry
  exit_nonzero             — FS-006, FS-009, FS-010 (generic non-zero exit)
  timeout                  — subprocess timeout
  unknown                  — no classification possible

Component values:
  skill | runner | guard | external_api | unknown
"""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path


# ---------------------------------------------------------------------------
# Internal constants
# ---------------------------------------------------------------------------

_HOME = os.path.expanduser("~")
# Workspace detection: honour the env var used by the harness if present, else
# derive from the standard location relative to the home directory.
_WORKSPACE_PATH = os.environ.get("OPENCLAW_WORKSPACE") or os.path.join(_HOME, ".openclaw", "workspace")

# Compiled regexes for normalize_target
_RE_GIT_SHA = re.compile(r"\b[0-9a-f]{7,40}\b")
_RE_URL_QUERY = re.compile(r"\?.*$")


# ---------------------------------------------------------------------------
# Tool-name extraction rules (ordered — first match wins)
# ---------------------------------------------------------------------------

# Map: regex on command string → normalized tool name
# Built from FS-001..FS-010 script names.
_SCRIPT_TOOL_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"google_sheets_append_row"), "google_sheets_append_row"),
    (re.compile(r"google_sheets_read_range"), "google_sheets_read_range"),
    (re.compile(r"gemini_video_understand"), "gemini_video_understand"),
    (re.compile(r"obsidian_link_maintenance"), "obsidian_link_maintenance"),
    (re.compile(r"household_ledger_runner"), "household_ledger_runner"),
]


# ---------------------------------------------------------------------------
# Error classification patterns (ordered — first match wins)
# ---------------------------------------------------------------------------

# Each entry: (re.Pattern | None, callable | None, error_class_string)
# Pattern matched against stderr_or_message (case-insensitive).
# callable is an optional extra predicate(match, text) → bool.

_ERROR_CLASS_PATTERNS: list[tuple[re.Pattern, str]] = [
    # credential_invalid_grant must precede google_sheets_api because
    # "invalid_grant" tokens can appear in google-auth error messages.
    (re.compile(r"invalid_grant|token.*(?:expired|revoked)|oauth.*invalid.*token", re.I), "credential_invalid_grant"),

    # Google Sheets / workspace API errors
    (re.compile(
        r"sheets\.googleapis\.com"
        r"|googleapiclient\.errors\.HttpError"
        r"|google\.auth\.exceptions"
        r"|Unable to parse range"
        r"|HttpError \d{3}.*sheet"
        r"|gws:.*auth"
        r"|google.*api.*error"
        r"|google.*transport.*error",
        re.I,
    ), "google_sheets_api"),

    # Gemini video API errors
    (re.compile(
        r"gemini.*video"
        r"|gemini_video_understand"
        r"|generate_content.*gemini"
        r"|HARM_CATEGORY.*gemini"
        r"|files/[a-z0-9].*gemini",
        re.I,
    ), "gemini_video_api"),

    # Obsidian vault / link maintenance errors
    (re.compile(
        r"obsidian"
        r"|wikilink"
        r"|obsidian.*vault.*not found"
        r"|malformed.*wikilink"
        r"|obsidian_link_maintenance",
        re.I,
    ), "obsidian_link_maintenance"),

    # Guard deny / require-approval (from run_with_profile stderr)
    (re.compile(
        r"GUARD DENY"
        r"|GUARD APPROVAL REQUIRED"
        r"|approval required",
        re.I,
    ), "guard_deny"),

    # Patch apply failures
    (re.compile(
        r"malformed patch"
        r"|Apply patch failed"
        r"|Hunk #\d+ FAILED"
        r"|patch:.*\*\*\*\*"
        r"|patch did not apply",
        re.I,
    ), "patch_failed"),

    # Timeout signals
    (re.compile(
        r"TIMEOUT:.*exceeded"
        r"|TimeoutExpired"
        r"|timed out after",
        re.I,
    ), "timeout"),
]

# GWS CLI: exit code 3 is also a google_sheets_api indicator (FS-003)
# This is handled inside signature() by cross-referencing the tool.


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def classify_error(stderr_or_message: str | None) -> str:
    """Return one of the documented error_class strings.

    Matches against ``_ERROR_CLASS_PATTERNS`` in order; returns ``'unknown'``
    when the input is empty or no pattern matches.

    Input is capped at 8 KiB to bound regex scan cost on pathologically
    large stderr blobs.
    """
    text = (stderr_or_message or "")[:8192]
    if not text.strip():
        return "unknown"
    for pattern, error_class in _ERROR_CLASS_PATTERNS:
        if pattern.search(text):
            return error_class
    return "exit_nonzero"


def normalize_target(value: str | None) -> str | None:
    """Normalize a path, command, or URL to a stable form.

    Rules applied:
    - ``None`` or empty string → ``None``
    - Workspace path (``~/.openclaw/workspace/...``) → ``<workspace>/...``
    - Home path (``~/...`` or ``/Users/...``) → ``~/...``
    - ``/tmp/...`` digit runs of ≥ 4 digits → stripped/replaced
    - URL scheme → lowercased
    - URL query parameters → stripped
    - Git SHAs (7-40 hex chars) in paths/URLs → stripped
    """
    if not value or not value.strip():
        return None

    result = value.strip()

    # 1. Workspace path → <workspace>/...
    if result.startswith(_WORKSPACE_PATH):
        remainder = result[len(_WORKSPACE_PATH):]
        return "<workspace>" + remainder

    # 2. Home path → ~/...
    if _HOME and result.startswith(_HOME):
        result = "~" + result[len(_HOME):]
        return result

    # 3. URL handling
    url_match = re.match(r"^([a-zA-Z][a-zA-Z0-9+\-.]*://)(.*)", result)
    if url_match:
        scheme = url_match.group(1).lower()
        rest = url_match.group(2)
        # Strip query params
        rest = _RE_URL_QUERY.sub("", rest)
        # Strip long hex SHAs in path segments
        rest = _RE_GIT_SHA.sub("<sha>", rest)
        return scheme + rest

    # 4. /tmp paths — strip digit runs ≥ 4 chars and git SHAs
    if result.startswith("/tmp/") or result.startswith("\\tmp\\"):
        # Remove long numeric segments (e.g. run12345 → run)
        result = re.sub(r"\d{4,}", "", result)
        # Also strip git SHAs embedded in segment names
        result = _RE_GIT_SHA.sub("<sha>", result)
        return result

    # 5. Git SHAs in plain paths (e.g. /cache/git-a1b2c3d4e5f6/work)
    if "/" in result and _RE_GIT_SHA.search(result):
        result = _RE_GIT_SHA.sub("<sha>", result)

    return result


def _extract_tool(command: list[str]) -> str:
    """Return a normalized tool/script name from a command argv list."""
    if not command:
        return "unknown"

    # Join command as a single string for pattern matching
    cmd_str = " ".join(str(part) for part in command)

    # Check for known script names first
    for pattern, tool_name in _SCRIPT_TOOL_PATTERNS:
        if pattern.search(cmd_str):
            return tool_name

    # First token is the executable
    executable = str(command[0])
    # If it's python3/python, look at the next argument
    basename = Path(executable).name
    if basename in ("python3", "python", "python2"):
        # Walk argv to find the script
        for arg in command[1:]:
            arg_str = str(arg)
            if not arg_str.startswith("-") and (arg_str.endswith(".py") or "/" in arg_str):
                return Path(arg_str).stem
        return basename

    # For shell executables, return just the executable name
    if basename in ("bash", "zsh", "sh", "fish"):
        return basename

    return basename


def _classify_from_event(event: dict) -> str:
    """Derive error_class from a ledger event.

    Priority:
    1. Guard-stage failures are always guard_deny.
    2. Known timeout status → timeout.
    3. Meta stderr field (if present) → classify_error().
    4. failure_reason field → classify_error().
    5. Command-based heuristics (known script names, exit codes).
    6. Generic exit_nonzero fallback.
    """
    failure_stage = str(event.get("failure_stage") or "").lower()
    failure_reason = str(event.get("failure_reason") or "").lower()
    status = str(event.get("status") or "").lower()
    exit_code = event.get("exit_code")
    command = event.get("command") or []
    cmd_str = " ".join(str(c) for c in command)

    # 1. Guard stage
    if failure_stage == "guard":
        return "guard_deny"
    if exit_code in (24, 25):
        return "guard_deny"
    if "guard deny" in failure_reason or "approval required" in failure_reason:
        return "guard_deny"

    # 2. Timeout
    if status == "timeout":
        return "timeout"

    # 3. Meta stderr
    meta = event.get("meta") or {}
    if isinstance(meta, dict):
        stderr_val = meta.get("stderr") or meta.get("error") or ""
        if stderr_val:
            cls = classify_error(str(stderr_val))
            if cls != "unknown":
                return cls

    # 4. failure_reason
    if failure_reason:
        cls = classify_error(failure_reason)
        if cls not in ("unknown", "exit_nonzero"):
            return cls

    # 5. Command-based heuristics (known scripts → fixed error_class)
    script_to_class: dict[str, str] = {
        "google_sheets_append_row": "google_sheets_api",
        "google_sheets_read_range": "google_sheets_api",
        "gemini_video_understand": "gemini_video_api",
        "obsidian_link_maintenance": "obsidian_link_maintenance",
        "gws": "google_sheets_api",
    }
    tool = _extract_tool(command)
    if tool in script_to_class and status == "failed":
        return script_to_class[tool]

    # gws exit code 3 is a network/auth error (FS-003)
    if tool == "gws" and exit_code == 3:
        return "google_sheets_api"

    # 6. Generic fallback
    if exit_code is not None and exit_code != 0:
        return "exit_nonzero"

    return "unknown"


def _classify_component(event: dict, tool: str) -> str:
    """Infer component string from event context."""
    failure_stage = str(event.get("failure_stage") or "").lower()
    status = str(event.get("status") or "").lower()
    exit_code = event.get("exit_code")

    if failure_stage == "guard" or exit_code in (24, 25):
        return "guard"

    if status == "blocked" and ("guard" in str(event.get("failure_reason") or "").lower()):
        return "guard"

    # Known external-API tools
    external_tools = {
        "google_sheets_append_row", "google_sheets_read_range",
        "gemini_video_understand", "gws",
    }
    if tool in external_tools:
        return "external_api"

    # Obsidian is a local tool but workspace-coupled
    if tool in ("obsidian_link_maintenance",):
        return "skill"

    # Shells
    if tool in ("bash", "zsh", "sh", "fish"):
        return "runner"

    # Python scripts
    command = event.get("command") or []
    if command and Path(str(command[0])).name in ("python3", "python", "python2"):
        return "skill"

    return "unknown"


def signature(event: dict) -> dict:
    """Build a structured failure signature from a ledger event.

    Returns a dict with stable keys:
      - component:   "skill" | "runner" | "guard" | "external_api" | "unknown"
      - tool:        normalized tool/script/runner name (str)
      - profile:     execution profile name or None
      - error_class: normalized class string
      - target:      normalized target or None
      - fingerprint: 8-char hex hash of (component, tool, error_class, target)
    """
    command = event.get("command") or []
    profile = event.get("profile") or None

    tool = _extract_tool(list(command))
    error_class = _classify_from_event(event)
    component = _classify_component(event, tool)

    # Derive a stable target
    target = _derive_target(event, component, tool, profile)
    if target is not None:
        target = normalize_target(target) or target

    # Fingerprint: hash of (component, tool, error_class, target or "")
    fp_input = f"{component}|{tool}|{error_class}|{target or ''}"
    fingerprint = hashlib.sha256(fp_input.encode()).hexdigest()[:8]

    return {
        "component": component,
        "tool": tool,
        "profile": profile,
        "error_class": error_class,
        "target": target,
        "fingerprint": fingerprint,
    }


def _derive_target(event: dict, component: str, tool: str, profile: str | None) -> str | None:
    """Derive a stable target string from the event.

    - Guard failures → ``profile:<profile_name>``
    - Sheets scripts → use range arg if available
    - Gemini → use file/content arg if available
    - Obsidian → vault path if detectable
    - Others → command_preview or first non-flag arg
    """
    if component == "guard":
        return f"profile:{profile}" if profile else "profile:unknown"

    command = list(event.get("command") or [])
    cmd_str = " ".join(str(c) for c in command)

    # For Google Sheets scripts, try to extract the range argument
    if tool in ("google_sheets_append_row", "google_sheets_read_range"):
        for i, arg in enumerate(command):
            if arg in ("--range", "-r") and i + 1 < len(command):
                return str(command[i + 1])
        # Fall through to command preview

    # For Gemini video, try to extract the file argument
    if tool == "gemini_video_understand":
        for i, arg in enumerate(command):
            if arg in ("--file", "-f", "--input") and i + 1 < len(command):
                return normalize_target(str(command[i + 1]))

    # For obsidian, extract vault path if available
    if tool == "obsidian_link_maintenance":
        for i, arg in enumerate(command):
            if arg in ("--vault", "--path", "-p") and i + 1 < len(command):
                return normalize_target(str(command[i + 1]))

    # For shell commands, return the command preview (trimmed)
    command_preview = event.get("command_preview") or ""
    if command_preview and len(command_preview) <= 128:
        return command_preview

    # Use the script path from argv if python3 invocation
    if command and Path(str(command[0])).name in ("python3", "python"):
        for arg in command[1:]:
            s = str(arg)
            if s.endswith(".py"):
                return normalize_target(s)

    return None
