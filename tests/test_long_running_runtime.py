from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from helm_workspace import clear_workspace_layout_cache
from scripts.command_guard import CommandClassification, GuardDecision
from scripts.long_running_runtime import (
    append_checkpoint,
    can_execute_external_action,
    create_task_run,
    empty_runtime_state,
    get_agent,
    load_runtime_state,
    pause_for_approval,
    record_agent_event,
    register_agent,
    resolve_approval,
    resume_from_last_checkpoint,
    save_runtime_state,
    upsert_task_run,
)
from scripts.run_with_profile import record_runtime_approval_pause


def test_checkpoint_resume_reuses_processed_items_and_idempotency_key() -> None:
    state = empty_runtime_state()
    task = create_task_run(
        task_id="task-url-capture",
        requester="kevin",
        source_surface="telegram",
        user_message="capture 100 URLs",
        normalized_intent="capture_urls",
        status="running",
    )
    upsert_task_run(state, task)

    first = append_checkpoint(
        state,
        task_id="task-url-capture",
        phase="capture",
        input_payload={"urls": list(range(100))},
        processed_items=list(range(56)),
        pending_items=list(range(56, 100)),
        output_artifacts=["notes/url-000.md"],
        tool_evidence=[{"tool": "obsidian_writer", "status": "ok"}],
    )
    duplicate = append_checkpoint(
        state,
        task_id="task-url-capture",
        phase="capture",
        input_payload={"urls": list(range(100))},
        processed_items=list(range(56)),
        pending_items=list(range(56, 100)),
        output_artifacts=["notes/url-000.md"],
        tool_evidence=[{"tool": "obsidian_writer", "status": "ok"}],
    )

    assert duplicate["checkpoint_id"] == first["checkpoint_id"]
    assert state["task_runs"]["task-url-capture"]["checkpoint_ids"] == [first["checkpoint_id"]]

    plan = resume_from_last_checkpoint(state, "task-url-capture")
    assert plan["resume_checkpoint_id"] == first["checkpoint_id"]
    assert plan["processed_items"] == list(range(56))
    assert plan["pending_items"] == list(range(56, 100))
    assert first["idempotency_key"] in plan["idempotency_keys"]
    assert state["task_runs"]["task-url-capture"]["status"] == "running"


def test_approval_pause_blocks_external_action_until_approved() -> None:
    state = empty_runtime_state()
    upsert_task_run(
        state,
        create_task_run(
            task_id="task-send",
            requester="kevin",
            source_surface="telegram",
            user_message="send report",
            normalized_intent="send_report",
            status="running",
            risk_class="external_send",
        ),
    )

    gate = pause_for_approval(
        state,
        task_id="task-send",
        pending_action="send_telegram",
        resource="chat-42",
        risk_reason="external message",
        risk_class="external_send",
        resume_command="helm resume task-send",
    )

    assert state["task_runs"]["task-send"]["status"] == "paused"
    assert state["task_runs"]["task-send"]["pending_approval_id"] == gate["approval_id"]
    assert gate["options"] == ["approve", "cancel"]
    assert can_execute_external_action(
        state,
        task_id="task-send",
        pending_action="send_telegram",
        resource="chat-42",
        risk_class="external_send",
    ) is False

    resolved = resolve_approval(
        state,
        approval_id=gate["approval_id"],
        response="approved",
        responder="kevin",
    )

    assert resolved["response"] == "approved"
    assert state["task_runs"]["task-send"]["status"] == "running"
    assert state["task_runs"]["task-send"]["pending_approval_id"] is None
    assert can_execute_external_action(
        state,
        task_id="task-send",
        pending_action="send_telegram",
        resource="chat-42",
        risk_class="external_send",
    ) is True

    state["task_runs"]["task-send"]["status"] = "completed"
    assert can_execute_external_action(
        state,
        task_id="task-send",
        pending_action="send_telegram",
        resource="chat-42",
        risk_class="external_send",
    ) is False


def test_approval_pause_reuses_matching_unresolved_gate() -> None:
    state = empty_runtime_state()
    upsert_task_run(
        state,
        create_task_run(
            task_id="task-send",
            requester="kevin",
            source_surface="telegram",
            user_message="send report",
            normalized_intent="send_report",
            status="running",
            risk_class="external_send",
        ),
    )

    first = pause_for_approval(
        state,
        task_id="task-send",
        pending_action="send_telegram",
        resource="chat-42",
        risk_reason="external message",
        risk_class="external_send",
    )
    second = pause_for_approval(
        state,
        task_id="task-send",
        pending_action="send_telegram",
        resource="chat-42",
        risk_reason="external message",
        risk_class="external_send",
    )

    assert second["approval_id"] == first["approval_id"]
    assert list(state["approval_gates"]) == [first["approval_id"]]


def test_cancelled_approval_cancels_task_and_still_blocks_action() -> None:
    state = empty_runtime_state()
    upsert_task_run(
        state,
        create_task_run(
            task_id="task-delete",
            requester="kevin",
            source_surface="cli",
            user_message="delete calendar event",
            normalized_intent="delete_event",
            status="running",
            risk_class="delete",
        ),
    )
    gate = pause_for_approval(
        state,
        task_id="task-delete",
        pending_action="delete_calendar_event",
        resource="event-1",
        risk_reason="destructive calendar mutation",
        risk_class="delete",
    )
    resolve_approval(
        state,
        approval_id=gate["approval_id"],
        response="cancelled",
        responder="kevin",
    )

    assert state["task_runs"]["task-delete"]["status"] == "cancelled"
    assert can_execute_external_action(
        state,
        task_id="task-delete",
        pending_action="delete_calendar_event",
        resource="event-1",
        risk_class="delete",
    ) is False


