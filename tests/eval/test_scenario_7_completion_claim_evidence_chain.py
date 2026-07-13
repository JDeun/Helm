from __future__ import annotations

from scripts.adaptive_harness_lib import postflight_payload_for_entry
from scripts.reply_gate import evaluate_claims


def test_scenario_7_completion_claim_evidence_chain() -> None:
    entry = {
        "task_id": "eval-claim-chain",
        "profile": "workspace_edit",
        "status": "completed",
        "completion_claims": [
            {
                "claim_id": "merge_ready",
                "claim": "merge ready",
                "evidence_type": "review",
                "evidence_refs": ["review:passed"],
                "depends_on": ["verified"],
            },
            {
                "criterion_id": "verified",
                "claim": "verified",
                "evidence_type": "test",
                "evidence_refs": ["test:pytest"],
            },
        ],
        "evidence_refs": ["review:passed"],
        "completion_evidence": ["test:pytest"],
    }

    entry["finalization_gate"] = evaluate_claims(entry)
    assert entry["finalization_gate"]["ok"]
    assert postflight_payload_for_entry(
        entry,
        task_id=entry["task_id"],
        contract={},
        enforcement_level="light",
        harness_policy={"validation": {}},
    )["ok"]

    entry["completion_evidence"] = []
    entry["finalization_gate"] = evaluate_claims(entry)
    assert not entry["finalization_gate"]["ok"]
    postflight = postflight_payload_for_entry(
        entry,
        task_id=entry["task_id"],
        contract={},
        enforcement_level="light",
        harness_policy={"validation": {}},
    )
    assert not next(check for check in postflight["checks"] if check["name"] == "completion_policy")["ok"]
