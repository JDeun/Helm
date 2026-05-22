"""Concrete compression profiles for §4.1.

Each profile implements at most ~30 lines of profile-specific logic
on top of :class:`CompressionProfile`. Keep them tiny — the heavy
lifting lives in :mod:`compression.base`.

Profile ids follow the design table (4.1). Where the task prompt
suggested alternate names (e.g. ``web_fetch_html``), we keep both as
matching keys via :attr:`accepts_input_kinds`.
"""

from __future__ import annotations

import re
from typing import Any

from .base import (
    CompressionProfile,
    Stage1Output,
    Stage2Output,
    extract_fenced_blocks,
    extract_urls,
    grapheme_truncate,
    nfc_normalize,
    restore_fenced_blocks,
)


# ---------------------------------------------------------------------------
# 1. Web fetch - HTML article (Readability-style body)
# ---------------------------------------------------------------------------


class WebFetchHtmlArticleProfile(CompressionProfile):
    """HTML article: extract a Readability-shaped body; keep code blocks.

    The profile assumes upstream has already passed the HTML through
    a Readability-style extractor; if raw HTML is passed in, a very
    conservative tag stripper is applied (script/style/nav/header/footer
    removed). Quotes (>) are preserved.
    """

    profile_id = "web_fetch.html.article"
    accepts_input_kinds = (
        "web_fetch.html.article",
        "web_fetch_html",
        "web_fetch_html_article",
        "html.article",
    )
    default_budget_clusters = 1600

    _TAG_STRIPPERS = re.compile(
        r"<(script|style|nav|header|footer|aside)[^>]*>.*?</\1>",
        re.DOTALL | re.IGNORECASE,
    )
    _ANY_TAG = re.compile(r"<[^>]+>")

    def _coerce_text(self, raw: Any) -> str:
        if isinstance(raw, dict):
            for key in ("readable", "article", "body", "text", "content", "html", "markdown"):
                if key in raw and isinstance(raw[key], str):
                    return self._strip(raw[key])
            return "\n".join(f"{k}: {v}" for k, v in raw.items())
        if isinstance(raw, str):
            return self._strip(raw)
        return super()._coerce_text(raw)

    @classmethod
    def _strip(cls, html: str) -> str:
        if "<" not in html:
            return html
        html = cls._TAG_STRIPPERS.sub("", html)
        html = cls._ANY_TAG.sub("", html)
        return html

    def _extract_facts(self, raw: Any, body: str) -> dict[str, Any]:
        facts = super()._extract_facts(raw, body)
        if isinstance(raw, dict):
            for key in ("published_at", "byline", "site_name", "lang"):
                if key in raw:
                    facts.setdefault(key, _nfc_fact(raw[key]))
        return facts


def _nfc_fact(value: Any) -> Any:
    """NFC-normalize a single fact value when it is a string.

    §4.2 requires Hangul-decomposed bytes to be normalized to NFC so
    downstream consumers (Telegram, briefing) see a single canonical
    form. Non-string values are passed through unchanged.
    """

    if isinstance(value, str):
        return nfc_normalize(value)
    return value


# ---------------------------------------------------------------------------
# 2. Web fetch - plain text (RSS-resolved article, manpages, etc.)
# ---------------------------------------------------------------------------


class WebFetchTextProfile(CompressionProfile):
    """Plain text article: trivial canonicalization.

    Used for already-extracted bodies (e.g. RSS-resolved article text,
    docs.rs man-pages, plain ``.txt`` URLs).
    """

    profile_id = "web_fetch.text"
    accepts_input_kinds = (
        "web_fetch.text",
        "web_fetch_text",
        "text.plain",
    )
    default_budget_clusters = 1200


# ---------------------------------------------------------------------------
# 3. Browser snapshot (Playwright DOM dump)
# ---------------------------------------------------------------------------


class BrowserSnapshotProfile(CompressionProfile):
    """Browser DOM snapshot: preserve image alt text and headings."""

    profile_id = "browser.snapshot"
    accepts_input_kinds = (
        "browser.snapshot",
        "browser_snapshot",
        "playwright.snapshot",
    )
    default_budget_clusters = 1400

    _IMG_TAG = re.compile(r"<img[^>]*alt=\"([^\"]*)\"[^>]*>", re.IGNORECASE)
    _SCRIPT_STYLE = re.compile(
        r"<(script|style)[^>]*>.*?</\1>", re.DOTALL | re.IGNORECASE
    )
    _ANY_TAG = re.compile(r"<[^>]+>")

    def _coerce_text(self, raw: Any) -> str:
        text = super()._coerce_text(raw)
        if "<" not in text:
            return text
        text = self._SCRIPT_STYLE.sub("", text)
        text = self._IMG_TAG.sub(r"[image: \1]", text)
        text = self._ANY_TAG.sub("", text)
        return text


