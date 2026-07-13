from __future__ import annotations

import json
import math
import urllib.error
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from scripts import long_running_runtime, model_health_lib, model_provider_probe, omfm_status


def test_omfm_provider_is_local_and_secret_free() -> None:
    assert model_health_lib._compatible_provider_config("omfm", {}) == (
        "omfm-local",
        "http://127.0.0.1:4567/v1",
    )
    assert model_provider_probe._LOCAL_PROVIDER_ENDPOINTS["omfm"] == "http://127.0.0.1:4567/v1/models"


def test_status_parser_supports_current_human_cli_output() -> None:
    parsed = omfm_status._parse_status(
        "omfm running\nport: 4567\nselected models: 2\nbest route: provider/model (12ms, lowest-latency)\n"
    )

    assert parsed["running"] is True
    assert parsed["port"] == 4567
    assert parsed["selected_model_count"] == 2
    assert parsed["best_route"]["model"] == "provider/model"


def test_omfm_daemon_off_is_down_with_local_auth() -> None:
    with patch.object(
        model_health_lib,
        "build_omfm_status",
        return_value={
            "status": "ready",
            "context_guards": {"balanced": {"compliant": True, "minimum_selected_context": 131_072}},
        },
    ), patch.object(model_health_lib, "_openai_like_request", side_effect=urllib.error.URLError("refused")):
        outcome = model_health_lib.probe_openai_compatible_chat_completion(
            "omfm/balanced", {"model": "omfm/balanced", "timeout_seconds": 1}, "omfm"
        )

    assert outcome.status == "down"
    assert outcome.auth_status == "local"
    assert outcome.generation_status == "failed"


def test_omfm_402_and_429_are_degraded() -> None:
    for code, expected in ((402, "quota"), (429, "rate_limit")):
        error = urllib.error.HTTPError(
            "http://127.0.0.1:4567/v1/chat/completions", code, "limited", {}, BytesIO(b"limited")
        )
        with patch.object(
            model_health_lib,
            "build_omfm_status",
            return_value={
                "status": "ready",
                "context_guards": {"balanced": {"compliant": True, "minimum_selected_context": 131_072}},
            },
        ), patch.object(model_health_lib, "_openai_like_request", side_effect=error):
            outcome = model_health_lib.probe_openai_compatible_chat_completion(
                "omfm/balanced", {"model": "omfm/balanced", "timeout_seconds": 1}, "omfm"
            )

        assert outcome.status == "degraded"
        assert outcome.auth_status == "local"
        assert outcome.generation_status == expected


def test_context_guard_skips_healthy_omfm_for_large_prompt() -> None:
    now = "2099-01-01T00:00:00+00:00"
    policy = {
        "models": [
            {"ref": "omfm/balanced", "provider": "omfm", "priority": 10, "context_window": 131_072},
            {"ref": "openai/stable", "provider": "openai", "priority": 20},
        ]
    }
    state = {
        "models": {
            "omfm/balanced": {"status": "healthy", "checked_at": now},
            "openai/stable": {"status": "healthy", "checked_at": now},
        }
    }

    choice = model_health_lib.select_model(policy, state, context_tokens=130_000)

    assert choice.model == "openai/stable"
    malformed = model_health_lib.select_model(
        {"models": [{"ref": "omfm/balanced", "provider": "omfm", "priority": 10, "context_window": "bad"}]},
        state,
        context_tokens=1,
    )
    assert malformed.model is None
    assert model_health_lib.model_context_allows(policy["models"][0], -1) is False
    assert model_health_lib.model_context_allows(policy["models"][0], "1") is False
    assert model_health_lib.model_context_allows(policy["models"][0], None) is False


def test_runtime_model_gate_requires_low_risk_explicit_opt_in_and_live_guard() -> None:
    now = "2099-01-01T00:00:00+00:00"
    policy = {
        "models": [
            {"ref": "omfm/balanced", "provider": "omfm", "priority": 10, "context_window": 131_072},
            {"ref": "openai/stable", "provider": "openai", "priority": 20},
        ]
    }
    state = {
        "models": {
            "omfm/balanced": {"status": "healthy", "checked_at": now},
            "openai/stable": {"status": "healthy", "checked_at": now},
        }
    }
    ready = {
        "status": "ready",
        "context_guards": {"balanced": {"compliant": True, "minimum_selected_context": 131_072}},
    }
    with patch.object(model_health_lib, "build_omfm_status", return_value=ready):
        allowed = model_health_lib.resolve_runtime_model(
            profile="inspect_local",
            model_policy={"allow_free_router": True, "context_tokens": 1},
            policy=policy,
            state=state,
        )
        high_risk = model_health_lib.resolve_runtime_model(
            profile="service_ops",
            model_policy={"allow_free_router": True, "context_tokens": 1},
            policy=policy,
            state=state,
        )
        no_opt_in = model_health_lib.resolve_runtime_model(
            profile="inspect_local",
            model_policy={"context_tokens": 1},
            policy=policy,
            state=state,
        )

    assert allowed.model == "omfm/balanced"
    assert high_risk.model == "openai/stable"
    assert high_risk.source == "omfm-runtime-gate"
    assert no_opt_in.model == "openai/stable"

    with patch.object(
        model_health_lib,
        "choose_model_from_discovery",
        return_value=model_health_lib.ModelHealthChoice("omfm/balanced", "hostile discovery", "runtime-discovery"),
    ) as discovery:
        only_omfm = model_health_lib.resolve_runtime_model(
            profile="service_ops",
            model_policy={"allow_free_router": True, "context_tokens": 1},
            policy={"models": [policy["models"][0]]},
            state={"models": {"omfm/balanced": state["models"]["omfm/balanced"]}},
        )
    assert only_omfm.model is None
    discovery.assert_called_once()


