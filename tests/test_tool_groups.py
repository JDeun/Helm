"""Tests for scripts/tool_groups.py — tool-group grant declarations.

Test plan:
  1.  load_tool_groups("inspect_local") returns dict with 3 keys;
      allow/ask/deny are disjoint and their union covers all 8 tool groups.
  2-5. Same check for workspace_edit, risky_edit, service_ops, remote_handoff.
  6.  load_tool_groups("nope") raises ValueError.
  7.  classify_tool("workspace_edit", "apply_patch") → "allow".
  8.  classify_tool("workspace_edit", "secrets_read") → "deny" if deny; else
      fall back to the actual inventory mapping (ask).
      NOTE: per inventory Section 7, workspace_edit/secrets_read → "ask".
            The test uses the real data value, not the task-spec default.
  9.  classify_tool("workspace_edit", "broad_shell") → "ask".
  10. classify_tool("workspace_edit", "unknown_tool") → "ask" (conservative default).
  11. compute_grant("risky_edit", [...]) returns the correct three buckets.
  12. compute_grant preserves request order within each bucket.
  13. JSON file round-trips (load → dump → load) without semantic drift.
  14. Helm copy and workspace copy of tool_groups.json are byte-identical.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.tool_groups import classify_tool, compute_grant, load_tool_groups, _DATA_FILE

_ALL_TOOL_GROUPS = frozenset([
    "read_file",
    "apply_patch",
    "focused_test",
    "git_diff",
    "broad_shell",
    "external_network",
    "secrets_read",
    "destructive_git",
])

_KNOWN_PROFILES = [
    "inspect_local",
    "workspace_edit",
    "risky_edit",
    "service_ops",
    "remote_handoff",
]


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _assert_well_formed(profile: str) -> dict:
    """Assert that a profile's tool-group dict is well-formed and return it."""
    groups = load_tool_groups(profile)

    # Must have exactly the three keys.
    assert set(groups.keys()) == {"allow", "ask", "deny"}, (
        f"{profile}: expected keys {{allow, ask, deny}}, got {set(groups.keys())}"
    )

    # Disjoint: no tool appears in more than one bucket.
    allow = set(groups["allow"])
    ask = set(groups["ask"])
    deny = set(groups["deny"])
    assert allow & ask == set(), f"{profile}: allow ∩ ask = {allow & ask} (must be empty)"
    assert allow & deny == set(), f"{profile}: allow ∩ deny = {allow & deny} (must be empty)"
    assert ask & deny == set(), f"{profile}: ask ∩ deny = {ask & deny} (must be empty)"

    # Union must cover all 8 tool groups exactly.
    union = allow | ask | deny
    assert union == _ALL_TOOL_GROUPS, (
        f"{profile}: union of tool groups {union} != expected {_ALL_TOOL_GROUPS}. "
        f"Missing: {_ALL_TOOL_GROUPS - union}. Extra: {union - _ALL_TOOL_GROUPS}"
    )

    return groups


# ---------------------------------------------------------------------------
# Tests 1–5: well-formed check for each profile
# ---------------------------------------------------------------------------

def test_load_tool_groups_inspect_local():
    """Test 1: inspect_local returns well-formed tool-group dict."""
    groups = _assert_well_formed("inspect_local")
    # Spot-check key constraints from inventory:
    # read_file, focused_test, git_diff must be allow
    assert "read_file" in groups["allow"]
    assert "focused_test" in groups["allow"]
    assert "git_diff" in groups["allow"]
    # apply_patch must be deny (writes_allowed=false)
    assert "apply_patch" in groups["deny"]
    # external_network must be deny (network_allowed=false)
    assert "external_network" in groups["deny"]


def test_load_tool_groups_workspace_edit():
    """Test 2: workspace_edit returns well-formed tool-group dict."""
    groups = _assert_well_formed("workspace_edit")
    # Core allows
    assert "read_file" in groups["allow"]
    assert "apply_patch" in groups["allow"]
    assert "focused_test" in groups["allow"]
    assert "git_diff" in groups["allow"]
    # external_network must be deny (network_allowed=false)
    assert "external_network" in groups["deny"]