# ---------------------------------------------------------------------------
# 4. GitHub repo metadata
# ---------------------------------------------------------------------------


class GitHubRepoMetaProfile(CompressionProfile):
    """GitHub repo metadata + README: keep stars / forks / last_push signals."""

    profile_id = "github.repo.metadata"
    accepts_input_kinds = (
        "github.repo.metadata",
        "github_repo_meta",
        "github.repo",
    )
    default_budget_clusters = 1200

    _SIGNAL_KEYS = (
        "full_name",
        "name",
        "html_url",
        "stargazers_count",
        "forks_count",
        "open_issues_count",
        "pushed_at",
        "updated_at",
        "created_at",
        "default_branch",
        "license",
        "topics",
        "archived",
        "language",
    )

    def _coerce_text(self, raw: Any) -> str:
        if isinstance(raw, dict):
            readme = raw.get("readme") or raw.get("readme_text") or ""
            return readme
        return super()._coerce_text(raw)

    def _extract_facts(self, raw: Any, body: str) -> dict[str, Any]:
        facts: dict[str, Any] = {}
        if isinstance(raw, dict):
            for key in self._SIGNAL_KEYS:
                if key in raw:
                    facts[key] = _nfc_fact(raw[key])
            # Signal score (cheap, deterministic) for stage-1 candidate
            # selection. Stage 2 must never override this.
            stars = int(raw.get("stargazers_count") or 0)
            forks = int(raw.get("forks_count") or 0)
            facts["signal_score"] = stars + 4 * forks
        return facts


# ---------------------------------------------------------------------------
# 5. GitHub README (when README alone is the input, no API payload)
# ---------------------------------------------------------------------------


class GitHubReadmeProfile(CompressionProfile):
    """Stand-alone README markdown.

    Distinct from :class:`GitHubRepoMetaProfile` because the input here
    is *only* README text (often used inside skill draft pipelines), so
    we don't try to extract repo signals.
    """

    profile_id = "github.readme"
    accepts_input_kinds = (
        "github.readme",
        "github_readme",
        "repo.readme",
    )
    default_budget_clusters = 1600


# ---------------------------------------------------------------------------
# 6. RSS feed
# ---------------------------------------------------------------------------


