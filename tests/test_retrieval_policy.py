from __future__ import annotations

from scripts.route_contract_lib import infer_chosen_tool
from scripts.retrieval_policy_lib import build_retrieval_plan, classify_retrieval


def test_classify_fetch_blocked_from_status_code() -> None:
    payload = classify_retrieval(status_code=403)
    assert payload.exit_classification == "fetch_blocked"
    assert payload.attempt_stage == "cheap_fetch"
    assert not payload.should_stop


def test_build_plan_escalates_to_browser_after_transformed_url_failure() -> None:
    payload = build_retrieval_plan(
        current_stage="transformed_url",
        error_text="rate limit and challenge page",
        body_hint="blocked by waf",
    )
    assert payload["exit_classification"] == "fetch_blocked"
    assert payload["next_attempt_stage"] == "browser_snapshot"


def test_build_plan_stops_on_auth_wall() -> None:
    payload = build_retrieval_plan(current_stage="browser_snapshot", auth_required=True)
    assert payload["exit_classification"] == "auth_required"
    assert payload["next_attempt_stage"] is None
    assert payload["should_stop"]


def test_build_plan_marks_network_discovery_as_api_reusable() -> None:
    payload = build_retrieval_plan(current_stage="browser_network", browser_used=True, network_discovery=True)
    assert payload["exit_classification"] == "api_reusable"
    assert payload["next_attempt_stage"] is None


def test_route_contract_ignores_env_prefix_in_nested_shell() -> None:
    chosen = infer_chosen_tool(["bash", "-lc", "HELM_PROFILE=strict python3 /tmp/router_runner.py --help"])
    assert chosen == "router_runner.py"
