from __future__ import annotations

from unittest.mock import patch

from scripts.model_health_lib import (
    ModelHealthChoice,
    policy_models,
    select_model,
    update_state_with_probe,
)


def test_select_model_prefers_fresh_healthy_state() -> None:
    policy = {
        "fresh_after_seconds": 300,
        "models": [
            {"ref": "openai/gpt-4.1-mini", "provider": "openai", "priority": 10, "probe": {"kind": "openai_chat_completion"}}
        ],
    }
    state = {
        "models": {
            "openai/gpt-4.1-mini": {
                "status": "healthy",
                "checked_at": "2099-01-01T00:00:00+00:00",
                "last_ok_at": "2099-01-01T00:00:00+00:00",
            }
        }
    }

    choice = select_model(policy, state)

    assert choice.model == "openai/gpt-4.1-mini"
    assert choice.source == "model-health-state"


def test_select_model_falls_back_to_discovery_choice() -> None:
    policy = {
        "models": [
            {"ref": "openai/gpt-4.1-mini", "provider": "openai", "priority": 10, "probe": {"kind": "openai_chat_completion"}}
        ],
    }

    with patch(
        "scripts.model_health_lib.choose_model_from_discovery",
        return_value=ModelHealthChoice("openai/gpt-4.1-mini", "runtime discovery detected provider", "runtime-discovery"),
    ):
        choice = select_model(policy, {"models": {}})

    assert choice.model == "openai/gpt-4.1-mini"
    assert choice.source == "runtime-discovery"


def test_policy_models_can_be_derived_from_available_providers() -> None:
    with patch("scripts.model_health_lib.probe_local_providers", return_value=[]), patch(
        "scripts.model_health_lib.probe_api_providers_from_env",
        return_value=[type("Probe", (), {"provider": "openai"})()],
    ):
        models = policy_models({})

    assert models[0]["provider"] == "openai"
    assert models[0]["probe"]["kind"] == "openai_chat_completion"


def test_update_state_with_probe_returns_unknown_for_missing_model() -> None:
    result = update_state_with_probe("missing/model", {"models": []})

    assert result["status"] == "unknown"
