from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import source_bundle as source_bundle_module
from source_bundle import (
    build_bundle,
    canonicalize_source_url,
    classify_source_tier,
    contextless_review,
    evaluate_claim_cluster,
    evaluate_risk_lane,
    fidelity_check,
    find_bundle,
    load_registry,
    materialize_artifacts,
    retro_note,
    source_tier_rank,
    upsert_bundle,
)


def sample_bundle() -> dict:
    return {
        "id": "bundle-1",
        "source_url": "https://official.example/report?utm_source=test",
        "source_type": "paper",
        "captured_at": "2026-07-13T10:00:00+09:00",
        "access_status": "full",
        "summary": "Verified report",
        "official_source": True,
        "claims": [
            {
                "claim_id": "c1",
                "text": "SystemX supports 2 reusable outputs.",
                "confidence": "high",
                "evidence": [
                    {"source_url": "https://official.example/report", "official": True, "access_status": "full"},
                    {"source_url": "https://independent.example/review", "official": False, "access_status": "full"},
                ],
            },
            {
                "claim_id": "c2",
                "text": "SystemX is always lossless.",
                "evidence": [
                    {"source_url": "https://official.example/report", "official": True, "access_status": "full"},
                    {"source_url": "https://counter.example/test", "access_status": "full", "stance": "contradicts"},
                ],
            },
        ],
        "interpretations": [
            {"interpretation_id": "i1", "text": "Reuse can reduce drift.", "based_on_claim_ids": ["c1"]}
        ],
        "non_goals": ["Do not replace the whole runtime."],
        "protected_terms": ["SystemX"],
    }


def test_registry_canonicalizes_and_deduplicates_url(tmp_path: Path) -> None:
    registry = tmp_path / "bundles.json"
    first = upsert_bundle(registry, sample_bundle())
    second_input = sample_bundle()
    second_input["source_url"] = "https://OFFICIAL.example/report#section"
    second = upsert_bundle(registry, second_input)

    assert first["created"] is True
    assert second["created"] is False
    assert len(load_registry(registry)["bundles"]) == 1
    assert canonicalize_source_url("https://official.example/report?utm_medium=x") == "https://official.example/report"


def test_canonical_url_preserves_content_query_order_and_ipv6_brackets() -> None:
    first = canonicalize_source_url("https://example.com/run?step=a&step=b&utm_source=x")
    second = canonicalize_source_url("https://example.com/run?step=b&step=a&utm_source=x")
    assert first == "https://example.com/run?step=a&step=b"
    assert second == "https://example.com/run?step=b&step=a"
    assert first != second
    assert (
        canonicalize_source_url("https://[2001:DB8::1]:443/run?step=b&utm_medium=x&step=a#fragment")
        == "https://[2001:db8::1]/run?step=b&step=a"
    )


def test_access_revalidation_upgrades_self_source_evidence(tmp_path: Path) -> None:
    registry = tmp_path / "bundles.json"
    raw = {
        "id": "access-1",
        "source_url": "https://official.example/changelog",
        "source_type": "release",
        "access_status": "partial",
        "uncertainties": ["body unread"],
        "official_source": True,
        "official_expected": True,
        "claims": [{"claim_id": "c1", "text": "Release exists."}],
    }
    first = upsert_bundle(registry, raw)["bundle"]
    assert first["claims"][0]["evidence_gate"]["status"] == "official_unread"
    raw.update({"access_status": "full", "uncertainties": []})
    second = upsert_bundle(registry, raw)["bundle"]
    assert second["claims"][0]["evidence"][0]["access_status"] == "full"
    assert second["claims"][0]["evidence_gate"]["status"] == "single_source"


def test_partial_source_requires_uncertainty_and_claim_rewrite_is_rejected(tmp_path: Path) -> None:
    raw = sample_bundle()
    raw.update({"access_status": "partial", "uncertainties": []})
    with pytest.raises(ValueError, match="uncertainty"):
        build_bundle(raw)

    registry = tmp_path / "bundles.json"
    upsert_bundle(registry, sample_bundle())
    rewritten = sample_bundle()
    rewritten["claims"][0]["text"] = "Silently altered conclusion."
    with pytest.raises(ValueError, match="silently rewritten"):
        upsert_bundle(registry, rewritten)