def test_agent_registration_uses_runtime_recovery_selection() -> None:
    choice = model_health_lib.ModelHealthChoice("omfm/balanced", "healthy", "model-health-state")
    with patch.object(long_running_runtime, "resolve_runtime_model", return_value=choice) as resolver:
        entry = long_running_runtime.register_agent(
            long_running_runtime.empty_runtime_state(),
            agent_id="researcher-1",
            role="[role:researcher]",
            allowed_tools=["read_file"],
            memory_scope="task",
            model_policy={"runtime_recovery": True, "allow_free_router": True, "context_tokens": 128},
            skill_profile="research",
            timeout=60,
            owner="coordinator",
            version="1",
            output_contract={"type": "sources"},
        )

    resolver.assert_called_once()
    assert entry["model_policy"]["selected_model"] == "omfm/balanced"
    assert entry["model_policy"]["selection_source"] == "model-health-state"


def test_runtime_gate_handles_case_and_injected_omfm_selection() -> None:
    policy = {
        "models": [
            {"ref": "OMFM/balanced", "provider": "OMFM", "priority": 10, "context_window": 131_072},
            {"ref": "openai/stable", "provider": "openai", "priority": 20},
        ]
    }
    state = {"models": {"OMFM/balanced": {"status": "healthy", "checked_at": "2099-01-01T00:00:00+00:00"}}}
    with patch.object(model_health_lib, "build_omfm_status", return_value={"status": "stopped"}):
        choice = model_health_lib.resolve_runtime_model(
            profile="service_ops",
            model_policy={"selected_model": "OMFM/balanced", "context_tokens": 1},
            policy=policy,
            state=state,
        )
    assert choice.model == "openai/stable"


def test_status_ready_requires_context_homogeneous_group(tmp_path: Path) -> None:
    (tmp_path / "config.json").write_text(
        json.dumps(
            {
                "port": 4567,
                "selectedModelIds": ["candidate"],
                "modelGroups": {"fast": [], "balanced": ["candidate"], "capable": []},
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "models-cache.json").write_text(
        json.dumps({"models": [{"id": "candidate", "contextLength": 131_072}]}),
        encoding="utf-8",
    )
    with patch.object(omfm_status.shutil, "which", return_value="/usr/local/bin/omfm"), \
         patch.object(omfm_status, "_command_status", return_value=({"running": True, "port": 4567}, None)), \
         patch.object(omfm_status, "_probe", return_value=(True, 1)):
        status = omfm_status.build_omfm_status(env={"PATH": "", "OMFM_HOME": str(tmp_path)})

    assert status["status"] == "ready"
    assert status["context_guards"]["balanced"]["compliant"] is True
    assert "key" not in json.dumps(status).casefold()


def test_status_rejects_non_finite_context_and_malformed_guard(tmp_path: Path) -> None:
    (tmp_path / "config.json").write_text(
        json.dumps({"selectedModelIds": ["candidate"], "modelGroups": {"balanced": ["candidate"]}}),
        encoding="utf-8",
    )
    (tmp_path / "models-cache.json").write_text(
        json.dumps({"models": [{"id": "candidate", "contextLength": math.inf}]}),
        encoding="utf-8",
    )
    with patch.object(omfm_status.shutil, "which", return_value="/usr/local/bin/omfm"), \
         patch.object(omfm_status, "_command_status", return_value=({"running": True}, None)), \
         patch.object(omfm_status, "_probe", return_value=(True, 1)):
        status = omfm_status.build_omfm_status(env={"PATH": "", "OMFM_HOME": str(tmp_path)})

    assert status["context_guards"]["balanced"]["compliant"] is False
    assert omfm_status.context_guard_allows(status, "omfm/balanced", -1) is False
    assert omfm_status.context_guard_allows({"status": "ready", "context_guards": "bad"}, "omfm/balanced", 1) is False


def test_recovery_policy_keeps_existing_chain_and_adds_omfm_last() -> None:
    policy = json.loads((Path(__file__).resolve().parents[1] / "references" / "model_recovery_policy.json").read_text(encoding="utf-8"))
    refs = [item["ref"] for item in sorted(policy["models"], key=lambda item: item["priority"])]

    assert refs[:3] == ["ollama/llama3.2:latest", "openai/gpt-4.1-mini", "google_gemini/gemini-2.5-flash"]
    assert refs[-1] == "omfm/balanced"
