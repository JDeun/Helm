from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.long_running_runtime import _stable_hash, empty_runtime_state
from scripts.request_intake import accept_request


def test_unseen_delivery_is_accepted_and_creates_task_run() -> None:
    state = empty_runtime_state()

    result = accept_request(
        state,
        "delivery-1",
        {"requester": "webhook", "user_message": "order created"},
        now_ms=1_000,
    )

    assert result["ack"] is True
    assert result["status"] == "accepted"
    assert result["delivery_id"] == "delivery-1"
    assert result["task_id"]
    assert result["task_id"] in state["task_runs"]
    assert state["task_runs"][result["task_id"]]["status"] == "pending"
    assert len(state["task_runs"]) == 1


def test_duplicate_delivery_id_returns_same_ack_and_creates_no_new_run() -> None:
    state = empty_runtime_state()
    payload = {"requester": "webhook", "user_message": "order created"}

    first = accept_request(state, "delivery-1", payload, now_ms=1_000)
    second = accept_request(state, "delivery-1", payload, now_ms=2_000)

    assert second["ack"] is True
    assert second["status"] == "duplicate"
    assert second["task_id"] == first["task_id"]
    assert second["delivery_id"] == "delivery-1"
    assert len(state["task_runs"]) == 1


def test_two_distinct_delivery_ids_create_two_runs() -> None:
    state = empty_runtime_state()
    payload = {"requester": "webhook", "user_message": "order created"}

    first = accept_request(state, "delivery-1", payload, now_ms=1_000)
    second = accept_request(state, "delivery-2", payload, now_ms=1_000)

    assert first["task_id"] != second["task_id"]
    assert len(state["task_runs"]) == 2
    assert first["status"] == "accepted"
    assert second["status"] == "accepted"


def test_missing_delivery_id_falls_back_to_stable_hash_of_payload() -> None:
    state = empty_runtime_state()
    payload = {"requester": "webhook", "user_message": "order created"}

    result = accept_request(state, None, payload, now_ms=1_000)

    assert result["ack"] is True
    assert result["status"] == "accepted"
    assert result["delivery_id"] == _stable_hash(payload)

    duplicate = accept_request(state, None, payload, now_ms=2_000)
    assert duplicate["status"] == "duplicate"
    assert duplicate["task_id"] == result["task_id"]
    assert len(state["task_runs"]) == 1

    other_payload = {"requester": "webhook", "user_message": "different"}
    third = accept_request(state, None, other_payload, now_ms=1_000)
    assert third["status"] == "accepted"
    assert third["task_id"] != result["task_id"]
    assert len(state["task_runs"]) == 2
