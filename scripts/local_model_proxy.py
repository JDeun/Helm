"""Local Model Guard Proxy — Spike (Task 15).

Spike phase: validator + nudge + policy in isolation. HTTP wrapping is
Task #15.1 (future).

This module is NOT a working HTTP server. It contains only the
validator, nudge builder, retry-policy decider, and event logger — the
four building-block functions that a future HTTP proxy would call around
each local-model round-trip.

Motivation (Forge-inspired)
---------------------------
Small and self-hosted local models (Ollama, llama.cpp, LM Studio, vLLM)
frequently produce malformed tool calls, premature plain-text answers, or
invalid JSON in tool arguments. A thin reliability layer between the host
runtime and the local model can catch these failures and retry with a
targeted nudge prompt, without modifying the agent loop or the provider
routing logic.

Design overview
---------------
Four public functions are intended to be composed in a guard loop:

1. ``validate_response`` — inspect a model response payload and classify
   each failure mode with an exact issue code.
2. ``build_nudge`` — produce a short retry-prompt from the issue list,
   ordered by priority (most actionable first).
3. ``should_retry`` — decide whether to issue another request given the
   attempt count, the issue list, and the loaded policy dict.
4. ``record_proxy_event`` — append a structured event to a JSONL event
   log for later analysis.

Atomic append strategy
-----------------------
``record_proxy_event`` uses ``O_APPEND`` (via ``open(..., 'a')``) rather
than the tempfile-rename pattern used for full-file atomic replacement.

Rationale:
- JSONL files are append-only by nature; each event is an independent
  line.  Readers consume them line-by-line and are tolerant of partial
  trailing lines (they simply stop at the last valid line).
- ``O_APPEND`` on POSIX guarantees that concurrent writers do not
  interleave within a single ``write()`` syscall as long as each write
  is smaller than PIPE_BUF (4 KiB on Linux/macOS).  A single JSONL
  event is well under that threshold.
- Atomic-replace of the whole file on every append would be O(n) in
  file size and would truncate concurrent readers who have not yet
  finished iterating.

Tradeoff:
- On a power failure the last partially-written line may be corrupt.
  Readers should skip lines that fail ``json.loads``.  This is
  acceptable for a diagnostic event log.

Issue codes (exact strings, no extras)
---------------------------------------
- ``malformed_tool_call``           tool_calls entry missing name or arguments
- ``non_json_when_tool_required``   payload requires a tool call but content is
                                    plain text (no tool_calls key)
- ``invalid_json_in_arguments``     tool_calls[*].arguments is present but not
                                    parseable JSON
- ``terminal_without_tool``         content is a final text answer but the spec
                                    required a tool call (e.g. a respond tool)
- ``empty_response``                content is empty string AND no tool_calls

Out of scope in this spike
--------------------------
- HTTP server / WSGI / ASGI wrapper (Task #15.1, future)
- Streaming (chunked) response handling
- Real subprocess call to a model backend
- OpenAI-compatible API surface
- Authentication / TLS
"""

from __future__ import annotations

import datetime
import json
import os
import pathlib
import sys

__all__ = [
    "validate_response",
    "build_nudge",
    "should_retry",
    "record_proxy_event",
]

# ---------------------------------------------------------------------------
# Issue code constants (single source of truth)
# ---------------------------------------------------------------------------

_MALFORMED_TOOL_CALL = "malformed_tool_call"
_NON_JSON_WHEN_TOOL_REQUIRED = "non_json_when_tool_required"
_INVALID_JSON_IN_ARGUMENTS = "invalid_json_in_arguments"
_TERMINAL_WITHOUT_TOOL = "terminal_without_tool"
_EMPTY_RESPONSE = "empty_response"

# Priority order for nudge building — most actionable first.
_ISSUE_PRIORITY: list[str] = [
    _MALFORMED_TOOL_CALL,
    _INVALID_JSON_IN_ARGUMENTS,
    _NON_JSON_WHEN_TOOL_REQUIRED,
    _TERMINAL_WITHOUT_TOOL,
    _EMPTY_RESPONSE,
]

# ---------------------------------------------------------------------------
# Nudge messages keyed by issue code
# ---------------------------------------------------------------------------

