#!/usr/bin/env python3
"""Action Scope Gate.

Implements design doc §6 (Action Scope Gate) from
/Users/kevin/Downloads/2026-05-21-helm-architecture-design.md

Five-permission lock derived from the verb in the *current user message*:

    inspect < save < edit < delete < external_send

The current message's narrowest scope verb locks the permission. Past
context, long-term memory, and cron defaults can never widen the lock.

This module is library-only; it produces structured decisions that a
caller (Telegram intake, openclaw skill, helm CLI) uses to refuse,
annotate, or permit the action.

Korean target extraction (documented constraint)
================================================

``extract_targets`` only auto-detects quoted strings, paths, and
CamelCase / dotted identifiers. Bare Korean noun phrases are
recognized by a **conservative classifier-suffix heuristic** only —
specifically, a run of 2+ Hangul syllables immediately followed by one
of the known classifier nouns (``일정``, ``노트``, ``파일``, ``메모``,
``메시지``, ``문서``, ``기록``, ``이벤트``). Other Korean prose is
intentionally NOT extracted to avoid false positives on freeform
sentences.

**Callers MUST resolve Korean targets before calling :func:`evaluate`**
in all cases where the heuristic above does not fire. The
``explicit_targets`` keyword is the contract surface for entity-resolved
names (e.g. ``"성균관대 일정" → calendar_event_id=xyz``).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Iterable, TypedDict

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ---------------------------------------------------------------------------
# Scope definitions
# ---------------------------------------------------------------------------


class ActionScopeKind(str, Enum):
    """Five permission scopes, ordered from narrowest to broadest."""

    INSPECT = "inspect"
    SAVE = "save"
    EDIT = "edit"
    DELETE = "delete"
    EXTERNAL_SEND = "external_send"


# Narrower scope ranks lower. The "current message's narrowest verb wins"
# rule selects the minimum rank among the verbs detected in the message.
_SCOPE_RANK: dict[ActionScopeKind, int] = {
    ActionScopeKind.INSPECT: 0,
    ActionScopeKind.SAVE: 1,
    ActionScopeKind.EDIT: 2,
    ActionScopeKind.DELETE: 3,
    ActionScopeKind.EXTERNAL_SEND: 4,
}


# ---------------------------------------------------------------------------
# Verb mapping table (Korean + English)
# ---------------------------------------------------------------------------


# Korean verbs are matched as substrings (no word boundaries, since
# Korean has no whitespace boundaries between particles). When extending
# this list, prefer the *root form* without trailing politeness/honorific
# suffixes — those are layered on by additional entries below (``~합니다``
# formal, ``~하자`` 청유/제안, ``~하시오`` 명령형). The matcher also
# tolerates a space between the verb stem and ``좀`` (e.g. ``수정 좀 해줘``)
# via dedicated ``좀`` entries. Synced with the OpenClaw mirror
# (``~/.openclaw/workspace/scripts/action_scope_gate.py``) so that intake
# on either side produces identical lock decisions.
_KO_VERBS: dict[ActionScopeKind, tuple[str, ...]] = {
    ActionScopeKind.INSPECT: (
        "확인해봐",
        "확인해줘",
        "확인해",
        "확인 부탁",
        "확인좀",
        "확인 좀",
        "확인합니다",
        "확인하자",
        "확인하시오",
        "확인 부탁드립니다",
        "살펴봐",
        "살펴봐줘",
        "살펴봅니다",
        "살펴보자",
        "살펴보시오",
        "검토해",
        "검토해줘",
        "검토합니다",
        "검토하자",
        "검토하시오",
        "봐줘",
        "체크해",
        "체크해줘",
        "체크합니다",
        "알려줘",
        "알려주세요",
        "보여줘",
        "보여주세요",
        "조회해",
        "조회해줘",
        "조회합니다",
        "조회하시오",
        "읽어줘",
        "읽어주세요",
        "찾아줘",
        "찾아주세요",
    ),
    ActionScopeKind.SAVE: (
        "저장해",
        "저장해줘",
        "저장합니다",
        "저장하자",
        "저장하시오",
        "기록해",
        "기록해줘",
        "기록합니다",
        "기록하자",
        "기록하시오",
        "남겨",
        "남겨줘",
        "남겨놔",
        "문서로 남겨",
        "정리해놔",
        "정리해둬",
        "기록해둬",
        "메모해",
        "메모해둬",
        "캡처해",
        "캡처해줘",
    ),
    ActionScopeKind.EDIT: (
        "수정해",
        "수정해줘",
        "수정 좀",
        "수정좀",
        "수정합니다",
        "수정하자",
        "수정하시오",
        "고쳐",
        "고쳐줘",
        "고칩니다",
        "고치자",
        "고치시오",
        "바꿔",
        "바꿔줘",
        "바꿉니다",
        "바꾸자",
        "바꾸시오",
        "업데이트해",
        "업데이트해줘",
        "업데이트합니다",
        "반영해",
        "반영해줘",
        "반영합니다",
        "갱신해",
        "갱신해줘",
        "갱신합니다",
        "추가해",
        "추가해줘",
        "추가합니다",
        "교체해",
    ),
    ActionScopeKind.DELETE: (
        "삭제해",
        "삭제해줘",
        "삭제합니다",
        "삭제하자",
        "삭제하시오",
        "지워",
        "지워줘",
        "지웁니다",
        "지우자",
        "지우시오",
        "없애",
        "없애줘",
        "없앱니다",
        "없애자",
        "제거해",
        "제거해줘",
        "제거합니다",
        "취소해",
        "취소해줘",
        "취소합니다",
    ),
    ActionScopeKind.EXTERNAL_SEND: (
        "보내",
        "보내줘",
        "보냅니다",
        "보내자",
        "보내시오",
        "전송해",
        "전송해줘",
        "전송 좀",
        "전송좀",
        "전송합니다",
        "전송하자",
        "전송하시오",
        "푸시해",
        "푸시해줘",
        "푸시합니다",
        "커밋해",
        "커밋해줘",
        "커밋합니다",
        "발송해",
        "발송합니다",
        "등록해",
        "등록해줘",
        "등록합니다",
        "올려",
        "올려줘",
        "올립니다",
        "공유해",
        "공유합니다",
    ),
}


# English verbs are matched as whole words (case-insensitive).
_EN_VERBS: dict[ActionScopeKind, tuple[str, ...]] = {
    ActionScopeKind.INSPECT: (
        "check",
        "review",
        "inspect",
        "show",
        "list",
        "look",
        "read",
        "find",
        "view",
        "see",
    ),
    ActionScopeKind.SAVE: (
        "save",
        "record",
        "log",
        "capture",
        "note",
        "store",
        "archive",
    ),
    ActionScopeKind.EDIT: (
        "edit",
        "update",
        "modify",
        "change",
        "fix",
        "patch",
        "rename",
        "refactor",
        "adjust",
    ),
    ActionScopeKind.DELETE: (
        "delete",
        "remove",
        "drop",
        "purge",
        "cancel",
        "rm",
    ),
    ActionScopeKind.EXTERNAL_SEND: (
        "send",
        "push",
        "commit",
        "post",
        "publish",
        "submit",
        "broadcast",
        "deploy",
        "share",
    ),
}


# Compiled English word-boundary patterns.
_EN_VERB_PATTERNS: dict[ActionScopeKind, tuple[re.Pattern[str], ...]] = {
    kind: tuple(re.compile(rf"\b{re.escape(verb)}\b", re.IGNORECASE) for verb in verbs)
    for kind, verbs in _EN_VERBS.items()
}


# Korean verbs sorted once at module load (longest-first), so that
# ``detect_verbs`` does not repeat the sort on every Telegram message.
# ``evaluate`` is intended to run on every inbound message, so even a
# small constant cost matters cumulatively. Stored as a frozen tuple
# per scope to make accidental mutation visible.
_KO_VERBS_SORTED: dict[ActionScopeKind, tuple[str, ...]] = {
    scope: tuple(sorted(verbs, key=len, reverse=True))
    for scope, verbs in _KO_VERBS.items()
}


# ---------------------------------------------------------------------------
# Mutable-state resource catalog
# ---------------------------------------------------------------------------


class MutableResourceMeta(TypedDict):
    """Schema for entries in :data:`MUTABLE_RESOURCES`.

    Tightens the public API: a stable structural description of each
    resource the gate guards. Previously the value type was
    ``dict[str, object]``, which leaked the underlying heterogeneity.
    """

    description: str
    verbs_required: tuple[ActionScopeKind, ...]
    needs_live_source: bool


# Surfaces that require an explicit verb on the current message before
# the assistant may mutate them. Keys are stable identifiers; values are
# the verb scope strictly required to touch them.
MUTABLE_RESOURCES: dict[str, MutableResourceMeta] = {
    "google_calendar": {
        "description": "Google Calendar events",
        "verbs_required": (
            ActionScopeKind.EDIT,
            ActionScopeKind.DELETE,
            ActionScopeKind.EXTERNAL_SEND,
        ),
        "needs_live_source": True,
    },
    "google_sheets": {
        "description": "Google Sheets (household ledger and all other sheets)",
        "verbs_required": (
            ActionScopeKind.EDIT,
            ActionScopeKind.DELETE,
            ActionScopeKind.EXTERNAL_SEND,
        ),
        "needs_live_source": True,
    },
    "cron_jobs": {
        "description": "cron job registration / modification / deletion",
        "verbs_required": (
            ActionScopeKind.EDIT,
            ActionScopeKind.DELETE,
            ActionScopeKind.SAVE,
        ),
        "needs_live_source": False,
    },
    "openclaw_memory": {
        "description": "~/.openclaw/workspace/MEMORY.md",
        "verbs_required": (ActionScopeKind.EDIT, ActionScopeKind.DELETE),
        "needs_live_source": False,
    },
    "openclaw_skills": {
        "description": "~/.openclaw/workspace/skills/**/SKILL.md",
        "verbs_required": (
            ActionScopeKind.EDIT,
            ActionScopeKind.DELETE,
            ActionScopeKind.SAVE,
        ),
        "needs_live_source": False,
    },
    "helm_memory_tree": {
        "description": "~/.helm/memory/**",
        "verbs_required": (
            ActionScopeKind.EDIT,
            ActionScopeKind.DELETE,
            ActionScopeKind.SAVE,
        ),
        "needs_live_source": False,
    },
    "obsidian_vault_existing": {
        "description": "Existing Obsidian vault note frontmatter or body",
        "verbs_required": (ActionScopeKind.EDIT, ActionScopeKind.DELETE),
        "needs_live_source": False,
    },
    "downloads_existing": {
        "description": "Existing files under ~/Downloads/ (new file creation is allowed under save scope)",
        "verbs_required": (ActionScopeKind.EDIT, ActionScopeKind.DELETE),
        "needs_live_source": False,
    },
    "telegram_outbound": {
        "description": "Telegram outbound message",
        "verbs_required": (ActionScopeKind.EXTERNAL_SEND,),
        "needs_live_source": True,
    },
    "git_repository": {
        "description": "git commit / push / branch / merge",
        "verbs_required": (ActionScopeKind.EXTERNAL_SEND, ActionScopeKind.EDIT),
        "needs_live_source": False,
    },
}


# Live-source-required intents: even for INSPECT, factual assertions for
# these resources must consult an authoritative live source rather than
# memory alone (design §6.7 + AGENTS.md rule 4).
_LIVE_SOURCE_TOPICS: frozenset[str] = frozenset(
    {
        "google_calendar",
        "google_sheets",
        "telegram_outbound",
        # External-fact assertions (news, prices, repo status) also need
        # a live source; callers may pass these as topics.
        "external_news",
        "stock_price",
        "exchange_rate",
        "external_repo_status",
    }
)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VerbMatch:
    """A single verb match inside the current message."""

    scope: ActionScopeKind
    verb: str
    language: str  # "ko" | "en"
    start: int
    end: int

    def as_dict(self) -> dict:
        return {
            "scope": self.scope.value,
            "verb": self.verb,
            "language": self.language,
            "start": self.start,
            "end": self.end,
        }


@dataclass
class ActionScopeDecision:
    """Outcome of running the action-scope gate against a message."""

    message: str
    matches: list[VerbMatch] = field(default_factory=list)
    locked_scope: ActionScopeKind | None = None
    requires_target: bool = False
    targets: list[str] = field(default_factory=list)
    needs_live_source: bool = False
    refusal_reason: str | None = None
    annotations: list[str] = field(default_factory=list)

    @property
    def allowed(self) -> bool:
        return self.refusal_reason is None and self.locked_scope is not None

    def forbids(self, attempted: ActionScopeKind) -> bool:
        """Return True if the attempted action exceeds the locked scope."""
        if self.locked_scope is None:
            return True
        return _SCOPE_RANK[attempted] > _SCOPE_RANK[self.locked_scope]

    def as_dict(self) -> dict:
        return {
            "message": self.message,
            "matches": [match.as_dict() for match in self.matches],
            "locked_scope": self.locked_scope.value if self.locked_scope else None,
            "requires_target": self.requires_target,
            "targets": list(self.targets),
            "needs_live_source": self.needs_live_source,
            "refusal_reason": self.refusal_reason,
            "annotations": list(self.annotations),
            "allowed": self.allowed,
        }


# ---------------------------------------------------------------------------
# Verb detection
# ---------------------------------------------------------------------------


def detect_verbs(message: str) -> list[VerbMatch]:
    """Return every verb match found in the message.

    Matches are returned sorted by start position. Overlapping Korean
    verbs are deduplicated so that a longer phrase ("문서로 남겨") wins
    over a shorter substring contained in it.
    """
    if not message:
        return []

    raw: list[VerbMatch] = []

    # Korean: substring matching, prefer longer verbs first so shorter
    # ones that are a strict substring of a longer match are skipped.
    # Pre-sorted at module load (_KO_VERBS_SORTED) to avoid re-sorting
    # on every call.
    for scope, verbs in _KO_VERBS_SORTED.items():
        for verb in verbs:
            start = 0
            while True:
                idx = message.find(verb, start)
                if idx < 0:
                    break
                raw.append(VerbMatch(scope=scope, verb=verb, language="ko", start=idx, end=idx + len(verb)))
                start = idx + len(verb)

    # English: word-boundary regex.
    for scope, patterns in _EN_VERB_PATTERNS.items():
        for pattern in patterns:
            for m in pattern.finditer(message):
                raw.append(
                    VerbMatch(
                        scope=scope,
                        verb=m.group(0),
                        language="en",
                        start=m.start(),
                        end=m.end(),
                    )
                )

    # Drop overlaps: keep the longest span that starts earliest.
    raw.sort(key=lambda item: (item.start, -(item.end - item.start)))
    selected: list[VerbMatch] = []
    occupied_until = -1
    for match in raw:
        if match.start < occupied_until:
            continue
        selected.append(match)
        occupied_until = match.end
    return selected


# ---------------------------------------------------------------------------
# Target extraction
# ---------------------------------------------------------------------------


# Heuristic explicit-target tokens. A real implementation would link
# this to an entity resolver; here we accept any quoted, capitalized,
# or path-like token. The caller can override `explicit_targets`.
_TARGET_TOKEN = re.compile(
    r"""(
        "[^"]+"            # double-quoted
      | '[^']+'            # single-quoted
      | `[^`]+`            # backtick-quoted
      | /[^\s,]+           # path
      | ~/[^\s,]+          # home path
      | [A-Z][A-Za-z0-9_.-]{2,}  # CamelCase identifier
    )""",
    re.VERBOSE,
)


# Conservative Korean target heuristic — see module docstring.
# We only fire on a *noun + classifier* shape (≥2 Hangul syllables
# followed by one of these classifier nouns). This avoids false
# positives on freeform Korean prose; freeform callers are expected
# to use ``explicit_targets``.
_KO_CLASSIFIERS: tuple[str, ...] = (
    "일정",
    "노트",
    "파일",
    "메모",
    "메시지",
    "문서",
    "기록",
    "이벤트",
)

# Hangul syllable block: 가-힣. We require ≥2 syllables of qualifier
# immediately followed by a classifier (the classifier may be attached
# with no whitespace, Korean-style). To stay conservative we never
# cross whitespace inside the qualifier — multi-word noun phrases must
# come through ``explicit_targets``.
_KO_TARGET_RE = re.compile(
    r"([가-힣]{2,})\s*("
    + "|".join(re.escape(c) for c in _KO_CLASSIFIERS)
    + r")"
)


def extract_targets(message: str) -> list[str]:
    """Return explicit target candidates (quoted strings, paths, IDs).

    Also extracts conservative Korean ``<noun>+<classifier>`` phrases
    (see module docstring). Callers needing broader Korean entity
    resolution must pass ``explicit_targets`` through :func:`evaluate`.
    """
    if not message:
        return []
    seen: list[str] = []
    # 1) ASCII-shaped tokens
    for m in _TARGET_TOKEN.finditer(message):
        token = m.group(0).strip()
        if token.startswith(("\"", "'", "`")) and token.endswith(token[0]):
            token = token[1:-1]
        if token and token not in seen:
            seen.append(token)
    # 2) Korean noun+classifier phrases
    for m in _KO_TARGET_RE.finditer(message):
        phrase = (m.group(1) + " " + m.group(2)).strip()
        if phrase and phrase not in seen:
            seen.append(phrase)
    return seen


