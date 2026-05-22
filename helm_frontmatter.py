"""Obsidian Frontmatter standard for Helm-managed notes.

Implementation of design document §2 (Obsidian as Editable Memory Surface).

This module exposes:

* :class:`Provenance`, :class:`TimeRange`, :class:`Frontmatter` dataclasses
  matching the 10-field strict schema from §2.2.
* :func:`validate_frontmatter` — strict gate enforcement.
* :data:`VAULT_LAYOUT` and :func:`validate_vault_layout` — verify the six-folder
  layout from §2.1 (00-Inbox / 10-Topics / 20-Sources / 30-Decisions / 40-Audit /
  90-Rejected).
* :func:`select_body` — precedence resolver
  (``user_edit > agent_summary > raw_chunk``).
* :func:`apply_agent_redraft` — non-destructive redraft helper.  The user body
  is *never* overwritten; redrafts are stored in the ``agent_redraft``
  frontmatter field with state forced to ``reviewed``.

This module does **not** read or write any file in Kevin's existing
``~/Documents/ObsidianVault``.  It only validates payloads and proposes new
content.  Filesystem mutation is the responsibility of the caller.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

from helm_state_model import State

__all__ = [
    "VAULT_LAYOUT",
    "VAULT_FOLDER_STATE",
    "ALLOWED_PROVENANCE_KIND",
    "ALLOWED_PROVENANCE_AGENT",
    "TimeRange",
    "Provenance",
    "Frontmatter",
    "FrontmatterValidationError",
    "validate_frontmatter",
    "validate_vault_layout",
    "select_body",
    "BodyPrecedence",
    "apply_agent_redraft",
]


# ---------------------------------------------------------------------------
# Vault layout (§2.1)
# ---------------------------------------------------------------------------


VAULT_LAYOUT: tuple[str, ...] = (
    "00-Inbox",
    "10-Topics",
    "20-Sources",
    "30-Decisions",
    "40-Audit",
    "90-Rejected",
)


# Mapping of folder name -> the canonical state(s) of notes stored there.
# Used both for layout validation and to suggest a target folder for a given
# note state.
VAULT_FOLDER_STATE: dict[str, frozenset[State]] = {
    "00-Inbox": frozenset({State.CAPTURED}),
    "10-Topics": frozenset({State.REVIEWED}),
    "20-Sources": frozenset({State.REVIEWED}),
    "30-Decisions": frozenset({State.APPLIED, State.PROMOTED}),
    "40-Audit": frozenset({State.APPLIED, State.PROMOTED}),
    "90-Rejected": frozenset({State.REJECTED}),
}


def validate_vault_layout(vault_root: Path) -> dict[str, list[str]]:
    """Verify that ``vault_root`` contains the six standard folders.

    Returns a dict with two keys:

    * ``"missing"`` — folders required by design §2.1 but absent.
    * ``"extra"`` — top-level folders found in the vault that are not in the
      design (informational only; existing vaults usually have legacy folders
      like ``01-Daily``).

    Does not modify the filesystem.  The function reads only metadata.
    """

    if not vault_root.exists() or not vault_root.is_dir():
        raise FileNotFoundError(f"vault root does not exist or is not a dir: {vault_root}")

    present = {p.name for p in vault_root.iterdir() if p.is_dir() and not p.name.startswith(".")}
    expected = set(VAULT_LAYOUT)
    missing = sorted(expected - present)
    extra = sorted(present - expected)
    return {"missing": missing, "extra": extra}


# ---------------------------------------------------------------------------
# Frontmatter dataclasses (§2.2)
# ---------------------------------------------------------------------------


ALLOWED_PROVENANCE_KIND: frozenset[str] = frozenset(
    {
        "auto_fetch",
        "web_fetch",
        "user_paste",
        "telegram_intake",
        "tool_output",
    }
)

ALLOWED_PROVENANCE_AGENT: frozenset[str] = frozenset(
    {
        "openclaw",
        "helm",
        "user",
    }
)


@dataclass(frozen=True)
class TimeRange:
    """ISO8601 string pair.

    We deliberately keep the values as strings to round-trip through YAML
    frontmatter without timezone surprises.  Validation parses them with
    :func:`datetime.fromisoformat` to confirm well-formedness.
    """

    start: str
    end: str


@dataclass(frozen=True)
class Provenance:
    kind: str
    agent: str
    fetched_at: str
    url: str | None = None


@dataclass(frozen=True)
class Frontmatter:
    """The 10-field strict schema for an Obsidian Helm note.

    Fields are taken from design §2.2:

    1. ``source_id``
    2. ``time_range`` (with ``.start`` and ``.end``)
    3. ``scope`` (shared keyspace with action-scope gate)
    4. ``provenance`` (kind / agent / fetched_at / url)
    5. ``state``
    6. ``topics``
    7. ``confidence``
    8. ``last_user_edit``

    Plus two implementation-required fields for non-destructive user edits:

    9. ``agent_redraft`` — alternate body proposed by the agent when the
       user-edited body must be preserved (§2.5).
    10. ``promoted_at`` — set only when the note reaches ``promoted``; used by
        the retention tier classifier.
    """

    source_id: str
    time_range: TimeRange
    scope: str
    provenance: Provenance
    state: State
    topics: tuple[str, ...] = ()
    confidence: float | None = None
    last_user_edit: str | None = None
    agent_redraft: str | None = None
    promoted_at: str | None = None

    # ---- (de)serialization --------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "time_range": {"start": self.time_range.start, "end": self.time_range.end},
            "scope": self.scope,
            "provenance": {
                "kind": self.provenance.kind,
                "agent": self.provenance.agent,
                "fetched_at": self.provenance.fetched_at,
                **({"url": self.provenance.url} if self.provenance.url else {}),
            },
            "state": self.state.value,
            "topics": list(self.topics),
            "confidence": self.confidence,
            "last_user_edit": self.last_user_edit,
            "agent_redraft": self.agent_redraft,
            "promoted_at": self.promoted_at,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "Frontmatter":
        tr_raw = payload.get("time_range") or {}
        if not isinstance(tr_raw, Mapping):
            raise FrontmatterValidationError("time_range must be a mapping with start/end")
        prov_raw = payload.get("provenance") or {}
        if not isinstance(prov_raw, Mapping):
            raise FrontmatterValidationError("provenance must be a mapping")
        topics_raw = payload.get("topics") or []
        if isinstance(topics_raw, str):
            # Tolerate single-string topics for YAML inline forms.
            topics_raw = [topics_raw]
        if not isinstance(topics_raw, Iterable):
            raise FrontmatterValidationError("topics must be a list")
        try:
            state = State.parse(payload.get("state"))
        except (ValueError, TypeError) as exc:
            raise FrontmatterValidationError(str(exc)) from exc
        try:
            time_range = TimeRange(start=str(tr_raw["start"]), end=str(tr_raw["end"]))
        except KeyError as exc:
            raise FrontmatterValidationError(
                "time_range missing 'start' or 'end'"
            ) from exc
        provenance = Provenance(
            kind=str(prov_raw.get("kind", "")),
            agent=str(prov_raw.get("agent", "")),
            fetched_at=str(prov_raw.get("fetched_at", "")),
            url=(str(prov_raw["url"]) if prov_raw.get("url") else None),
        )
        confidence = payload.get("confidence")
        if confidence is not None:
            confidence = float(confidence)
        return cls(
            source_id=str(payload.get("source_id", "")),
            time_range=time_range,
            scope=str(payload.get("scope", "")),
            provenance=provenance,
            state=state,
            topics=tuple(str(t) for t in topics_raw),
            confidence=confidence,
            last_user_edit=payload.get("last_user_edit"),
            agent_redraft=payload.get("agent_redraft"),
            promoted_at=payload.get("promoted_at"),
        )

    # ---- vault folder mapping ----------------------------------------------

    def suggested_folder(self) -> str:
        """Return the design-prescribed top-level folder for this note's state."""

        for folder, states in VAULT_FOLDER_STATE.items():
            if self.state in states:
                return folder
        raise FrontmatterValidationError(f"no folder maps to state={self.state.value}")


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


