"""Filesystem-backed Memory Tree.

Design ref: ``2026-05-21-helm-architecture-design.md`` §1.

Dependency contract
-------------------
The module restricts itself to stdlib *and* a single allowed Helm
helper (:mod:`scripts.state_io`) so the ledger-append path stays
identical to every other Helm writer (locking + ``fsync``). It does
not pull in third-party packages, intelligence-tier modules, or any
of the policy / guard surfaces — the intent is that ``memory_tree``
can run in the same constrained environments as the rest of Helm
without dragging extra dependencies in.

R3 history note: prior versions inlined a small ``fcntl.flock``
block here and the doc claimed a literal "stdlib-only" rationale for
the duplication. ``scripts.state_io.append_jsonl_atomic`` is itself
stdlib-only (``json/os/sys/threading/warnings/pathlib/fcntl/msvcrt``),
so the duplication carried no actual dependency benefit while making
the two paths diverge on ``fsync`` (state_io fsyncs, the inline copy
did not). R5 M4 centralizes the append via ``state_io`` for a single
bit-for-bit ledger-write contract.

Notes on integration with existing Helm/OpenClaw state:

* This module *only* writes inside the configured ``root`` (default
  ``~/.helm/memory``) and appends to the configured ``ledger_path``
  (default ``~/.helm/task-ledger.jsonl``).
* It never modifies ``~/.openclaw/workspace/MEMORY.md`` or the daily
  notes under ``~/.openclaw/workspace/memory/YYYY-MM-DD.md`` — those
  remain user / operator surfaces.  The OpenClaw mirror under
  ``~/.openclaw/workspace/memory/{source,topic}_summary/`` is documented
  as a *standard layout* (see :func:`openclaw_mirror_paths`); creation of
  actual content is left to the user / promotion pipeline.
"""

from __future__ import annotations

import enum
import hashlib
import json
import os
import re
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping, Sequence


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


DEFAULT_ROOT = Path.home() / ".helm" / "memory"
DEFAULT_LEDGER = Path.home() / ".helm" / "task-ledger.jsonl"

# Source ids known at design time (extendable).  Used only for validation
# helpers; arbitrary source ids are accepted.
KNOWN_SOURCE_IDS: tuple[str, ...] = (
    "telegram",
    "calendar-personal",
    "sheets-household-ledger",
    "github-trending",
    "gmail-primary",
    "notion-helm",
    "obsidian-vault",
    "drive-personal",
    "web-fetch",
)

# Topic ids that the design doc explicitly calls out as Phase-1 concerns.
KNOWN_TOPIC_IDS: tuple[str, ...] = (
    "helm",
    "openclaw",
    "aimi",
    "household",
    "vehicle",
    "career",
    "ai-briefing",
    "personal-agent-research",
)

# Refresh-related kind used for task-ledger entries.
LEDGER_KIND = "memory_refresh"

# Maximum length of a sanitized id segment used in filenames.
_MAX_ID_LEN = 96
_ID_RE = re.compile(r"[^a-z0-9._-]+")


# ---------------------------------------------------------------------------
# Triggers
# ---------------------------------------------------------------------------


class RefreshTrigger(str, enum.Enum):
    """The 5 refresh triggers from the design doc §1.3."""

    CRON = "cron"  # connector auto-fetch (source-only)
    TELEGRAM_ANSWER = "telegram_answer"  # right before answering on Telegram
    TASK_LEDGER_CHANGE = "task_ledger_change"  # other task-ledger entry mutated
    OBSIDIAN_USER_EDIT = "obsidian_user_edit"  # user edited a vault note
    GLOBAL_COMPACT = "global_compact"  # explicit or weekly global rebuild


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sanitize_id(value: str) -> str:
    """Reduce ``value`` to a filesystem-safe identifier segment."""

    if not isinstance(value, str) or not value.strip():
        raise ValueError("id must be a non-empty string")
    lowered = value.strip().lower()
    cleaned = _ID_RE.sub("-", lowered).strip("-._")
    if not cleaned:
        raise ValueError(f"id contains no usable characters: {value!r}")
    return cleaned[:_MAX_ID_LEN]


def compute_hash(text: str) -> str:
    """Return a stable short content hash (sha256, hex, 16 chars)."""

    if text is None:
        text = ""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".tmp-memtree-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except FileNotFoundError:
            pass
        raise


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


# ---------------------------------------------------------------------------
# Frontmatter (very small YAML-ish subset)
# ---------------------------------------------------------------------------


