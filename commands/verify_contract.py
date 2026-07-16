"""helm verify-contract — behavioral operating-invariant battery.

Complements the STRUCTURAL checks in ``doctor``/``validate`` (files exist,
JSON parses, references are consistent) with a BEHAVIORAL check: do
Helm's safety invariants still actually hold at runtime? This is the
primitive that should be re-run after a runtime/dependency bump to catch
a regression that structural validation cannot see (e.g. the command
guard silently starting to *allow* a destructive command).

Each probe below exercises real Helm code paths (never a real destructive
command) and returns ``{"name", "ok", "detail"}``. ``verify_operating_contract``
runs the fixed battery and aggregates the result.
"""

from __future__ import annotations

import argparse
import json
import tempfile
import warnings
from datetime import datetime, timedelta, timezone
from pathlib import Path

from commands import read_json, target_root
from scripts.command_guard import evaluate_command_guard
from scripts.io_utils import atomic_write_json
from scripts.long_running_runtime import (
    create_task_run,
    empty_runtime_state,
    pause_for_approval,
    resolve_approval,
    upsert_task_run,
)

# Minimal profile table sufficient to drive the guard probes below.
# Mirrors the shape used by scripts.command_guard callers (see
# tests/test_command_guard.py) without depending on a real workspace's
# execution_profiles.json.
_PROBE_PROFILES: dict[str, dict[str, object]] = {
    "inspect_local": {"writes_allowed": False, "network_allowed": False},
    "risky_edit": {"writes_allowed": True, "network_allowed": False},
}


def _probe_guard_denies_destructive() -> dict:
    """`rm -rf /` must classify as deny (or require_approval), never allow."""
    try:
        decision = evaluate_command_guard(
            command=["rm", "-rf", "/"],
            selected_profile="risky_edit",
            profiles=_PROBE_PROFILES,
            workspace=Path(tempfile.gettempdir()),
        )
        ok = decision.action in ("deny", "require_approval")
        detail = f"rm -rf / classified as action={decision.action}"
        return {"name": "guard_denies_destructive", "ok": ok, "detail": detail}
    except Exception as exc:  # noqa: BLE001 - probe must never raise
        return {"name": "guard_denies_destructive", "ok": False, "detail": f"error: {exc}"}


def _probe_guard_fails_closed() -> dict:
    """A corrupt guard policy must fail closed to require_approval.

    Simulates policy corruption with a throwaway temp file (never touches
    the workspace's real ``references/guard_policy.json``) and evaluates a
    benign command through it. Even for a harmless command, a corrupt
    policy must never resolve to ``allow``.
    """
    try:
        with tempfile.TemporaryDirectory() as tmp_dir:
            corrupt_policy = Path(tmp_dir) / "corrupt_guard_policy.json"
            corrupt_policy.write_text("{ this is not valid json !!", encoding="utf-8")
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                decision = evaluate_command_guard(
                    command=["ls"],
                    selected_profile="inspect_local",
                    profiles=_PROBE_PROFILES,
                    workspace=Path(tmp_dir),
                    policy_path=corrupt_policy,
                )
        ok = decision.action == "require_approval"
        detail = f"corrupt policy -> action={decision.action}"
        return {"name": "guard_fails_closed", "ok": ok, "detail": detail}
    except Exception as exc:  # noqa: BLE001 - probe must never raise
        return {"name": "guard_fails_closed", "ok": False, "detail": f"error: {exc}"}


def _probe_approval_ttl_consume_once() -> dict:
    """Approval gates are consume-once and TTL-bounded.

    Resolving the same approval gate twice must raise, and an expired
    gate must be rejected rather than silently approved.
    """
    try:
        state = empty_runtime_state()
        upsert_task_run(
            state,
            create_task_run(
                task_id="verify-contract-probe",
                requester="helm-verify-contract",
                source_surface="cli",
                user_message="verify-contract synthetic probe",
                normalized_intent="verify_contract_probe",
                status="running",
                risk_class="external_send",
            ),
        )
        gate = pause_for_approval(
            state,
            task_id="verify-contract-probe",
            pending_action="probe_action",
            resource="probe-resource",
            risk_reason="verify-contract synthetic probe",
            risk_class="external_send",
        )
        resolve_approval(state, approval_id=gate["approval_id"], response="approved", responder="helm-verify-contract")

        consume_once_raises = False
        try:
            resolve_approval(state, approval_id=gate["approval_id"], response="approved", responder="helm-verify-contract")
        except ValueError:
            consume_once_raises = True

        expired_state = empty_runtime_state()
        upsert_task_run(
            expired_state,
            create_task_run(
                task_id="verify-contract-probe-expired",
                requester="helm-verify-contract",
                source_surface="cli",
                user_message="verify-contract synthetic expired probe",
                normalized_intent="verify_contract_probe_expired",
                status="running",
                risk_class="external_send",
            ),
        )
        expired_at = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        expired_gate = pause_for_approval(
            expired_state,
            task_id="verify-contract-probe-expired",
            pending_action="probe_action",
            resource="probe-resource",
            risk_reason="verify-contract synthetic probe",
            risk_class="external_send",
            expires_at=expired_at,
        )
        expired_rejected = False
        try:
            resolve_approval(
                expired_state,
                approval_id=expired_gate["approval_id"],
                response="approved",
                responder="helm-verify-contract",
            )
        except ValueError:
            expired_rejected = True

        ok = consume_once_raises and expired_rejected
        detail = f"consume_once_raises={consume_once_raises} expired_rejected={expired_rejected}"
        return {"name": "approval_ttl_consume_once", "ok": ok, "detail": detail}
    except Exception as exc:  # noqa: BLE001 - probe must never raise
        return {"name": "approval_ttl_consume_once", "ok": False, "detail": f"error: {exc}"}


def _probe_ledger_append_atomic() -> dict:
    """atomic_write_json must round-trip a value via tempfile + os.replace."""
    try:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "ledger_probe.json"
            payload = {"probe": "verify-contract", "value": 42, "nested": [1, 2, 3]}
            atomic_write_json(path, payload)
            loaded = read_json(path, None)
        ok = loaded == payload
        detail = "atomic_write_json round-trip ok" if ok else f"round-trip mismatch: {loaded!r} != {payload!r}"
        return {"name": "ledger_append_atomic", "ok": ok, "detail": detail}
    except Exception as exc:  # noqa: BLE001 - probe must never raise
        return {"name": "ledger_append_atomic", "ok": False, "detail": f"error: {exc}"}


def verify_operating_contract(root: Path) -> dict:
    """Run the fixed operating-invariant probe battery and aggregate results."""
    checks = [
        _probe_guard_denies_destructive(),
        _probe_guard_fails_closed(),
        _probe_approval_ttl_consume_once(),
        _probe_ledger_append_atomic(),
    ]
    return {
        "workspace": str(root),
        "checks": checks,
        "ok": all(check["ok"] for check in checks),
    }


def cmd_verify_contract(args: argparse.Namespace) -> int:
    root = target_root(args.path)
    payload = verify_operating_contract(root)
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0 if payload["ok"] else 1
    print(f"workspace={payload['workspace']}")
    print("verify-contract=ok" if payload["ok"] else "verify-contract=failed")
    for check in payload["checks"]:
        status = "ok" if check["ok"] else "fail"
        print(f"{status:>4} {check['name']}: {check['detail']}")
    return 0 if payload["ok"] else 1
