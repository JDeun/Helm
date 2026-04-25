from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from intelligence_tier import IntelligenceTier


# ---------------------------------------------------------------------------
# No snapshot
# ---------------------------------------------------------------------------

def test_no_snapshot_mode_is_deterministic_only() -> None:
    tier = IntelligenceTier(discovery_snapshot={})
    assert tier.mode() == "deterministic_only"


def test_no_snapshot_cloud_disabled() -> None:
    tier = IntelligenceTier(discovery_snapshot={})
    assert tier.cloud_calls_enabled() is False


def test_no_snapshot_local_disabled() -> None:
    tier = IntelligenceTier(discovery_snapshot={})
    assert tier.local_model_calls_enabled() is False


def test_no_snapshot_available_tiers_baseline_only() -> None:
    tier = IntelligenceTier(discovery_snapshot={})
    assert tier.available_tiers() == ["L0_static_safety", "L1_deterministic_scoring"]


# ---------------------------------------------------------------------------
# Local-only snapshot
# ---------------------------------------------------------------------------

_LOCAL_SNAPSHOT = {
    "providers": [
        {"name": "ollama", "kind": "local", "available": True},
    ]
}


def test_local_only_mode() -> None:
    tier = IntelligenceTier(discovery_snapshot=_LOCAL_SNAPSHOT)
    assert tier.mode() == "local_model_available"


def test_local_only_local_enabled() -> None:
    tier = IntelligenceTier(discovery_snapshot=_LOCAL_SNAPSHOT)
    assert tier.local_model_calls_enabled() is True


def test_local_only_cloud_disabled() -> None:
    tier = IntelligenceTier(discovery_snapshot=_LOCAL_SNAPSHOT)
    assert tier.cloud_calls_enabled() is False


def test_local_only_available_tiers_includes_l3() -> None:
    tier = IntelligenceTier(discovery_snapshot=_LOCAL_SNAPSHOT)
    tiers = tier.available_tiers()
    assert "L3_local_model" in tiers
    assert "L4_cloud_provider" not in tiers


# ---------------------------------------------------------------------------
# Cloud-only snapshot
# ---------------------------------------------------------------------------

_CLOUD_SNAPSHOT = {
    "providers": [
        {"name": "openai", "kind": "cloud", "available": True},
    ]
}


def test_cloud_only_mode() -> None:
    tier = IntelligenceTier(discovery_snapshot=_CLOUD_SNAPSHOT)
    assert tier.mode() == "cloud_available"


def test_cloud_only_cloud_enabled() -> None:
    tier = IntelligenceTier(discovery_snapshot=_CLOUD_SNAPSHOT)
    assert tier.cloud_calls_enabled() is True


def test_cloud_only_local_disabled() -> None:
    tier = IntelligenceTier(discovery_snapshot=_CLOUD_SNAPSHOT)
    assert tier.local_model_calls_enabled() is False


def test_cloud_only_available_tiers_includes_l4() -> None:
    tier = IntelligenceTier(discovery_snapshot=_CLOUD_SNAPSHOT)
    tiers = tier.available_tiers()
    assert "L4_cloud_provider" in tiers
    assert "L3_local_model" not in tiers


# ---------------------------------------------------------------------------
# Both local and cloud available
# ---------------------------------------------------------------------------

_BOTH_SNAPSHOT = {
    "providers": [
        {"name": "ollama", "kind": "local", "available": True},
        {"name": "anthropic", "kind": "cloud", "available": True},
    ]
}


def test_both_mode_prefers_local_label() -> None:
    tier = IntelligenceTier(discovery_snapshot=_BOTH_SNAPSHOT)
    assert tier.mode() == "local_model_available"


def test_both_cloud_enabled() -> None:
    tier = IntelligenceTier(discovery_snapshot=_BOTH_SNAPSHOT)
    assert tier.cloud_calls_enabled() is True


def test_both_local_enabled() -> None:
    tier = IntelligenceTier(discovery_snapshot=_BOTH_SNAPSHOT)
    assert tier.local_model_calls_enabled() is True


def test_both_available_tiers_includes_l3_and_l4() -> None:
    tier = IntelligenceTier(discovery_snapshot=_BOTH_SNAPSHOT)
    tiers = tier.available_tiers()
    assert "L3_local_model" in tiers
    assert "L4_cloud_provider" in tiers


# ---------------------------------------------------------------------------
# Alternative snapshot shape: {"local": [...], "cloud": [...]}
# ---------------------------------------------------------------------------

def test_local_cloud_sublist_shape() -> None:
    snapshot = {
        "local": [{"name": "llama.cpp"}],
        "cloud": [{"name": "openai"}],
    }
    tier = IntelligenceTier(discovery_snapshot=snapshot)
    assert tier.local_model_calls_enabled() is True
    assert tier.cloud_calls_enabled() is True


# ---------------------------------------------------------------------------
# Kind-based detection (no name match)
# ---------------------------------------------------------------------------

def test_kind_local_detected_without_name_match() -> None:
    snapshot = {"providers": [{"name": "my-custom-model", "kind": "local"}]}
    tier = IntelligenceTier(discovery_snapshot=snapshot)
    assert tier.local_model_calls_enabled() is True


def test_kind_cloud_detected_without_name_match() -> None:
    snapshot = {"providers": [{"name": "my-api-gateway", "kind": "cloud"}]}
    tier = IntelligenceTier(discovery_snapshot=snapshot)
    assert tier.cloud_calls_enabled() is True
