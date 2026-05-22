"""SmallCode Phase 4: repeated-failure policy transition evaluator.

Evaluates a chronological list of failure events and returns the first
triggered policy-transition action (or None if no rule fires).

Rules are evaluated in first-match-wins order:
  1. Same fingerprint repeats >= 2  → stop_retry_and_diagnose
  2. >= 2 patch_failed events (any fingerprint)  → reload_file_and_decompose
  3. >= 3 events with the same signature.tool  → create_skill_repair_candidate
  4. Any event with error_class == credential_invalid_grant  → auth_recovery_profile

Each event is expected to have an outer envelope with:
  "signature"    — output of failure_signature.signature()
  "task_name"    — (str, optional)
  "skill"        — (str, optional)
  "occurred_at"  — (str, optional)
"""
from __future__ import annotations


def evaluate(history: list[dict]) -> dict | None:
    """Given a chronological list of failure events, return the first triggered
    transition dict, or None if no rule fires.

    Rules (first-match-wins):
      1. Same fingerprint repeats >= 2 in history
         → {"action": "stop_retry_and_diagnose", "reason": ..., "signature": <sig>}
      2. >= 2 events with error_class == "patch_failed" (even with different fingerprints)
         → {"action": "reload_file_and_decompose", "reason": ..., "signature": <sig>}
      3. >= 3 events with the same signature.tool
         → {"action": "create_skill_repair_candidate", "reason": ..., "signature": <sig>}
      4. Any event with error_class == "credential_invalid_grant"
         → {"action": "auth_recovery_profile", "reason": ..., "signature": <sig>}

    Exceptions are not swallowed — callers see them directly.
    """
    if not history:
        return None

    # --- Rule 1: same fingerprint >= 2 ---
    fingerprint_counts: dict[str, int] = {}
    fingerprint_first_sig: dict[str, dict] = {}
    for event in history:
        sig = event.get("signature") or {}
        fp = sig.get("fingerprint")
        if fp:
            fingerprint_counts[fp] = fingerprint_counts.get(fp, 0) + 1
            if fp not in fingerprint_first_sig:
                fingerprint_first_sig[fp] = sig
    for fp, count in fingerprint_counts.items():
        if count >= 2:
            sig = fingerprint_first_sig[fp]
            return transition_record(
                action="stop_retry_and_diagnose",
                reason=(
                    f"fingerprint '{fp}' repeated {count} time(s); "
                    f"tool={sig.get('tool')!r} error_class={sig.get('error_class')!r}"
                ),
                signature=sig,
            )

    # --- Rule 2: >= 2 patch_failed events ---
    patch_failed_events = [
        e for e in history
        if (e.get("signature") or {}).get("error_class") == "patch_failed"
    ]
    if len(patch_failed_events) >= 2:
        sig = patch_failed_events[-1].get("signature") or {}
        return transition_record(
            action="reload_file_and_decompose",
            reason=(
                f"{len(patch_failed_events)} patch_failed event(s) detected; "
                f"file may need to be reloaded and the change decomposed"
            ),
            signature=sig,
        )

    # --- Rule 3: >= 3 events with the same signature.tool ---
    tool_counts: dict[str, int] = {}
    tool_first_sig: dict[str, dict] = {}
    for event in history:
        sig = event.get("signature") or {}
        tool = sig.get("tool")
        if tool:
            tool_counts[tool] = tool_counts.get(tool, 0) + 1
            if tool not in tool_first_sig:
                tool_first_sig[tool] = sig
    for tool, count in tool_counts.items():
        if count >= 3:
            sig = tool_first_sig[tool]
            return transition_record(
                action="create_skill_repair_candidate",
                reason=(
                    f"tool/skill '{tool}' failed {count} time(s); "
                    f"a repair candidate should be created"
                ),
                signature=sig,
            )

    # --- Rule 4: any credential_invalid_grant ---
    for event in history:
        sig = event.get("signature") or {}
        if sig.get("error_class") == "credential_invalid_grant":
            return transition_record(
                action="auth_recovery_profile",
                reason=(
                    f"credential_invalid_grant detected; "
                    f"tool={sig.get('tool')!r} — auth recovery required"
                ),
                signature=sig,
            )

    return None


def transition_record(action: str, reason: str, signature: dict) -> dict:
    """Return a JSON-serializable dict for the ledger's ``policy_transition`` field.

    Returns a fresh dict each call (no shared mutable state).
    """
    return {
        "action": action,
        "reason": reason,
        "signature": dict(signature),
    }