def test_conflict_gate_exposes_all_required_states() -> None:
    verified = evaluate_claim_cluster(
        "c1",
        [
            {"source_url": "https://official.example/a", "official": True, "access_status": "full"},
            {"source_url": "https://independent.example/a", "official": False, "access_status": "full"},
        ],
    )
    single = evaluate_claim_cluster(
        "c2", [{"source_url": "https://one.example/a", "official": True, "access_status": "full"}]
    )
    conflicted = evaluate_claim_cluster(
        "c3", [{"source_url": "https://one.example/a", "access_status": "full", "stance": "contradicts"}]
    )
    unread = evaluate_claim_cluster(
        "c4", [{"source_url": "https://official.example/a", "official": True, "access_status": "partial"}], official_expected=True
    )
    stale = evaluate_claim_cluster(
        "c5",
        [{"source_url": "https://official.example/a", "official": True, "access_status": "full", "published_at": "2020-01-01"}],
        freshness_required=True,
        now=datetime(2026, 7, 13, tzinfo=timezone.utc),
    )

    assert [verified["status"], single["status"], conflicted["status"], unread["status"], stale["status"]] == [
        "verified",
        "single_source",
        "conflicted",
        "official_unread",
        "stale_or_unclear",
    ]
    assert verified["decision"] == "promote"
    assert conflicted["decision"] == "reject"


def test_multi_artifact_pipeline_keeps_lineage_and_verified_only_publishable_claims(tmp_path: Path) -> None:
    registry = tmp_path / "bundles.json"
    bundle = upsert_bundle(registry, sample_bundle())["bundle"]
    output = tmp_path / "output"

    result = materialize_artifacts(registry, [bundle["id"]], output)

    assert result["ok"] is True
    assert result["completion_sip"]["ok"] is True
    assert result["ssot_check"]["ok"] is True
    assert result["contextless_review"]["ok"] is True
    assert len(result["artifacts"]) == 7
    script = (output / "bundle-1-script.yaml").read_text(encoding="utf-8")
    video = json.loads((output / "bundle-1-video.json").read_text(encoding="utf-8"))
    briefing = json.loads((output / "bundle-1-briefing.json").read_text(encoding="utf-8"))
    prd = (output / "bundle-1-prd.md").read_text(encoding="utf-8")
    assert "c1" in script and "c2" not in script
    assert [scene["claim_id"] for scene in video["scenes"]] == ["c1"]
    assert {item["evidence_state"] for item in briefing["items"]} == {"verified", "conflicted"}
    assert "## Source-derived requirements" in prd and "## Non-goals" in prd and "Held evidence" in prd
    stored = find_bundle(registry, bundle_id="bundle-1")
    assert stored is not None
    assert len(stored["derived_artifacts"]) == 7


def test_forged_derived_artifact_input_is_rejected(tmp_path: Path) -> None:
    raw = sample_bundle()
    raw["derived_artifacts"] = [
        {"type": "content", "path": str(tmp_path / "missing.md"), "sha256": "0" * 64, "bytes": 999}
    ]
    with pytest.raises(ValueError, match="readback evidence"):
        upsert_bundle(tmp_path / "bundles.json", raw)


def test_cross_bundle_claim_id_collision_fails_closed_before_derivation(tmp_path: Path) -> None:
    registry = tmp_path / "bundles.json"
    first = upsert_bundle(registry, sample_bundle())["bundle"]
    second = upsert_bundle(
        registry,
        {
            "id": "bundle-2",
            "source_url": "https://other.example/report",
            "access_status": "full",
            "claims": [{"claim_id": "c1", "text": "A separate local claim."}],
        },
    )["bundle"]
    bundles = [find_bundle(registry, bundle_id=first["id"]), find_bundle(registry, bundle_id=second["id"])]
    assert all(bundle is not None for bundle in bundles)
    review = contextless_review([bundle for bundle in bundles if bundle is not None], {"bad-video.json": '{"claim_id":"c1"}'})
    assert review["ok"] is False
    assert "ambiguous cross-bundle" in review["issues"][0]["issue"]
    output = tmp_path / "output"
    with pytest.raises(ValueError, match="unique across"):
        materialize_artifacts(registry, [first["id"], second["id"]], output)
    assert not output.exists()


