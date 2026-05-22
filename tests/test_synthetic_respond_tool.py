"""Tests for scripts/synthetic_respond_tool.py (Task 16).

Coverage targets (11 cases):
 1. respond_tool_schema() returns dict with required name/description/parameters.
 2. inject_respond_tool([]) returns list of length 1 containing respond.
 3. inject_respond_tool([respond_tool]) is idempotent (still length 1).
 4. inject_respond_tool([other_tool]) returns length-2 list with respond appended.
 5. strip_respond_call with a single respond call → content set, tool_calls=[].
 6. strip_respond_call with [other, respond] → tool_calls=[other], content=respond message.
 7. strip_respond_call with no respond call → dict unchanged.
 8. strip_respond_call with non-JSON arguments → graceful degradation (_strip_warning key).
 9. enforce_final_response({tool_calls:[]}, required=True) → invalid with 'terminal_without_respond'.
10. enforce_final_response({tool_calls:[respond_call]}, required=True) → valid.
11. enforce_final_response({content:'final', tool_calls:[]}, required=False) → valid.
"""

import importlib
import json
import sys
import unittest
from pathlib import Path

# Ensure the scripts directory is importable regardless of how pytest is invoked.
_SCRIPTS_DIR = Path(__file__).parent.parent / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))


def _fresh_module():
    """Return a freshly imported copy of synthetic_respond_tool.

    Re-importing clears the module-level cache so each test that needs a
    pristine state gets one without side effects from earlier tests.
    """
    mod_name = "synthetic_respond_tool"
    if mod_name in sys.modules:
        del sys.modules[mod_name]
    return importlib.import_module(mod_name)


class TestRespondToolSchema(unittest.TestCase):
    """Case 1 — schema structure."""

    def setUp(self):
        self.srt = _fresh_module()

    def test_schema_has_required_keys(self):
        schema = self.srt.respond_tool_schema()
        self.assertIn("type", schema)
        self.assertIn("function", schema)
        fn = schema["function"]
        self.assertEqual(fn["name"], "respond")
        self.assertIn("description", fn)
        self.assertTrue(fn["description"])  # non-empty
        self.assertIn("parameters", fn)
        params = fn["parameters"]
        self.assertIn("properties", params)
        self.assertIn("message", params["properties"])

    def test_schema_is_cached(self):
        """Second call returns equal content (cache hit), but a new dict copy."""
        s1 = self.srt.respond_tool_schema()
        s2 = self.srt.respond_tool_schema()
        self.assertEqual(s1, s2)
        self.assertIsNot(s1, s2)  # copy-on-read: different dict objects


class TestInjectRespondTool(unittest.TestCase):
    """Cases 2-4 — inject_respond_tool."""

    def setUp(self):
        self.srt = _fresh_module()

    def _respond_schema(self):
        return self.srt.respond_tool_schema()

    def test_inject_into_empty_list(self):
        """Case 2 — empty list → length-1 list containing respond."""
        result = self.srt.inject_respond_tool([])
        self.assertEqual(len(result), 1)
        name = result[0].get("function", {}).get("name", result[0].get("name"))
        self.assertEqual(name, "respond")

    def test_inject_idempotent(self):
        """Case 3 — list already has respond → no duplicate added."""
        schema = self._respond_schema()
        result = self.srt.inject_respond_tool([schema])
        self.assertEqual(len(result), 1)

    def test_inject_appends_to_existing(self):
        """Case 4 — other tool present → respond appended at end."""
        other = {"type": "function", "function": {"name": "search", "description": "search"}}
        result = self.srt.inject_respond_tool([other])
        self.assertEqual(len(result), 2)
        last_name = result[-1].get("function", {}).get("name", result[-1].get("name"))
        self.assertEqual(last_name, "respond")

    def test_does_not_mutate_input(self):
        """inject_respond_tool must not mutate the input list."""
        original = []
        self.srt.inject_respond_tool(original)
        self.assertEqual(original, [])

        other = {"type": "function", "function": {"name": "x"}}
        original2 = [other]
        self.srt.inject_respond_tool(original2)
        self.assertEqual(len(original2), 1)


