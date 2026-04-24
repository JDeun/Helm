"""Intelligence tier skeleton for future extension.

v2.0 implements L0 (static safety rules) and L1 (deterministic scoring via
route_contract_lib). L2-L4 are documented here as future extension points.

Tiers:
  L0: Static safety rules (command_guard.py)
  L1: Deterministic scoring (route_contract_lib.py)
  L2: Optional BM25 / lexical retrieval (future)
  L3: Optional local model / local classifier (future)
  L4: Optional cloud provider (future)
"""
from __future__ import annotations


class IntelligenceTier:
    """Resolve the active intelligence tier from a discovery snapshot."""

    def __init__(self, *, discovery_snapshot: dict) -> None:
        self.discovery_snapshot = discovery_snapshot

    def mode(self) -> str:
        return "deterministic_only"

    def cloud_calls_enabled(self) -> bool:
        return False

    def local_model_calls_enabled(self) -> bool:
        return False

    def available_tiers(self) -> list[str]:
        return ["L0_static_safety", "L1_deterministic_scoring"]