def test_contextless_and_fidelity_checks_reject_fact_drift() -> None:
    bundle = build_bundle(sample_bundle())
    malicious = contextless_review(
        [bundle],
        {"bad-video.json": '{"claim_id":"c2","text":"promoted despite conflict"}'},
    )
    assert malicious["ok"] is False
    assert "non-verified" in malicious["issues"][0]["issue"]

    original = "[c1] SystemX is not safe because failure rate is 12%. https://official.example/report"
    rewritten = "[c1] It is safe."
    check = fidelity_check(original, rewritten, protected_terms=["SystemX"])
    assert check["ok"] is False
    assert check["missing_protected_terms"] == ["SystemX"]
    assert check["polarity_changed"] is True
    assert check["causality_changed"] is True

    invented = fidelity_check(original, original + "\n[c1] SystemX improves reliability.", protected_terms=["SystemX"])
    assert invented["ok"] is False
    assert invented["unsupported_assertions"]

    injected = fidelity_check(original, original + "\n[c9] ProjectNova reports 99%. https://new.example/fact")
    assert injected["ok"] is False
    assert injected["added_facts"]["claim_refs"] == ["c9"]
    assert injected["added_facts"]["numbers"] == ["99%"]

    for payload in ("The tool cures cancer.", "이 도구는 암을 치료한다."):
        bypass = fidelity_check(original, original + "\n" + payload)
        assert bypass["ok"] is False
        assert bypass["unsupported_assertions"]
        assert bypass["added_lexical_tokens"]


def test_cross_bundle_semantic_polarity_conflict_blocks_both_verified_claims(tmp_path: Path) -> None:
    registry = tmp_path / "bundles.json"

    def claim(claim_id: str, text: str, polarity: str, domain: str) -> dict:
        return {
            "claim_id": claim_id,
            "cluster_key": "feature-availability",
            "polarity": polarity,
            "text": text,
            "evidence": [
                {"source_url": f"https://{domain}.example/official", "official": True, "access_status": "full"},
                {"source_url": f"https://{domain}.example/review", "official": False, "access_status": "full"},
            ],
        }

    first = upsert_bundle(
        registry,
        {"id": "semantic-a", "source_url": "https://a.example/report", "access_status": "full", "claims": [claim("positive-claim", "Feature is available.", "positive", "a")]},
    )["bundle"]
    second = upsert_bundle(
        registry,
        {"id": "semantic-b", "source_url": "https://b.example/report", "access_status": "full", "claims": [claim("negative-claim", "Feature is not available.", "negative", "b")]},
    )["bundle"]
    assert all(item["claims"][0]["evidence_gate"]["status"] == "verified" for item in (first, second))

    output = tmp_path / "output"
    result = materialize_artifacts(registry, [first["id"], second["id"]], output)
    video = json.loads((output / "source-bundle-video.json").read_text(encoding="utf-8"))
    briefing = json.loads((output / "source-bundle-briefing.json").read_text(encoding="utf-8"))
    assert result["semantic_conflicts"][0]["cluster_key"] == "feature-availability"
    assert video["status"] == "blocked_no_verified_claims"
    assert video["scenes"] == []
    assert {item["evidence_state"] for item in briefing["items"]} == {"conflicted"}