class TestStripRespondCall(unittest.TestCase):
    """Cases 5-8 — strip_respond_call."""

    def setUp(self):
        self.srt = _fresh_module()

    def _respond_call(self, message="hi"):
        return {"name": "respond", "arguments": json.dumps({"message": message})}

    def _other_call(self):
        return {"name": "search", "arguments": json.dumps({"query": "test"})}

    def test_single_respond_call_stripped(self):
        """Case 5 — respond call → content promoted, tool_calls empty."""
        response = {"tool_calls": [self._respond_call("hi")], "content": None}
        result = self.srt.strip_respond_call(response)
        self.assertEqual(result["content"], "hi")
        self.assertEqual(result["tool_calls"], [])

    def test_respond_mixed_with_other_calls(self):
        """Case 6 — [other, respond] → other kept, respond message promoted."""
        other = self._other_call()
        response = {
            "tool_calls": [other, self._respond_call("final answer")],
            "content": None,
        }
        result = self.srt.strip_respond_call(response)
        self.assertEqual(result["content"], "final answer")
        self.assertEqual(len(result["tool_calls"]), 1)
        self.assertEqual(result["tool_calls"][0]["name"], "search")

    def test_no_respond_call_unchanged(self):
        """Case 7 — no respond call → original response returned unchanged."""
        response = {"content": "plain", "tool_calls": []}
        result = self.srt.strip_respond_call(response)
        self.assertIs(result, response)

    def test_non_json_arguments_graceful(self):
        """Case 8 — bad JSON in arguments → _strip_warning added, input unchanged."""
        bad_call = {"name": "respond", "arguments": "NOT JSON {{{{"}
        response = {"tool_calls": [bad_call], "content": None}
        result = self.srt.strip_respond_call(response)
        self.assertIn("_strip_warning", result)
        # Original response must not be mutated
        self.assertNotIn("_strip_warning", response)
        # Content must NOT have been modified
        self.assertIsNone(result.get("content"))

    def test_does_not_mutate_input(self):
        """strip_respond_call must return a new dict."""
        response = {"tool_calls": [self._respond_call("hi")], "content": None}
        result = self.srt.strip_respond_call(response)
        self.assertIsNot(result, response)
        # Original still has the respond call
        self.assertEqual(len(response["tool_calls"]), 1)

    def test_multiple_respond_calls_uses_first(self):
        """Multiple respond calls: first message wins."""
        c1 = self._respond_call("first")
        c2 = self._respond_call("second")
        response = {"tool_calls": [c1, c2], "content": None}
        result = self.srt.strip_respond_call(response)
        self.assertEqual(result["content"], "first")
        self.assertEqual(result["tool_calls"], [])


class TestEnforceFinalResponse(unittest.TestCase):
    """Cases 9-11 — enforce_final_response."""

    def setUp(self):
        self.srt = _fresh_module()

    def _respond_call(self):
        return {"name": "respond", "arguments": json.dumps({"message": "done"})}

    def test_empty_tool_calls_required_is_invalid(self):
        """Case 9 — no respond call, required=True → invalid."""
        result = self.srt.enforce_final_response({"tool_calls": []}, required=True)
        self.assertFalse(result["valid"])
        self.assertEqual(result["issue"], "terminal_without_respond")

    def test_respond_call_required_is_valid(self):
        """Case 10 — respond call present, required=True → valid."""
        result = self.srt.enforce_final_response(
            {"tool_calls": [self._respond_call()]}, required=True
        )
        self.assertTrue(result["valid"])

    def test_not_required_always_valid(self):
        """Case 11 — required=False → always valid regardless of content."""
        result = self.srt.enforce_final_response(
            {"content": "final", "tool_calls": []}, required=False
        )
        self.assertTrue(result["valid"])

    def test_required_false_with_no_tool_calls_key(self):
        """Edge — missing tool_calls key, required=False → valid."""
        result = self.srt.enforce_final_response({}, required=False)
        self.assertTrue(result["valid"])

    def test_required_true_with_missing_tool_calls_key(self):
        """Edge — missing tool_calls key, required=True → invalid."""
        result = self.srt.enforce_final_response({}, required=True)
        self.assertFalse(result["valid"])
        self.assertEqual(result["issue"], "terminal_without_respond")


class TestRespondToolSchemaCopyOnRead(unittest.TestCase):
    """Fix 3 — copy-on-read and expanduser tests."""

    def setUp(self):
        self.srt = _fresh_module()

    def test_respond_tool_schema_returns_copy_not_cache(self):
        """Mutating the returned dict does not affect a subsequent call."""
        s1 = self.srt.respond_tool_schema()
        s1["__injected__"] = True
        s2 = self.srt.respond_tool_schema()
        self.assertNotIn("__injected__", s2)

    def test_RESPOND_TOOL_SCHEMA_PATH_expands_tilde(self):
        """Env var with ~ is expanded to the home directory.

        Writes a minimal schema JSON to a real temp file under HOME so that
        the expanduser() resolution can be verified end-to-end without any
        monkey-patching.
        """
        import os
        import tempfile
        import json as _json
        from pathlib import Path
        from unittest.mock import patch

        home = Path.home()
        # Create a real file inside a tmp dir under HOME so that a tilde path
        # pointing to it expands correctly.
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".json",
            dir=home,
            delete=False,
        ) as tf:
            schema_data = {
                "type": "function",
                "function": {
                    "name": "respond",
                    "description": "test",
                    "parameters": {
                        "type": "object",
                        "properties": {"message": {"type": "string"}},
                        "required": ["message"],
                    },
                },
            }
            _json.dump(schema_data, tf)
            tmp_name = Path(tf.name).name  # just the filename, no dir

        tilde_path = f"~/{tmp_name}"
        original_env = os.environ.get("RESPOND_TOOL_SCHEMA_PATH")
        os.environ["RESPOND_TOOL_SCHEMA_PATH"] = tilde_path
        self.srt._SCHEMA_CACHE.clear()
        try:
            result = self.srt.respond_tool_schema()
        finally:
            if original_env is None:
                del os.environ["RESPOND_TOOL_SCHEMA_PATH"]
            else:
                os.environ["RESPOND_TOOL_SCHEMA_PATH"] = original_env
            (home / tmp_name).unlink(missing_ok=True)

        # If expanduser worked, the file was read correctly and schema populated.
        self.assertIn("type", result)
        self.assertEqual(result.get("function", {}).get("name"), "respond")


if __name__ == "__main__":
    unittest.main()