def test_expired_approval_cannot_be_resolved_or_used() -> None:
    state = empty_runtime_state()
    upsert_task_run(
        state,
        create_task_run(
            task_id="task-stale",
            requester="kevin",
            source_surface="telegram",
            user_message="send stale message",
            normalized_intent="send_stale",
            status="running",
            risk_class="external_send",
        ),
    )
    expired_at = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    gate = pause_for_approval(
        state,
        task_id="task-stale",
        pending_action="send_telegram",
        resource="chat-42",
        risk_reason="external message",
        risk_class="external_send",
        expires_at=expired_at,
    )

    with pytest.raises(ValueError, match="expired"):
        resolve_approval(
            state,
            approval_id=gate["approval_id"],
            response="approved",
            responder="kevin",
        )

    gate["response"] = "approved"
    state["approval_gates"][gate["approval_id"]] = gate
    assert can_execute_external_action(
        state,
        task_id="task-stale",
        pending_action="send_telegram",
        resource="chat-42",
        risk_class="external_send",
    ) is False


def test_agent_registry_returns_copies_and_records_recoverable_events() -> None:
    state = empty_runtime_state()
    upsert_task_run(
        state,
        create_task_run(
            task_id="task-research",
            requester="kevin",
            source_surface="cli",
            user_message="research topic",
            normalized_intent="research",
            status="running",
        ),
    )
    entry = register_agent(
        state,
        agent_id="source-fetcher",
        role="source fetcher",
        allowed_tools=["web_fetch", "web_fetch", "read_file"],
        memory_scope="task",
        model_policy={"tier": "fast"},
        skill_profile="research",
        timeout=120,
        owner="coordinator",
        version="1.0.0",
        output_contract={"type": "sources"},
    )

    assert entry["allowed_tools"] == ["read_file", "web_fetch"]
    fetched = get_agent(state, "source-fetcher")
    fetched["allowed_tools"].append("tamper")
    assert state["agent_registry"]["source-fetcher"]["allowed_tools"] == ["read_file", "web_fetch"]

    event = record_agent_event(
        state,
        task_id="task-research",
        agent_id="source-fetcher",
        event="fetch_failed",
        recoverable=True,
        detail={"fallback": "cache"},
    )
    assert event["recoverable"] is True
    assert state["task_runs"]["task-research"]["agent_events"][0]["detail"] == {"fallback": "cache"}


def test_runtime_state_round_trips_atomically(tmp_path: Path) -> None:
    state_path = tmp_path / "state" / "runtime.json"
    state = empty_runtime_state()
    upsert_task_run(
        state,
        create_task_run(
            task_id="task-1",
            requester="kevin",
            source_surface="cli",
            user_message="work",
            normalized_intent="work",
        ),
    )

    saved = save_runtime_state(state, state_path)
    saved["task_runs"]["task-1"]["status"] = "tampered"

    loaded = load_runtime_state(state_path)
    assert loaded["task_runs"]["task-1"]["status"] == "pending"
    assert json.loads(state_path.read_text(encoding="utf-8"))["runtime_state_schema_version"] == 1


def test_run_with_profile_records_runtime_approval_pause(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    (workspace / "references").mkdir(parents=True)
    monkeypatch.setenv("HELM_WORKSPACE", str(workspace))
    clear_workspace_layout_cache()

    task = {
        "task_id": "task-runner",
        "task_name": "dangerous command",
        "profile": "risky_edit",
        "skill": None,
        "command": ["python3", "-c", "print('x')"],
        "command_preview": "python3 -c 'print('\"'\"'x'\"'\"')'",
        "delivery_mode": "inline",
        "meta": {"requester": "kevin"},
        "checkpoint_id": "cp-1",
    }
    decision = GuardDecision(
        action="require_approval",
        risk_score=0.8,
        score_breakdown={"destructive": 0.8},
        selected_profile="risky_edit",
        recommended_profile=None,
        reasons=("require_approval rules matched",),
        matched_rules=("rule-1",),
        classification=CommandClassification(
            normalized_command="python3 -c print",
            argv=("python3", "-c", "print('x')"),
            shell_wrapped=False,
            shell_inner_command=None,
            categories=("write",),
            matched_rules=("rule-1",),
            writes_detected=True,
            network_detected=False,
            destructive_detected=False,
            privilege_detected=False,
            remote_detected=False,
        ),
        approval_required=True,
        approval_hint="--approve-risk",
    )

    gate = record_runtime_approval_pause(task, decision)
    assert gate is not None
    assert task["runtime_pause"]["approval_id"] == gate["approval_id"]

    state = load_runtime_state(workspace / ".helm" / "long-running-runtime.json")
    assert state["task_runs"]["task-runner"]["status"] == "paused"
    assert state["task_runs"]["task-runner"]["checkpoint_ids"] == ["cp-1"]
    assert state["approval_gates"][gate["approval_id"]]["pending_action"].startswith("python3")
    assert "--approve-risk" in state["approval_gates"][gate["approval_id"]]["resume_command"]

    clear_workspace_layout_cache()
