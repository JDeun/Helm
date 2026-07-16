"""Tests for commands/verify_contract.py — `helm verify-contract`.

`verify_operating_contract` runs a fixed battery of BEHAVIORAL invariant
probes (as opposed to the STRUCTURAL checks in doctor/validate):

  1. guard_denies_destructive — command_guard classifies `rm -rf /` as
     deny (or require_approval).
  2. guard_fails_closed — a corrupt/missing guard policy still yields
     require_approval, never allow.
  3. approval_ttl_consume_once — an approval gate can only be resolved
     once, and an expired gate is rejected.
  4. ledger_append_atomic — atomic_write_json round-trips a value.

Test inventory (~10 cases):
  - each probe individually reports ok on a healthy environment
  - guard_fails_closed reports ok even though the policy is corrupt
  - verify_operating_contract aggregates all checks + overall ok
  - cmd_verify_contract prints human text and exits 0 on success
  - cmd_verify_contract --json prints JSON and exits 0 on success
  - cmd_verify_contract exits 1 and reports failures when a probe is broken
"""

from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from commands.verify_contract import cmd_verify_contract, verify_operating_contract


# ---------------------------------------------------------------------------
# verify_operating_contract — probe-level behavior
# ---------------------------------------------------------------------------


def test_verify_operating_contract_all_checks_pass_on_healthy_state(tmp_path: Path) -> None:
    payload = verify_operating_contract(tmp_path)

    assert payload["workspace"] == str(tmp_path)
    assert payload["ok"] is True
    names = {check["name"] for check in payload["checks"]}
    assert names == {
        "guard_denies_destructive",
        "guard_fails_closed",
        "approval_ttl_consume_once",
        "ledger_append_atomic",
    }
    for check in payload["checks"]:
        assert check["ok"] is True, check


def test_guard_denies_destructive_flags_rm_rf_root(tmp_path: Path) -> None:
    payload = verify_operating_contract(tmp_path)
    check = next(c for c in payload["checks"] if c["name"] == "guard_denies_destructive")
    assert check["ok"] is True
    assert "deny" in check["detail"] or "require_approval" in check["detail"]


def test_guard_fails_closed_reports_ok_when_policy_is_corrupt(tmp_path: Path) -> None:
    # The probe simulates its own corrupt policy internally; a healthy
    # workspace root should still make this probe report ok=True because
    # the guard correctly fails closed to require_approval.
    payload = verify_operating_contract(tmp_path)
    check = next(c for c in payload["checks"] if c["name"] == "guard_fails_closed")
    assert check["ok"] is True
    assert "require_approval" in check["detail"]


def test_approval_ttl_consume_once_reports_ok(tmp_path: Path) -> None:
    payload = verify_operating_contract(tmp_path)
    check = next(c for c in payload["checks"] if c["name"] == "approval_ttl_consume_once")
    assert check["ok"] is True


def test_ledger_append_atomic_reports_ok(tmp_path: Path) -> None:
    payload = verify_operating_contract(tmp_path)
    check = next(c for c in payload["checks"] if c["name"] == "ledger_append_atomic")
    assert check["ok"] is True


# ---------------------------------------------------------------------------
# Failure surfacing — guard no longer denies rm -rf /
# ---------------------------------------------------------------------------


def test_guard_denies_destructive_fails_if_guard_allows(tmp_path: Path) -> None:
    from scripts.command_guard import GuardDecision, CommandClassification

    fake_classification = CommandClassification(
        normalized_command="rm -rf /",
        argv=("rm", "-rf", "/"),
        shell_wrapped=False,
        shell_inner_command=None,
        categories=("destructive",),
        matched_rules=(),
        writes_detected=True,
        network_detected=False,
        destructive_detected=True,
        privilege_detected=False,
        remote_detected=False,
    )
    fake_decision = GuardDecision(
        action="allow",
        risk_score=0.0,
        selected_profile="risky_edit",
        recommended_profile=None,
        reasons=(),
        matched_rules=(),
        classification=fake_classification,
        approval_required=False,
    )
    with patch("commands.verify_contract.evaluate_command_guard", return_value=fake_decision):
        payload = verify_operating_contract(tmp_path)

    check = next(c for c in payload["checks"] if c["name"] == "guard_denies_destructive")
    assert check["ok"] is False
    assert payload["ok"] is False


# ---------------------------------------------------------------------------
# cmd_verify_contract — CLI surface
# ---------------------------------------------------------------------------


def _run_cmd(args: argparse.Namespace) -> tuple[int, str]:
    captured = io.StringIO()
    old_stdout = sys.stdout
    sys.stdout = captured
    try:
        exit_code = cmd_verify_contract(args)
    finally:
        sys.stdout = old_stdout
    return exit_code, captured.getvalue()


def test_cmd_verify_contract_human_output_exits_0(tmp_path: Path) -> None:
    args = argparse.Namespace(path=str(tmp_path), json=False)
    exit_code, output = _run_cmd(args)

    assert exit_code == 0
    assert "verify-contract=ok" in output
    assert "guard_denies_destructive" in output


def test_cmd_verify_contract_json_output_exits_0(tmp_path: Path) -> None:
    args = argparse.Namespace(path=str(tmp_path), json=True)
    exit_code, output = _run_cmd(args)

    assert exit_code == 0
    payload = json.loads(output)
    assert payload["ok"] is True
    assert len(payload["checks"]) == 4


def test_cmd_verify_contract_exits_1_when_a_probe_fails(tmp_path: Path) -> None:
    def _broken(root: Path) -> dict:
        return {
            "workspace": str(root),
            "checks": [{"name": "ledger_append_atomic", "ok": False, "detail": "boom"}],
            "ok": False,
        }

    args = argparse.Namespace(path=str(tmp_path), json=True)
    with patch("commands.verify_contract.verify_operating_contract", side_effect=_broken):
        exit_code, output = _run_cmd(args)

    assert exit_code == 1
    payload = json.loads(output)
    assert payload["ok"] is False