class FrontmatterValidationError(ValueError):
    """Raised when frontmatter violates the §2.2 strict gate."""


def _is_iso8601(value: str) -> bool:
    try:
        # ``fromisoformat`` accepts both ``2026-05-21`` and full datetimes.
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def validate_frontmatter(fm: Frontmatter) -> None:
    """Strict gate per design §2.2.

    Raises :class:`FrontmatterValidationError` on the first violation
    encountered.  All required fields must be present, non-empty, and
    well-formed.
    """

    if not fm.source_id or not fm.source_id.strip():
        raise FrontmatterValidationError("source_id is required")
    if not fm.scope or not fm.scope.strip():
        raise FrontmatterValidationError("scope is required")
    if "/" not in fm.scope:
        # scope shares keyspace with action-scope gate; force at least one slash
        # to discourage flat strings like "household".
        raise FrontmatterValidationError(
            f"scope {fm.scope!r} must be in 'area/sub' form (e.g. ops/low)"
        )

    # time_range
    for label, value in (("start", fm.time_range.start), ("end", fm.time_range.end)):
        if not value or not _is_iso8601(value):
            raise FrontmatterValidationError(
                f"time_range.{label} must be ISO8601, got {value!r}"
            )
    if fm.time_range.start > fm.time_range.end:
        raise FrontmatterValidationError(
            f"time_range.start ({fm.time_range.start}) is after end ({fm.time_range.end})"
        )

    # provenance
    if fm.provenance.kind not in ALLOWED_PROVENANCE_KIND:
        raise FrontmatterValidationError(
            f"provenance.kind {fm.provenance.kind!r} not in {sorted(ALLOWED_PROVENANCE_KIND)}"
        )
    if fm.provenance.agent not in ALLOWED_PROVENANCE_AGENT:
        raise FrontmatterValidationError(
            f"provenance.agent {fm.provenance.agent!r} not in {sorted(ALLOWED_PROVENANCE_AGENT)}"
        )
    if not _is_iso8601(fm.provenance.fetched_at):
        raise FrontmatterValidationError(
            f"provenance.fetched_at must be ISO8601, got {fm.provenance.fetched_at!r}"
        )
    if fm.provenance.url is not None and not fm.provenance.url.strip():
        raise FrontmatterValidationError("provenance.url, if present, must be non-empty")

    # confidence
    if fm.confidence is not None and not (0.0 <= fm.confidence <= 1.0):
        raise FrontmatterValidationError(
            f"confidence {fm.confidence!r} must be within [0, 1]"
        )

    # topics
    if not isinstance(fm.topics, tuple):
        raise FrontmatterValidationError("topics must be a tuple")
    for topic in fm.topics:
        if not topic or not topic.strip():
            raise FrontmatterValidationError("topics entries must be non-empty strings")

    # last_user_edit (optional, but if present must be ISO8601)
    if fm.last_user_edit is not None and not _is_iso8601(fm.last_user_edit):
        raise FrontmatterValidationError(
            f"last_user_edit must be ISO8601, got {fm.last_user_edit!r}"
        )

    # promoted_at must be present when state==promoted
    if fm.state is State.PROMOTED and (
        fm.promoted_at is None or not _is_iso8601(fm.promoted_at)
    ):
        raise FrontmatterValidationError(
            "state=promoted requires promoted_at (ISO8601)"
        )

    # durable promotion requires confidence per §5.3
    if fm.state is State.PROMOTED and fm.confidence is None:
        raise FrontmatterValidationError(
            "state=promoted requires confidence to be set"
        )