def _format_scalar(value) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    # Strings that would be re-parsed as a non-string scalar (bool/null/
    # numeric) or that contain YAML-significant characters (``:``, ``#``,
    # ``,``, ``[``, ``]``, leading ``-``) must be quoted to survive the
    # round-trip through _parse_value / _parse_frontmatter. Whitespace at
    # the boundaries also requires quoting.
    if _needs_quoting(text):
        escaped = text.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return text


# Tokens that _parse_value would reinterpret as a non-string scalar.
_LITERAL_KEYWORDS: frozenset[str] = frozenset({"null", "true", "false"})
# Characters that, if present in an unquoted scalar, would corrupt a
# round-trip via the lightweight inline-list / fence parser.
_UNQUOTED_FORBIDDEN: tuple[str, ...] = (":", "#", "\n", '"', ",", "[", "]")


def _needs_quoting(text: str) -> bool:
    """Return True if the scalar must be double-quoted for safe round-trip."""

    if text == "":
        return False  # _parse_value("") returns None, but _format_scalar(None)
                       # already short-circuits; an empty *string* coming in
                       # via str() is rare and not a round-trip hazard.
    if text.strip() != text:
        return True
    if text.lower() in _LITERAL_KEYWORDS:
        return True
    # Leading ``-`` makes _parse_value try int(); negative numerals like
    # "-1" would round-trip to int and lose string identity.
    if text[0] == "-":
        return True
    if any(ch in text for ch in _UNQUOTED_FORBIDDEN):
        return True
    # A bare token that parses as int / float would lose string identity.
    stripped = text
    try:
        if "." in stripped:
            float(stripped)
            return True
        int(stripped)
        return True
    except ValueError:
        pass
    return False


def _format_list(items: Sequence) -> str:
    return "[" + ", ".join(_format_scalar(item) for item in items) + "]"


def _render_frontmatter(data: Mapping[str, object]) -> str:
    lines: list[str] = ["---"]
    for key, value in data.items():
        if isinstance(value, (list, tuple)):
            lines.append(f"{key}: {_format_list(value)}")
        else:
            lines.append(f"{key}: {_format_scalar(value)}")
    lines.append("---")
    return "\n".join(lines) + "\n"


def _parse_frontmatter(text: str) -> tuple[dict[str, object], str]:
    """Parse the leading ``---``-delimited block.

    Recognises scalars, ``[a, b, c]`` inline lists, and quoted strings.
    Unknown shapes fall back to raw string preservation so we never crash
    on user-edited notes.
    """

    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    header = text[3:end].lstrip("\n")
    tail_start = end + len("\n---")
    if tail_start < len(text) and text[tail_start] == "\n":
        tail_start += 1
    body = text[tail_start:]

    out: dict[str, object] = {}
    for raw in header.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if ":" not in raw:
            continue
        key, _, value = raw.partition(":")
        key = key.strip()
        value = value.strip()
        out[key] = _parse_value(value)
    return out, body


def _split_inline_list(inner: str) -> list[str]:
    """Split the body of ``[a, b, "c, d"]`` honouring quoted commas.

    Returns the raw token fragments (still trimmed) so each can be passed
    back through :func:`_parse_value`.
    """

    parts: list[str] = []
    buf: list[str] = []
    in_quote = False
    escape = False
    for ch in inner:
        if escape:
            buf.append(ch)
            escape = False
            continue
        if ch == "\\" and in_quote:
            buf.append(ch)
            escape = True
            continue
        if ch == '"':
            in_quote = not in_quote
            buf.append(ch)
            continue
        if ch == "," and not in_quote:
            parts.append("".join(buf).strip())
            buf = []
            continue
        buf.append(ch)
    tail = "".join(buf).strip()
    if tail or parts:
        parts.append(tail)
    return parts


