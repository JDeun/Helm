"""Tests for scripts/validation_gate.py.

Coverage targets (spec §TDD, items 6-11):
6.  .py extension → returns ["python -m py_compile <path>"] with substituted path.
7.  .ts → tsc command.
8.  .json → json.tool command.
9.  .md → empty list.
10. Unknown extension .xyz → empty list.
11. run_gates calls runner with the expected arglist; mock runner returns
    synthetic result; function returns parsed result.
"""
from __future__ import annotations

import importlib
import sys
import types
from typing import Any
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _get_module():
    """Return a freshly loaded scripts.validation_gate module."""
    mod_name = "scripts.validation_gate"
    if mod_name in sys.modules:
        del sys.modules[mod_name]
    return importlib.import_module(mod_name)


# ---------------------------------------------------------------------------
# Test 6 — .py extension
# ---------------------------------------------------------------------------

class TestGatesForPathPython:
    def test_py_extension_returns_py_compile_command(self):
        mod = _get_module()
        result = mod.gates_for_path("scripts/edit_policy.py")
        assert len(result) == 1
        assert result[0] == "python -m py_compile scripts/edit_policy.py"

    def test_py_path_substitution_is_correct(self):
        mod = _get_module()
        path = "/absolute/path/to/foo.py"
        result = mod.gates_for_path(path)
        assert path in result[0]

    def test_uppercase_py_extension_treated_case_insensitively(self):
        """Extension matching is case-insensitive per spec."""
        mod = _get_module()
        result = mod.gates_for_path("SCRIPT.PY")
        assert len(result) == 1
        assert "py_compile" in result[0]


# ---------------------------------------------------------------------------
# Test 7 — .ts extension
# ---------------------------------------------------------------------------

class TestGatesForPathTypeScript:
    def test_ts_extension_returns_tsc_command(self):
        mod = _get_module()
        result = mod.gates_for_path("src/main.ts")
        assert len(result) == 1
        assert "tsc" in result[0]
        assert "src/main.ts" in result[0]

    def test_tsx_extension_also_returns_tsc_command(self):
        mod = _get_module()
        result = mod.gates_for_path("components/Button.tsx")
        assert len(result) == 1
        assert "tsc" in result[0]

    def test_ts_command_contains_no_emit(self):
        mod = _get_module()
        result = mod.gates_for_path("index.ts")
        assert "--noEmit" in result[0]


# ---------------------------------------------------------------------------
# Test 8 — .json extension
# ---------------------------------------------------------------------------

class TestGatesForPathJson:
    def test_json_extension_returns_json_tool_command(self):
        mod = _get_module()
        result = mod.gates_for_path("references/gate_policy.json")
        assert len(result) == 1
        assert "json.tool" in result[0]
        assert "references/gate_policy.json" in result[0]

    def test_json_path_substituted_correctly(self):
        mod = _get_module()
        path = "config/settings.json"
        result = mod.gates_for_path(path)
        assert result[0].endswith(path)


# ---------------------------------------------------------------------------
# Test 9 — .md extension
# ---------------------------------------------------------------------------

class TestGatesForPathMarkdown:
    def test_md_returns_empty_list(self):
        mod = _get_module()
        result = mod.gates_for_path("README.md")
        assert result == []

    def test_md_uppercase_returns_empty_list(self):
        mod = _get_module()
        result = mod.gates_for_path("README.MD")
        assert result == []


# ---------------------------------------------------------------------------
# Test 10 — unknown extension
# ---------------------------------------------------------------------------

class TestGatesForPathUnknownExtension:
    def test_xyz_extension_returns_empty_list(self):
        mod = _get_module()
        result = mod.gates_for_path("archive.xyz")
        assert result == []

    def test_no_extension_returns_empty_list(self):
        mod = _get_module()
        result = mod.gates_for_path("Makefile")
        assert result == []

    def test_rb_extension_returns_empty_list(self):
        mod = _get_module()
        result = mod.gates_for_path("script.rb")
        assert result == []


# ---------------------------------------------------------------------------
# Test 11 — run_gates with mock runner
# ---------------------------------------------------------------------------

