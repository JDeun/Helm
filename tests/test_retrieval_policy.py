from __future__ import annotations

import unittest

from scripts.route_contract_lib import infer_chosen_tool
from scripts.retrieval_policy_lib import build_retrieval_plan, classify_retrieval


class RetrievalPolicyTests(unittest.TestCase):
    def test_classify_fetch_blocked_from_status_code(self) -> None:
        payload = classify_retrieval(status_code=403)
        self.assertEqual(payload.exit_classification, "fetch_blocked")
        self.assertEqual(payload.attempt_stage, "cheap_fetch")
        self.assertFalse(payload.should_stop)

    def test_build_plan_escalates_to_browser_after_transformed_url_failure(self) -> None:
        payload = build_retrieval_plan(
            current_stage="transformed_url",
            error_text="rate limit and challenge page",
            body_hint="blocked by waf",
        )
        self.assertEqual(payload["exit_classification"], "fetch_blocked")
        self.assertEqual(payload["next_attempt_stage"], "browser_snapshot")

    def test_build_plan_stops_on_auth_wall(self) -> None:
        payload = build_retrieval_plan(current_stage="browser_snapshot", auth_required=True)
        self.assertEqual(payload["exit_classification"], "auth_required")
        self.assertIsNone(payload["next_attempt_stage"])
        self.assertTrue(payload["should_stop"])

    def test_build_plan_marks_network_discovery_as_api_reusable(self) -> None:
        payload = build_retrieval_plan(current_stage="browser_network", browser_used=True, network_discovery=True)
        self.assertEqual(payload["exit_classification"], "api_reusable")
        self.assertIsNone(payload["next_attempt_stage"])

    def test_route_contract_ignores_env_prefix_in_nested_shell(self) -> None:
        chosen = infer_chosen_tool(["bash", "-lc", "HELM_PROFILE=strict python3 /tmp/router_runner.py --help"])
        self.assertEqual(chosen, "router_runner.py")


if __name__ == "__main__":
    unittest.main()