def _parse_value(value: str) -> object:
    if value == "" or value.lower() == "null":
        return None
    if value.lower() == "true":
        return True
    if value.lower() == "false":
        return False
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        parts = _split_inline_list(inner)
        return [_parse_value(p) for p in parts]
    if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        return value[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    try:
        if "." in value:
            return float(value)
        return int(value)
    except ValueError:
        return value


# ---------------------------------------------------------------------------
# Summary dataclasses
# ---------------------------------------------------------------------------


@dataclass
class SourceSummary:
    """source-layer summary (one connector / origin)."""

    source_id: str
    summary_text: str = ""
    last_seen: str = field(default_factory=_utcnow_iso)
    last_success: str = field(default_factory=_utcnow_iso)
    freshness_sla_minutes: int = 60
    provenance: str = "agent"
    chunk_count: int = 0
    hot_chunks: list[str] = field(default_factory=list)
    refresh_reason: str = RefreshTrigger.CRON.value

    def frontmatter(self) -> dict[str, object]:
        return {
            "kind": "source_summary",
            "source_id": self.source_id,
            "last_seen": self.last_seen,
            "last_success": self.last_success,
            "freshness_sla_minutes": self.freshness_sla_minutes,
            "provenance": self.provenance,
            "chunk_count": self.chunk_count,
            "hot_chunks": list(self.hot_chunks),
            "refresh_reason": self.refresh_reason,
        }


@dataclass
class TopicSummary:
    """topic-layer summary (multiple sources -> one topic)."""

    topic_id: str
    summary_text: str = ""
    linked_sources: list[str] = field(default_factory=list)
    linked_tasks: list[str] = field(default_factory=list)
    last_refresh: str = field(default_factory=_utcnow_iso)
    refresh_reason: str = RefreshTrigger.CRON.value

    def frontmatter(self) -> dict[str, object]:
        return {
            "kind": "topic_summary",
            "topic_id": self.topic_id,
            "linked_sources": list(self.linked_sources),
            "linked_tasks": list(self.linked_tasks),
            "last_refresh": self.last_refresh,
            "refresh_reason": self.refresh_reason,
        }


@dataclass
class GlobalSummary:
    """global-layer summary (compact injected memory candidate)."""

    summary_text: str = ""
    generated_at: str = field(default_factory=_utcnow_iso)
    token_budget: int = 3000
    included_topics: list[str] = field(default_factory=list)
    excluded_topics: list[str] = field(default_factory=list)

    def frontmatter(self) -> dict[str, object]:
        return {
            "kind": "global_summary",
            "generated_at": self.generated_at,
            "token_budget": self.token_budget,
            "included_topics": list(self.included_topics),
            "excluded_topics": list(self.excluded_topics),
        }


# ---------------------------------------------------------------------------
# Refresh result + paths
# ---------------------------------------------------------------------------


@dataclass
class RefreshResult:
    layer: str  # "source" | "topic" | "global"
    target: str  # source_id / topic_id / "current"
    trigger: RefreshTrigger
    reason: str
    before_hash: str
    after_hash: str
    path: Path
    task_id: str
    timestamp: str
    promoted_to_topic: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return self.before_hash != self.after_hash

    def to_ledger_entry(self) -> dict[str, object]:
        return {
            "task_id": self.task_id,
            "kind": LEDGER_KIND,
            "layer": self.layer,
            "target": self.target,
            "trigger": self.trigger.value,
            "reason": self.reason,
            "before_hash": self.before_hash,
            "after_hash": self.after_hash,
            "promoted_to_topic": list(self.promoted_to_topic),
            "path": str(self.path),
            "timestamp": self.timestamp,
            "changed": self.changed,
        }


@dataclass(frozen=True)
class MemoryTreePaths:
    root: Path
    source_dir: Path
    topic_dir: Path
    global_dir: Path
    global_file: Path

    @classmethod
    def from_root(cls, root: Path) -> "MemoryTreePaths":
        root = Path(root).expanduser()
        return cls(
            root=root,
            source_dir=root / "source",
            topic_dir=root / "topic",
            global_dir=root / "global",
            global_file=root / "global" / "current.md",
        )


def openclaw_mirror_paths(workspace: Path | None = None) -> MemoryTreePaths:
    """Return the standard OpenClaw mirror layout.

    This does **not** create any files; it just names the standard
    locations so other tooling (briefing, ontology sync, ...) can agree
    on where the compact summary candidates live.
    """

    base = Path(workspace).expanduser() if workspace else (Path.home() / ".openclaw" / "workspace")
    root = base / "memory"
    return MemoryTreePaths(
        root=root,
        source_dir=root / "source_summary",
        topic_dir=root / "topic_summary",
        global_dir=root,
        global_file=root / "global_summary.md",
    )


# ---------------------------------------------------------------------------
# MemoryTree facade
# ---------------------------------------------------------------------------


class MemoryTree:
    """High-level facade for the source / topic / global tree."""

    def __init__(
        self,
        root: Path | str | None = None,
        *,
        ledger_path: Path | str | None = None,
        clock=None,
        task_id_clock=None,
    ) -> None:
        """Create a MemoryTree.

        Parameters
        ----------
        clock:
            Optional callable returning ISO8601 strings for ``last_seen`` /
            ``last_success`` / ``timestamp`` fields. Defaults to
            :func:`_utcnow_iso`.
        task_id_clock:
            Optional callable returning a numeric epoch-like value used as
            the leading segment of new task ids. Defaults to
            :func:`time.time`. Threaded so tests can pin the task-id
            stream for deterministic ledger comparisons.
        """
        self.paths = MemoryTreePaths.from_root(Path(root) if root else DEFAULT_ROOT)
        self.ledger_path = Path(ledger_path).expanduser() if ledger_path else DEFAULT_LEDGER
        self._clock = clock or _utcnow_iso
        self._task_id_clock = task_id_clock or time.time

    # ------------------------------------------------------------------ paths

    def source_path(self, source_id: str) -> Path:
        return self.paths.source_dir / f"{_sanitize_id(source_id)}.md"

    def topic_path(self, topic_id: str) -> Path:
        return self.paths.topic_dir / f"{_sanitize_id(topic_id)}.md"

    @property
    def global_path(self) -> Path:
        return self.paths.global_file

    def ensure_directories(self) -> None:
        for path in (
            self.paths.source_dir,
            self.paths.topic_dir,
            self.paths.global_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ read

    def read_source(self, source_id: str) -> SourceSummary | None:
        path = self.source_path(source_id)
        text = _read_text(path)
        if not text:
            return None
        front, body = _parse_frontmatter(text)
        return SourceSummary(
            source_id=str(front.get("source_id", source_id)),
            summary_text=body.strip(),
            last_seen=str(front.get("last_seen") or self._clock()),
            last_success=str(front.get("last_success") or self._clock()),
            freshness_sla_minutes=int(front.get("freshness_sla_minutes") or 60),
            provenance=str(front.get("provenance") or "agent"),
            chunk_count=int(front.get("chunk_count") or 0),
            hot_chunks=[str(x) for x in (front.get("hot_chunks") or [])],
            refresh_reason=str(front.get("refresh_reason") or RefreshTrigger.CRON.value),
        )

    def read_topic(self, topic_id: str) -> TopicSummary | None:
        path = self.topic_path(topic_id)
        text = _read_text(path)
        if not text:
            return None
        front, body = _parse_frontmatter(text)
        return TopicSummary(
            topic_id=str(front.get("topic_id", topic_id)),
            summary_text=body.strip(),
            linked_sources=[str(x) for x in (front.get("linked_sources") or [])],
            linked_tasks=[str(x) for x in (front.get("linked_tasks") or [])],
            last_refresh=str(front.get("last_refresh") or self._clock()),
            refresh_reason=str(front.get("refresh_reason") or RefreshTrigger.CRON.value),
        )

    def read_global(self) -> GlobalSummary | None:
        text = _read_text(self.global_path)
        if not text:
            return None
        front, body = _parse_frontmatter(text)
        return GlobalSummary(
            summary_text=body.strip(),
            generated_at=str(front.get("generated_at") or self._clock()),
            token_budget=int(front.get("token_budget") or 3000),
            included_topics=[str(x) for x in (front.get("included_topics") or [])],
            excluded_topics=[str(x) for x in (front.get("excluded_topics") or [])],
        )

    # ------------------------------------------------------------------ refresh

    def refresh_source(
        self,
        source_id: str,
        summary: SourceSummary | str,
        *,
        trigger: RefreshTrigger = RefreshTrigger.CRON,
        reason: str = "",
        task_id: str | None = None,
    ) -> RefreshResult:
        """Write/update a source summary and append a ledger entry."""

        if isinstance(summary, str):
            existing = self.read_source(source_id) or SourceSummary(source_id=source_id)
            existing.summary_text = summary.strip()
            existing.last_seen = self._clock()
            existing.last_success = self._clock()
            existing.refresh_reason = trigger.value
            summary_obj = existing
        else:
            summary_obj = summary
            if not summary_obj.source_id:
                summary_obj.source_id = source_id
            summary_obj.refresh_reason = trigger.value
            summary_obj.last_seen = summary_obj.last_seen or self._clock()
            summary_obj.last_success = summary_obj.last_success or self._clock()

        path = self.source_path(summary_obj.source_id)
        return self._write_layer(
            layer="source",
            target=summary_obj.source_id,
            path=path,
            frontmatter=summary_obj.frontmatter(),
            body=summary_obj.summary_text,
            trigger=trigger,
            reason=reason,
            task_id=task_id,
        )

    def refresh_topic(
        self,
        topic_id: str,
        summary: TopicSummary | str,
        *,
        trigger: RefreshTrigger = RefreshTrigger.TASK_LEDGER_CHANGE,
        reason: str = "",
        task_id: str | None = None,
        promoted_to_topic: Iterable[str] | None = None,
    ) -> RefreshResult:
        if isinstance(summary, str):
            existing = self.read_topic(topic_id) or TopicSummary(topic_id=topic_id)
            existing.summary_text = summary.strip()
            existing.last_refresh = self._clock()
            existing.refresh_reason = trigger.value
            summary_obj = existing
        else:
            summary_obj = summary
            if not summary_obj.topic_id:
                summary_obj.topic_id = topic_id
            summary_obj.last_refresh = summary_obj.last_refresh or self._clock()
            summary_obj.refresh_reason = trigger.value

        path = self.topic_path(summary_obj.topic_id)
        return self._write_layer(
            layer="topic",
            target=summary_obj.topic_id,
            path=path,
            frontmatter=summary_obj.frontmatter(),
            body=summary_obj.summary_text,
            trigger=trigger,
            reason=reason,
            task_id=task_id,
            promoted_to_topic=list(promoted_to_topic or []),
        )

    def refresh_global(
        self,
        summary: GlobalSummary | str | None = None,
        *,
        trigger: RefreshTrigger = RefreshTrigger.GLOBAL_COMPACT,
        reason: str = "",
        task_id: str | None = None,
    ) -> RefreshResult:
        if summary is None:
            summary_obj = self._compose_global_from_topics()
        elif isinstance(summary, str):
            current = self.read_global() or GlobalSummary()
            current.summary_text = summary.strip()
            current.generated_at = self._clock()
            summary_obj = current
        else:
            summary_obj = summary
            summary_obj.generated_at = summary_obj.generated_at or self._clock()

        return self._write_layer(
            layer="global",
            target="current",
            path=self.global_path,
            frontmatter=summary_obj.frontmatter(),
            body=summary_obj.summary_text,
            trigger=trigger,
            reason=reason,
            task_id=task_id,
        )

    # -- trigger-specific entry points (design §1.3) ---------------------

    def refresh_for_cron(
        self,
        source_id: str,
        summary: SourceSummary | str,
        *,
        reason: str = "connector auto-fetch",
        task_id: str | None = None,
    ) -> RefreshResult:
        """Cron / auto-fetch path — *source-only* refresh."""

        return self.refresh_source(
            source_id,
            summary,
            trigger=RefreshTrigger.CRON,
            reason=reason,
            task_id=task_id,
        )

    def refresh_for_telegram_answer(
        self,
        source_id: str,
        topic_id: str,
        *,
        source_summary: SourceSummary | str | None = None,
        topic_summary: TopicSummary | str | None = None,
        reason: str = "telegram answer freshness gate",
        task_id: str | None = None,
    ) -> list[RefreshResult]:
        """Telegram answer path — refresh *only* the relevant source+topic."""

        results: list[RefreshResult] = []
        if source_summary is not None:
            results.append(
                self.refresh_source(
                    source_id,
                    source_summary,
                    trigger=RefreshTrigger.TELEGRAM_ANSWER,
                    reason=reason,
                    task_id=task_id,
                )
            )
        if topic_summary is not None:
            results.append(
                self.refresh_topic(
                    topic_id,
                    topic_summary,
                    trigger=RefreshTrigger.TELEGRAM_ANSWER,
                    reason=reason,
                    task_id=task_id,
                )
            )
        return results

    def refresh_for_task_ledger_change(
        self,
        topic_id: str,
        summary: TopicSummary | str,
        *,
        reason: str = "task ledger change",
        task_id: str | None = None,
    ) -> RefreshResult:
        return self.refresh_topic(
            topic_id,
            summary,
            trigger=RefreshTrigger.TASK_LEDGER_CHANGE,
            reason=reason,
            task_id=task_id,
        )

    def refresh_for_obsidian_user_edit(
        self,
        source_id: str,
        topic_id: str,
        *,
        source_summary: SourceSummary | str | None = None,
        topic_summary: TopicSummary | str | None = None,
        reason: str = "obsidian user edit",
        task_id: str | None = None,
    ) -> list[RefreshResult]:
        results: list[RefreshResult] = []
        if source_summary is not None:
            results.append(
                self.refresh_source(
                    source_id,
                    source_summary,
                    trigger=RefreshTrigger.OBSIDIAN_USER_EDIT,
                    reason=reason,
                    task_id=task_id,
                )
            )
        if topic_summary is not None:
            results.append(
                self.refresh_topic(
                    topic_id,
                    topic_summary,
                    trigger=RefreshTrigger.OBSIDIAN_USER_EDIT,
                    reason=reason,
                    task_id=task_id,
                )
            )
        return results

    def refresh_for_global_compact(
        self,
        *,
        reason: str = "global compact",
        task_id: str | None = None,
    ) -> RefreshResult:
        return self.refresh_global(
            None,
            trigger=RefreshTrigger.GLOBAL_COMPACT,
            reason=reason,
            task_id=task_id,
        )

    # ------------------------------------------------------------------ list

    def list_sources(self) -> list[str]:
        if not self.paths.source_dir.exists():
            return []
        return sorted(p.stem for p in self.paths.source_dir.glob("*.md"))

    def list_topics(self) -> list[str]:
        if not self.paths.topic_dir.exists():
            return []
        return sorted(p.stem for p in self.paths.topic_dir.glob("*.md"))

    # ------------------------------------------------------------------ internals

    def _compose_global_from_topics(self) -> GlobalSummary:
        topics = self.list_topics()
        included: list[str] = []
        snippets: list[str] = []
        for topic_id in topics:
            ts = self.read_topic(topic_id)
            if not ts or not ts.summary_text.strip():
                continue
            included.append(topic_id)
            # 1st-pass loss-less: keep entire body; budget caller's job.
            snippets.append(f"## {topic_id}\n{ts.summary_text.strip()}")
        body = "\n\n".join(snippets) if snippets else "(no topic summaries)"
        return GlobalSummary(
            summary_text=body,
            generated_at=self._clock(),
            included_topics=included,
            excluded_topics=[t for t in topics if t not in included],
        )

    def _write_layer(
        self,
        *,
        layer: str,
        target: str,
        path: Path,
        frontmatter: Mapping[str, object],
        body: str,
        trigger: RefreshTrigger,
        reason: str,
        task_id: str | None,
        promoted_to_topic: list[str] | None = None,
    ) -> RefreshResult:
        before_text = _read_text(path)
        before_hash = compute_hash(before_text)

        rendered = _render_frontmatter(frontmatter) + (body.rstrip() + "\n")
        _atomic_write(path, rendered)

        after_hash = compute_hash(rendered)
        ts = self._clock()
        result = RefreshResult(
            layer=layer,
            target=target,
            trigger=trigger,
            reason=reason,
            before_hash=before_hash,
            after_hash=after_hash,
            path=path,
            task_id=task_id or _new_task_id(self._task_id_clock),
            timestamp=ts,
            promoted_to_topic=list(promoted_to_topic or []),
        )
        self._append_ledger(result)
        return result

    def _append_ledger(self, result: RefreshResult) -> None:
        # R5 M4: delegate to scripts.state_io.append_jsonl_atomic so the
        # ledger-write semantics (cross-platform locking + fsync) are
        # identical to every other Helm writer. The previous inline
        # fcntl.flock block omitted ``os.fsync`` which meant a crash
        # between ``fh.flush`` and the kernel writeback could lose the
        # most-recent refresh row. See module docstring for the
        # dependency-contract rationale.
        entry = result.to_ledger_entry()
        from scripts.state_io import append_jsonl_atomic
        append_jsonl_atomic(self.ledger_path, entry)


def _new_task_id(clock=None) -> str:
    """Generate a fresh task id.

    ``clock`` is an optional callable returning a numeric epoch-like
    value (defaults to :func:`time.time`). Threaded through
    :class:`MemoryTree` so tests can pin the task-id stream.
    """
    fn = clock or time.time
    return f"memtree-{int(fn())}-{uuid.uuid4().hex[:8]}"


__all__ = [
    "DEFAULT_LEDGER",
    "DEFAULT_ROOT",
    "GlobalSummary",
    "KNOWN_SOURCE_IDS",
    "KNOWN_TOPIC_IDS",
    "LEDGER_KIND",
    "MemoryTree",
    "MemoryTreePaths",
    "RefreshResult",
    "RefreshTrigger",
    "SourceSummary",
    "TopicSummary",
    "compute_hash",
    "openclaw_mirror_paths",
]