def test_load_tool_groups_risky_edit():
    """Test 3: risky_edit returns well-formed tool-group dict."""
    groups = _assert_well_formed("risky_edit")
    # Core allows
    assert "read_file" in groups["allow"]
    assert "apply_patch" in groups["allow"]
    # external_network must be deny (network_allowed=false)
    assert "external_network" in groups["deny"]
    # secrets_read is sensitive — must not be in allow
    assert "secrets_read" not in groups["allow"]


def test_load_tool_groups_service_ops():
    """Test 4: service_ops returns well-formed tool-group dict."""
    groups = _assert_well_formed("service_ops")
    # external_network must be allow (network_allowed=true, core purpose)
    assert "external_network" in groups["allow"]
    # destructive_git must be deny per inventory
    assert "destructive_git" in groups["deny"]


def test_load_tool_groups_remote_handoff():
    """Test 5: remote_handoff returns well-formed tool-group dict."""
    groups = _assert_well_formed("remote_handoff")
    # external_network must be allow (network is the defining capability)
    assert "external_network" in groups["allow"]
    # destructive_git must be deny (too risky on remote)
    assert "destructive_git" in groups["deny"]


# ---------------------------------------------------------------------------
# Test 6: unknown profile raises ValueError
# ---------------------------------------------------------------------------

def test_load_tool_groups_unknown_profile_raises():
    """Test 6: load_tool_groups('nope') raises ValueError."""
    import pytest
    with pytest.raises(ValueError, match="nope"):
        load_tool_groups("nope")


# ---------------------------------------------------------------------------
# Tests 7–10: classify_tool
# ---------------------------------------------------------------------------

def test_classify_tool_workspace_edit_apply_patch_is_allow():
    """Test 7: classify_tool('workspace_edit', 'apply_patch') → 'allow'."""
    assert classify_tool("workspace_edit", "apply_patch") == "allow"


def test_classify_tool_workspace_edit_secrets_read():
    """Test 8: classify_tool('workspace_edit', 'secrets_read').

    Per inventory Section 7, workspace_edit/secrets_read → 'ask'.
    The task spec listed it as 'deny' but the inventory takes precedence.
    """
    result = classify_tool("workspace_edit", "secrets_read")
    # Inventory says 'ask' for workspace_edit/secrets_read.
    assert result == "ask", (
        f"Expected 'ask' for workspace_edit/secrets_read (per inventory), got {result!r}"
    )


def test_classify_tool_workspace_edit_broad_shell_is_ask():
    """Test 9: classify_tool('workspace_edit', 'broad_shell') → 'ask'."""
    assert classify_tool("workspace_edit", "broad_shell") == "ask"


def test_classify_tool_unknown_tool_defaults_to_ask():
    """Test 10: classify_tool with an unknown tool name defaults to 'ask' (conservative)."""
    assert classify_tool("workspace_edit", "unknown_tool_xyz") == "ask"


def test_classify_tool_unknown_profile_raises():
    """classify_tool on unknown profile must raise ValueError."""
    import pytest
    with pytest.raises(ValueError):
        classify_tool("nonexistent_profile", "read_file")


# ---------------------------------------------------------------------------
# Test 11: compute_grant correctness
# ---------------------------------------------------------------------------

def test_compute_grant_risky_edit():
    """Test 11: compute_grant('risky_edit', [...]) returns the correct buckets.

    Per inventory: risky_edit has read_file=allow, external_network=deny,
    secrets_read=ask.
    """
    result = compute_grant("risky_edit", ["read_file", "external_network", "secrets_read"])
    assert result["granted"] == ["read_file"], f"granted={result['granted']!r}"
    assert result["requires_approval"] == ["secrets_read"], f"requires_approval={result['requires_approval']!r}"
    assert result["denied"] == ["external_network"], f"denied={result['denied']!r}"