# ---------------------------------------------------------------------------
# Gate entry point
# ---------------------------------------------------------------------------


def evaluate(
    message: str,
    *,
    explicit_targets: Iterable[str] | None = None,
    topics: Iterable[str] | None = None,
) -> ActionScopeDecision:
    """Run the gate against `message`.

    Parameters
    ----------
    message:
        The *current* user message (raw text).
    explicit_targets:
        Targets the caller has already resolved (overrides heuristic
        extraction). Use this when the intake layer has entity-resolved
        names like "성균관대 일정" → calendar_event_id=xyz.
    topics:
        Topic identifiers for which the answer requires a live source
        check (e.g. ``("google_sheets",)`` when the user asks about a
        household-ledger row). Used to set ``needs_live_source`` even
        for INSPECT scope.
    """
    decision = ActionScopeDecision(message=message)
    matches = detect_verbs(message)
    decision.matches = matches

    if matches:
        # "narrowest verb wins": _SCOPE_RANK assigns 0 to INSPECT (narrowest)
        # and 4 to EXTERNAL_SEND (broadest), so `min(...)` picks the lowest
        # rank — i.e. the most-restrictive verb among the current matches.
        # This is the design intent: if the user message mixes INSPECT and
        # DELETE verbs, the resulting lock is the *safer* one (INSPECT).
        narrowest_match = min(matches, key=lambda m: _SCOPE_RANK[m.scope])
        decision.locked_scope = narrowest_match.scope
    else:
        decision.refusal_reason = "no_verb_detected"
        decision.annotations.append(
            "현재 메시지에서 명시 동사를 찾지 못했습니다. 의도를 다시 확인하세요."
        )
        return decision

    # Target requirement
    if decision.locked_scope in {
        ActionScopeKind.EDIT,
        ActionScopeKind.DELETE,
        ActionScopeKind.EXTERNAL_SEND,
    }:
        decision.requires_target = True

    targets = (
        list(explicit_targets)
        if explicit_targets is not None
        else extract_targets(message)
    )
    decision.targets = targets

    if decision.requires_target and not targets:
        decision.refusal_reason = "missing_explicit_target"
        decision.annotations.append(
            f"{decision.locked_scope.value} 권한은 현재 메시지에 명시된 대상이 필요합니다."
        )

    # Live-source requirement
    topic_set = set(topics or ())
    if topic_set & _LIVE_SOURCE_TOPICS:
        decision.needs_live_source = True
        decision.annotations.append(
            "live source 확인 필요: " + ", ".join(sorted(topic_set & _LIVE_SOURCE_TOPICS))
        )

    # INSPECT scope must never mutate; record an annotation so callers
    # surface the rule when the caller asks "can I delete X here?".
    if decision.locked_scope == ActionScopeKind.INSPECT:
        decision.annotations.append("inspect 권한: 모든 mutation 금지")

    return decision


