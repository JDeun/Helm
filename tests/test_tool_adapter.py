"""Tests for scripts/tool_adapter.py — generic connector / tool-adapter layer.

Test plan:
  1. load_adapters(default registry) registers the built-in "echo" adapter;
     describe() reports name + supported ops + arg schema.
  2. invoke_tool() routes a call to the echo adapter and returns its result.
  3. invoke_tool() on an unknown tool name returns an error dict, never raises.
  4. invoke_tool() with a denying guard hook returns an error dict and never
     reaches the adapter's invoke().
  5. load_adapters() skips a malformed connector entry (missing "name" /
     unknown "type" / non-dict entry) without crashing, while still loading
     the well-formed entries in the same file.
  6. The MCP adapter stub describes itself but reports "not_implemented" on
     invoke — it is a documented extension point, not a live client.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.tool_adapter import (
    EchoAdapter,
    McpAdapterStub,
    invoke_tool,
    load_adapters,
)

_DEFAULT_REGISTRY_PATH = ROOT / "references" / "connectors.json"


# ---------------------------------------------------------------------------
# 1. register + describe
# ---------------------------------------------------------------------------

def test_load_adapters_registers_echo_from_default_registry():
    adapters = load_adapters(_DEFAULT_REGISTRY_PATH)

    assert "echo" in adapters
    description = adapters["echo"].describe()
    assert description["name"] == "echo"
    assert "echo" in description["ops"]
    assert "arg_schema" in description


# ---------------------------------------------------------------------------
# 2. invoke routes to the echo adapter
# ---------------------------------------------------------------------------

def test_invoke_tool_routes_to_echo_adapter():
    adapters = load_adapters(_DEFAULT_REGISTRY_PATH)

    result = invoke_tool(adapters, "echo", "echo", {"message": "hi"})

    assert result["ok"] is True
    assert result["result"]["message"] == "hi"


# ---------------------------------------------------------------------------
# 3. unknown tool -> error dict, never raises
# ---------------------------------------------------------------------------

def test_invoke_tool_unknown_tool_returns_error_dict():
    adapters = load_adapters(_DEFAULT_REGISTRY_PATH)

    result = invoke_tool(adapters, "does_not_exist", "op", {})

    assert result["ok"] is False
    assert result["error"] == "unknown_tool"


# ---------------------------------------------------------------------------
# 4. guard hook can deny
# ---------------------------------------------------------------------------

def test_invoke_tool_guard_hook_can_deny():
    calls: list[tuple[str, dict]] = []

    class SpyAdapter:
        def describe(self) -> dict:
            return {"name": "spy", "ops": ["noop"], "arg_schema": {}}

        def invoke(self, op: str, args: dict) -> dict:
            calls.append((op, args))
            return {"ok": True, "result": args}

    registry = {"spy": SpyAdapter()}

    def deny_all(name: str, op: str, args: dict) -> dict:
        return {"allow": False, "reason": "policy_denied"}

    result = invoke_tool(registry, "spy", "noop", {"x": 1}, guard=deny_all)

    assert result["ok"] is False
    assert result["error"] == "denied"
    assert result["reason"] == "policy_denied"
    assert calls == []  # adapter.invoke() must never be reached


def test_invoke_tool_default_guard_allows_all():
    registry = load_adapters(_DEFAULT_REGISTRY_PATH)

    result = invoke_tool(registry, "echo", "echo", {"a": 1})

    assert result["ok"] is True


# ---------------------------------------------------------------------------
# 5. malformed connector entries are skipped, not crashed on
# ---------------------------------------------------------------------------

def test_load_adapters_skips_malformed_entries(tmp_path, capsys):
    registry_path = tmp_path / "connectors.json"
    registry_path.write_text(
        json.dumps(
            {
                "connectors": [
                    {"name": "good_echo", "type": "local_callable", "ops": ["echo"]},
                    {"type": "local_callable"},  # missing "name"
                    {"name": "bad_type", "type": "not_a_real_type"},  # unknown type
                    "not_a_dict",  # wrong shape entirely
                    {"name": "good_mcp", "type": "mcp", "ops": ["query"]},
                ]
            }
        ),
        encoding="utf-8",
    )

    adapters = load_adapters(registry_path)

    assert set(adapters.keys()) == {"good_echo", "good_mcp"}
    assert isinstance(adapters["good_mcp"], McpAdapterStub)
    # Skips are reported (drift-tolerant, like commands.read_json) rather than silent.
    captured = capsys.readouterr()
    assert "bad_type" in captured.err or "not_a_real_type" in captured.err


def test_load_adapters_missing_file_returns_empty_dict(tmp_path):
    adapters = load_adapters(tmp_path / "does_not_exist.json")
    assert adapters == {}


def test_load_adapters_malformed_top_level_json_returns_empty_dict(tmp_path):
    registry_path = tmp_path / "connectors.json"
    registry_path.write_text("{not valid json", encoding="utf-8")

    adapters = load_adapters(registry_path)

    assert adapters == {}


# ---------------------------------------------------------------------------
# 6. MCP adapter is a documented stub, not a live client
# ---------------------------------------------------------------------------

def test_mcp_adapter_stub_describe_and_not_implemented_invoke():
    stub = McpAdapterStub(name="notion_mcp", ops=["query"])

    description = stub.describe()
    assert description["name"] == "notion_mcp"
    assert "query" in description["ops"]

    result = stub.invoke("query", {"q": "anything"})
    assert result["ok"] is False
    assert result["error"] == "not_implemented"


def test_echo_adapter_unknown_op_returns_error_dict():
    adapter = EchoAdapter(name="echo")

    result = adapter.invoke("not_a_real_op", {})

    assert result["ok"] is False
    assert result["error"] == "unknown_op"