def test_compute_grant_unknown_profile_raises():
    """compute_grant on unknown profile raises ValueError."""
    import pytest
    with pytest.raises(ValueError):
        compute_grant("bogus_profile", ["read_file"])


# ---------------------------------------------------------------------------
# Test 12: compute_grant preserves request order within each bucket
# ---------------------------------------------------------------------------

def test_compute_grant_preserves_order():
    """Test 12: compute_grant preserves the original request order within each bucket.

    Uses workspace_edit: allow=[read_file, apply_patch, focused_test, git_diff],
    ask=[broad_shell, secrets_read, destructive_git], deny=[external_network].
    We submit: [focused_test, read_file, secrets_read, broad_shell]
    Expected granted (in input order): [focused_test, read_file]
    Expected requires_approval (in input order): [secrets_read, broad_shell]
    """
    requested = ["focused_test", "read_file", "secrets_read", "broad_shell"]
    result = compute_grant("workspace_edit", requested)

    assert result["granted"] == ["focused_test", "read_file"], (
        f"Order must match input order within bucket; got {result['granted']!r}"
    )
    assert result["requires_approval"] == ["secrets_read", "broad_shell"], (
        f"Order must match input order within bucket; got {result['requires_approval']!r}"
    )
    assert result["denied"] == [], f"No denied tools expected; got {result['denied']!r}"


def test_compute_grant_disjoint_and_complete():
    """compute_grant output buckets are disjoint and together cover exactly the input."""
    requested = ["read_file", "apply_patch", "broad_shell", "external_network", "secrets_read"]
    result = compute_grant("inspect_local", requested)

    all_output = result["granted"] + result["requires_approval"] + result["denied"]
    # Complete — all requested tools appear
    assert sorted(all_output) == sorted(requested), (
        f"Output must cover exactly the input. Got {all_output!r}"
    )
    # Disjoint — no tool appears twice
    assert len(all_output) == len(set(all_output)), (
        f"Duplicate tool in output: {all_output!r}"
    )


# ---------------------------------------------------------------------------
# Test 13: JSON round-trip
# ---------------------------------------------------------------------------

def test_json_round_trip():
    """Test 13: load → dump → load does not change the semantic content."""
    original = json.loads(_DATA_FILE.read_text(encoding="utf-8"))

    # Dump and reload
    dumped = json.dumps(original, ensure_ascii=False)
    reloaded = json.loads(dumped)

    # Compare profiles section semantically (list order preserved)
    orig_profiles = original.get("profiles", {})
    reloaded_profiles = reloaded.get("profiles", {})

    assert set(orig_profiles.keys()) == set(reloaded_profiles.keys()), (
        "Profile keys changed after round-trip"
    )
    for profile in orig_profiles:
        assert orig_profiles[profile] == reloaded_profiles[profile], (
            f"Profile {profile!r} changed after round-trip"
        )


# ---------------------------------------------------------------------------
# Test 14: Helm copy and workspace copy are byte-identical
# ---------------------------------------------------------------------------

def test_helm_and_workspace_tool_groups_are_identical():
    """Test 14: The tool_groups.json file is byte-identical in both worktrees."""
    helm_path = Path(__file__).resolve().parents[1] / "references" / "tool_groups.json"
    workspace_path = Path.home() / ".openclaw" / "workspace" / ".worktrees" / "harness-eng" / "references" / "tool_groups.json"

    if not workspace_path.exists():
        import pytest
        pytest.skip(f"Workspace copy not found at {workspace_path}; skipping mirror check")

    helm_content = helm_path.read_bytes()
    workspace_content = workspace_path.read_bytes()

    assert helm_content == workspace_content, (
        f"tool_groups.json differs between Helm and workspace worktrees.\n"
        f"Helm: {helm_path}\nWorkspace: {workspace_path}"
    )