_NUDGE_MESSAGES: dict[str, str] = {
    _MALFORMED_TOOL_CALL: (
        "Your last tool call was missing required fields (name or arguments). "
        "Reply with a properly structured tool call: "
        '{"name": "<tool>", "arguments": {...}}.'
    ),
    _INVALID_JSON_IN_ARGUMENTS: (
        "The arguments field in your tool call is not valid JSON. "
        "Ensure arguments is a JSON object with double-quoted keys and values."
    ),
    _NON_JSON_WHEN_TOOL_REQUIRED: (
        "A tool call is required here but you replied with plain text. "
        "Reply exclusively with a JSON tool call — do not include prose."
    ),
    _TERMINAL_WITHOUT_TOOL: (
        "You gave a final answer but a tool call (e.g. respond or finalize) "
        "is required before completing. Call the appropriate tool now."
    ),
    _EMPTY_RESPONSE: (
        "Your response was empty. Provide either a tool call or a content reply."
    ),
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def validate_response(payload: dict) -> dict:
    """Inspect a model response payload and classify failure modes.

    Parameters
    ----------
    payload:
        A model JSON response.  Expected shape (OpenAI-style)::

            {
              "content": "<str or None>",
              "tool_calls": [
                  {"name": "<str>", "arguments": "<str or dict>"},
                  ...
              ],
              # Optional hint for the validator:
              "tool_required": True | False,
            }

        ``content`` and ``tool_calls`` may be absent; the validator
        treats absence as empty/None.

        ``tool_required`` is an optional boolean that the caller can set
        to ``True`` when the surrounding spec mandates a tool call
        (e.g. when a synthetic respond-tool is injected).  When omitted
        the validator does not flag ``non_json_when_tool_required`` or
        ``terminal_without_tool``.

    Returns
    -------
    dict
        ``{"valid": bool, "issues": list[str], "repair_hint": str | None}``

        *valid* is ``True`` iff *issues* is empty.
        *repair_hint* is ``None`` when there are no issues; otherwise it
        is the highest-priority nudge message (same text as
        :func:`build_nudge` would return first).
    """
    issues: list[str] = []

    content: str = payload.get("content") or ""
    tool_calls: list[dict] | None = payload.get("tool_calls")
    tool_required: bool = bool(payload.get("tool_required", False))

    # --- empty_response --------------------------------------------------
    if not content.strip() and not tool_calls:
        issues.append(_EMPTY_RESPONSE)

    # --- tool_calls structural checks ------------------------------------
    if tool_calls is not None:
        for tc in tool_calls:
            if not isinstance(tc, dict):
                if _MALFORMED_TOOL_CALL not in issues:
                    issues.append(_MALFORMED_TOOL_CALL)
                continue

            missing_keys = not tc.get("name") or "arguments" not in tc
            if missing_keys:
                if _MALFORMED_TOOL_CALL not in issues:
                    issues.append(_MALFORMED_TOOL_CALL)
                continue

            # Check arguments is valid JSON (if a string)
            args = tc["arguments"]
            if isinstance(args, str):
                try:
                    json.loads(args)
                except (json.JSONDecodeError, ValueError):
                    if _INVALID_JSON_IN_ARGUMENTS not in issues:
                        issues.append(_INVALID_JSON_IN_ARGUMENTS)

    # --- tool-required checks --------------------------------------------
    if tool_required:
        has_tool_calls = bool(tool_calls)
        has_content = bool(content.strip())

        if not has_tool_calls and has_content:
            # Model replied with plain text instead of a tool call.
            issues.append(_NON_JSON_WHEN_TOOL_REQUIRED)

        if not has_tool_calls and has_content:
            # The text reply constitutes a premature terminal answer.
            issues.append(_TERMINAL_WITHOUT_TOOL)

    valid = len(issues) == 0
    repair_hint: str | None = None
    if issues:
        # Use highest-priority issue for the repair hint.
        for priority_code in _ISSUE_PRIORITY:
            if priority_code in issues:
                repair_hint = _NUDGE_MESSAGES.get(priority_code)
                break

    return {"valid": valid, "issues": issues, "repair_hint": repair_hint}


def build_nudge(issues: list[str]) -> str:
    """Return a short retry-nudge message for the model.

    Iterates ``issues`` in priority order (highest-priority first) and
    concatenates one nudge sentence per recognised issue code.  Unknown
    codes are included as a generic reminder.

    Parameters
    ----------
    issues:
        Non-empty list of issue codes from :func:`validate_response`.

    Returns
    -------
    str
        A non-empty nudge string.  The caller should append this to the
        conversation before re-requesting a model response.
    """
    if not issues:
        return "Please provide a valid response."

    seen: set[str] = set()
    parts: list[str] = []

    # Emit in priority order first.
    for priority_code in _ISSUE_PRIORITY:
        if priority_code in issues and priority_code not in seen:
            seen.add(priority_code)
            parts.append(_NUDGE_MESSAGES[priority_code])

    # Any unrecognised codes that weren't in the priority list.
    for code in issues:
        if code not in seen:
            seen.add(code)
            parts.append(f"Please correct the issue: {code}.")

    return " ".join(parts)


def should_retry(attempt: int, issues: list[str], policy: dict) -> bool:
    """Decide whether to retry given the attempt count and policy.

    Parameters
    ----------
    attempt:
        Zero-based attempt index.  ``attempt=0`` is the first retry
        decision (after the first model call).
    issues:
        Issue codes from :func:`validate_response`.
    policy:
        Policy dict matching the shape of
        ``references/local_model_proxy_policy.json``::

            {
              "max_retries": 2,
              "nudge_on": ["malformed_tool_call", ...],
              "abort_on": ["terminal_without_tool"]
            }

    Returns
    -------
    bool
        ``True`` if a retry should be attempted; ``False`` to abort.

    Decision rules (evaluated in order):
    1. No issues → no retry needed → ``False``.
    2. Any issue in ``abort_on`` → ``False`` (abort immediately).
    3. ``attempt >= max_retries`` → ``False`` (exhausted budget).
    4. Any issue in ``nudge_on`` → ``True``.
    5. Default → ``False``.
    """
    if not issues:
        return False

    abort_on: list[str] = policy.get("abort_on", [])
    nudge_on: list[str] = policy.get("nudge_on", [])
    max_retries: int = policy.get("max_retries", 0)

    # Rule 2: abort takes priority over retry budget.
    if any(issue in abort_on for issue in issues):
        return False

    # Rule 3: exhausted retry budget.
    if attempt >= max_retries:
        return False

    # Rule 4: at least one issue is in nudge_on → retry.
    if any(issue in nudge_on for issue in issues):
        return True

    return False


def record_proxy_event(
    traces_dir: pathlib.Path,
    model: str,
    issues: list[str],
    action: str,
    attempt: int,
) -> None:
    """Append a structured event to ``<traces_dir>/proxy-events.jsonl``.

    Each event line is a JSON object with the following fields:

    - ``timestamp``  ISO-8601 UTC timestamp (seconds precision)
    - ``model``      Model identifier string
    - ``issues``     List of issue codes from :func:`validate_response`
    - ``action``     One of ``"retry"``, ``"abort"``, ``"pass"`` (or
                     any caller-defined string)
    - ``attempt``    Zero-based attempt index

    The write uses ``O_APPEND`` (``open(..., 'a')``) for atomic line
    appends.  See the module docstring for the full rationale.  The
    directory is created if it does not yet exist.

    Parameters
    ----------
    traces_dir:
        Directory that will contain ``proxy-events.jsonl``.
    model:
        Short model identifier (e.g. ``"ollama/mistral:7b"``).
    issues:
        Issue codes (may be empty for a passing event).
    action:
        Disposition taken: ``"retry"``, ``"abort"``, or ``"pass"``.
    attempt:
        Zero-based attempt index at the time the event is recorded.
    """
    traces_dir.mkdir(parents=True, exist_ok=True)
    event_path = traces_dir / "proxy-events.jsonl"

    event: dict = {
        "timestamp": _utc_now_iso(),
        "model": model,
        "issues": issues,
        "action": action,
        "attempt": attempt,
    }
    line = json.dumps(event, ensure_ascii=False) + "\n"

    # O_APPEND guarantees that each write() call is atomic on POSIX as
    # long as the payload is below PIPE_BUF (~4 KiB).
    with open(event_path, "a", encoding="utf-8") as fh:
        fh.write(line)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _utc_now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string (seconds precision)."""
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
