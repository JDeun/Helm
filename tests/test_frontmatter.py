# tests/test_frontmatter.py
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from helm_frontmatter import (
    ALLOWED_PROVENANCE_AGENT,
    ALLOWED_PROVENANCE_KIND,
    VAULT_FOLDER_STATE,
    VAULT_LAYOUT,
    BodyPrecedence,
    Frontmatter,
    FrontmatterValidationError,
    Provenance,
    TimeRange,
    apply_agent_redraft,
    select_body,
    validate_frontmatter,
    validate_vault_layout,
)
from helm_state_model import State


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _valid_fm(**overrides) -> Frontmatter:
    base = Frontmatter(
        source_id="gmail-primary",
        time_range=TimeRange(start="2026-05-20T00:00:00+09:00", end="2026-05-21T00:00:00+09:00"),
        scope="ops/low",
        provenance=Provenance(
            kind="auto_fetch",
            agent="helm",
            fetched_at="2026-05-21T09:00:00+09:00",
            url=None,
        ),
        state=State.CAPTURED,
        topics=("helm",),
        confidence=0.8,
        last_user_edit=None,
    )
    if overrides:
        return Frontmatter(
            source_id=overrides.get("source_id", base.source_id),
            time_range=overrides.get("time_range", base.time_range),
            scope=overrides.get("scope", base.scope),
            provenance=overrides.get("provenance", base.provenance),
            state=overrides.get("state", base.state),
            topics=overrides.get("topics", base.topics),
            confidence=overrides.get("confidence", base.confidence),
            last_user_edit=overrides.get("last_user_edit", base.last_user_edit),
            agent_redraft=overrides.get("agent_redraft", base.agent_redraft),
            promoted_at=overrides.get("promoted_at", base.promoted_at),
        )
    return base


# ---------------------------------------------------------------------------
# Vault layout (§2.1)
# ---------------------------------------------------------------------------


def test_vault_layout_constants() -> None:
    assert VAULT_LAYOUT == (
        "00-Inbox",
        "10-Topics",
        "20-Sources",
        "30-Decisions",
        "40-Audit",
        "90-Rejected",
    )


def test_folder_state_mapping_covers_all_states() -> None:
    """Every Helm state must map to at least one folder."""

    covered: set[State] = set()
    for states in VAULT_FOLDER_STATE.values():
        covered.update(states)
    assert covered == {
        State.CAPTURED,
        State.REVIEWED,
        State.APPLIED,
        State.PROMOTED,
        State.REJECTED,
    }


def test_validate_vault_layout_detects_missing(tmp_path: Path) -> None:
    (tmp_path / "00-Inbox").mkdir()
    (tmp_path / "10-Topics").mkdir()
    report = validate_vault_layout(tmp_path)
    assert "20-Sources" in report["missing"]
    assert "30-Decisions" in report["missing"]
    assert report["extra"] == []


def test_validate_vault_layout_detects_extra(tmp_path: Path) -> None:
    for folder in VAULT_LAYOUT:
        (tmp_path / folder).mkdir()
    (tmp_path / "01-Daily").mkdir()
    report = validate_vault_layout(tmp_path)
    assert report["missing"] == []
    assert "01-Daily" in report["extra"]


def test_validate_vault_layout_ignores_hidden(tmp_path: Path) -> None:
    for folder in VAULT_LAYOUT:
        (tmp_path / folder).mkdir()
    (tmp_path / ".obsidian").mkdir()
    report = validate_vault_layout(tmp_path)
    assert report["missing"] == []
    assert report["extra"] == []


