"""Tests for scripts/edit_policy.py.

Coverage targets (spec §TDD, items 1-5):
1. load_edit_policy returns dict with all documented keys.
2. should_create_checkpoint("skill_router") → True.
3. should_create_checkpoint("memory_cleanup") → False.
4. record_patch_failure returns 1, 2, 3 on three consecutive calls for same path.
5. next_action_for_path returns "retry" when below max, then
   "reload_context_then_decompose" at max+.
"""
from __future__ import annotations

import importlib
import sys
import types

import pytest


# ---------------------------------------------------------------------------
# Helpers — reload the module so cached state is fresh for each test run.
# ---------------------------------------------------------------------------

def _get_module():
    """Return a freshly loaded scripts.edit_policy module."""
    # Force a fresh import so the module-level cache is cleared.
    mod_name = "scripts.edit_policy"
    if mod_name in sys.modules:
        del sys.modules[mod_name]
    return importlib.import_module(mod_name)


# ---------------------------------------------------------------------------
# Test 1 — load_edit_policy returns dict with all documented keys
# ---------------------------------------------------------------------------

class TestLoadEditPolicy:
    def test_returns_dict_with_required_keys(self):
        mod = _get_module()
        policy = mod.load_edit_policy()
        assert isinstance(policy, dict)
        assert "default" in policy
        assert "max_patch_failures_per_file" in policy
        assert "on_repeated_patch_failure" in policy
        assert "requires_checkpoint_for" in policy

    def test_cached_on_second_call(self):
        mod = _get_module()
        p1 = mod.load_edit_policy()
        p2 = mod.load_edit_policy()
        assert p1 == p2  # copy-on-read: equal content, but distinct objects

    def test_reload_param_forces_fresh_read(self):
        mod = _get_module()
        p1 = mod.load_edit_policy()
        p2 = mod.load_edit_policy(reload=True)
        # May or may not be the same object, but must be equal in content.
        assert p1 == p2

    def test_missing_file_raises_file_not_found(self, tmp_path, monkeypatch):
        mod = _get_module()
        missing = tmp_path / "nonexistent.json"
        monkeypatch.setattr(mod, "_POLICY_PATH", missing)
        monkeypatch.setattr(mod, "_cached_policy", None)
        with pytest.raises(FileNotFoundError, match="edit_policy"):
            mod.load_edit_policy()


# ---------------------------------------------------------------------------
# Test 2 & 3 — should_create_checkpoint
# ---------------------------------------------------------------------------

class TestShouldCreateCheckpoint:
    def test_known_kind_returns_true(self):
        mod = _get_module()
        assert mod.should_create_checkpoint("skill_router") is True

    def test_another_known_kind_returns_true(self):
        mod = _get_module()
        assert mod.should_create_checkpoint("shared_workflow") is True

    def test_automation_kind_returns_true(self):
        mod = _get_module()
        assert mod.should_create_checkpoint("automation") is True

    def test_unknown_kind_returns_false(self):
        mod = _get_module()
        assert mod.should_create_checkpoint("memory_cleanup") is False

    def test_empty_string_returns_false(self):
        mod = _get_module()
        assert mod.should_create_checkpoint("") is False


# ---------------------------------------------------------------------------
# Test 4 — record_patch_failure increments counter correctly
# ---------------------------------------------------------------------------

class TestRecordPatchFailure:
    def test_three_consecutive_calls_return_1_2_3(self):
        mod = _get_module()
        state: dict = {}
        path = "scripts/some_file.py"

        assert mod.record_patch_failure(state, path) == 1
        assert mod.record_patch_failure(state, path) == 2
        assert mod.record_patch_failure(state, path) == 3

    def test_different_paths_track_independently(self):
        mod = _get_module()
        state: dict = {}
        path_a = "scripts/a.py"
        path_b = "scripts/b.py"

        mod.record_patch_failure(state, path_a)
        mod.record_patch_failure(state, path_a)
        assert mod.record_patch_failure(state, path_b) == 1
        assert mod.record_patch_failure(state, path_a) == 3

    def test_modifies_state_in_place(self):
        mod = _get_module()
        state: dict = {}
        mod.record_patch_failure(state, "x.py")
        assert "__patch_failures__" in state
        assert state["__patch_failures__"]["x.py"] == 1

    def test_starts_from_zero_on_empty_state(self):
        mod = _get_module()
        state: dict = {}
        result = mod.record_patch_failure(state, "new.py")
        assert result == 1


# ---------------------------------------------------------------------------
# Test 5 — next_action_for_path
# ---------------------------------------------------------------------------

class TestNextActionForPath:
    def _make_state(self, mod, path: str, failures: int) -> dict:
        state: dict = {}
        for _ in range(failures):
            mod.record_patch_failure(state, path)
        return state

    def test_returns_retry_below_max(self):
        mod = _get_module()
        policy = mod.load_edit_policy()
        max_failures = policy["max_patch_failures_per_file"]  # 2

        for count in range(max_failures):
            state = self._make_state(mod, "f.py", count)
            assert mod.next_action_for_path(state, "f.py") == "retry", (
                f"Expected 'retry' at count={count}"
            )

    def test_returns_escalation_at_max(self):
        mod = _get_module()
        policy = mod.load_edit_policy()
        max_failures = policy["max_patch_failures_per_file"]  # 2
        expected = policy["on_repeated_patch_failure"]

        state = self._make_state(mod, "f.py", max_failures)
        assert mod.next_action_for_path(state, "f.py") == expected

    def test_returns_escalation_above_max(self):
        mod = _get_module()
        policy = mod.load_edit_policy()
        max_failures = policy["max_patch_failures_per_file"]
        expected = policy["on_repeated_patch_failure"]

        state = self._make_state(mod, "f.py", max_failures + 5)
        assert mod.next_action_for_path(state, "f.py") == expected

    def test_returns_retry_for_unseen_path(self):
        mod = _get_module()
        state: dict = {}
        assert mod.next_action_for_path(state, "never_seen.py") == "retry"

    def test_escalation_value_matches_policy(self):
        mod = _get_module()
        policy = mod.load_edit_policy()
        assert policy["on_repeated_patch_failure"] == "reload_context_then_decompose"


# ---------------------------------------------------------------------------
# Fix 5 — copy-on-read tests
# ---------------------------------------------------------------------------

class TestLoadEditPolicyCopyOnRead:
    def test_load_edit_policy_returns_independent_copy(self):
        """Mutating the returned dict does not corrupt the cache for the next call."""
        mod = _get_module()
        p1 = mod.load_edit_policy()
        p1["__injected__"] = "poison"
        p1["requires_checkpoint_for"].append("__evil__")
        p2 = mod.load_edit_policy()
        assert "__injected__" not in p2
        assert "__evil__" not in p2.get("requires_checkpoint_for", [])
