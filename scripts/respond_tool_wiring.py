"""Respond Tool Wiring — Wave 2 (N-D).

Tier-aware helper that gates synthetic respond-tool injection on
``HELM_SYNTHETIC_RESPOND`` and the detected model tier.

Design rationale
----------------
The synthetic respond tool (from ``synthetic_respond_tool.py``) should only
be injected for local/small models (``L3_local_model``) where plain-text
terminal drift is a known failure mode.  Cloud-tier models handle structured
output more reliably and do not need the synthetic tool.

This module is intentionally thin: it wraps ``inject_respond_tool``,
``strip_respond_call``, and ``enforce_final_response`` behind a feature flag
and a model-tier gate, providing a single integration point for external
runners.

Feature flag
------------
``HELM_SYNTHETIC_RESPOND`` — enable synthetic respond tool injection.
Default: False (opt-in).  Checked at call time.

Purity
------
``prepare_tools`` and ``finalize_response`` are pure — they never mutate
their input arguments.
"""

from __future__ import annotations

from scripts.env_flags import env_flag
from scripts.synthetic_respond_tool import (
    enforce_final_response,
    inject_respond_tool,
    strip_respond_call,
)

__all__ = [
    "synthetic_respond_enabled",
    "prepare_tools",
    "finalize_response",
]

_LOCAL_TIER = "L3_local_model"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def synthetic_respond_enabled() -> bool:
    """Return True iff env var HELM_SYNTHETIC_RESPOND is truthy.

    Truthy values: '1', 'true', 'yes' (case-insensitive, stripped).
    All other values including unset → False (opt-in default).

    Checked at call time so runtime env changes take effect immediately.
    """
    return env_flag("HELM_SYNTHETIC_RESPOND")


def prepare_tools(tools: list[dict], *, model_tier: str) -> list[dict]:
    """Return a tools list possibly augmented with the synthetic respond tool.

    Injection happens iff:
    - ``synthetic_respond_enabled()`` is True
    - AND ``model_tier == "L3_local_model"`` (only for small/local models)

    Otherwise the input list is returned as a new list with identical contents
    (or the same reference when injection is skipped and no copy is needed for
    correctness).  The input list is never mutated.

    Parameters
    ----------
    tools:
        Existing list of tool schema dicts.
    model_tier:
        Tier string from intelligence classification (e.g. ``"L3_local_model"``,
        ``"L4_cloud_provider"``).

    Returns
    -------
    list[dict]
        New list — input is never mutated.
    """
    if synthetic_respond_enabled() and model_tier == _LOCAL_TIER:
        return inject_respond_tool(tools)
    # Return a new list (purity: caller should not rely on identity)
    return list(tools)


def finalize_response(response: dict, *, tool_required: bool) -> dict:
    """Return a response with respond-tool calls stripped and validated.

    Always strips respond calls (cheap; no behavior change when absent).
    If ``tool_required=True`` and the (stripped) response has no tool_calls
    AND the original had no respond call, a ``"_finalize_warning"`` key is
    added with the result of ``enforce_final_response``'s
    ``{"valid": False, "issue": "terminal_without_respond"}`` indicator.

    This function never raises.  The ``_finalize_warning`` key is a passive
    indicator — it is up to the caller to decide how to handle it.

    Parameters
    ----------
    response:
        Model response dict (``tool_calls`` list, ``content`` str or None).
    tool_required:
        When True, the response must contain a respond call; absence is
        flagged via ``_finalize_warning``.

    Returns
    -------
    dict
        New response dict (input is never mutated).
    """
    # Always attempt to strip respond calls
    stripped = strip_respond_call(response)

    # Check enforcement only when tool_required
    if not tool_required:
        return stripped

    # If strip_respond_call found and processed a respond call, content was set
    # and tool_calls was reduced — that counts as valid.
    # We check enforcement on the *original* response (before strip) because
    # strip_respond_call only succeeds when a respond call is present.
    enforcement = enforce_final_response(response, required=True)
    if not enforcement["valid"]:
        # No respond call found — add warning to stripped response
        result = dict(stripped)
        result["_finalize_warning"] = enforcement
        return result

    return stripped