# ---------------------------------------------------------------------------
# Body precedence (§2.3)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BodyPrecedence:
    """Result of :func:`select_body`."""

    body: str
    source: str  # "user_edit" | "agent_summary" | "raw_chunk"


def select_body(
    *,
    user_edit: str | None,
    agent_summary: str | None,
    raw_chunk: str | None,
) -> BodyPrecedence:
    """Return the highest-precedence non-empty body.

    Order per design §2.3: ``user_edit > agent_summary > raw_chunk``.
    Raises :class:`ValueError` if all three are missing.
    """

    if user_edit and user_edit.strip():
        return BodyPrecedence(body=user_edit, source="user_edit")
    if agent_summary and agent_summary.strip():
        return BodyPrecedence(body=agent_summary, source="agent_summary")
    if raw_chunk and raw_chunk.strip():
        return BodyPrecedence(body=raw_chunk, source="raw_chunk")
    raise ValueError("no body candidate provided")


# ---------------------------------------------------------------------------
# Non-destructive redraft (§2.5)
# ---------------------------------------------------------------------------


def apply_agent_redraft(
    fm: Frontmatter,
    *,
    redraft: str,
    user_body_present: bool,
) -> Frontmatter:
    """Stage an agent-proposed body without overwriting the user body.

    * If ``user_body_present`` is ``True`` (the note already has a user-edited
      body), the redraft is stored in the ``agent_redraft`` frontmatter field
      and ``state`` is forced to ``reviewed`` per §2.5.
    * If ``user_body_present`` is ``False``, ``apply_agent_redraft`` still
      stores the redraft in ``agent_redraft`` for audit but leaves ``state``
      unchanged; the caller is free to materialize the body into the note.

    This function is a pure transform; the new :class:`Frontmatter` instance is
    returned and the original is untouched.
    """

    if not redraft or not redraft.strip():
        raise ValueError("redraft body must be non-empty")
    new_state = State.REVIEWED if user_body_present else fm.state
    return replace(fm, agent_redraft=redraft, state=new_state)
