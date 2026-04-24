from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from route_contract_lib import infer_chosen_tool, infer_interaction_workflow, score_skill_relevance


def test_infers_python_module_name() -> None:
    assert infer_chosen_tool(["python3", "-m", "tools.router_runner", "--help"]) == "tools.router_runner"


def test_infers_nested_shell_command_tool() -> None:
    assert (
        infer_chosen_tool(["bash", "-lc", "python3 /tmp/router_runner.py --help"])
        == "router_runner.py"
    )


def test_design_request_starts_with_divergence() -> None:
    payload = infer_interaction_workflow(
        request="새 agent 구조 설계 방향을 비교해줘",
        command=[],
    )

    assert payload["mode"] == "diverge_then_converge"
    assert payload["next_action"] == "present_options"


def test_execute_request_converges_even_with_design_terms() -> None:
    payload = infer_interaction_workflow(
        request="문서의 설계안대로 바로 구현 진행해",
        command=[],
    )

    assert payload["mode"] == "converge_with_tradeoff_note"
    assert payload["next_action"] == "run"


def test_skill_relevance_flags_unsupported_skill() -> None:
    payload = score_skill_relevance(
        skill="travel-ops-ko",
        profile="inspect_local",
        contract={"route_decision": {"task_type": "travel"}},
        request="household ledger category update",
        task_name=None,
        command=["true"],
    )

    assert payload["verdict"] == "poor"


def test_skill_relevance_accepts_runner_signal() -> None:
    payload = score_skill_relevance(
        skill="travel-ops-ko",
        profile="inspect_local",
        contract={"runner": {"entrypoint": "flightclaw-search"}},
        request="항공권 검색",
        task_name=None,
        command=["/Users/kevin/.local/bin/flightclaw-search", "SEL", "NYC"],
    )

    assert payload["verdict"] in {"weak", "strong"}