def test_validate_vault_layout_missing_root(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        validate_vault_layout(tmp_path / "does-not-exist")


def test_suggested_folder_by_state() -> None:
    captured = _valid_fm(state=State.CAPTURED)
    assert captured.suggested_folder() == "00-Inbox"

    rejected = _valid_fm(state=State.REJECTED)
    assert rejected.suggested_folder() == "90-Rejected"

    promoted = _valid_fm(
        state=State.PROMOTED,
        confidence=0.96,
        promoted_at="2026-05-21T10:00:00+09:00",
    )
    assert promoted.suggested_folder() == "30-Decisions"


# ---------------------------------------------------------------------------
# Frontmatter validation (§2.2)
# ---------------------------------------------------------------------------


def test_valid_frontmatter_passes() -> None:
    validate_frontmatter(_valid_fm())


def test_missing_source_id_rejected() -> None:
    with pytest.raises(FrontmatterValidationError):
        validate_frontmatter(_valid_fm(source_id="  "))


def test_scope_must_have_slash() -> None:
    with pytest.raises(FrontmatterValidationError):
        validate_frontmatter(_valid_fm(scope="household"))


def test_time_range_must_be_iso8601() -> None:
    with pytest.raises(FrontmatterValidationError):
        validate_frontmatter(
            _valid_fm(time_range=TimeRange(start="yesterday", end="today"))
        )


def test_time_range_start_after_end_rejected() -> None:
    with pytest.raises(FrontmatterValidationError):
        validate_frontmatter(
            _valid_fm(
                time_range=TimeRange(
                    start="2026-05-22T00:00:00+09:00",
                    end="2026-05-20T00:00:00+09:00",
                )
            )
        )


def test_provenance_kind_must_be_allowed() -> None:
    bad = Provenance(
        kind="random_source",
        agent="helm",
        fetched_at="2026-05-21T09:00:00+09:00",
    )
    with pytest.raises(FrontmatterValidationError):
        validate_frontmatter(_valid_fm(provenance=bad))


def test_provenance_agent_must_be_allowed() -> None:
    bad = Provenance(
        kind="auto_fetch",
        agent="stranger",
        fetched_at="2026-05-21T09:00:00+09:00",
    )
    with pytest.raises(FrontmatterValidationError):
        validate_frontmatter(_valid_fm(provenance=bad))


def test_provenance_agent_allowed_values() -> None:
    assert ALLOWED_PROVENANCE_AGENT == {"openclaw", "helm", "user"}
    assert "telegram_intake" in ALLOWED_PROVENANCE_KIND


def test_confidence_must_be_in_unit_interval() -> None:
    with pytest.raises(FrontmatterValidationError):
        validate_frontmatter(_valid_fm(confidence=1.5))
    with pytest.raises(FrontmatterValidationError):
        validate_frontmatter(_valid_fm(confidence=-0.1))


def test_promoted_state_requires_promoted_at_and_confidence() -> None:
    # missing promoted_at
    with pytest.raises(FrontmatterValidationError):
        validate_frontmatter(_valid_fm(state=State.PROMOTED, confidence=0.96))
    # missing confidence
    with pytest.raises(FrontmatterValidationError):
        validate_frontmatter(
            _valid_fm(
                state=State.PROMOTED,
                confidence=None,
                promoted_at="2026-05-21T10:00:00+09:00",
            )
        )
    # both present: pass
    validate_frontmatter(
        _valid_fm(
            state=State.PROMOTED,
            confidence=0.96,
            promoted_at="2026-05-21T10:00:00+09:00",
        )
    )


def test_topic_must_be_non_empty() -> None:
    with pytest.raises(FrontmatterValidationError):
        validate_frontmatter(_valid_fm(topics=("helm", "  ")))


def test_to_dict_and_from_dict_round_trip() -> None:
    fm = _valid_fm()
    again = Frontmatter.from_dict(fm.to_dict())
    assert again == fm


def test_from_dict_tolerates_single_topic_string() -> None:
    payload = _valid_fm().to_dict()
    payload["topics"] = "helm"
    fm = Frontmatter.from_dict(payload)
    assert fm.topics == ("helm",)


def test_from_dict_rejects_unknown_state() -> None:
    payload = _valid_fm().to_dict()
    payload["state"] = "abandoned"
    with pytest.raises(FrontmatterValidationError):
        Frontmatter.from_dict(payload)


# ---------------------------------------------------------------------------
# Body precedence (§2.3)
# ---------------------------------------------------------------------------


def test_select_body_user_wins() -> None:
    result = select_body(
        user_edit="user body",
        agent_summary="agent body",
        raw_chunk="raw body",
    )
    assert result == BodyPrecedence(body="user body", source="user_edit")


def test_select_body_falls_back_to_agent() -> None:
    result = select_body(
        user_edit=None,
        agent_summary="agent body",
        raw_chunk="raw body",
    )
    assert result.source == "agent_summary"


def test_select_body_falls_back_to_raw() -> None:
    result = select_body(user_edit=None, agent_summary=None, raw_chunk="raw body")
    assert result.source == "raw_chunk"


def test_select_body_whitespace_treated_as_empty() -> None:
    result = select_body(user_edit="   ", agent_summary="agent body", raw_chunk=None)
    assert result.source == "agent_summary"


def test_select_body_all_missing_raises() -> None:
    with pytest.raises(ValueError):
        select_body(user_edit=None, agent_summary=None, raw_chunk=None)


# ---------------------------------------------------------------------------
# Non-destructive redraft (§2.5)
# ---------------------------------------------------------------------------


def test_apply_agent_redraft_with_user_body_forces_reviewed() -> None:
    fm = _valid_fm(state=State.APPLIED)
    out = apply_agent_redraft(fm, redraft="proposed body", user_body_present=True)
    assert out.agent_redraft == "proposed body"
    assert out.state is State.REVIEWED
    # original is untouched (dataclass is frozen)
    assert fm.agent_redraft is None
    assert fm.state is State.APPLIED


def test_apply_agent_redraft_without_user_body_keeps_state() -> None:
    fm = _valid_fm(state=State.CAPTURED)
    out = apply_agent_redraft(fm, redraft="proposed body", user_body_present=False)
    assert out.agent_redraft == "proposed body"
    assert out.state is State.CAPTURED


def test_apply_agent_redraft_empty_rejected() -> None:
    fm = _valid_fm()
    with pytest.raises(ValueError):
        apply_agent_redraft(fm, redraft="   ", user_body_present=True)


def test_redraft_does_not_change_other_fields() -> None:
    fm = _valid_fm(state=State.APPLIED)
    out = apply_agent_redraft(fm, redraft="X", user_body_present=True)
    assert out.source_id == fm.source_id
    assert out.scope == fm.scope
    assert out.provenance == fm.provenance
    assert out.time_range == fm.time_range
    assert out.confidence == fm.confidence
