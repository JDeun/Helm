"""Fast-ACK async intake seam for external webhook/queue callers (P13).

An external caller (webhook/queue) that does not receive an ACK quickly
will retry, sending duplicate inbound requests for the same logical
event. This module collapses a retry-storm into a single ``task_run`` by
keying on the caller-supplied ``delivery_id`` (or, if absent, a stable
hash of the payload) against a ``seen_deliveries`` map persisted on the
long-running runtime state.

This module is a pure state-transform: it does not run an HTTP server
and does not persist anything itself. Callers own ``state`` (typically
loaded via :func:`scripts.long_running_runtime.load_runtime_state`) and
are responsible for calling
:func:`scripts.long_running_runtime.save_runtime_state` after applying
the returned mutation.
"""

from __future__ import annotations

from typing import Any

from scripts.long_running_runtime import _stable_hash, create_task_run, upsert_task_run


def accept_request(
    state: dict[str, Any],
    delivery_id: str | None,
    payload: dict[str, Any],
    *,
    now_ms: int | None = None,
) -> dict[str, Any]:
    """Fast-ACK an inbound request, deduping retries by ``delivery_id``.

    If ``delivery_id`` has not been seen before, a pending task_run is
    created and the ack records ``status: "accepted"``. If it has been
    seen, the prior ack is replayed verbatim (same ``task_id``) with
    ``status: "duplicate"`` and no new task_run is created.

    When ``delivery_id`` is falsy, a stable hash of ``payload`` is used
    as the effective idempotency key instead.
    """
    # The payload is untrusted (external webhook/queue): guard field access so a
    # non-dict body still ACKs/dedups instead of crashing the intake seam.
    fields = payload if isinstance(payload, dict) else {}
    key = delivery_id or _stable_hash(payload)
    seen_deliveries = state.setdefault("seen_deliveries", {})

    existing = seen_deliveries.get(key)
    if isinstance(existing, dict) and existing.get("task_id"):
        return {
            "ack": True,
            "status": "duplicate",
            "task_id": existing["task_id"],
            "delivery_id": key,
        }

    task = create_task_run(
        requester=str(fields.get("requester") or "external"),
        source_surface=str(fields.get("source_surface") or "webhook"),
        user_message=str(fields.get("user_message") or ""),
        normalized_intent=str(fields.get("normalized_intent") or "inbound_request"),
        risk_class=str(fields.get("risk_class") or "low"),
        status="pending",
        metadata={
            "delivery_id": key,
            "received_at_ms": now_ms,
            "payload": payload if isinstance(payload, dict) else {"_raw": payload},
        },
    )
    upsert_task_run(state, task)
    seen_deliveries[key] = {"task_id": task["task_id"], "delivery_id": key}

    return {
        "ack": True,
        "status": "accepted",
        "task_id": task["task_id"],
        "delivery_id": key,
    }
