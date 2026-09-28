"""helm model-repair-check — Wave 2 CLI smoke probe.

A "doctor"-style subcommand that verifies the model-repair library is
loadable and that feature-flag detection works correctly.  Prints the
env-flag state, loaded policy summary, and respond-tool schema name.

Exit 0 always — this is a probe, not a gating check.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_POLICY_PATH = ROOT / "references" / "local_model_proxy_policy.json"
_SCHEMA_PATH = ROOT / "references" / "respond_tool_schema.json"


def cmd_model_repair_check(args: argparse.Namespace) -> int:
    """Print env detection, policy, and schema name. Exit 0 always."""
    from scripts.model_repair import repair_enabled
    from scripts.respond_tool_wiring import synthetic_respond_enabled

    repair_on = repair_enabled()
    respond_on = synthetic_respond_enabled()

    print(f"HELM_MODEL_REPAIR         = {repair_on}")
    print(f"HELM_SYNTHETIC_RESPOND    = {respond_on}")
    print()

    # Load and summarise policy
    try:
        policy = json.loads(_POLICY_PATH.read_text(encoding="utf-8"))
        print(f"policy.max_retries        = {policy.get('max_retries', 'n/a')}")
        print(f"policy.nudge_on           = {policy.get('nudge_on', [])}")
        print(f"policy.abort_on           = {policy.get('abort_on', [])}")
    except (OSError, json.JSONDecodeError) as exc:
        print(f"warning: could not load policy: {exc}", file=sys.stderr)
    print()

    # Load and summarise respond-tool schema
    try:
        schema = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
        schema_name = schema.get("function", {}).get("name", schema.get("name", "unknown"))
        print(f"respond_tool_schema.name  = {schema_name}")
    except (OSError, json.JSONDecodeError) as exc:
        print(f"warning: could not load respond_tool_schema: {exc}", file=sys.stderr)

    return 0