def test_materialize_rolls_back_files_and_registry_as_one_transaction(tmp_path: Path) -> None:
    registry = tmp_path / "bundles.json"
    bundle = upsert_bundle(registry, sample_bundle())["bundle"]
    registry_before = registry.read_bytes()
    output = tmp_path / "output"
    output.mkdir()
    preserved = output / "bundle-1-content.md"
    preserved.write_text("preexisting content\n", encoding="utf-8")

    with patch.object(source_bundle_module, "_write_json_atomic", side_effect=OSError("injected registry failure")):
        with pytest.raises(OSError, match="injected"):
            materialize_artifacts(registry, [bundle["id"]], output)

    assert registry.read_bytes() == registry_before
    assert preserved.read_text(encoding="utf-8") == "preexisting content\n"
    assert sorted(path.name for path in output.iterdir()) == ["bundle-1-content.md"]


def test_source_write_path_enforces_capability_lane(tmp_path: Path) -> None:
    denied = {"allowed": False, "reason": "capability_disabled"}
    registry = tmp_path / "bundles.json"
    with patch.object(source_bundle_module, "evaluate_risk_lane", return_value=denied):
        with pytest.raises(PermissionError, match="capability lane"):
            upsert_bundle(registry, sample_bundle())
    assert not registry.exists()


def test_capability_lane_reuses_action_scope_and_blocks_escalation() -> None:
    config = ROOT / "references" / "capability_boundaries.json"
    blocked = evaluate_risk_lane("파일을 확인해줘", "local_write", target="note.md", config_path=config)
    allowed = evaluate_risk_lane("파일을 저장해줘", "local_write", target="note.md", config_path=config)
    disabled = evaluate_risk_lane("보안 장치를 제어해줘", "high_risk_control", target="device", config_path=config, explicit_approval=True)
    assert blocked["allowed"] is False
    assert allowed["allowed"] is True
    assert allowed["execution_profile"] == "workspace_edit"
    assert disabled == {"allowed": False, "capability": "high_risk_control", "reason": "capability_disabled"}


def test_invalid_scheme_and_unknown_interpretation_claim_are_rejected() -> None:
    raw = sample_bundle()
    raw["source_url"] = "javascript:alert(1)"
    with pytest.raises(ValueError, match="http"):
        build_bundle(raw)
    raw = sample_bundle()
    raw["interpretations"][0]["based_on_claim_ids"] = ["missing"]
    with pytest.raises(ValueError, match="unknown claims"):
        build_bundle(raw)
    raw = sample_bundle()
    raw["interpretations"][0]["based_on_claim_ids"] = []
    with pytest.raises(ValueError, match="supporting claim"):
        build_bundle(raw)


def test_retro_note_is_deduplicated_and_never_auto_promoted(tmp_path: Path) -> None:
    log = tmp_path / "review.jsonl"
    first = retro_note(log, problem="capture lost lineage", evidence="test failure 17", candidate_type="skill", bundle_ids=["bundle-1"])
    second = retro_note(log, problem="capture lost lineage", evidence="test failure 17", candidate_type="skill", bundle_ids=["bundle-1"])
    assert first["created"] is True
    assert second["created"] is False
    assert first["candidate"]["status"] == "candidate"
    assert first["candidate"]["quality_label"] == "raw"
    assert first["candidate"]["promotion_requires_review"] is True
    assert len(log.read_text(encoding="utf-8").splitlines()) == 1


def test_source_tier_ordering_helper_ranks_primary_and_raw_above_derived_above_model_generated() -> None:
    assert source_tier_rank("primary") == source_tier_rank("raw")
    assert source_tier_rank("primary") > source_tier_rank("derived") > source_tier_rank("model_generated")
    # Absent/unknown tiers default to primary rank so untiered evidence is unaffected.
    assert source_tier_rank(None) == source_tier_rank("primary")
    assert source_tier_rank("") == source_tier_rank("primary")
    assert source_tier_rank("not-a-real-tier") == source_tier_rank("primary")
    assert classify_source_tier({}) == "primary"
    assert classify_source_tier({"source_tier": "MODEL_GENERATED"}) == "model_generated"
    assert classify_source_tier({"source_tier": "bogus"}) == "primary"


