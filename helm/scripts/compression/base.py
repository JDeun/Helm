"""Base primitives for the Compression Profile Registry.

This module is intentionally self-contained: only the Python standard
library is used. No ``regex`` / ``grapheme`` third-party dependency is
required so that the registry can run inside minimal sandboxes
(briefing cron, OpenClaw connectors, Helm CLI).

See ``compression/__init__.py`` for the architectural contract.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Iterable

# ---------------------------------------------------------------------------
# Unicode helpers
# ---------------------------------------------------------------------------


def nfc_normalize(text: str) -> str:
    """Return NFC-normalized text. Other normalization forms are forbidden
    by §4.2 (CJK / Hangul jamo composition safety).

    ``None`` and non-``str`` inputs are coerced to ``str`` (or empty
    string) so this is safe to call on raw connector payloads.
    """
    if text is None:
        return ""
    if not isinstance(text, str):
        text = str(text)
    return unicodedata.normalize("NFC", text)


# Regional indicator + variation selectors + ZWJ + Hangul jamo handling.
# We approximate a grapheme cluster boundary using:
#   * extending characters: category Mn, Mc, Me
#   * Hangul jamo (V, T) attaching to a preceding L/V/LV/LVT
#   * ZWJ (U+200D) sequences with subsequent extended pictographic
#   * variation selectors (U+FE00..U+FE0F, U+E0100..U+E01EF)
#   * regional indicator pairs (flags)
#
# This is a pragmatic subset of UAX #29 sufficient for Korean / emoji
# safety. It is not a complete UAX #29 implementation, but it never
# splits inside any cluster that the design explicitly calls out.


def _is_extend(ch: str) -> bool:
    cp = ord(ch)
    cat = unicodedata.category(ch)
    if cat in ("Mn", "Mc", "Me"):
        return True
    if cp == 0x200D:  # ZWJ
        return True
    if 0xFE00 <= cp <= 0xFE0F:
        return True
    if 0xE0100 <= cp <= 0xE01EF:
        return True
    if 0x1F3FB <= cp <= 0x1F3FF:  # emoji skin tone modifiers
        return True
    return False


def _is_regional_indicator(ch: str) -> bool:
    cp = ord(ch)
    return 0x1F1E6 <= cp <= 0x1F1FF


def _is_hangul_l(ch: str) -> bool:
    return 0x1100 <= ord(ch) <= 0x115F or 0xA960 <= ord(ch) <= 0xA97C


def _is_hangul_v(ch: str) -> bool:
    return 0x1160 <= ord(ch) <= 0x11A7 or 0xD7B0 <= ord(ch) <= 0xD7C6


def _is_hangul_t(ch: str) -> bool:
    return 0x11A8 <= ord(ch) <= 0x11FF or 0xD7CB <= ord(ch) <= 0xD7FB


def _is_hangul_lv(ch: str) -> bool:
    cp = ord(ch)
    if not (0xAC00 <= cp <= 0xD7A3):
        return False
    return (cp - 0xAC00) % 28 == 0


def _is_hangul_lvt(ch: str) -> bool:
    cp = ord(ch)
    if not (0xAC00 <= cp <= 0xD7A3):
        return False
    return (cp - 0xAC00) % 28 != 0


def grapheme_clusters(text: str) -> list[str]:
    """Split *text* into a list of grapheme clusters.

    Implements the rules of §4.2 sufficient to keep Hangul syllables,
    emoji ZWJ sequences, regional indicator flags and combining marks
    intact. Always NFC-normalizes the input first.
    """
    text = nfc_normalize(text)
    if not text:
        return []
    clusters: list[str] = []
    buf: list[str] = []

    def flush() -> None:
        if buf:
            clusters.append("".join(buf))
            buf.clear()

    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if not buf:
            buf.append(ch)
            i += 1
            continue
        prev = buf[-1]
        # 1. Extend / ZWJ / variation selector / skin tone -> stay in cluster.
        if _is_extend(ch):
            buf.append(ch)
            i += 1
            continue
        # 2. After ZWJ, attach the next pictographic-ish glyph.
        if prev == "‍":
            buf.append(ch)
            i += 1
            continue
        # 3. Hangul L/V/LV/LVT rules.
        if _is_hangul_l(prev) and (_is_hangul_l(ch) or _is_hangul_v(ch) or _is_hangul_lv(ch) or _is_hangul_lvt(ch)):
            buf.append(ch)
            i += 1
            continue
        if (_is_hangul_lv(prev) or _is_hangul_v(prev)) and (_is_hangul_v(ch) or _is_hangul_t(ch)):
            buf.append(ch)
            i += 1
            continue
        if (_is_hangul_lvt(prev) or _is_hangul_t(prev)) and _is_hangul_t(ch):
            buf.append(ch)
            i += 1
            continue
        # 4. Regional indicator pair (flag): two RIs form one cluster.
        if (
            _is_regional_indicator(prev)
            and _is_regional_indicator(ch)
            and len(buf) == 1
        ):
            buf.append(ch)
            i += 1
            continue
        # 5. \r\n stays together.
        if prev == "\r" and ch == "\n":
            buf.append(ch)
            i += 1
            continue
        flush()
        buf.append(ch)
        i += 1
    flush()
    return clusters


def grapheme_len(text: str) -> int:
    """Return the number of grapheme clusters in *text*."""
    return len(grapheme_clusters(text))


def grapheme_truncate(text: str, max_clusters: int, ellipsis: str = "…") -> str:
    """Truncate *text* to at most ``max_clusters`` grapheme clusters.

    If truncation occurs and ``ellipsis`` is non-empty, the ellipsis is
    appended (and counted toward the cluster budget). Never splits a
    grapheme cluster — this is the §4.2 CJK safety guarantee.
    """
    if max_clusters <= 0:
        return ""
    clusters = grapheme_clusters(text)
    if len(clusters) <= max_clusters:
        return "".join(clusters)
    if not ellipsis:
        return "".join(clusters[:max_clusters])
    ellipsis_len = len(grapheme_clusters(ellipsis))
    keep = max(0, max_clusters - ellipsis_len)
    return "".join(clusters[:keep]) + ellipsis


# ---------------------------------------------------------------------------
# Fenced code blocks
# ---------------------------------------------------------------------------

# Match ``` or ~~~ fenced blocks (markdown-style). We must preserve the
# fence char count and language tag verbatim per §4.2.
_FENCE_RE = re.compile(
    r"(?P<fence>^[ \t]*(?P<marker>```+|~~~+)[^\n]*(?:\n.*?)?\n?[ \t]*(?P=marker)[ \t]*(?:$|\n))",
    re.DOTALL | re.MULTILINE,
)


def extract_fenced_blocks(text: str) -> tuple[str, list[str]]:
    """Replace fenced code blocks with sentinel placeholders.

    Returns ``(redacted_text, blocks)``. ``blocks[i]`` is the original
    fenced block (including its fences); the placeholder
    ``HELM_FENCE_{i}`` appears in ``redacted_text`` exactly
    where the block used to be. Use :func:`restore_fenced_blocks` to
    reinsert them after manipulation.

    Inline code (``` `foo` ```) is *not* extracted: §4.2 forbids
    *modifying* inline code but does not require placeholders.
    """
    blocks: list[str] = []

    def _replace(match: re.Match[str]) -> str:
        idx = len(blocks)
        blocks.append(match.group("fence"))
        return f"HELM_FENCE_{idx}"

    redacted = _FENCE_RE.sub(_replace, text)
    return redacted, blocks


def restore_fenced_blocks(text: str, blocks: list[str]) -> str:
    """Inverse of :func:`extract_fenced_blocks`."""
    for idx, block in enumerate(blocks):
        text = text.replace(f"HELM_FENCE_{idx}", block)
    return text


# ---------------------------------------------------------------------------
# URLs
# ---------------------------------------------------------------------------

# Match http(s) URLs, including query strings and fragments. Trailing
# punctuation (.,;:) is excluded because it's almost never part of the
# URL in prose.
_URL_RE = re.compile(
    r"https?://[^\s<>\"']+",
)


def extract_urls(text: str) -> list[str]:
    """Return every distinct http(s) URL in *text*, in first-seen order.

    URLs are returned *verbatim* — no shortening, no tracking-param
    stripping — to satisfy §4.2's "풀 URL 보존" rule. Trailing sentence
    punctuation is trimmed (``. , ; : ) ] }``).
    """
    seen: list[str] = []
    seen_set: set[str] = set()
    for match in _URL_RE.finditer(text):
        url = match.group(0).rstrip(".,;:)]}")
        if url not in seen_set:
            seen.append(url)
            seen_set.add(url)
    return seen


# ---------------------------------------------------------------------------
# Korean honorific preservation
# ---------------------------------------------------------------------------

# Honorific suffixes we must never strip / replace. The list is
# intentionally short and conservative: -님 and -씨 are the two most
# common in Korean address forms, and "주인님" is the project-specific
# fixed term called out in §4.2.
KOREAN_HONORIFICS: tuple[str, ...] = ("님", "씨", "주인님")


def contains_korean_honorific(text: str) -> bool:
    """Return ``True`` if *text* contains any of :data:`KOREAN_HONORIFICS`."""
    text = nfc_normalize(text)
    return any(token in text for token in KOREAN_HONORIFICS)


# ---------------------------------------------------------------------------
# Stage outputs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Stage1Output:
    """Loss-less canonical form. Used for factual assertions."""

    profile_id: str
    text: str
    facts: dict[str, Any] = field(default_factory=dict)
    urls: list[str] = field(default_factory=list)
    code_blocks: list[str] = field(default_factory=list)
    raw_token_estimate: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "stage": 1,
            "text": self.text,
            "facts": dict(self.facts),
            "urls": list(self.urls),
            "code_blocks": list(self.code_blocks),
            "raw_token_estimate": self.raw_token_estimate,
        }


@dataclass(frozen=True)
class Stage2Output:
    """Judgment summary. Display-only. NEVER a factual ground truth."""

    profile_id: str
    summary: str
    budget_clusters: int
    display_only: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "stage": 2,
            "summary": self.summary,
            "budget_clusters": self.budget_clusters,
            "display_only": self.display_only,
        }


@dataclass(frozen=True)
class ProfileResult:
    """Convenience pair returned by :meth:`CompressionProfile.run`."""

    stage1: Stage1Output
    stage2: Stage2Output


# ---------------------------------------------------------------------------
# Base profile
# ---------------------------------------------------------------------------


class CompressionProfile:
    """Abstract base for §4 profiles.

    Concrete profiles override :meth:`canonicalize` and
    :meth:`summarize`. The default implementations enforce the §4.2
    invariants:

    * NFC normalize input
    * extract & reinsert fenced code blocks (never compressed in stage 1)
    * collect URLs verbatim
    * truncate stage 2 in grapheme cluster units only
    """

    #: Unique profile identifier, recorded in the task ledger.
    profile_id: str = "abstract"

    #: Conservative default stage 2 budget (grapheme clusters).
    default_budget_clusters: int = 800

    # Subclasses MAY override to register additional URL patterns.
    accepts_input_kinds: tuple[str, ...] = ()

    def matches(self, input_kind: str, url: str | None = None) -> bool:
        """Return ``True`` if this profile handles ``(input_kind, url)``."""
        return input_kind in self.accepts_input_kinds

    # --- Stage 1 -----------------------------------------------------------

    def canonicalize(self, raw: Any) -> Stage1Output:
        """Produce the loss-less stage 1 canonical form.

        The default implementation:

        1. Coerces ``raw`` to text via :meth:`_coerce_text`.
        2. NFC normalizes.
        3. Extracts fenced code blocks (kept verbatim, never compressed).
        4. Collects URLs verbatim.
        5. Returns the body with fences restored.

        Subclasses should call ``super().canonicalize(raw)`` and then
        augment :attr:`Stage1Output.facts` with profile-specific metadata
        (published_at, repo signals, RSS items, etc.).
        """
        text = nfc_normalize(self._coerce_text(raw))
        redacted, blocks = extract_fenced_blocks(text)
        urls = extract_urls(text)
        body = restore_fenced_blocks(redacted, blocks)
        return Stage1Output(
            profile_id=self.profile_id,
            text=body,
            facts=self._extract_facts(raw, body),
            urls=urls,
            code_blocks=blocks,
            raw_token_estimate=_estimate_tokens(body),
        )

    def _coerce_text(self, raw: Any) -> str:
        if isinstance(raw, str):
            return raw
        if isinstance(raw, bytes):
            return raw.decode("utf-8", errors="replace")
        if isinstance(raw, dict):
            # Prefer common body keys, then fall back to a stable repr.
            for key in ("body", "text", "content", "html", "markdown", "snippet"):
                if key in raw and isinstance(raw[key], str):
                    return raw[key]
            return "\n".join(f"{k}: {v}" for k, v in raw.items())
        if isinstance(raw, list):
            return "\n".join(self._coerce_text(item) for item in raw)
        return "" if raw is None else str(raw)

    def _extract_facts(self, raw: Any, body: str) -> dict[str, Any]:
        """Hook for subclasses to inject profile-specific facts."""
        facts: dict[str, Any] = {}
        if isinstance(raw, dict):
            for key in ("title", "url", "published_at", "author", "lang"):
                if key in raw:
                    facts[key] = raw[key]
        return facts

    # --- Stage 2 -----------------------------------------------------------

    def summarize(
        self,
        canonical: Stage1Output,
        budget: int | None = None,
    ) -> Stage2Output:
        """Produce the judgment summary (display only).

        The default implementation:

        * Replaces every fenced code block with ``[code block #i]`` so it
          is never re-rendered or modified (§4.2).
        * Preserves URLs and Korean honorifics verbatim.
        * Truncates on grapheme cluster boundaries.
        """
        budget = budget or self.default_budget_clusters
        body = canonical.text
        # Replace every fenced block in the rendered body with a stable
        # reference. We rely on the fact that ``canonical.code_blocks``
        # appears in the body in order.
        for idx, block in enumerate(canonical.code_blocks):
            body = body.replace(block, f"[code block #{idx + 1}]", 1)
        body = _collapse_whitespace(body)
        truncated = grapheme_truncate(body, budget)
        return Stage2Output(
            profile_id=self.profile_id,
            summary=truncated,
            budget_clusters=budget,
        )

    # --- Combined ----------------------------------------------------------

    def run(self, raw: Any, budget: int | None = None) -> ProfileResult:
        stage1 = self.canonicalize(raw)
        stage2 = self.summarize(stage1, budget=budget)
        return ProfileResult(stage1=stage1, stage2=stage2)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


# Approximate tokens-per-grapheme-cluster ratio for CJK/Korean prose,
# matching GPT-4 family tokenizer behaviour closely enough for sanity
# checks. Pulled out of the function so it can be tuned in one place.
# Heuristic only — never used for factual assertions.
_CJK_TOKENS_PER_CLUSTER: float = 1.6


def _estimate_tokens(text: str) -> int:
    """Rough token count for budget heuristics.

    Uses :data:`_CJK_TOKENS_PER_CLUSTER` as the divisor. This is *only*
    a heuristic and never feeds factual assertions.
    """
    if not text:
        return 0
    clusters = grapheme_len(text)
    return max(1, int(clusters / _CJK_TOKENS_PER_CLUSTER))


def _collapse_whitespace(text: str) -> str:
    """Collapse runs of >2 blank lines and trim trailing spaces.

    Fenced code blocks must be restored *before* calling this — the
    function is safe only on prose.
    """
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def iter_subclasses(cls: type) -> Iterable[type]:
    """Yield all subclasses recursively. Used by the registry."""
    for sub in cls.__subclasses__():
        yield sub
        yield from iter_subclasses(sub)
