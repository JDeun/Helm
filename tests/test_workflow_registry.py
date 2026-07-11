from scripts.workflow_registry import validate_registry


def test_workflow_registry_requires_verification_handoff_and_stop_contracts() -> None:
    issues = validate_registry({"units": [{"name": "weak", "trigger_condition": "manual"}]})
    assert "units[0] missing verification_steps" in issues
    assert "units[0] missing handoff_conditions" in issues
    assert "units[0] missing stop_conditions" in issues


def test_workflow_registry_accepts_complete_unit() -> None:
    unit = {
        "name": "demo", "trigger_condition": "manual", "allowed_inputs": [], "required_live_sources": [],
        "mutable_surfaces": [], "forbidden_actions": [], "verification_steps": ["readback"],
        "final_reporting_rules": ["cite evidence"], "handoff_conditions": ["approval"], "stop_conditions": ["complete"],
    }
    assert validate_registry({"units": [unit]}) == []