def test_model_generated_only_cluster_is_not_promoted() -> None:
    gate = evaluate_claim_cluster(
        "c1",
        [
            {"source_url": "https://a.example/x", "official": True, "access_status": "full", "source_tier": "model_generated"},
            {"source_url": "https://b.example/y", "official": True, "access_status": "full", "source_tier": "model_generated"},
        ],
    )
    # Absent the tier gate this would satisfy the readable-urls>=2 + official verified rule.
    assert gate["status"] != "verified"
    assert gate["decision"] in {"hold", "reject"}
    assert gate["model_generated_only"] is True


def test_primary_or_raw_source_with_corroboration_still_promotes() -> None:
    primary_plus_model_generated = evaluate_claim_cluster(
        "c1",
        [
            {"source_url": "https://official.example/report", "official": True, "access_status": "full", "source_tier": "primary"},
            {"source_url": "https://helper.example/note", "official": False, "access_status": "full", "source_tier": "model_generated"},
        ],
    )
    raw_plus_model_generated = evaluate_claim_cluster(
        "c2",
        [
            {"source_url": "https://official.example/report", "official": True, "access_status": "full", "source_tier": "raw"},
            {"source_url": "https://helper.example/note", "official": False, "access_status": "full", "source_tier": "model_generated"},
        ],
    )
    for gate in (primary_plus_model_generated, raw_plus_model_generated):
        assert gate["status"] == "verified"
        assert gate["decision"] == "promote"
        assert gate["model_generated_only"] is False


def test_tier_ordering_respected_when_mixing_sources() -> None:
    # A derived source (someone's write-up) still counts as real corroboration alongside a
    # model-generated assertion, even with no primary/raw source present.
    derived_plus_model_generated = evaluate_claim_cluster(
        "c1",
        [
            {"source_url": "https://writeup.example/a", "official": True, "access_status": "full", "source_tier": "derived"},
            {"source_url": "https://helper.example/b", "official": False, "access_status": "full", "source_tier": "model_generated"},
        ],
    )
    assert derived_plus_model_generated["status"] == "verified"
    assert derived_plus_model_generated["model_generated_only"] is False

    # The same URL tiered differently across evidence records should be scored by its best tier.
    best_tier_wins = evaluate_claim_cluster(
        "c2",
        [
            {"source_url": "https://official.example/report", "official": True, "access_status": "full", "source_tier": "model_generated"},
            {"source_url": "https://official.example/report", "official": True, "access_status": "full", "source_tier": "primary"},
            {"source_url": "https://helper.example/b", "official": False, "access_status": "full", "source_tier": "model_generated"},
        ],
    )
    assert best_tier_wins["model_generated_only"] is False
    assert best_tier_wins["status"] == "verified"


def test_tier_absent_inputs_reproduce_existing_behavior() -> None:
    # No source_tier field anywhere: must behave exactly as before tiering existed.
    untiered = evaluate_claim_cluster(
        "c1",
        [
            {"source_url": "https://official.example/a", "official": True, "access_status": "full"},
            {"source_url": "https://independent.example/a", "official": False, "access_status": "full"},
        ],
    )
    assert untiered["status"] == "verified"
    assert untiered["decision"] == "promote"
    assert untiered["model_generated_only"] is False

    single = evaluate_claim_cluster(
        "c2", [{"source_url": "https://one.example/a", "official": True, "access_status": "full"}]
    )
    assert single["status"] == "single_source"
    assert single["model_generated_only"] is False


def test_claim_level_model_generated_only_evidence_is_not_verified_in_built_bundle() -> None:
    raw = sample_bundle()
    raw["claims"] = [
        {
            "claim_id": "c1",
            "text": "SystemX supports 2 reusable outputs.",
            "evidence": [
                {"source_url": "https://official.example/report", "official": True, "access_status": "full", "source_tier": "model_generated"},
                {"source_url": "https://independent.example/review", "official": False, "access_status": "full", "source_tier": "model_generated"},
            ],
        }
    ]
    bundle = build_bundle(raw)
    gate = bundle["claims"][0]["evidence_gate"]
    assert gate["status"] != "verified"
    assert gate["model_generated_only"] is True
    assert bundle["claims"][0]["evidence"][0]["source_tier"] == "model_generated"
