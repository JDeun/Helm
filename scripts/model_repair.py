"""Model Repair Orchestrator — Wave 2 (N-C).

Provides clean library entry points so external runners can consume
``local_model_proxy`` functions through a single, feature-flagged interface.

Design rationale
----------------
``scripts/intelligence_tier.py`` classifies the tier from a discovery snapshot
but does NOT make model calls.  Actual model calls live in the external runtime
(Claude Code, OpenClaw runner, etc.).  Wave 2's job is therefore *library
hardening*: expose composable entry points with clear feature-flag semantics
and shadow-mode defaults — ready for runner-side consumption.

Policy loading
--------------
When ``policy=None`` is passed to :func:`evaluate_response`, this module
re-reads ``references/local_model_proxy_policy.json`` on each call.  This is
intentional: the policy file can change between invocations (e.g., hot-reload
by an operator), and ``evaluate_response`` is called rarely (once per model
round-trip).  The cost of a file read is negligible compared to a model call.
The private ``_POLICY_CACHE`` in ``local_model_proxy`` is not used here because
it is a module implementation detail.

Feature flags
-------------
- ``HELM_MODEL_REPAIR``       — enable repair mode (default: shadow/off).
- Both flags are checked at *call time*, not module-load time, so tests can
  freely change env vars between calls.
"""

from __future__ import annotations

import json
import pathlib
from typing import Callable

from scripts.env_flags import env_flag
from scripts.local_model_proxy import (
    build_nudge,
    record_proxy_event,
    should_retry,
    validate_response,
)

__all__ = [
    "repair_enabled",
    "evaluate_response",
    "repair_loop",
]

