#!/usr/bin/env python3
"""Generic connector / tool-adapter layer (primitive P15).

Helm today only has permission matrices (``scripts/tool_groups.py``) and
LLM-provider switches — no single seam for *calling* an external tool or
MCP server. This module is that seam: new connectors are onboarded by
adding a DATA entry to ``references/connectors.json``, not by writing new
invocation code.

Public API
----------
``ToolAdapter``
    Structural protocol every adapter implements: ``describe() -> dict``
    (name + supported ops + arg schema) and ``invoke(op, args) -> dict``.

``load_adapters(path) -> dict[str, ToolAdapter]``
    Read the connector registry JSON and build one adapter instance per
    well-formed entry. Unknown/malformed entries are skipped and reported
    to stderr — never raised — mirroring the drift-tolerant contract of
    :func:`commands.read_json`.

``invoke_tool(registry, name, op, args, *, guard=None) -> dict``
    Look up ``name`` in ``registry``, run the optional pre-invocation
    ``guard`` hook, then call the adapter. Unknown tools and denied calls
    return an ``{"ok": False, "error": ...}`` dict rather than raising.

Guard hook contract
--------------------
``guard(name: str, op: str, args: dict) -> bool | dict``

Return ``True``/``False`` or a dict with an ``"allow"`` key (and an
optional ``"reason"``) to allow or deny the call before it reaches the
adapter. The default guard is a no-op that allows everything — this is
the plug point for policy: ``scripts.command_guard.evaluate_command_guard``
or a lookup through ``scripts.tool_groups.classify_tool`` can be wrapped
in a small adapter function and passed as ``guard=`` here to gate tool
calls the same way shell commands are gated today.

Built-in adapters
------------------
``EchoAdapter`` (registry ``type: "local_callable"`` or ``"echo"``)
    A trivial, network-free adapter used for tests and as a template for
    wrapping a local Python callable. Its one op, ``"echo"``, returns the
    args it was given.

``McpAdapterStub`` (registry ``type: "mcp"``)
    A thin, documented extension point for a real MCP client. It can
    ``describe()`` itself (so it shows up in tool listings / docs) but
    ``invoke()`` always returns ``{"ok": False, "error": "not_implemented"}``
    — wiring an actual MCP session is intentionally out of scope here.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from commands import read_json

__all__ = [
    "ToolAdapter",
    "EchoAdapter",
    "McpAdapterStub",
    "load_adapters",
    "invoke_tool",
]


# ---------------------------------------------------------------------------
# Adapter protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class ToolAdapter(Protocol):
    """Structural interface every connector adapter must satisfy."""

    def describe(self) -> dict:
        """Return {"name": ..., "ops": [...], "arg_schema": {...}}."""
        ...

    def invoke(self, op: str, args: dict) -> dict:
        """Perform ``op`` with ``args`` and return a result dict.

        Must never raise for expected failure modes (unknown op, bad
        args) — return ``{"ok": False, "error": ...}`` instead.
        """
        ...


# ---------------------------------------------------------------------------
# Built-in adapters
# ---------------------------------------------------------------------------


class EchoAdapter:
    """No-network, testable adapter. Wraps a fixed set of local "ops".

    Registered from ``type: "local_callable"`` or ``type: "echo"`` entries
    in ``references/connectors.json``. This is the template for wrapping
    an in-process Python callable behind the same ``invoke()`` seam used
    by real connectors.
    """

    def __init__(
        self,
        name: str,
        ops: list[str] | None = None,
        config: dict | None = None,
    ) -> None:
        self.name = name
        self.ops = list(ops) if ops else ["echo"]
        self.config = dict(config) if config else {}

    def describe(self) -> dict:
        return {
            "name": self.name,
            "type": "local_callable",
            "ops": list(self.ops),
            "arg_schema": {
                op: {"args": "object (echoed back unchanged)"} for op in self.ops
            },
        }

    def invoke(self, op: str, args: dict) -> dict:
        if op not in self.ops:
            return {
                "ok": False,
                "error": "unknown_op",
                "tool": self.name,
                "op": op,
            }
        return {
            "ok": True,
            "tool": self.name,
            "op": op,
            "result": dict(args or {}),
        }


class McpAdapterStub:
    """Documented extension point for a real MCP-server client.

    This class intentionally does not open a session, spawn a process, or
    make any network call. It exists so a connector entry with
    ``type: "mcp"`` in ``references/connectors.json`` can be described
    (and thus discovered/listed) today, with the live client wired in
    later behind the exact same ``describe()``/``invoke()`` seam — no
    caller-visible change when that happens.
    """

    def __init__(
        self,
        name: str,
        ops: list[str] | None = None,
        config: dict | None = None,
    ) -> None:
        self.name = name
        self.ops = list(ops) if ops else []
        self.config = dict(config) if config else {}

    def describe(self) -> dict:
        return {
            "name": self.name,
            "type": "mcp",
            "ops": list(self.ops),
            "arg_schema": {},
            "status": "stub",
        }

    def invoke(self, op: str, args: dict) -> dict:  # noqa: ARG002 - stub signature parity
        return {
            "ok": False,
            "error": "not_implemented",
            "tool": self.name,
            "op": op,
            "detail": (
                "McpAdapterStub is a documented extension point; wire a real "
                "MCP client into invoke() to make this connector live."
            ),
        }


_ADAPTER_FACTORIES = {
    "local_callable": EchoAdapter,
    "echo": EchoAdapter,
    "mcp": McpAdapterStub,
}


# ---------------------------------------------------------------------------
# Registry loading
# ---------------------------------------------------------------------------


def _warn_skip(reason: str, entry: Any) -> None:
    print(
        f"warning: skipping malformed connector entry ({reason}): {entry!r}",
        file=sys.stderr,
    )


def _build_adapter(entry: Any) -> tuple[str, ToolAdapter] | None:
    if not isinstance(entry, dict):
        _warn_skip("entry is not an object", entry)
        return None

    name = entry.get("name")
    if not isinstance(name, str) or not name:
        _warn_skip("missing or invalid 'name'", entry)
        return None

    adapter_type = entry.get("type")
    factory = _ADAPTER_FACTORIES.get(adapter_type) if isinstance(adapter_type, str) else None
    if factory is None:
        _warn_skip(f"unknown type {adapter_type!r} for connector {name!r}", entry)
        return None

    ops = entry.get("ops")
    if ops is not None and not isinstance(ops, list):
        _warn_skip(f"'ops' must be a list for connector {name!r}", entry)
        return None

    config = entry.get("config")
    if config is not None and not isinstance(config, dict):
        _warn_skip(f"'config' must be an object for connector {name!r}", entry)
        return None

    return name, factory(name=name, ops=ops, config=config)


def load_adapters(path: Path) -> dict[str, ToolAdapter]:
    """Load the connector registry at ``path`` into adapter instances.

    Reads ``{"connectors": [...]}``. Missing file, unreadable/malformed
    JSON, or a non-dict top level all resolve to an empty registry (via
    :func:`commands.read_json`'s drift-tolerant default). Each entry in
    the ``connectors`` list is validated independently; a malformed entry
    is skipped and reported to stderr but never crashes the load, and
    well-formed entries in the same file still load.
    """
    raw = read_json(path, {})
    if not isinstance(raw, dict):
        return {}

    connectors = raw.get("connectors")
    if not isinstance(connectors, list):
        return {}

    adapters: dict[str, ToolAdapter] = {}
    for entry in connectors:
        built = _build_adapter(entry)
        if built is None:
            continue
        name, adapter = built
        adapters[name] = adapter
    return adapters


# ---------------------------------------------------------------------------
# Invocation
# ---------------------------------------------------------------------------


def _allow_all(name: str, op: str, args: dict) -> dict:  # noqa: ARG001 - default no-op guard
    return {"allow": True}


def invoke_tool(
    registry: dict[str, ToolAdapter],
    name: str,
    op: str,
    args: dict,
    *,
    guard: Any = None,
) -> dict:
    """Route a call through ``registry[name].invoke(op, args)``.

    ``guard`` is an optional pre-invocation hook, ``guard(name, op, args)
    -> bool | dict``, checked before the adapter is called. It defaults to
    a no-op that allows everything. Wrap ``scripts.command_guard`` or
    ``scripts.tool_groups.classify_tool`` in a small function with this
    signature to gate tool calls under the same policy shell commands use.

    Never raises for expected failure modes: an unknown tool name, a
    denying guard, or an adapter-level error all return
    ``{"ok": False, "error": ...}``.
    """
    adapter = registry.get(name)
    if adapter is None:
        return {"ok": False, "error": "unknown_tool", "tool": name, "op": op}

    guard_fn = guard if guard is not None else _allow_all
    decision = guard_fn(name, op, args)
    if isinstance(decision, dict):
        allowed = bool(decision.get("allow", True))
        reason = decision.get("reason")
    else:
        allowed = bool(decision)
        reason = None

    if not allowed:
        result = {"ok": False, "error": "denied", "tool": name, "op": op}
        if reason:
            result["reason"] = reason
        return result

    try:
        return adapter.invoke(op, args)
    except Exception as exc:  # noqa: BLE001 - adapters must not be able to crash the caller
        return {
            "ok": False,
            "error": "adapter_error",
            "tool": name,
            "op": op,
            "detail": str(exc),
        }