# ---------------------------------------------------------------------------
# Test for runner wiring: ledger entry has tool_grant (Test 12 from task)
# ---------------------------------------------------------------------------

def test_runner_ledger_entry_has_tool_grant_for_inspect_local():
    """Test runner wiring (test 12): a run under inspect_local records tool_grant in the task.

    This unit test mocks the subprocess and ledger write to verify the
    task stub produced by the runner includes a well-formed tool_grant block.
    """
    import json
    from unittest.mock import MagicMock, patch
    import subprocess as _sp

    # Minimal fake profile for inspect_local
    _FAKE_PROFILES = {
        "inspect_local": {
            "description": "Read-only local inspection.",
            "backend": "local",
            "runtime_backend": "local-shell",
            "runtime_target_kind": "workspace",
            "isolation": "shared-session",
            "handoff_required": False,
            "writes_allowed": False,
            "network_allowed": False,
            "checkpoint": "never",
        }
    }

    from scripts.command_guard import GuardDecision, CommandClassification

    fake_decision = GuardDecision(
        action="allow",
        risk_score=0.0,
        score_breakdown={},
        selected_profile="inspect_local",
        recommended_profile=None,
        reasons=("test allow",),
        matched_rules=tuple(),
        classification=CommandClassification(
            normalized_command="echo hello",
            argv=("echo", "hello"),
            shell_wrapped=False,
            shell_inner_command=None,
            categories=("read",),
            matched_rules=tuple(),
            writes_detected=False,
            network_detected=False,
            destructive_detected=False,
            privilege_detected=False,
            remote_detected=False,
        ),
        approval_required=False,
        approval_hint=None,
    )

    captured_tasks: list[dict] = []

    def capture_finalize(task: dict) -> None:
        captured_tasks.append(dict(task))

    args = MagicMock()
    args.profile = "inspect_local"
    args.guard_mode = "off"
    args.guard_json = False
    args.approve_risk = False
    args.command = ["echo", "hello"]
    args.runtime_target = None
    args.task_name = "test-tool-grant"
    args.task_goal = None
    args.skill = None
    args.meta_json = None
    args.task_id = None
    args.label = None
    args.path = None
    args.runtime_note = None
    args.delivery_mode = "inline"
    args.timeout = 1800

    with patch("scripts.run_with_profile.load_profiles", return_value=_FAKE_PROFILES), \
         patch("scripts.run_with_profile.validate_skill_profile"), \
         patch("scripts.run_with_profile.append_ledger"), \
         patch("scripts.run_with_profile._best_effort_index"), \
         patch("scripts.run_with_profile.run_checkpoint", return_value=None), \
         patch("scripts.run_with_profile.evaluate_command_guard", return_value=fake_decision), \
         patch("scripts.run_with_profile.finalize_task", side_effect=capture_finalize), \
         patch("scripts.run_with_profile.latest_snapshot_path", return_value=None), \
         patch("scripts.run_with_profile.subprocess.run",
               return_value=_sp.CompletedProcess(args=["echo", "hello"], returncode=0)):

        from scripts.run_with_profile import cmd_run
        rc = cmd_run(args)

    assert captured_tasks, "finalize_task must have been called to produce a ledger entry"
    task = captured_tasks[0]

    assert "tool_grant" in task, (
        f"Ledger entry must contain 'tool_grant' key. Got keys: {list(task.keys())}"
    )
    tg = task["tool_grant"]
    assert tg.get("profile") == "inspect_local", (
        f"tool_grant.profile must be 'inspect_local', got {tg.get('profile')!r}"
    )
    assert isinstance(tg.get("granted"), list), "tool_grant.granted must be a list"
    assert len(tg["granted"]) > 0, "tool_grant.granted must be non-empty for inspect_local"
    assert isinstance(tg.get("requires_approval"), list)
    assert isinstance(tg.get("denied"), list)