# ---------------------------------------------------------------------------
# Cross-check helpers
# ---------------------------------------------------------------------------


def attempted_action_allowed(
    decision: ActionScopeDecision,
    attempted: ActionScopeKind,
    *,
    resource: str | None = None,
) -> tuple[bool, str | None]:
    """Check whether ``attempted`` is permitted under the decision.

    Returns ``(allowed, reason_if_blocked)``.

    If ``resource`` is supplied and exists in :data:`MUTABLE_RESOURCES`,
    we additionally check that the attempted scope is one the resource
    accepts.
    """
    if decision.refusal_reason is not None:
        return False, decision.refusal_reason
    if decision.forbids(attempted):
        return (
            False,
            f"locked_scope={decision.locked_scope.value} forbids attempted={attempted.value}",
        )
    if resource is not None:
        meta = MUTABLE_RESOURCES.get(resource)
        if meta is None:
            return False, f"unknown_resource:{resource}"
        if attempted != ActionScopeKind.INSPECT and attempted not in meta["verbs_required"]:
            return (
                False,
                f"resource={resource} does not accept scope={attempted.value}",
            )
    return True, None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Evaluate the action-scope gate for a user message."
    )
    parser.add_argument(
        "--message",
        required=True,
        help="The current user message (raw text).",
    )
    parser.add_argument(
        "--target",
        action="append",
        default=[],
        help="Explicit target identifier; may be repeated.",
    )
    parser.add_argument(
        "--topic",
        action="append",
        default=[],
        help="Topic identifier hint (e.g. google_sheets). May be repeated.",
    )
    parser.add_argument(
        "--attempt",
        choices=[kind.value for kind in ActionScopeKind],
        help="If given, also report whether this scope would be allowed.",
    )
    parser.add_argument(
        "--resource",
        choices=sorted(MUTABLE_RESOURCES),
        help="Optional resource identifier used with --attempt.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    decision = evaluate(
        args.message,
        explicit_targets=args.target or None,
        topics=args.topic or None,
    )
    payload: dict[str, object] = decision.as_dict()
    if args.attempt:
        attempted = ActionScopeKind(args.attempt)
        allowed, reason = attempted_action_allowed(
            decision, attempted, resource=args.resource
        )
        payload["attempt"] = {
            "scope": attempted.value,
            "resource": args.resource,
            "allowed": allowed,
            "reason": reason,
        }
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0 if decision.allowed else 3


if __name__ == "__main__":
    raise SystemExit(main())