_POLICY_PATH = pathlib.Path(__file__).resolve().parent.parent / "references" / "local_model_proxy_policy.json"


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _load_default_policy() -> dict:
    """Load and return the default policy from references/local_model_proxy_policy.json."""
    with _POLICY_PATH.open("r", encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def repair_enabled() -> bool:
    """Return True iff env var HELM_MODEL_REPAIR is truthy.

    Truthy values: '1', 'true', 'yes' (case-insensitive, stripped).
    All other values including unset → False (opt-in default).

    Checked at call time so runtime env changes (e.g. in tests) take effect.
    """
    return env_flag("HELM_MODEL_REPAIR")


def evaluate_response(
    payload: dict,
    *,
    model: str,
    tool_required: bool,
    attempt: int = 0,
    policy: dict | None = None,
    traces_dir: pathlib.Path | None = None,
) -> dict:
    """One-shot repair decision combining validate_response + build_nudge + should_retry.

    Parameters
    ----------
    payload:
        Model response dict (OpenAI-style with optional ``tool_required`` field).
    model:
        Model identifier string (used in trace events).
    tool_required:
        Whether the spec mandates a tool call.  Forwarded to ``validate_response``
        via the ``tool_required`` key in an augmented payload copy.
    attempt:
        Zero-based attempt index for this round-trip.
    policy:
        Policy dict.  When ``None``, ``references/local_model_proxy_policy.json``
        is loaded fresh on this call (see module docstring for rationale).
    traces_dir:
        When provided, a trace event is appended via ``record_proxy_event``
        regardless of the ``repair_enabled()`` flag.  Shadow logging is always
        on when the caller passes this argument.

    Returns
    -------
    dict
        Keys:
        - ``verdict``     — ``"ok"`` | ``"nudge_and_retry"`` | ``"abort"`` | ``"give_up"``
        - ``issues``      — list of issue codes from ``validate_response``
        - ``nudge``       — nudge string when verdict is ``"nudge_and_retry"``; else ``None``
        - ``next_attempt``— ``attempt + 1`` when retrying; else ``attempt``
        - ``shadow_mode`` — ``True`` when ``repair_enabled()`` is ``False``

    Behavior modes
    --------------
    - Shadow mode (``repair_enabled()`` is False): verdict and issues are still
      computed for diagnostic purposes, but ``"shadow_mode": True`` is added.
      The caller is responsible for honoring or ignoring the verdict.
    - Enforce mode (``repair_enabled()`` is True): ``"shadow_mode": False``;
      caller is expected to act on the verdict.
    """
    if policy is None:
        policy = _load_default_policy()

    # Augment payload with tool_required hint for the validator
    augmented = dict(payload)
    augmented["tool_required"] = tool_required

    validation = validate_response(augmented)
    issues: list[str] = validation["issues"]

    shadow = not repair_enabled()

    # Determine verdict
    if not issues:
        verdict = "ok"
        nudge = None
        next_attempt = attempt
    else:
        retry = should_retry(attempt, issues, policy)
        if retry:
            verdict = "nudge_and_retry"
            nudge = build_nudge(issues)
            next_attempt = attempt + 1
        else:
            # Distinguish abort (abort_on issue present) from give_up (budget exhausted)
            abort_on: list[str] = policy.get("abort_on", [])
            if any(issue in abort_on for issue in issues):
                verdict = "abort"
            else:
                verdict = "give_up"
            nudge = None
            next_attempt = attempt

    if traces_dir is not None:
        record_proxy_event(traces_dir, model, issues, verdict, attempt)

    return {
        "verdict": verdict,
        "issues": issues,
        "nudge": nudge,
        "next_attempt": next_attempt,
        "shadow_mode": shadow,
    }


def repair_loop(
    *,
    invoke_model_fn: Callable,
    tools: list[dict],
    model: str,
    tool_required: bool,
    policy: dict | None = None,
    traces_dir: pathlib.Path | None = None,
    max_attempts: int = 3,
) -> dict:
    """Drive a validate-nudge-retry loop until verdict is terminal or attempts exhausted.

    This is the canonical integration shape for external runners.  A typical
    runner wraps its model-call inside ``invoke_model_fn`` and calls this
    function once; the loop handles all retry logic internally.

    Parameters
    ----------
    invoke_model_fn:
        Callable with signature ``(tools: list[dict], nudge: str | None) -> dict``.
        Receives the (possibly augmented) tools list and an optional nudge
        message to prepend to the conversation.  Returns the raw model response.
    tools:
        Tool schemas to pass to the model on the first call.
    model:
        Model identifier for trace events.
    tool_required:
        Whether a tool call is required (forwarded to ``evaluate_response``).
    policy:
        Policy dict.  ``None`` → loads from file (same semantics as
        ``evaluate_response``).
    traces_dir:
        Shadow-log all events here when provided.
    max_attempts:
        Hard ceiling on model calls (applies regardless of ``repair_enabled()``).

    Returns
    -------
    dict
        ``{"response": <last response>, "issues": <last issues>, "attempts": <int>}``

    Shadow mode
    -----------
    When ``repair_enabled()`` is ``False`` (the default), the loop invokes the
    model exactly once, validates, logs (if traces_dir set), and returns without
    injecting nudges or retrying.  This ensures safe rollout: operators can
    observe diagnostics before enabling enforce mode.
    """
    if policy is None:
        policy = _load_default_policy()

    response: dict = {}
    nudge: str | None = None
    attempts = 0

    for _i in range(max_attempts):
        response = invoke_model_fn(tools, nudge)
        attempts += 1

        result = evaluate_response(
            response,
            model=model,
            tool_required=tool_required,
            attempt=_i,
            policy=policy,
            traces_dir=traces_dir,
        )

        verdict = result["verdict"]

        # In shadow mode: report once and stop (no retry, no nudge injection)
        if result["shadow_mode"]:
            break

        # Enforce mode: act on verdict
        if verdict == "nudge_and_retry":
            nudge = result["nudge"]
            continue

        # ok, abort, or give_up → terminal
        break

    return {
        "response": response,
        "issues": result["issues"],
        "attempts": attempts,
    }