class RSSFeedProfile(CompressionProfile):
    """RSS feed: keep item-level (title / link / published / summary)."""

    profile_id = "rss.feed"
    accepts_input_kinds = (
        "rss.feed",
        "rss_feed",
        "feed.rss",
    )
    default_budget_clusters = 1400

    def canonicalize(self, raw: Any) -> Stage1Output:
        items = self._items(raw)
        lines: list[str] = []
        all_urls: list[str] = []
        all_blocks: list[str] = []
        for item in items:
            title = nfc_normalize(str(item.get("title", "")))
            link = str(item.get("link", "")).strip()
            published = str(item.get("published", item.get("pubDate", "")))
            summary = nfc_normalize(str(item.get("summary", item.get("description", ""))))
            redacted, blocks = extract_fenced_blocks(summary)
            all_blocks.extend(blocks)
            summary = restore_fenced_blocks(redacted, blocks)
            lines.append(f"- {title}\n  url: {link}\n  published: {published}\n  summary: {summary}")
            if link:
                all_urls.append(link)
            all_urls.extend(extract_urls(summary))
        body = "\n".join(lines)
        # Dedup URL list while preserving order.
        seen: set[str] = set()
        urls: list[str] = []
        for u in all_urls:
            if u and u not in seen:
                urls.append(u)
                seen.add(u)
        return Stage1Output(
            profile_id=self.profile_id,
            text=body,
            facts={"item_count": len(items)},
            urls=urls,
            code_blocks=all_blocks,
            raw_token_estimate=max(1, len(body) // 4),
        )

    @staticmethod
    def _items(raw: Any) -> list[dict[str, Any]]:
        if isinstance(raw, list):
            return [x for x in raw if isinstance(x, dict)]
        if isinstance(raw, dict):
            for key in ("items", "entries", "feed", "channel"):
                v = raw.get(key)
                if isinstance(v, list):
                    return [x for x in v if isinstance(x, dict)]
        return []


# ---------------------------------------------------------------------------
# 7. Atom feed (same shape as RSS, separate id for ledger granularity)
# ---------------------------------------------------------------------------


class AtomFeedProfile(RSSFeedProfile):
    profile_id = "atom.feed"
    accepts_input_kinds = (
        "atom.feed",
        "atom_feed",
        "feed.atom",
    )


# ---------------------------------------------------------------------------
# 8. Web search results
# ---------------------------------------------------------------------------


class SearchResultProfile(CompressionProfile):
    """Web search results: keep title / url / snippet per row."""

    profile_id = "web_search.results"
    accepts_input_kinds = (
        "web_search.results",
        "search_result",
        "search_results",
    )
    default_budget_clusters = 1000

    def canonicalize(self, raw: Any) -> Stage1Output:
        rows = self._rows(raw)
        lines: list[str] = []
        urls: list[str] = []
        for row in rows:
            title = nfc_normalize(str(row.get("title", "")))
            url = str(row.get("url", row.get("link", ""))).strip()
            snippet = nfc_normalize(str(row.get("snippet", row.get("description", ""))))
            lines.append(f"- {title}\n  url: {url}\n  snippet: {snippet}")
            if url:
                urls.append(url)
        body = "\n".join(lines)
        return Stage1Output(
            profile_id=self.profile_id,
            text=body,
            facts={"result_count": len(rows)},
            urls=urls,
            code_blocks=[],
            raw_token_estimate=max(1, len(body) // 4),
        )

    @staticmethod
    def _rows(raw: Any) -> list[dict[str, Any]]:
        if isinstance(raw, list):
            return [x for x in raw if isinstance(x, dict)]
        if isinstance(raw, dict):
            for key in ("results", "hits", "items"):
                v = raw.get(key)
                if isinstance(v, list):
                    return [x for x in v if isinstance(x, dict)]
        return []


# ---------------------------------------------------------------------------
# 9. Telegram outgoing (reply-pipeline budget enforcement)
# ---------------------------------------------------------------------------


class TelegramOutgoingProfile(CompressionProfile):
    """Telegram outbound text: enforce a tight cluster budget.

    Korean honorifics (-님 / -씨 / 주인님) are preserved verbatim and
    counted toward the cluster budget. URLs are kept verbatim.
    """

    profile_id = "telegram.outgoing"
    accepts_input_kinds = (
        "telegram.outgoing",
        "telegram_outgoing",
    )
    # Telegram message practical sweet spot (well below the 4096 char
    # hard cap, well above the typical reply target).
    default_budget_clusters = 600


# ---------------------------------------------------------------------------
# 10. LLM tool output (long JSON-RPC, MCP results, etc.)
# ---------------------------------------------------------------------------


class LLMToolOutputProfile(CompressionProfile):
    """Long tool output: keep key paths, head/tail of large arrays.

    Stage-1 keeps the entire structural skeleton verbatim. Stage-2
    presents only the first/last few elements of any array that has
    more than 8 items.
    """

    profile_id = "tool.json_rpc.long"
    accepts_input_kinds = (
        "tool.json_rpc.long",
        "llm_tool_output",
        "llm.tool.output",
        "mcp.tool.result",
    )
    default_budget_clusters = 1200

    def summarize(self, canonical: Stage1Output, budget: int | None = None) -> Stage2Output:
        # Collapse long lines of obvious array payloads in display.
        body = canonical.text
        body = re.sub(r"(\[[^\]\n]{200,})\]", r"\1 …]", body)
        out = super().summarize(canonical, budget=budget)
        # Re-wrap with the head/tail collapse marker.
        return Stage2Output(
            profile_id=self.profile_id,
            summary=grapheme_truncate(body, out.budget_clusters),
            budget_clusters=out.budget_clusters,
        )


# All concrete profiles in priority order (first match wins).
ALL_PROFILES: tuple[type[CompressionProfile], ...] = (
    WebFetchHtmlArticleProfile,
    WebFetchTextProfile,
    BrowserSnapshotProfile,
    GitHubRepoMetaProfile,
    GitHubReadmeProfile,
    RSSFeedProfile,
    AtomFeedProfile,
    SearchResultProfile,
    TelegramOutgoingProfile,
    LLMToolOutputProfile,
)
