"""Synthetic Respond Tool — Spike (Task 16).

Spike phase: schema injection, respond-call stripping, and terminal
enforcement in isolation. Full runner integration is out of scope.

Motivation (Forge §5)
---------------------
Small and self-hosted local models frequently drift between "text answer"
and "tool call" modes.  A common failure mode is that the model emits a
plain-text final answer when the runner expects a tool call — breaking the
agent loop.

Forge mitigates this by injecting a synthetic ``respond(message=...)``
tool into every request.  The model stays in tool-calling mode for all
turns, including its final reply.  The runner strips the synthetic call
when delivering the answer to the user, so the agent loop remains clean.

This module provides the three building-block functions that a runner
(or proxy) would call:

1. ``respond_tool_schema`` — load and cache the canonical schema dict.
2. ``inject_respond_tool`` — prepend/append the respond tool to a tool
   list, idempotently.
3. ``strip_respond_call`` — extract the respond message from a response
   payload and promote it to ``content``.
4. ``enforce_final_response`` — validate that a response uses the respond
   tool when one is required.

Purity contract
---------------
- ``inject_respond_tool`` returns a *new* list; the input list is never
  mutated.
- ``strip_respond_call`` returns a *new* dict; the input dict is never
  mutated.
- ``respond_tool_schema`` caches the parsed schema in a module-level dict
  so repeated calls pay no I/O cost.  The cache dict itself is an
  implementation detail; callers receive a fresh copy of the value.
- ``enforce_final_response`` is a pure predicate with no side effects.

Non-JSON arguments
------------------
When a ``respond`` tool call carries arguments that are not valid JSON,
``strip_respond_call`` degrades gracefully: it returns the original
response unchanged and adds a ``_strip_warning`` key describing the
parse failure.  It never raises.
"""

import copy
import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Internal cache — populated once per process on first call to
# respond_tool_schema().  Never mutate this after population.
# ---------------------------------------------------------------------------
_SCHEMA_CACHE: dict = {}

_SCHEMA_PATH = Path(__file__).parent.parent / "references" / "respond_tool_schema.json"

_RESPOND_TOOL_NAME = "respond"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def respond_tool_schema() -> dict:
    """Return the canonical respond-tool schema dict.

    The schema is loaded from ``references/respond_tool_schema.json``
    on the first call and cached for subsequent calls.  Callers receive
    the cached object directly; do not mutate the return value.

    Returns
    -------
    dict
        OpenAI-style function-tool schema with keys ``type``,
        ``function.name``, ``function.description``, and
        ``function.parameters``.

    Raises
    ------
    FileNotFoundError
        If the schema file cannot be located.
    json.JSONDecodeError
        If the schema file contains invalid JSON.
    """
    if not _SCHEMA_CACHE:
        schema_path = Path(os.environ.get("RESPOND_TOOL_SCHEMA_PATH", str(_SCHEMA_PATH))).expanduser()
        with schema_path.open("r", encoding="utf-8") as fh:
            loaded = json.load(fh)
        _SCHEMA_CACHE.update(loaded)
    return dict(_SCHEMA_CACHE)


def inject_respond_tool(tools: list[dict]) -> list[dict]:
    """Return a new list with the respond tool appended, if not already present.

    Idempotent: calling this function on a list that already contains a
    ``respond`` tool returns a new list with identical contents (no
    duplicate is added).

    Parameters
    ----------
    tools:
        Existing list of tool schema dicts.  Must not be ``None``.

    Returns
    -------
    list[dict]
        New list — the input list is never mutated.
    """
    for tool in tools:
        name = _tool_name(tool)
        if name == _RESPOND_TOOL_NAME:
            return list(tools)

    schema = respond_tool_schema()
    return list(tools) + [schema]


def strip_respond_call(response: dict) -> dict:
    """Extract the respond tool call and promote its message to content.

    If ``response`` contains a ``respond`` tool call in ``tool_calls``,
    this function returns a *new* response dict where:

    - The ``respond`` call is removed from ``tool_calls``.
    - ``response['content']`` is set to the ``message`` argument of the
      first ``respond`` call found (subsequent respond calls, if any,
      are logged as warnings and also removed).

    If no ``respond`` call is present, the original dict is returned
    unchanged.

    Non-JSON arguments degrade gracefully: the original response is
    returned with an added ``_strip_warning`` key.  The input is never
    mutated.

    Parameters
    ----------
    response:
        Model response dict.  Expected keys: ``tool_calls`` (list),
        ``content`` (str or None).

    Returns
    -------
    dict
        New dict with the respond call stripped, or the original dict
        (augmented with ``_strip_warning``) on parse failure.
    """
    tool_calls = response.get("tool_calls", [])
    if not tool_calls:
        return response

    respond_calls = []
    other_calls = []
    for call in tool_calls:
        if _tool_call_name(call) == _RESPOND_TOOL_NAME:
            respond_calls.append(call)
        else:
            other_calls.append(call)

    if not respond_calls:
        return response

    if len(respond_calls) > 1:
        logger.warning(
            "strip_respond_call: found %d respond calls; using the first, "
            "discarding the rest.",
            len(respond_calls),
        )

    first_respond = respond_calls[0]
    raw_args = first_respond.get("arguments", "{}")

    try:
        args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
        message = args.get("message", "")
    except (json.JSONDecodeError, AttributeError) as exc:
        warning_text = (
            f"strip_respond_call: could not parse respond arguments "
            f"({type(exc).__name__}: {exc}); response returned unchanged."
        )
        logger.warning(warning_text)
        result = dict(response)
        result["_strip_warning"] = warning_text
        return result

    new_response = dict(response)
    new_response["tool_calls"] = other_calls
    new_response["content"] = message
    return new_response


def enforce_final_response(response: dict, required: bool) -> dict:
    """Validate that the response uses the respond tool when required.

    Parameters
    ----------
    response:
        Model response dict.
    required:
        When ``True``, the response MUST contain a ``respond`` tool call
        to be considered valid.  When ``False``, any response is valid.

    Returns
    -------
    dict
        ``{'valid': True}`` when the response is acceptable.
        ``{'valid': False, 'issue': 'terminal_without_respond'}`` when
        ``required=True`` and no respond call is found.
    """
    if not required:
        return {"valid": True}

    tool_calls = response.get("tool_calls", [])
    for call in tool_calls:
        if _tool_call_name(call) == _RESPOND_TOOL_NAME:
            return {"valid": True}

    return {"valid": False, "issue": "terminal_without_respond"}


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _tool_name(tool: dict) -> str:
    """Extract the tool name from an OpenAI-style tool schema dict."""
    # Handle both {"name": ...} and {"function": {"name": ...}} shapes.
    if "function" in tool:
        return tool["function"].get("name", "")
    return tool.get("name", "")


def _tool_call_name(call: dict) -> str:
    """Extract the tool name from a tool-call dict in a response payload."""
    # Tool calls typically have a flat {"name": ..., "arguments": ...}
    # shape, but some runtimes nest them under "function".
    if "function" in call:
        return call["function"].get("name", "")
    return call.get("name", "")
