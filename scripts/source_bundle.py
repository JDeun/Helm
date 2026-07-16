#!/usr/bin/env python3
"""File-native SourceBundle registry and downstream quality harness.

The registry is the canonical source-of-truth.  Derived artifacts contain
lineage, never become a second claim store, and are verified by readback before
they are reported as written.
"""
from __future__ import annotations

import argparse
import copy
import contextlib
import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

try:  # Unix is Helm/OpenClaw's supported runtime; the fallback keeps imports portable.
    import fcntl
except ImportError:  # pragma: no cover - exercised only on non-Unix runtimes
    fcntl = None


ACCESS_STATUSES = frozenset({"full", "partial", "blocked"})
EVIDENCE_STATUSES = frozenset({"verified", "single_source", "conflicted", "official_unread", "stale_or_unclear"})
CONFIDENCE_LEVELS = frozenset({"low", "medium", "high"})
POLARITIES = frozenset({"positive", "negative"})
# Provenance tiers, ordered weakest -> strongest. "raw" ranks with "primary": both are
# first-hand material (a raw capture vs. a curated/official primary document), as opposed
# to "derived" (someone's write-up of a source) or "model_generated" (an LLM asserted it
# with no external source backing it).
SOURCE_TIERS = ("model_generated", "derived", "primary", "raw")
_SOURCE_TIER_RANK = {"model_generated": 0, "derived": 1, "primary": 2, "raw": 2}
DEFAULT_SOURCE_TIER = "primary"  # untiered evidence ranks as primary, preserving pre-tiering behavior
TRACKING_KEYS = frozenset({"fbclid", "gclid", "mc_cid", "mc_eid"})
TRACKING_PREFIXES = ("utm_",)
CLAIM_REF_RE = re.compile(r"\[([A-Za-z0-9_.:-]+)\]")
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{1,127}$")
DEFAULT_OFFICIAL_UNREAD_WARNING_RATIO = 0.30
CAPABILITY_CONFIG = Path(__file__).resolve().parents[1] / "references" / "capability_boundaries.json"
NEGATION_RE = re.compile(r"\b(?:not|no|never|without|cannot|can't|doesn't|isn't|aren't)\b|않|아니|없|불가|금지", re.IGNORECASE)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_datetime(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if re.fullmatch(r"\d{8}", text):
        text = f"{text[:4]}-{text[4:6]}-{text[6:]}"
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def canonicalize_source_url(value: str) -> str:
    """Canonicalize an HTTP(S) URL without changing content-bearing query data."""
    parsed = urlsplit(str(value or "").strip())
    if parsed.scheme.casefold() not in {"http", "https"} or not parsed.hostname:
        raise ValueError("source_url must be an absolute http(s) URL")
    hostname = parsed.hostname.casefold()
    host = f"[{hostname}]" if ":" in hostname else hostname
    port = parsed.port
    if port and not ((parsed.scheme.casefold() == "http" and port == 80) or (parsed.scheme.casefold() == "https" and port == 443)):
        host = f"{host}:{port}"
    path = re.sub(r"/{2,}", "/", parsed.path or "/")
    if path != "/":
        path = path.rstrip("/")
    query = [
        (key, val)
        for key, val in parse_qsl(parsed.query, keep_blank_values=True)
        if key.casefold() not in TRACKING_KEYS and not key.casefold().startswith(TRACKING_PREFIXES)
    ]
    return urlunsplit((parsed.scheme.casefold(), host, path, urlencode(query, doseq=True), ""))


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")[:56] or "source"


def bundle_id_for(source_url: str, captured_at: str, label: str = "") -> str:
    parsed = urlsplit(source_url)
    when = (_parse_datetime(captured_at) or datetime.now(timezone.utc)).date().isoformat()
    hint = label or f"{parsed.hostname}-{parsed.path}"
    digest = hashlib.sha256(source_url.encode("utf-8")).hexdigest()[:8]
    return f"{when}-{_slug(hint)}-{digest}"


def _unique_strings(values: Iterable[object]) -> list[str]:
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in result:
            result.append(text)
    return result


def source_tier_rank(tier: object) -> int:
    """Ordinal rank of a provenance tier: primary/raw > derived > model_generated.

    Absent or unrecognized tiers rank as DEFAULT_SOURCE_TIER so evidence that predates
    (or simply omits) tiering is scored exactly as it was before tiering existed.
    """
    text = str(tier or "").strip().casefold()
    return _SOURCE_TIER_RANK.get(text, _SOURCE_TIER_RANK[DEFAULT_SOURCE_TIER])


def classify_source_tier(item: object) -> str:
    """Read the normalized provenance tier off a source/claim evidence mapping."""
    text = str((item or {}).get("source_tier") if isinstance(item, dict) else "").strip().casefold()
    return text if text in _SOURCE_TIER_RANK else DEFAULT_SOURCE_TIER


def _normalize_evidence(raw: object, bundle: dict[str, Any]) -> dict[str, Any]:
    if isinstance(raw, str):
        item: dict[str, Any] = {"locator": raw}
    elif isinstance(raw, dict):
        item = dict(raw)
    else:
        raise ValueError("claim evidence must be a string or object")
    source_url = canonicalize_source_url(str(item.get("source_url") or bundle["source_url"]))
    access_status = str(item.get("access_status") or bundle["access_status"])
    if access_status not in ACCESS_STATUSES:
        raise ValueError(f"invalid evidence access_status: {access_status}")
    stance = str(item.get("stance") or "supports")
    if stance not in {"supports", "contradicts"}:
        raise ValueError("evidence stance must be supports or contradicts")
    return {
        "source_url": source_url,
        "locator": str(item.get("locator") or item.get("evidence") or "source"),
        "official": bool(item.get("official", bundle.get("official_source", False))),
        "access_status": access_status,
        "stance": stance,
        "published_at": item.get("published_at") or bundle.get("published_at"),
        "source_tier": item.get("source_tier"),
    }


def _normalize_cluster_key(value: str) -> str:
    folded = NEGATION_RE.sub(" ", str(value or "").casefold())
    tokens = re.findall(r"[a-z0-9가-힣]+", folded)
    stop = {"a", "an", "the", "is", "are", "was", "were", "does", "do"}
    normalized = "-".join(token for token in tokens if token not in stop)[:127]
    if not normalized:
        raise ValueError("claim cluster_key requires meaningful text")
    return normalized


def evaluate_claim_cluster(
    claim_id: str,
    evidence: Iterable[dict[str, Any]],
    *,
    official_expected: bool = False,
    freshness_required: bool = False,
    max_age_days: int = 45,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Classify a claim cluster using conservative ConfRAG-style precedence."""
    items = [dict(item) for item in evidence]
    urls = sorted({canonicalize_source_url(str(item["source_url"])) for item in items if item.get("source_url")})
    readable = [item for item in items if item.get("access_status") == "full"]
    readable_urls = {canonicalize_source_url(str(item["source_url"])) for item in readable if item.get("source_url")}
    official_readable = [item for item in readable if item.get("official")]
    contradictions = [
        {"source_url": item.get("source_url"), "locator": item.get("locator")}
        for item in items
        if item.get("stance") == "contradicts"
    ]
    # Prefer the highest-tier evidence available per source when weighing corroboration:
    # if the same URL shows up tiered differently across evidence records, its best tier wins.
    readable_tier_rank_by_url: dict[str, int] = {}
    for item in readable:
        if not item.get("source_url"):
            continue
        url = canonicalize_source_url(str(item["source_url"]))
        rank = source_tier_rank(classify_source_tier(item))
        if rank > readable_tier_rank_by_url.get(url, -1):
            readable_tier_rank_by_url[url] = rank
    model_generated_rank = _SOURCE_TIER_RANK["model_generated"]
    corroborated_by_non_model_source = any(
        rank > model_generated_rank for rank in readable_tier_rank_by_url.values()
    )
    model_generated_only = bool(readable_tier_rank_by_url) and not corroborated_by_non_model_source
    status = "single_source"
    if contradictions:
        status = "conflicted"
    elif official_expected and not official_readable:
        status = "official_unread"
    elif freshness_required:
        dates = [parsed for parsed in (_parse_datetime(item.get("published_at")) for item in items) if parsed]
        reference = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        if not dates or max(dates) < reference - timedelta(days=max_age_days):
            status = "stale_or_unclear"
        elif len(readable_urls) >= 2 and official_readable and not model_generated_only:
            status = "verified"
    elif len(readable_urls) >= 2 and official_readable and not model_generated_only:
        status = "verified"
    decision = "promote" if status == "verified" else "reject" if status == "conflicted" else "hold"
    return {
        "claim_id": claim_id,
        "status": status,
        "source_count": len(urls),
        "readable_source_count": len(readable_urls),
        "official_source_count": len({item.get("source_url") for item in official_readable}),
        "contradictions": contradictions,
        "decision": decision,
        "model_generated_only": model_generated_only,
    }


def _normalize_claim(raw: object, bundle: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("each claim must be an object")
    claim_id = str(raw.get("claim_id") or "").strip()
    text = str(raw.get("text") or "").strip()
    if not ID_RE.fullmatch(claim_id):
        raise ValueError(f"invalid claim_id: {claim_id!r}")
    if not text:
        raise ValueError(f"claim {claim_id} requires text")
    confidence = str(raw.get("confidence") or "medium")
    if confidence not in CONFIDENCE_LEVELS:
        raise ValueError(f"invalid claim confidence: {confidence}")
    raw_evidence = raw.get("evidence")
    if raw_evidence is None:
        raw_evidence = [{"locator": "source"}]
    elif isinstance(raw_evidence, (str, dict)):
        raw_evidence = [raw_evidence]
    if not isinstance(raw_evidence, list) or not raw_evidence:
        raise ValueError(f"claim {claim_id} requires evidence")
    evidence = [_normalize_evidence(item, bundle) for item in raw_evidence]
    gate = evaluate_claim_cluster(
        claim_id,
        evidence,
        official_expected=bool(raw.get("official_expected", bundle.get("official_expected", False))),
        freshness_required=bool(raw.get("freshness_required", False)),
        max_age_days=int(raw.get("max_age_days") or 45),
    )
    polarity = str(raw.get("polarity") or ("negative" if NEGATION_RE.search(text) else "positive"))
    if polarity not in POLARITIES:
        raise ValueError(f"invalid claim polarity: {polarity}")
    return {
        "claim_id": claim_id,
        "cluster_key": _normalize_cluster_key(str(raw.get("cluster_key") or text)),
        "polarity": polarity,
        "text": text,
        "confidence": confidence,
        "official_expected": bool(raw.get("official_expected", bundle.get("official_expected", False))),
        "freshness_required": bool(raw.get("freshness_required", False)),
        "max_age_days": int(raw.get("max_age_days") or 45),
        "evidence": evidence,
        "evidence_gate": gate,
    }


def _normalize_interpretation(raw: object, claim_ids: set[str]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("each interpretation must be an object")
    interpretation_id = str(raw.get("interpretation_id") or raw.get("claim_id") or "").strip()
    text = str(raw.get("text") or "").strip()
    based_on = _unique_strings(raw.get("based_on_claim_ids") or [])
    if not ID_RE.fullmatch(interpretation_id) or not text:
        raise ValueError("interpretations require a valid interpretation_id and text")
    if not based_on:
        raise ValueError(f"interpretation {interpretation_id} requires at least one supporting claim")
    unknown = sorted(set(based_on) - claim_ids)
    if unknown:
        raise ValueError(f"interpretation {interpretation_id} references unknown claims: {unknown}")
    return {"interpretation_id": interpretation_id, "text": text, "based_on_claim_ids": based_on}


def _refresh_quality(bundle: dict[str, Any]) -> dict[str, Any]:
    statuses = [str((claim.get("evidence_gate") or {}).get("status")) for claim in bundle.get("claims", [])]
    unread = statuses.count("official_unread")
    ratio = unread / len(statuses) if statuses else 0.0
    bundle["quality"] = {
        "claim_count": len(statuses),
        "evidence_state_counts": {status: statuses.count(status) for status in sorted(EVIDENCE_STATUSES)},
        "official_unread_ratio": round(ratio, 4),
        "coverage_warning": ratio >= DEFAULT_OFFICIAL_UNREAD_WARNING_RATIO,
        "coverage_warning_threshold": DEFAULT_OFFICIAL_UNREAD_WARNING_RATIO,
    }
    return bundle


def build_bundle(raw: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("bundle input must be an object")
    if "derived_artifacts" in raw:
        raise ValueError("derived_artifacts are readback evidence and may only be added by register_derived_artifact")
    source_url = canonicalize_source_url(str(raw.get("source_url") or ""))
    access_status = str(raw.get("access_status") or "")
    if access_status not in ACCESS_STATUSES:
        raise ValueError(f"access_status must be one of {sorted(ACCESS_STATUSES)}")
    uncertainties = _unique_strings(raw.get("uncertainties") or [])
    if access_status != "full" and not uncertainties:
        raise ValueError("partial or blocked sources require at least one uncertainty")
    captured_at = str(raw.get("captured_at") or utc_now_iso())
    if _parse_datetime(captured_at) is None:
        raise ValueError("captured_at must be an ISO-8601 timestamp")
    bundle_id = str(raw.get("id") or bundle_id_for(source_url, captured_at, str(raw.get("summary") or "")))
    if not ID_RE.fullmatch(bundle_id):
        raise ValueError(f"invalid bundle id: {bundle_id!r}")
    bundle: dict[str, Any] = {
        "schema_version": 1,
        "id": bundle_id,
        "source_url": source_url,
        "source_type": str(raw.get("source_type") or "web"),
        "captured_at": captured_at,
        "updated_at": str(raw.get("updated_at") or captured_at),
        "access_status": access_status,
        "summary": str(raw.get("summary") or "").strip(),
        "published_at": raw.get("published_at"),
        "official_source": bool(raw.get("official_source", False)),
        "official_expected": bool(raw.get("official_expected", False)),
        "claims": [],
        "interpretations": [],
        "uncertainties": uncertainties,
        "risks": _unique_strings(raw.get("risks") or []),
        "non_goals": _unique_strings(raw.get("non_goals") or []),
        "protected_terms": _unique_strings(raw.get("protected_terms") or []),
        "derived_artifacts": [],
    }
    claims = [_normalize_claim(item, bundle) for item in raw.get("claims") or []]
    claim_ids = [claim["claim_id"] for claim in claims]
    if len(claim_ids) != len(set(claim_ids)):
        raise ValueError("claim_id values must be unique within a bundle")
    bundle["claims"] = claims
    interpretations = [_normalize_interpretation(item, set(claim_ids)) for item in raw.get("interpretations") or []]
    interpretation_ids = [item["interpretation_id"] for item in interpretations]
    if len(interpretation_ids) != len(set(interpretation_ids)):
        raise ValueError("interpretation_id values must be unique within a bundle")
    bundle["interpretations"] = interpretations
    return _refresh_quality(bundle)


def _default_registry_payload() -> dict[str, Any]:
    return {"schema_version": 1, "bundles": []}


def load_registry(path: Path) -> dict[str, Any]:
    if not path.exists():
        return _default_registry_payload()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid SourceBundle registry {path}: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != 1 or not isinstance(payload.get("bundles"), list):
        raise ValueError(f"invalid SourceBundle registry shape: {path}")
    return payload


@contextlib.contextmanager
def _registry_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(path.suffix + ".lock")
    with lock_path.open("a+", encoding="utf-8") as handle:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _write_json_atomic(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def _merge_by_id(existing: list[dict[str, Any]], incoming: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    result = [dict(item) for item in existing]
    index = {str(item.get(key)): pos for pos, item in enumerate(result)}
    for item in incoming:
        identity = str(item.get(key))
        if identity in index:
            current = result[index[identity]]
            if current.get("text") != item.get("text"):
                raise ValueError(f"{key} {identity} cannot be silently rewritten")
            if key == "claim_id":
                if any(current.get(field) != item.get(field) for field in ("cluster_key", "polarity")):
                    raise ValueError(f"{key} {identity} cannot silently change cluster_key or polarity")
                evidence = current.get("evidence") or []
                for record in item.get("evidence") or []:
                    if record not in evidence:
                        evidence.append(record)
                current["evidence"] = evidence
                current["evidence_gate"] = evaluate_claim_cluster(
                    identity,
                    evidence,
                    official_expected=bool(item.get("official_expected", False)),
                    freshness_required=bool(item.get("freshness_required", False)),
                    max_age_days=int(item.get("max_age_days") or 45),
                )
            continue
        index[identity] = len(result)
        result.append(dict(item))
    return result


def _merge_bundle(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    merged = dict(existing)
    rank = {"blocked": 0, "partial": 1, "full": 2}
    if rank[incoming["access_status"]] > rank[existing["access_status"]]:
        merged["access_status"] = incoming["access_status"]
    for key in ("uncertainties", "risks", "non_goals", "protected_terms"):
        merged[key] = _unique_strings([*(existing.get(key) or []), *(incoming.get(key) or [])])
    if incoming.get("summary") and not existing.get("summary"):
        merged["summary"] = incoming["summary"]
    merged["official_source"] = bool(existing.get("official_source") or incoming.get("official_source"))
    merged["official_expected"] = bool(existing.get("official_expected") or incoming.get("official_expected"))
    merged["claims"] = _merge_by_id(existing.get("claims") or [], incoming.get("claims") or [], "claim_id")
    for claim in merged["claims"]:
        for evidence in claim.get("evidence") or []:
            if evidence.get("source_url") == merged["source_url"] and rank[merged["access_status"]] > rank.get(str(evidence.get("access_status")), 0):
                evidence["access_status"] = merged["access_status"]
                evidence["official"] = bool(evidence.get("official") or merged.get("official_source"))
        claim["evidence_gate"] = evaluate_claim_cluster(
            str(claim["claim_id"]),
            claim.get("evidence") or [],
            official_expected=bool(claim.get("official_expected", merged.get("official_expected", False))),
            freshness_required=bool(claim.get("freshness_required", False)),
            max_age_days=int(claim.get("max_age_days") or 45),
        )
    merged["interpretations"] = _merge_by_id(
        existing.get("interpretations") or [], incoming.get("interpretations") or [], "interpretation_id"
    )
    artifacts = [dict(item) for item in existing.get("derived_artifacts") or []]
    artifact_keys = {(str(item.get("type")), str(item.get("path")), str(item.get("sha256"))) for item in artifacts}
    for item in incoming.get("derived_artifacts") or []:
        key = (str(item.get("type")), str(item.get("path")), str(item.get("sha256")))
        if key not in artifact_keys:
            artifacts.append(dict(item))
            artifact_keys.add(key)
    merged["derived_artifacts"] = artifacts
    merged["updated_at"] = utc_now_iso()
    return _refresh_quality(merged)


def _require_source_write(target: Path) -> dict[str, Any]:
    decision = evaluate_risk_lane(
        "SourceBundle 파일을 저장해줘",
        "local_write",
        target=str(target),
        config_path=CAPABILITY_CONFIG,
    )
    if not decision["allowed"]:
        raise PermissionError(f"SourceBundle write blocked by capability lane: {decision['reason']}")
    return decision


def upsert_bundle(registry_path: Path, raw: dict[str, Any]) -> dict[str, Any]:
    incoming = build_bundle(raw)
    _require_source_write(registry_path)
    with _registry_lock(registry_path):
        registry = load_registry(registry_path)
        by_url = {bundle.get("source_url"): pos for pos, bundle in enumerate(registry["bundles"])}
        by_id = {bundle.get("id"): bundle.get("source_url") for bundle in registry["bundles"]}
        if incoming["id"] in by_id and by_id[incoming["id"]] != incoming["source_url"]:
            raise ValueError(f"bundle id collision: {incoming['id']}")
        created = incoming["source_url"] not in by_url
        if created:
            registry["bundles"].append(incoming)
            bundle = incoming
        else:
            position = by_url[incoming["source_url"]]
            bundle = _merge_bundle(registry["bundles"][position], incoming)
            registry["bundles"][position] = bundle
        _write_json_atomic(registry_path, registry)
    return {"created": created, "registry_path": str(registry_path), "bundle": bundle}


def find_bundle(registry_path: Path, *, bundle_id: str | None = None, source_url: str | None = None) -> dict[str, Any] | None:
    wanted_url = canonicalize_source_url(source_url) if source_url else None
    for bundle in load_registry(registry_path)["bundles"]:
        if bundle_id and bundle.get("id") == bundle_id:
            return dict(bundle)
        if wanted_url and bundle.get("source_url") == wanted_url:
            return dict(bundle)
    return None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def completion_sip(paths: Iterable[Path]) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    for raw in paths:
        path = Path(raw)
        exists = path.exists() and path.is_file()
        size = path.stat().st_size if exists else 0
        records.append(
            {"path": str(path), "exists": exists, "bytes": size, "sha256": _sha256(path) if exists else None, "readback_ok": exists and size > 0}
        )
    return {"ok": bool(records) and all(item["readback_ok"] for item in records), "artifacts": records}


def retro_note(
    candidate_log: Path,
    *,
    problem: str,
    evidence: str,
    candidate_type: str = "memory",
    bundle_ids: Iterable[str] = (),
) -> dict[str, Any]:
    """Append a review-only failure lesson without promoting memory or editing policy."""
    problem = problem.strip()
    evidence = evidence.strip()
    if candidate_type not in {"memory", "skill"} or not problem or not evidence:
        raise ValueError("retro note requires type memory|skill, problem, and evidence")
    identity = hashlib.sha256(f"{candidate_type}\0{problem}\0{evidence}".encode("utf-8")).hexdigest()[:16]
    candidate_id = f"retro-{identity}"
    with _registry_lock(candidate_log):
        existing: list[dict[str, Any]] = []
        if candidate_log.exists():
            for line in candidate_log.read_text(encoding="utf-8", errors="replace").splitlines():
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(row, dict):
                    existing.append(row)
        prior = next((row for row in existing if row.get("candidate_id") == candidate_id), None)
        if prior is not None:
            return {"created": False, "candidate": prior, "path": str(candidate_log)}
        row = {
            "candidate_id": candidate_id,
            "candidate_type": f"{candidate_type}_improvement",
            "status": "candidate",
            "quality_label": "raw",
            "problem": problem,
            "evidence": evidence,
            "bundle_ids": _unique_strings(bundle_ids),
            "promotion_requires_review": True,
            "created_at": utc_now_iso(),
        }
        candidate_log.parent.mkdir(parents=True, exist_ok=True)
        with candidate_log.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
    return {"created": True, "candidate": row, "path": str(candidate_log)}


def register_derived_artifact(registry_path: Path, bundle_id: str, artifact_type: str, path: Path) -> dict[str, Any]:
    evidence = completion_sip([path])
    if not evidence["ok"]:
        raise ValueError(f"cannot register unreadable derived artifact: {path}")
    record = {
        "type": artifact_type,
        "path": str(path.resolve()),
        "created_at": utc_now_iso(),
        "bytes": evidence["artifacts"][0]["bytes"],
        "sha256": evidence["artifacts"][0]["sha256"],
    }
    with _registry_lock(registry_path):
        registry = load_registry(registry_path)
        bundle = next((item for item in registry["bundles"] if item.get("id") == bundle_id), None)
        if bundle is None:
            raise KeyError(f"unknown bundle: {bundle_id}")
        existing = bundle.setdefault("derived_artifacts", [])
        if not any(item.get("path") == record["path"] and item.get("sha256") == record["sha256"] for item in existing):
            existing.append(record)
            bundle["updated_at"] = utc_now_iso()
            _write_json_atomic(registry_path, registry)
    return record


def _yaml_scalar(value: object) -> str:
    return json.dumps(str(value), ensure_ascii=False)


def _lineage_header(bundle: dict[str, Any], registry_path: Path) -> str:
    return "\n".join(
        [
            "---",
            "schema_version: 1",
            f"bundle_id: {_yaml_scalar(bundle['id'])}",
            f"source_url: {_yaml_scalar(bundle['source_url'])}",
            f"access_status: {_yaml_scalar(bundle['access_status'])}",
            f"source_bundle_registry: {_yaml_scalar(str(registry_path.resolve()))}",
            "---",
            "",
        ]
    )


def annotate_note_lineage(note_path: Path, bundle: dict[str, Any], registry_path: Path) -> dict[str, Any]:
    text = note_path.read_text(encoding="utf-8")
    fields = {
        "bundle_id": bundle["id"],
        "source_url": bundle["source_url"],
        "access_status": bundle["access_status"],
        "uncertainty": "; ".join(bundle.get("uncertainties") or []) or "none recorded",
        "source_bundle_registry": str(registry_path.resolve()),
    }
    if text.startswith("---\n") and "\n---\n" in text[4:]:
        end = text.find("\n---\n", 4)
        frontmatter = text[4:end].splitlines()
        keys = set(fields)
        frontmatter = [line for line in frontmatter if line.split(":", 1)[0].strip() not in keys]
        frontmatter.extend(f"{key}: {_yaml_scalar(value)}" for key, value in fields.items())
        updated = "---\n" + "\n".join(frontmatter) + text[end:]
    else:
        updated = _lineage_header(bundle, registry_path) + text
    _write_text_atomic(note_path, updated)
    evidence = completion_sip([note_path])
    if not evidence["ok"]:
        raise RuntimeError(f"note lineage readback failed: {note_path}")
    return evidence["artifacts"][0]


def _write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text.rstrip() + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def _write_bytes_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def _snapshot_regular_files(paths: Iterable[Path]) -> dict[Path, bytes | None]:
    snapshots: dict[Path, bytes | None] = {}
    for path in paths:
        if not os.path.lexists(path):
            snapshots[path] = None
        elif path.is_symlink() or not path.is_file():
            raise ValueError(f"transaction target must be a regular file or absent: {path}")
        else:
            snapshots[path] = path.read_bytes()
    return snapshots


def _restore_file_snapshots(snapshots: dict[Path, bytes | None]) -> list[str]:
    errors: list[str] = []
    for path, data in snapshots.items():
        try:
            if data is None:
                if os.path.lexists(path):
                    if path.is_dir() and not path.is_symlink():
                        raise OSError("rollback refuses to remove a directory")
                    path.unlink()
            else:
                _write_bytes_atomic(path, data)
        except OSError as exc:
            errors.append(f"{path}: {exc}")
    return errors


def verified_claims(bundle: dict[str, Any]) -> list[dict[str, Any]]:
    return [claim for claim in bundle.get("claims") or [] if (claim.get("evidence_gate") or {}).get("status") == "verified"]


def apply_semantic_conflict_gate(bundles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    clusters: dict[str, list[tuple[dict[str, Any], dict[str, Any]]]] = {}
    for bundle in bundles:
        for claim in bundle.get("claims") or []:
            key = str(claim.get("cluster_key") or _normalize_cluster_key(str(claim.get("text") or "")))
            claim["cluster_key"] = key
            claim.setdefault("polarity", "negative" if NEGATION_RE.search(str(claim.get("text") or "")) else "positive")
            clusters.setdefault(key, []).append((bundle, claim))
    conflicts: list[dict[str, Any]] = []
    for cluster_key, members in sorted(clusters.items()):
        bundle_ids = {str(bundle["id"]) for bundle, _ in members}
        claim_ids = {str(claim["claim_id"]) for _, claim in members}
        polarities = {str(claim["polarity"]) for _, claim in members}
        if len(bundle_ids) < 2 or len(claim_ids) < 2 or len(polarities) < 2:
            continue
        detail = {
            "cluster_key": cluster_key,
            "members": [
                {"bundle_id": bundle["id"], "claim_id": claim["claim_id"], "polarity": claim["polarity"]}
                for bundle, claim in members
            ],
        }
        conflicts.append(detail)
        for bundle, claim in members:
            gate = dict(claim.get("evidence_gate") or {})
            gate.update({"status": "conflicted", "decision": "reject", "semantic_conflict": detail})
            claim["evidence_gate"] = gate
            _refresh_quality(bundle)
    return conflicts


def _render_capture(bundle: dict[str, Any], registry_path: Path) -> str:
    lines = [_lineage_header(bundle, registry_path), f"# {bundle.get('summary') or bundle['id']}", "", "## Source", "", f"- URL: {bundle['source_url']}", f"- Access: `{bundle['access_status']}`", "", "## Claims"]
    for claim in bundle.get("claims") or []:
        state = (claim.get("evidence_gate") or {}).get("status")
        lines.append(f"- [{claim['claim_id']}] {claim['text']} (`{state}`)")
    if not bundle.get("claims"):
        lines.append("- No source-backed claims recorded.")
    lines.extend(["", "## Interpretations"])
    for item in bundle.get("interpretations") or []:
        lines.append(f"- Interpretation `{item['interpretation_id']}`: {item['text']} (based on: {', '.join(item['based_on_claim_ids']) or 'none'})")
    if not bundle.get("interpretations"):
        lines.append("- None recorded.")
    lines.extend(["", "## Uncertainties", *[f"- {item}" for item in bundle.get("uncertainties") or ["None recorded."]]])
    return "\n".join(lines)


def _render_prd(bundles: list[dict[str, Any]], registry_path: Path) -> str:
    ids = ", ".join(bundle["id"] for bundle in bundles)
    urls = "\n".join(f"- {bundle['id']}: {bundle['source_url']}" for bundle in bundles)
    requirements = [f"- [{claim['claim_id']}] {claim['text']} (bundle: `{bundle['id']}`)" for bundle in bundles for claim in verified_claims(bundle)]
    held = [
        f"- [{claim['claim_id']}] {claim['text']} — `{(claim.get('evidence_gate') or {}).get('status')}`"
        for bundle in bundles
        for claim in bundle.get("claims") or []
        if claim not in verified_claims(bundle)
    ]
    non_goals = [f"- {item} (bundle: `{bundle['id']}`)" for bundle in bundles for item in bundle.get("non_goals") or []]
    return "\n".join(
        [
            "---",
            f"bundle_ids: {_yaml_scalar(ids)}",
            f"source_bundle_registry: {_yaml_scalar(str(registry_path.resolve()))}",
            "---",
            "",
            "# Source-derived PRD",
            "",
            "## Source lineage",
            "",
            urls,
            "",
            "## Source-derived requirements",
            "",
            *(requirements or ["- No verified requirements; promotion is blocked."]),
            "",
            "## Held evidence (not requirements)",
            "",
            *(held or ["- None."]),
            "",
            "## Non-goals",
            "",
            *(non_goals or ["- No source-declared non-goals were supplied."]),
        ]
    )


def _render_insight(bundles: list[dict[str, Any]], registry_path: Path) -> str:
    lines = ["---", f"bundle_ids: {_yaml_scalar(', '.join(bundle['id'] for bundle in bundles))}", f"source_bundle_registry: {_yaml_scalar(str(registry_path.resolve()))}", "---", "", "# SourceBundle insight synthesis", "", "## Source lineage", "", *[f"- {bundle['id']}: {bundle['source_url']}" for bundle in bundles], "", "## Evidence matrix", ""]
    for bundle in bundles:
        for claim in bundle.get("claims") or []:
            gate = claim.get("evidence_gate") or {}
            lines.append(f"- `{bundle['id']}` [{claim['claim_id']}] `{gate.get('status')}` sources={gate.get('source_count')}: {claim['text']}")
    lines.extend(["", "## Cross-source observations", ""])
    interpretations = [f"- `{bundle['id']}` interpretation `{item['interpretation_id']}`: {item['text']}" for bundle in bundles for item in bundle.get("interpretations") or []]
    lines.extend(interpretations or ["- No interpretations were supplied; no synthetic thesis was invented."])
    lines.extend(["", "## Coverage and uncertainty", ""])
    for bundle in bundles:
        lines.append(f"- `{bundle['id']}` access=`{bundle['access_status']}`, coverage_warning={str((bundle.get('quality') or {}).get('coverage_warning')).lower()}")
        lines.extend(f"  - {item}" for item in bundle.get("uncertainties") or [])
    return "\n".join(lines)


def _briefing_payload(bundles: list[dict[str, Any]], registry_path: Path) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "source_bundle_registry": str(registry_path.resolve()),
        "bundle_ids": [bundle["id"] for bundle in bundles],
        "sources": [{"bundle_id": bundle["id"], "source_url": bundle["source_url"]} for bundle in bundles],
        "coverage_warning": any((bundle.get("quality") or {}).get("coverage_warning") for bundle in bundles),
        "items": [
            {
                "bundle_id": bundle["id"],
                "source_url": bundle["source_url"],
                "claim_id": claim["claim_id"],
                "cluster_key": claim["cluster_key"],
                "polarity": claim["polarity"],
                "text": claim["text"],
                "evidence_state": (claim.get("evidence_gate") or {}).get("status"),
                "decision": (claim.get("evidence_gate") or {}).get("decision"),
                "core_eligible": (claim.get("evidence_gate") or {}).get("status") == "verified",
            }
            for bundle in bundles
            for claim in bundle.get("claims") or []
        ],
    }


def _render_script_yaml(bundles: list[dict[str, Any]], registry_path: Path) -> str:
    claims = [(bundle, claim) for bundle in bundles for claim in verified_claims(bundle)]
    lines = ["schema_version: 1", f"source_bundle_registry: {_yaml_scalar(str(registry_path.resolve()))}", "sources:", *[f"  - bundle_id: {_yaml_scalar(bundle['id'])}\n    source_url: {_yaml_scalar(bundle['source_url'])}" for bundle in bundles], "scenes:"]
    if not claims:
        lines.append("  []")
    for index, (bundle, claim) in enumerate(claims, 1):
        lines.extend(
            [
                f"  - scene: {index}",
                f"    bundle_id: {_yaml_scalar(bundle['id'])}",
                f"    source_url: {_yaml_scalar(bundle['source_url'])}",
                f"    claim_id: {_yaml_scalar(claim['claim_id'])}",
                f"    narration: {_yaml_scalar(claim['text'])}",
            ]
        )
    return "\n".join(lines)


def _video_payload(bundles: list[dict[str, Any]], registry_path: Path) -> dict[str, Any]:
    scenes = [
        {"bundle_id": bundle["id"], "source_url": bundle["source_url"], "claim_id": claim["claim_id"], "text": claim["text"]}
        for bundle in bundles
        for claim in verified_claims(bundle)
    ]
    return {
        "schema_version": 1,
        "artifact_type": "source-backed-video-manifest",
        "source_bundle_registry": str(registry_path.resolve()),
        "bundle_ids": [bundle["id"] for bundle in bundles],
        "sources": [{"bundle_id": bundle["id"], "source_url": bundle["source_url"]} for bundle in bundles],
        "status": "ready" if scenes else "blocked_no_verified_claims",
        "scenes": scenes,
    }


def _render_content(bundles: list[dict[str, Any]], registry_path: Path) -> str:
    lines = ["---", f"bundle_ids: {_yaml_scalar(', '.join(bundle['id'] for bundle in bundles))}", f"source_bundle_registry: {_yaml_scalar(str(registry_path.resolve()))}", "---", "", "# Source-backed content", "", "## Source lineage", "", *[f"- {bundle['id']}: {bundle['source_url']}" for bundle in bundles], "", "## Verified claims", ""]
    claims = [(bundle, claim) for bundle in bundles for claim in verified_claims(bundle)]
    lines.extend(f"- [{claim['claim_id']}] {claim['text']} (source: {bundle['source_url']})" for bundle, claim in claims)
    if not claims:
        lines.append("- No verified claims are available; publication is blocked.")
    return "\n".join(lines)


def _fact_tokens(text: str) -> dict[str, set[str]]:
    numbers = set(re.findall(r"(?<!\w)[+-]?(?:\d[\d,]*(?:\.\d+)?%?|\d{4}-\d{1,2}-\d{1,2})(?!\w)", text))
    urls = set(re.findall(r"https?://[^\s)>]+", text))
    proper = set(re.findall(r"\b(?:[A-Z]{2,}|[A-Z][A-Za-z0-9_-]*[A-Z][A-Za-z0-9_-]*)\b", text))
    claim_refs = set(CLAIM_REF_RE.findall(text))
    return {"numbers": numbers, "urls": urls, "proper_terms": proper, "claim_refs": claim_refs}


def _assertion_lines(text: str) -> set[str]:
    assertions: set[str] = set()
    in_frontmatter = False
    for index, raw in enumerate(text.splitlines()):
        stripped = raw.strip()
        if stripped == "---" and (index == 0 or in_frontmatter):
            in_frontmatter = not in_frontmatter
            continue
        if in_frontmatter or not stripped or stripped.startswith("#"):
            continue
        normalized = re.sub(r"^[-*+]\s+", "", stripped)
        assertions.add(re.sub(r"\s+", " ", normalized).casefold())
    return assertions


def _significant_lexical_tokens(text: str) -> set[str]:
    stop = {
        "the", "and", "for", "from", "with", "this", "that", "source", "bundle", "registry",
        "content", "claim", "claims", "verified", "http", "https", "com", "none", "available",
        "그리고", "에서", "으로", "대한", "있는", "없는", "source_bundle_registry", "bundle_ids",
    }
    return {
        token
        for token in re.findall(r"[A-Za-z가-힣][A-Za-z0-9가-힣_-]{2,}", text.casefold())
        if token not in stop
    }


def fidelity_check(original: str, rewritten: str, *, protected_terms: Iterable[str] = ()) -> dict[str, Any]:
    before = _fact_tokens(original)
    after = _fact_tokens(rewritten)
    missing = {key: sorted(values - after[key]) for key, values in before.items() if values - after[key]}
    added = {key: sorted(values - before[key]) for key, values in after.items() if values - before[key]}
    added_assertions = sorted(_assertion_lines(rewritten) - _assertion_lines(original))
    added_lexical = sorted(_significant_lexical_tokens(rewritten) - _significant_lexical_tokens(original))
    missing_protected = [term for term in protected_terms if term and term in original and term not in rewritten]
    negative = re.compile(r"\b(?:not|no|never|without)\b|없|아니|불가|금지", re.IGNORECASE)
    causal = re.compile(r"\b(?:because|therefore|causes?|leads? to|due to)\b|때문|따라서|인해|원인", re.IGNORECASE)
    polarity_changed = bool(negative.search(original)) != bool(negative.search(rewritten))
    causality_changed = bool(causal.search(original)) != bool(causal.search(rewritten))
    return {
        "ok": not missing and not added and not added_assertions and not added_lexical and not missing_protected and not polarity_changed and not causality_changed,
        "missing_facts": missing,
        "added_facts": added,
        "unsupported_assertions": added_assertions,
        "added_lexical_tokens": added_lexical,
        "missing_protected_terms": missing_protected,
        "polarity_changed": polarity_changed,
        "causality_changed": causality_changed,
    }


def ssot_check(bundles: list[dict[str, Any]], artifacts: dict[str, str], registry_path: Path) -> dict[str, Any]:
    issues: list[dict[str, str]] = []
    canonical = str(registry_path.resolve())
    for path, text in artifacts.items():
        if canonical not in text:
            issues.append({"path": path, "issue": "missing canonical registry reference"})
        for bundle in bundles:
            if bundle["id"] in text and bundle["source_url"] not in text:
                issues.append({"path": path, "issue": f"bundle {bundle['id']} is missing canonical source_url"})
    return {"ok": not issues, "canonical_source": canonical, "issues": issues}


def contextless_review(bundles: list[dict[str, Any]], artifacts: dict[str, str]) -> dict[str, Any]:
    owners: dict[str, list[str]] = {}
    for bundle in bundles:
        for claim in bundle.get("claims") or []:
            owners.setdefault(str(claim["claim_id"]), []).append(str(bundle["id"]))
    known = {claim["claim_id"] for bundle in bundles for claim in bundle.get("claims") or []}
    verified = {claim["claim_id"] for bundle in bundles for claim in verified_claims(bundle)}
    issues: list[dict[str, str]] = [
        {"path": "<bundle-set>", "issue": f"ambiguous cross-bundle claim reference: {claim_id} ({', '.join(bundle_ids)})"}
        for claim_id, bundle_ids in sorted(owners.items())
        if len(bundle_ids) > 1
    ]
    for path, text in artifacts.items():
        refs = set(CLAIM_REF_RE.findall(text))
        refs.update(re.findall(r'["\']claim_id["\']\s*:\s*["\']([A-Za-z0-9_.:-]+)["\']', text))
        refs.update(re.findall(r'^\s*claim_id:\s*["\']?([A-Za-z0-9_.:-]+)', text, re.MULTILINE))
        for unknown in sorted(refs - known):
            issues.append({"path": path, "issue": f"unknown claim reference: {unknown}"})
        if path.endswith(("script.yaml", "video.json", "content.md")):
            for held in sorted(refs - verified):
                issues.append({"path": path, "issue": f"non-verified claim used in publishable artifact: {held}"})
    return {"ok": not issues, "issues": issues}


def materialize_artifacts(
    registry_path: Path,
    bundle_ids: list[str],
    output_dir: Path,
    *,
    humanized_content: str | None = None,
) -> dict[str, Any]:
    if not bundle_ids:
        raise ValueError("at least one bundle id is required")
    if len(bundle_ids) != len(set(bundle_ids)):
        raise ValueError("bundle ids must be unique when materializing artifacts")
    capability_lane = _require_source_write(output_dir)
    with _registry_lock(registry_path):
        registry = load_registry(registry_path)
        stored_by_id = {str(bundle.get("id")): bundle for bundle in registry["bundles"]}
        bundles: list[dict[str, Any]] = []
        for bundle_id in bundle_ids:
            if bundle_id not in stored_by_id:
                raise KeyError(f"unknown bundle: {bundle_id}")
            bundles.append(copy.deepcopy(stored_by_id[bundle_id]))
        claim_owners: dict[str, list[str]] = {}
        for bundle in bundles:
            for claim in bundle.get("claims") or []:
                claim_owners.setdefault(str(claim["claim_id"]), []).append(str(bundle["id"]))
        collisions = {claim_id: owners for claim_id, owners in claim_owners.items() if len(owners) > 1}
        if collisions:
            raise ValueError(f"claim_id values must be unique across a materialized bundle set: {collisions}")
        semantic_conflicts = apply_semantic_conflict_gate(bundles)
        prefix = bundles[0]["id"] if len(bundles) == 1 else "source-bundle"
        rendered: dict[Path, tuple[str, str]] = {}
        for bundle in bundles:
            rendered[output_dir / f"{bundle['id']}-obsidian.md"] = ("obsidian_capture", _render_capture(bundle, registry_path))
        rendered.update(
            {
                output_dir / f"{prefix}-insight-synthesis.md": ("insight_synthesis", _render_insight(bundles, registry_path)),
                output_dir / f"{prefix}-prd.md": ("prd", _render_prd(bundles, registry_path)),
                output_dir / f"{prefix}-briefing.json": ("briefing_item", json.dumps(_briefing_payload(bundles, registry_path), ensure_ascii=False, indent=2)),
                output_dir / f"{prefix}-script.yaml": ("script_yaml", _render_script_yaml(bundles, registry_path)),
                output_dir / f"{prefix}-video.json": ("video_manifest", json.dumps(_video_payload(bundles, registry_path), ensure_ascii=False, indent=2)),
            }
        )
        original_content = _render_content(bundles, registry_path)
        fidelity = fidelity_check(
            original_content,
            humanized_content if humanized_content is not None else original_content,
            protected_terms=[term for bundle in bundles for term in bundle.get("protected_terms") or []],
        )
        if not fidelity["ok"]:
            raise ValueError(f"humanized content failed fidelity check: {fidelity}")
        rendered[output_dir / f"{prefix}-content.md"] = ("content", humanized_content or original_content)
        text_map = {str(path): text for path, (_, text) in rendered.items()}
        ssot = ssot_check(bundles, text_map, registry_path)
        contextless = contextless_review(bundles, text_map)
        if not ssot["ok"] or not contextless["ok"]:
            raise ValueError(f"artifact preflight failed: ssot={ssot} contextless={contextless}")
        snapshots = _snapshot_regular_files([*rendered, registry_path])
        try:
            for path, (_, text) in rendered.items():
                _write_text_atomic(path, text)
            sip = completion_sip(rendered)
            if not sip["ok"]:
                raise RuntimeError(f"artifact readback failed: {sip}")
            readback_by_path = {str(Path(item["path"])): item for item in sip["artifacts"]}
            created_at = utc_now_iso()
            expected_records: list[tuple[str, str, str]] = []
            for path, (artifact_type, _) in rendered.items():
                evidence = readback_by_path[str(path)]
                record = {
                    "type": artifact_type,
                    "path": str(path.resolve()),
                    "created_at": created_at,
                    "bytes": evidence["bytes"],
                    "sha256": evidence["sha256"],
                }
                for bundle in bundles:
                    if bundle["id"] not in text_map[str(path)]:
                        continue
                    stored = stored_by_id[bundle["id"]]
                    existing = stored.setdefault("derived_artifacts", [])
                    if not any(item.get("path") == record["path"] and item.get("sha256") == record["sha256"] for item in existing):
                        existing.append(dict(record))
                    stored["updated_at"] = created_at
                    expected_records.append((bundle["id"], record["path"], str(record["sha256"])))
            _write_json_atomic(registry_path, registry)
            registry_readback = load_registry(registry_path)
            readback_by_id = {str(bundle.get("id")): bundle for bundle in registry_readback["bundles"]}
            if not all(
                any(item.get("path") == path and item.get("sha256") == digest for item in readback_by_id[bundle_id].get("derived_artifacts") or [])
                for bundle_id, path, digest in expected_records
            ):
                raise RuntimeError("derived artifact registry readback failed")
        except Exception as exc:
            rollback_errors = _restore_file_snapshots(snapshots)
            if rollback_errors:
                raise RuntimeError(f"materialize failed and rollback was incomplete: {rollback_errors}") from exc
            raise
    return {
        "ok": True,
        "bundle_ids": bundle_ids,
        "artifacts": [{"type": rendered[Path(item["path"])][0], **item} for item in sip["artifacts"]],
        "completion_sip": sip,
        "ssot_check": ssot,
        "contextless_review": contextless,
        "fidelity_check": fidelity,
        "semantic_conflicts": semantic_conflicts,
        "capability_lane": capability_lane,
        "transactional_write": True,
        "registry_readback": True,
    }


def evaluate_risk_lane(
    user_message: str,
    capability: str,
    *,
    target: str | None,
    config_path: Path,
    explicit_approval: bool = False,
) -> dict[str, Any]:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    lane = (config.get("capabilities") or {}).get(capability)
    if not isinstance(lane, dict):
        return {"allowed": False, "capability": capability, "reason": "unknown_capability"}
    if not lane.get("enabled", False):
        return {"allowed": False, "capability": capability, "reason": "capability_disabled"}
    try:
        from action_scope_gate import ActionScopeKind, attempted_action_allowed, evaluate
    except ImportError:
        try:
            from scripts.action_scope_gate import ActionScopeKind, attempted_action_allowed, evaluate
        except ImportError:
            from scripts.action_scope import ActionScopeKind, attempted_action_allowed, evaluate
    scope = ActionScopeKind(str(lane["action_scope"]))
    decision = evaluate(user_message, explicit_targets=[target] if target else None)
    allowed, reason = attempted_action_allowed(decision, scope)
    if allowed and lane.get("explicit_approval_required") and not explicit_approval:
        allowed, reason = False, "explicit_approval_required"
    return {
        "allowed": allowed,
        "capability": capability,
        "reason": reason,
        "execution_profile": lane.get("execution_profile"),
        "action_scope": scope.value,
        "scope_decision": decision.as_dict(),
    }


def _default_registry() -> Path:
    root = Path.cwd()
    state = ".helm" if (root / "helm.py").exists() else ".openclaw"
    return root / state / "source-bundles.json"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="SourceBundle registry and quality harness")
    parser.add_argument("--registry", default=str(_default_registry()))
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create")
    create.add_argument("--input", required=True, help="JSON SourceBundle input")
    show = sub.add_parser("show")
    group = show.add_mutually_exclusive_group(required=True)
    group.add_argument("--id")
    group.add_argument("--url")
    derive = sub.add_parser("derive")
    derive.add_argument("--id", action="append", required=True)
    derive.add_argument("--output-dir", required=True)
    derive.add_argument("--humanized-content")
    gate = sub.add_parser("gate")
    gate.add_argument("--input", required=True, help="JSON claim cluster input")
    lane = sub.add_parser("lane")
    lane.add_argument("--message", required=True)
    lane.add_argument("--capability", required=True)
    lane.add_argument("--target")
    lane.add_argument("--config", required=True)
    lane.add_argument("--approved", action="store_true")
    annotate = sub.add_parser("annotate-note")
    annotate.add_argument("--id", required=True)
    annotate.add_argument("--note", required=True)
    retro = sub.add_parser("retro")
    retro.add_argument("--candidate-log", required=True)
    retro.add_argument("--type", choices=["memory", "skill"], default="memory")
    retro.add_argument("--problem", required=True)
    retro.add_argument("--evidence", required=True)
    retro.add_argument("--bundle-id", action="append", default=[])
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    registry_path = Path(args.registry).expanduser()
    try:
        if args.command == "create":
            payload = upsert_bundle(registry_path, json.loads(Path(args.input).read_text(encoding="utf-8")))
        elif args.command == "show":
            bundle = find_bundle(registry_path, bundle_id=args.id, source_url=args.url)
            payload = {"found": bundle is not None, "bundle": bundle}
        elif args.command == "derive":
            humanized = Path(args.humanized_content).read_text(encoding="utf-8") if args.humanized_content else None
            payload = materialize_artifacts(registry_path, args.id, Path(args.output_dir).expanduser(), humanized_content=humanized)
        elif args.command == "gate":
            raw = json.loads(Path(args.input).read_text(encoding="utf-8"))
            payload = evaluate_claim_cluster(
                str(raw["claim_id"]),
                raw.get("evidence") or [],
                official_expected=bool(raw.get("official_expected", False)),
                freshness_required=bool(raw.get("freshness_required", False)),
                max_age_days=int(raw.get("max_age_days") or 45),
            )
        elif args.command == "lane":
            payload = evaluate_risk_lane(
                args.message,
                args.capability,
                target=args.target,
                config_path=Path(args.config),
                explicit_approval=args.approved,
            )
        elif args.command == "retro":
            payload = retro_note(
                Path(args.candidate_log).expanduser(),
                problem=args.problem,
                evidence=args.evidence,
                candidate_type=args.type,
                bundle_ids=args.bundle_id,
            )
        else:
            bundle = find_bundle(registry_path, bundle_id=args.id)
            if bundle is None:
                raise KeyError(f"unknown bundle: {args.id}")
            note = Path(args.note).expanduser()
            readback = annotate_note_lineage(note, bundle, registry_path)
            artifact = register_derived_artifact(registry_path, bundle["id"], "obsidian_capture", note)
            payload = {"ok": True, "readback": readback, "artifact": artifact}
    except (OSError, KeyError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 2
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if payload.get("allowed", True) is not False else 3


if __name__ == "__main__":
    raise SystemExit(main())