class TestRunGates:
    def _make_completed_process(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> Any:
        """Build a minimal CompletedProcess-like object."""
        result = MagicMock()
        result.returncode = returncode
        result.stdout = stdout
        result.stderr = stderr
        return result

    def test_run_gates_calls_runner_with_expected_argv(self):
        mod = _get_module()
        mock_runner = MagicMock(return_value=self._make_completed_process())
        path = "scripts/foo.py"

        mod.run_gates(path, runner=mock_runner)

        mock_runner.assert_called_once()
        call_args = mock_runner.call_args
        argv = call_args[0][0]  # first positional arg → argv list
        assert argv == ["python", "-m", "py_compile", path]

    def test_run_gates_passes_capture_output_and_text_true(self):
        mod = _get_module()
        mock_runner = MagicMock(return_value=self._make_completed_process())

        mod.run_gates("x.py", runner=mock_runner)

        call_kwargs = mock_runner.call_args[1]
        assert call_kwargs.get("capture_output") is True
        assert call_kwargs.get("text") is True

    def test_run_gates_returns_parsed_result(self):
        mod = _get_module()
        fake_result = self._make_completed_process(
            returncode=1,
            stdout="some output",
            stderr="compile error",
        )
        mock_runner = MagicMock(return_value=fake_result)

        results = mod.run_gates("broken.py", runner=mock_runner)

        assert len(results) == 1
        entry = results[0]
        assert entry["returncode"] == 1
        assert entry["stdout"] == "some output"
        assert entry["stderr"] == "compile error"
        assert entry["cmd"] == ["python", "-m", "py_compile", "broken.py"]

    def test_run_gates_returns_empty_list_for_md(self):
        mod = _get_module()
        mock_runner = MagicMock()

        results = mod.run_gates("README.md", runner=mock_runner)

        assert results == []
        mock_runner.assert_not_called()

    def test_run_gates_returns_empty_list_for_unknown_extension(self):
        mod = _get_module()
        mock_runner = MagicMock()

        results = mod.run_gates("data.csv", runner=mock_runner)

        assert results == []
        mock_runner.assert_not_called()

    def test_run_gates_js_uses_node_check(self):
        mod = _get_module()
        mock_runner = MagicMock(return_value=self._make_completed_process())

        mod.run_gates("app.js", runner=mock_runner)

        argv = mock_runner.call_args[0][0]
        assert argv == ["node", "--check", "app.js"]

    def test_run_gates_mjs_extension_treated_as_javascript(self):
        mod = _get_module()
        mock_runner = MagicMock(return_value=self._make_completed_process())

        mod.run_gates("module.mjs", runner=mock_runner)

        argv = mock_runner.call_args[0][0]
        assert argv[0] == "node"

    def test_run_gates_json_uses_python_json_tool(self):
        mod = _get_module()
        mock_runner = MagicMock(return_value=self._make_completed_process())

        mod.run_gates("config.json", runner=mock_runner)

        argv = mock_runner.call_args[0][0]
        assert "json.tool" in " ".join(argv)
        assert "config.json" in argv

    def test_run_gates_multiple_results_all_captured(self):
        """If a language had multiple gates, all results are returned."""
        mod = _get_module()

        # Temporarily inject a multi-gate entry for testing purposes.
        original_policy = mod._load_gate_policy()
        patched_policy = dict(original_policy)
        patched_policy["python"] = [
            "python -m py_compile {path}",
            "python -m py_compile {path}",  # duplicate to simulate multi-gate
        ]

        call_count = 0

        def counting_runner(argv, **kwargs):
            nonlocal call_count
            call_count += 1
            result = MagicMock()
            result.returncode = 0
            result.stdout = f"run {call_count}"
            result.stderr = ""
            return result

        import unittest.mock as mock
        with mock.patch.object(mod, "_load_gate_policy", return_value=patched_policy):
            # Also reset cache to use the patched policy.
            results = mod.run_gates("multi.py", runner=counting_runner)

        assert len(results) == 2
        assert call_count == 2


# ---------------------------------------------------------------------------
# Fix 5 — copy-on-read test for _load_gate_policy
# ---------------------------------------------------------------------------

class TestLoadGatePolicyCopyOnRead:
    def test_load_gate_policy_returns_independent_copy(self):
        """Mutating the returned dict does not corrupt the cache for the next call."""
        mod = _get_module()
        p1 = mod._load_gate_policy()
        # Mutate — inject a key and tamper with an existing list
        p1["__injected__"] = ["poison"]
        if "python" in p1:
            p1["python"].append("__evil__")
        p2 = mod._load_gate_policy()
        assert "__injected__" not in p2
        if "python" in p2:
            assert "__evil__" not in p2["python"]
