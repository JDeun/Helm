"""Helm State Model.

Implementation of design document §5 (Helm State Model).

Five-state machine: captured / reviewed / applied / promoted / rejected.

Two important gates are enforced here:

1. ``applied -> promoted`` 3-way gate.  The transition is allowed only when at
   least one of three conditions holds:

     a. ``user explicit approval``
     b. recurrence threshold reached (same pattern applied ``N`` times without
        side effects)
     c. ``confidence >= 0.95`` AND ``scope`` starts with ``ops/`` AND
        ``scope`` is classified as ``low`` risk (i.e. ``ops/low``)

2. Telegram reply lint: phrases like "저장했다 / 반영했다 / 운영 규칙으로 반영했습니다"
   are only valid once a note has reached the ``promoted`` state.  Earlier states
   must use weaker phrasing ("Inbox에 저장했습니다" for captured, "정책에 1차
   반영했습니다 (롤백 가능)" for applied).

The module also classifies a note's storage tier:

* ``raw_capture`` — loose, captured-only, prunable after 30 days.
* ``durable`` — strict, promoted, retained indefinitely.

In addition to the note-lifecycle machinery, this module hosts the
**task-state control-flow container** (Forge "Control Flow Is Not
Memory"): structured fields — ``required_steps``, ``completed_steps``,
``blockers``, ``external_side_effect_approvals``, ``finalization_state``,
``recovered_messages`` — plus helpers (``new_task_state``,
``load_task_state``, ``save_task_state``, ``is_finalized``,
``mark_step_completed``, ``record_approval``, ``record_recovered_message``,
``mark_recovered_message``, ``unhandled_recovered_messages``) that
survive transcript compaction and remain authoritative for completion
checks.

Side effects: this module is pure logic.  It never writes files.  Tests rely on
this purity to exercise transition rules.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Iterable

__all__ = [
    "State",
    "PromotionEvidence",
    "TransitionError",
    "allowed_transitions",
    "can_transition",
    "assert_transition",
    "can_promote",
    "promotion_reason",
    "RAW_CAPTURE_RETENTION_DAYS",
    "is_prunable",
    "retention_tier",
    "PROMOTED_PHRASES",
    "APPLIED_PHRASES",
    "CAPTURED_PHRASES",
    "lint_telegram_phrase",
    "PhraseLintError",
    # Task-state control-flow container (Forge: "Control Flow Is Not Memory").
    "TASK_STATE_SCHEMA_VERSION",
    "FINALIZATION_STATES",
    "RECOVERED_MESSAGE_STATUSES",
    "new_task_state",
    "load_task_state",
    "save_task_state",
    "is_finalized",
    "unhandled_recovered_messages",
    "mark_step_completed",
    "record_approval",
    "record_recovered_message",
    "mark_recovered_message",
]


# ---------------------------------------------------------------------------
# State enum
# ---------------------------------------------------------------------------


class State(str, Enum):
    """Helm note lifecycle states.

    Inherits from ``str`` so values round-trip cleanly through JSON / YAML
    frontmatter without an additional encoder.
    """

    CAPTURED = "captured"
    REVIEWED = "reviewed"
    APPLIED = "applied"
    PROMOTED = "promoted"
    REJECTED = "rejected"

    @classmethod
    def parse(cls, value: object) -> "State":
        if isinstance(value, cls):
            return value
        if isinstance(value, str):
            try:
                return cls(value.strip().lower())
            except ValueError as exc:
                raise ValueError(f"unknown state: {value!r}") from exc
        raise TypeError(f"state must be str or State, got {type(value).__name__}")


# ---------------------------------------------------------------------------
# Transition graph
# ---------------------------------------------------------------------------


# Encoded directly from design §5.2.  ``rejected`` is terminal except via
# explicit user intervention which is out of scope for the automated state
# machine.  ``reviewed -> captured`` is explicitly forbidden ("역행 금지").
_FORWARD: dict[State, frozenset[State]] = {
    State.CAPTURED: frozenset({State.REVIEWED, State.REJECTED}),
    State.REVIEWED: frozenset({State.APPLIED, State.REJECTED}),
    # applied may roll back into rejected, or be promoted, or remain.
    State.APPLIED: frozenset({State.PROMOTED, State.REJECTED}),
    # promoted -> applied is an explicit rollback path requiring an incident_id.
    State.PROMOTED: frozenset({State.APPLIED, State.REJECTED}),
    State.REJECTED: frozenset(),
}


def allowed_transitions(state: State | str) -> frozenset[State]:
    """Return the set of states reachable in one step from ``state``."""

    return _FORWARD[State.parse(state)]


class TransitionError(ValueError):
    """Raised when an illegal state transition is attempted."""


def can_transition(
    from_state: State | str,
    to_state: State | str,
    *,
    incident_id: str | None = None,
    user_explicit: bool = False,
) -> bool:
    """Return True if ``from_state`` may transition to ``to_state``.

    ``promoted -> applied`` requires either ``incident_id`` (a rollback under
    incident) or ``user_explicit=True``.  All other transitions follow the
    static graph in :data:`_FORWARD`.
    """

    src = State.parse(from_state)
    dst = State.parse(to_state)
    if dst not in _FORWARD[src]:
        return False
    if src is State.PROMOTED and dst is State.APPLIED:
        return bool(incident_id) or user_explicit
    return True


def assert_transition(
    from_state: State | str,
    to_state: State | str,
    *,
    incident_id: str | None = None,
    user_explicit: bool = False,
) -> None:
    """Raise :class:`TransitionError` if the transition is not allowed."""

    if not can_transition(
        from_state,
        to_state,
        incident_id=incident_id,
        user_explicit=user_explicit,
    ):
        src = State.parse(from_state)
        dst = State.parse(to_state)
        raise TransitionError(
            f"illegal transition: {src.value} -> {dst.value}"
            + (
                "; promoted->applied requires incident_id or user_explicit"
                if src is State.PROMOTED and dst is State.APPLIED
                else ""
            )
        )


# ---------------------------------------------------------------------------
# applied -> promoted three-way gate (§5.2)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PromotionEvidence:
    """Evidence supporting an ``applied -> promoted`` transition.

    Any single field that satisfies its own clause is sufficient; the gate is a
    logical OR across (a) user explicit approval, (b) recurrence threshold,
    (c) confidence + scope.
    """

    user_explicit: bool = False
    recurrence_count: int = 0
    recurrence_threshold: int = 3
    side_effects_observed: bool = False
    confidence: float = 0.0
    scope: str = ""

    # ---- individual clauses -------------------------------------------------

    def clause_user_explicit(self) -> bool:
        return bool(self.user_explicit)

    def clause_recurrence(self) -> bool:
        return (
            self.recurrence_count >= self.recurrence_threshold
            and not self.side_effects_observed
        )

    def clause_confidence_scope(self) -> bool:
        # Design: confidence >= 0.95 AND scope=ops/low (or any hierarchical sub-scope).
        scope = (self.scope or "").strip().lower()
        return self.confidence >= 0.95 and (scope == "ops/low" or scope.startswith("ops/low/"))

    # ---- combined gate ------------------------------------------------------

    def satisfied(self) -> bool:
        return (
            self.clause_user_explicit()
            or self.clause_recurrence()
            or self.clause_confidence_scope()
        )

    def reason(self) -> str | None:
        if self.clause_user_explicit():
            return "user_explicit"
        if self.clause_recurrence():
            return "recurrence_threshold"
        if self.clause_confidence_scope():
            return "confidence_scope_ops_low"
        return None


def can_promote(evidence: PromotionEvidence) -> bool:
    """Return True iff the 3-way promotion gate is satisfied."""

    return evidence.satisfied()


def promotion_reason(evidence: PromotionEvidence) -> str | None:
    """Return the satisfied clause name or ``None`` when the gate fails."""

    return evidence.reason()


# ---------------------------------------------------------------------------
# Raw capture vs durable promotion (§5.3)
# ---------------------------------------------------------------------------


# Design §5.3 specifies 30 days for raw capture prune.
RAW_CAPTURE_RETENTION_DAYS: int = 30


def retention_tier(state: State | str) -> str:
    """Return ``"raw_capture"`` or ``"durable"`` for the given state.

    * ``captured`` and ``rejected`` notes live in the loose tier and are prune
      candidates after :data:`RAW_CAPTURE_RETENTION_DAYS`.
    * ``reviewed`` and ``applied`` are intermediate but retained until promoted
      or rejected; we classify them as ``durable`` for safety (do not prune
      mid-flight).
    * ``promoted`` is always ``durable``.
    """

    s = State.parse(state)
    if s in {State.CAPTURED, State.REJECTED}:
        return "raw_capture"
    return "durable"


def is_prunable(
    state: State | str,
    captured_at: datetime,
    *,
    now: datetime | None = None,
    retention_days: int = RAW_CAPTURE_RETENTION_DAYS,
    tz_assume_utc: bool = True,
) -> bool:
    """Return True if a raw-capture note is past its prune window.

    Parameters
    ----------
    tz_assume_utc:
        Default ``True`` — naive ``captured_at`` / ``now`` inputs are
        silently coerced to UTC (legacy behaviour, preserved for
        compatibility). Set to ``False`` to opt into strict mode where a
        naive ``captured_at`` raises ``ValueError`` instead of being
        coerced; this avoids prune decisions drifting across DST
        boundaries when callers pass local-time datetimes.
    """

    if retention_tier(state) != "raw_capture":
        return False
    if captured_at.tzinfo is None:
        if not tz_assume_utc:
            raise ValueError(
                "captured_at is naive (no tzinfo); pass a tz-aware datetime "
                "or set tz_assume_utc=True to opt into UTC coercion"
            )
        captured_at = captured_at.replace(tzinfo=timezone.utc)
    current = now or datetime.now(tz=timezone.utc)
    if current.tzinfo is None:
        if not tz_assume_utc:
            raise ValueError(
                "now is naive (no tzinfo); pass a tz-aware datetime or set "
                "tz_assume_utc=True to opt into UTC coercion"
            )
        current = current.replace(tzinfo=timezone.utc)
    return current - captured_at >= timedelta(days=retention_days)


# ---------------------------------------------------------------------------
# Telegram phrase lint (§5.5)
# ---------------------------------------------------------------------------


# Phrases that imply a *promoted* operational rule.  Lint reject if used at
# anything below ``promoted``.
PROMOTED_PHRASES: tuple[str, ...] = (
    "운영 규칙으로 반영",
    "운영 규칙으로 반영했",
    "운영에 반영",
    "정책으로 반영",
)

# Phrases that imply *applied* but rollback-capable state.
APPLIED_PHRASES: tuple[str, ...] = (
    "정책에 1차 반영",
    "정책에 반영",
    "1차 반영",
    "반영했습니다",
    "반영했어요",
    "반영했다",
)

# Phrases for captured-only success.
CAPTURED_PHRASES: tuple[str, ...] = (
    "Inbox에 저장",
    "저장했습니다",
    "저장했어요",
    "저장했다",
)


class PhraseLintError(ValueError):
    """Raised when a Telegram phrase overstates the actual state."""


# Korean negation suffixes that, when they appear immediately after a
# matched assertion phrase, invert its polarity. Matching is intentionally
# narrow: only the segment from the end of the matched phrase up to the
# next sentence-terminator is inspected, and only well-formed Korean
# negation forms count. This avoids false positives like
#   "운영 규칙으로 반영하지 않았습니다"     → contains "운영 규칙으로 반영"
#   "정책에 반영하기 어렵습니다"            → contains "정책에 반영"
#   "저장하지 못했습니다"                   → contains "저장"
# (R2 I6 / R0 Minor #21.)
_NEGATION_PATTERNS: tuple[str, ...] = (
    "하지 않",         # ~하지 않았습니다 / ~하지 않다
    "하지않",          # same, no-space variant
    "지 않",           # ~지 않
    "지않",            # ~지않
    "안 ",             # leading "안 "
    "안되",            # "안 되" no-space variant
    "안 되",           # "안 되"
    "못 ",             # "못 했"
    "못했",            # "못했습니다"
    "못 했",           # "못 했"
    "어렵",            # "~기 어렵습니다"
    "어려",            # "어려워서…"  (declension variant)
    "불가",            # "불가능"
    "실패",            # "실패했"
    "취소",            # "취소했"
    "보류",            # "보류했"
    "없",              # "~ 수 없"
)

# Sentence-ending punctuation. The negation scan stops at the first
# of these so a later clause's negation cannot cancel an earlier
# positive assertion.
_SENTENCE_TERMINATORS: tuple[str, ...] = (".", "!", "?", "。", "!", "?", "\n")


def _segment_after(text: str, match_end: int, window: int = 24) -> str:
    """Return the text from ``match_end`` up to the next sentence end.

    The window cap keeps the scan local: a negation in a later sentence
    must not silence an assertion in the current one.
    """
    end = len(text)
    soft_end = min(match_end + window, end)
    for i, ch in enumerate(text[match_end:soft_end], start=match_end):
        if ch in _SENTENCE_TERMINATORS:
            return text[match_end:i]
    return text[match_end:soft_end]


def _is_negated_after(text: str, match_end: int) -> bool:
    """Return ``True`` if the assertion at ``match_end`` is immediately negated.

    Only inspects the immediately-following clause (bounded by sentence
    terminators or a 24-char window). This deliberately misses negations
    that appear *before* the assertion ("저장은 했지만 반영하지는 못했습니다")
    — those are typically still assertions of the matched phrase, just
    with a hedged tail. The doctest in :func:`lint_telegram_phrase`
    illustrates the supported shapes.
    """
    segment = _segment_after(text, match_end)
    return any(pattern in segment for pattern in _NEGATION_PATTERNS)


def _contains_any(
    text: str, phrases: Iterable[str], *, respect_negation: bool = True
) -> str | None:
    """Return the first phrase that occurs in ``text`` as a positive assertion.

    When ``respect_negation`` is ``True`` (the default), a match whose
    immediately-following clause carries a Korean negation marker
    (``하지 않``, ``못 했``, ``어렵`` etc.) is treated as a non-match so
    the caller does not lint a denial as a positive assertion. See
    :func:`_is_negated_after` for the negation grammar.
    """
    for phrase in phrases:
        start = text.find(phrase)
        while start != -1:
            match_end = start + len(phrase)
            if not (respect_negation and _is_negated_after(text, match_end)):
                return phrase
            start = text.find(phrase, match_end)
    return None


def lint_telegram_phrase(text: str, state: State | str) -> None:
    """Validate that ``text`` does not overstate the action versus ``state``.

    Specifically:

    * Promoted-only phrases ("운영 규칙으로 반영했습니다") require
      ``state == promoted``.
    * Applied phrases ("반영했습니다", "정책에 1차 반영했습니다") require
      ``state in {applied, promoted}``.
    * Captured phrases ("저장했습니다") require any successful state, i.e. not
      ``rejected``.

    Korean negation guard
    ---------------------
    A matched phrase whose immediately-following clause carries a
    negation marker (``하지 않``, ``못 했``, ``어렵``, ``실패``, ``취소``,
    ``보류``, ``없``…) is treated as a non-assertion and does **not**
    trigger a lint error. This addresses the R0 Minor #21 / R2 I6
    false-positive where ``"운영 규칙으로 반영하지 않았습니다"`` matched
    the promoted-only phrase ``"운영 규칙으로 반영"`` as a substring.

    Raises :class:`PhraseLintError` describing the violation.  Returns ``None``
    if no rule is violated.
    """

    s = State.parse(state)

    promoted_hit = _contains_any(text, PROMOTED_PHRASES)
    if promoted_hit and s is not State.PROMOTED:
        raise PhraseLintError(
            f"phrase {promoted_hit!r} requires state=promoted, got {s.value}"
        )

    applied_hit = _contains_any(text, APPLIED_PHRASES)
    if applied_hit and s not in {State.APPLIED, State.PROMOTED}:
        raise PhraseLintError(
            f"phrase {applied_hit!r} requires state in {{applied, promoted}}, got {s.value}"
        )

    captured_hit = _contains_any(text, CAPTURED_PHRASES)
    if captured_hit and s is State.REJECTED:
        raise PhraseLintError(
            f"phrase {captured_hit!r} cannot be used when state=rejected"
        )


# ---------------------------------------------------------------------------
# Task-state control-flow container
# ---------------------------------------------------------------------------
#
# Forge's "Control Flow Is Not Memory" principle: a model's message history
# is *memory* and is subject to compaction. Required steps, completion
# state, approvals, blockers, and recovered messages are *control state*
# and must live outside the transcript. After compaction, this structured
# state — not the model's recollection — is authoritative.
#
# This section is intentionally schema-light: the task state is a plain
# ``dict`` so callers (runner, approver, message handler, completion-check
# code) can read and mutate it without depending on a dataclass-bound API.
# Backward compatibility is honored by ``load_task_state`` filling defaults
# for missing fields and preserving any unknown extra keys for round-trip.
#
# Verb convention for mutators:
#
# * ``record_*`` (e.g. ``record_approval``, ``record_recovered_message``)
#   *appends* a new entry to a list. Duplicate-id rejection is the
#   per-call concern of the function (only ``record_recovered_message``
#   currently enforces it; approvals are intentionally append-only).
# * ``mark_*`` (e.g. ``mark_step_completed``, ``mark_recovered_message``)
#   *mutates* an existing entry's status / membership. Raises on
#   unknown id or unknown step. Idempotent where the spec calls for it
#   (``mark_step_completed``).
#
# Read helpers (``is_finalized``, ``unhandled_recovered_messages``)
# return defensive deep copies — callers cannot corrupt internal state
# by mutating the returned values.


TASK_STATE_SCHEMA_VERSION: int = 1

FINALIZATION_STATES: frozenset[str] = frozenset(
    {"pending", "in_progress", "finalized", "abandoned"}
)

RECOVERED_MESSAGE_STATUSES: frozenset[str] = frozenset(
    {"handled", "superseded", "active_unhandled", "blocked_by_truncation"}
)


# Field-name → default-factory. ``list`` is used as a factory so each new
# state object gets its own list (no mutable-default aliasing).
_TASK_STATE_DEFAULT_FACTORIES: dict[str, object] = {
    "task_state_schema_version": lambda: TASK_STATE_SCHEMA_VERSION,
    "required_steps": list,
    "completed_steps": list,
    "blockers": list,
    "external_side_effect_approvals": list,
    "finalization_state": lambda: "pending",
    "recovered_messages": list,
}


def _utcnow_iso8601() -> str:
    """Return current UTC time as ISO8601 with a ``+00:00`` offset.

    Uses ``datetime.now(timezone.utc)`` so the value is tz-aware and stable
    across the test suite's freezegun-free assertions.
    """
    return datetime.now(tz=timezone.utc).isoformat()


def new_task_state() -> dict:
    """Return a fresh task-state dict populated with defaults."""

    state: dict = {}
    for name, factory in _TASK_STATE_DEFAULT_FACTORIES.items():
        state[name] = factory()  # type: ignore[operator]
    return state


def load_task_state(raw: dict | None) -> dict:
    """Load a task-state dict, filling defaults for missing fields.

    Unknown extra keys are preserved verbatim so the loader can be safely
    used against state objects written by newer code paths. The returned
    dict is always a fresh object (the input is not mutated).
    """

    if raw is None:
        return new_task_state()
    if not isinstance(raw, dict):
        raise TypeError(
            f"task state must be a dict, got {type(raw).__name__}"
        )
    state = dict(raw)
    for name, factory in _TASK_STATE_DEFAULT_FACTORIES.items():
        if name not in state:
            state[name] = factory()  # type: ignore[operator]
    return state


def save_task_state(state: dict) -> dict:
    """Return a JSON-serializable deep copy of ``state`` for persistence.

    Preserves unknown extra keys. Does not mutate the input. The returned
    object is a full deep copy — nested lists / dicts are *not* shared
    with the input, so subsequent mutations to ``state`` cannot affect
    a previously-saved snapshot (and vice versa). The shape is a plain
    dict so callers may json-dump it directly.
    """

    if not isinstance(state, dict):
        raise TypeError(
            f"task state must be a dict, got {type(state).__name__}"
        )
    return copy.deepcopy(state)


def is_finalized(state: dict) -> bool:
    """Return True iff the task is *both* flagged finalized *and* complete.

    "Finalized" requires:

    * ``finalization_state == "finalized"`` — the runner explicitly
      closed the task, AND
    * every step in ``required_steps`` appears in ``completed_steps``.

    Either condition alone is insufficient. A task with all steps done
    but ``finalization_state == "pending"`` is still mid-flight; a task
    flagged finalized with a step missing is malformed and must not be
    treated as complete.

    Dirty-data guard: if ``completed_steps`` contains an entry that is
    not in ``required_steps``, ``is_finalized`` raises ``ValueError``
    rather than silently returning ``False`` forever. This surfaces a
    real class of bug (callers appending to ``completed_steps``
    directly, bypassing :func:`mark_step_completed`) instead of hiding
    it. Use :func:`mark_step_completed` to record progress; it
    enforces this invariant on write as well.
    """

    if state.get("finalization_state") != "finalized":
        return False
    required = list(state.get("required_steps") or [])
    completed = list(state.get("completed_steps") or [])
    required_set = set(required)
    unknown = [s for s in completed if s not in required_set]
    if unknown:
        raise ValueError(
            f"completed_steps contains entries not in required_steps: "
            f"{unknown!r}; required_steps={required!r}"
        )
    return required_set.issubset(set(completed))


def unhandled_recovered_messages(state: dict) -> list[dict]:
    """Return recovered messages whose status is ``active_unhandled``.

    This is the read side of the recovered-context regression fix:
    callers (Telegram bridge, completion-check code) consult this list
    to find requests that survived compaction and still need action.

    The returned list contains *copies* of the entries — mutating the
    result does not affect the state. Use :func:`mark_recovered_message`
    to change status.
    """

    return [
        copy.deepcopy(m)
        for m in (state.get("recovered_messages") or [])
        if isinstance(m, dict) and m.get("status") == "active_unhandled"
    ]


def mark_step_completed(state: dict, step: str) -> None:
    """Mark ``step`` as completed.

    * Raises ``ValueError`` if ``step`` is not in ``required_steps``.
    * Idempotent: a second call for the same step is a no-op.
    * Appends in call order — preserves the sequence in which steps
      actually finished, which may differ from ``required_steps`` order.
    """

    required = state.setdefault("required_steps", [])
    completed = state.setdefault("completed_steps", [])
    if step not in required:
        raise ValueError(
            f"unknown step {step!r}; required_steps={list(required)!r}"
        )
    if step in completed:
        return
    completed.append(step)


def record_approval(
    state: dict,
    action: str,
    target: str,
    approved_by: str,
) -> None:
    """Append a side-effect-approval record with an ISO8601 timestamp.

    The approval list is the authoritative log of "the user (or operator)
    explicitly OK'd this external side effect" — it does not replace the
    promotion gate, but it is the durable record consulted after
    compaction.
    """

    approvals = state.setdefault("external_side_effect_approvals", [])
    approvals.append(
        {
            "action": action,
            "target": target,
            "approved_by": approved_by,
            "approved_at": _utcnow_iso8601(),
        }
    )


def record_recovered_message(
    state: dict,
    source: str,
    message_id: str,
    action_verb: str | None,
    topic_continuity_score: float | None,
) -> None:
    """Add a recovered-message entry with status ``active_unhandled``.

    Raises ``ValueError`` if ``message_id`` already exists in
    ``recovered_messages`` — no silent overwrite. Use
    :func:`mark_recovered_message` to mutate the status of an existing
    entry.

    ``action_verb`` and ``topic_continuity_score`` may both be ``None``;
    they are stored as-is. ``None`` for ``action_verb`` indicates the
    recovery source did not extract a verb (e.g. a non-imperative
    message); ``None`` for ``topic_continuity_score`` indicates no
    continuity heuristic was applied.
    """

    messages = state.setdefault("recovered_messages", [])
    for existing in messages:
        if isinstance(existing, dict) and existing.get("message_id") == message_id:
            raise ValueError(
                f"recovered message_id {message_id!r} already exists; "
                "use mark_recovered_message() to update status"
            )
    messages.append(
        {
            "source": source,
            "message_id": message_id,
            "action_verb": action_verb,
            "status": "active_unhandled",
            "topic_continuity_score": topic_continuity_score,
        }
    )


def mark_recovered_message(state: dict, message_id: str, status: str) -> None:
    """Set ``status`` on the recovered-message entry with ``message_id``.

    Raises ``ValueError`` on unknown ``message_id`` or unknown ``status``.
    Valid statuses are in :data:`RECOVERED_MESSAGE_STATUSES`.
    """

    if status not in RECOVERED_MESSAGE_STATUSES:
        raise ValueError(
            f"unknown recovered-message status {status!r}; "
            f"valid={sorted(RECOVERED_MESSAGE_STATUSES)!r}"
        )
    messages = state.get("recovered_messages") or []
    for entry in messages:
        if isinstance(entry, dict) and entry.get("message_id") == message_id:
            entry["status"] = status
            return
    raise ValueError(f"no recovered message with message_id={message_id!r}")
