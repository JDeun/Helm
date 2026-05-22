# tests/test_compression.py
"""Tests for the §4 Compression Profile Registry.

Covers the §4.2 preservation rules: CJK NFC + grapheme safety, fenced
code blocks, URLs, emoji, Korean honorifics; plus profile registry
resolution and stage-1 / stage-2 contract.
"""
from __future__ import annotations

import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.compression import (
    CompressionRegistry,
    extract_fenced_blocks,
    extract_urls,
    grapheme_clusters,
    grapheme_truncate,
    nfc_normalize,
    resolve,
    restore_fenced_blocks,
)
from scripts.compression.profiles import (
    AtomFeedProfile,
    BrowserSnapshotProfile,
    GitHubReadmeProfile,
    GitHubRepoMetaProfile,
    LLMToolOutputProfile,
    RSSFeedProfile,
    SearchResultProfile,
    TelegramOutgoingProfile,
    WebFetchHtmlArticleProfile,
    WebFetchTextProfile,
)


# ---------------------------------------------------------------------------
# Unicode primitives
# ---------------------------------------------------------------------------


def test_nfc_normalize_decomposed_hangul() -> None:
    # NFD: ㄱ + ㅏ + ㅁ -> "감" after NFC.
    decomposed = unicodedata.normalize("NFD", "감자")
    assert decomposed != "감자"
    assert nfc_normalize(decomposed) == "감자"


def test_grapheme_clusters_hangul_basic() -> None:
    clusters = grapheme_clusters("주인님")
    assert clusters == ["주", "인", "님"]


def test_grapheme_clusters_emoji_zwj_family() -> None:
    # 👨‍👩‍👧 is a single grapheme cluster (ZWJ joined).
    family = "👨‍👩‍👧"
    clusters = grapheme_clusters(family)
    assert clusters == [family]


def test_grapheme_clusters_flag() -> None:
    flag_kr = "\U0001F1F0\U0001F1F7"  # KR flag (RI K + RI R)
    clusters = grapheme_clusters(flag_kr)
    assert clusters == [flag_kr]


def test_grapheme_clusters_skin_tone() -> None:
    waving = "👋\U0001F3FD"  # waving hand + medium skin tone
    clusters = grapheme_clusters(waving)
    assert clusters == [waving]


def test_grapheme_truncate_does_not_split_korean_syllable() -> None:
    text = "주인님, 안녕하세요"  # 10 clusters incl. punctuation/space
    out = grapheme_truncate(text, 3)
    # Must end on a complete cluster boundary; ellipsis included by default.
    assert out.endswith("…")
    # Reconstructible characters must all be complete syllables.
    for ch in out.rstrip("…"):
        # No bare jamo leaked into the output.
        assert not (0x1100 <= ord(ch) <= 0x11FF), f"bare jamo {ch!r}"


def test_grapheme_truncate_keeps_korean_honorific() -> None:
    text = "주인님께서는 오늘도 멋지십니다"
    out = grapheme_truncate(text, 5)
    # The honorific should fit inside the first five clusters and be
    # present verbatim (not stripped by truncation).
    assert "주인님" in out


def test_grapheme_truncate_no_truncation_when_within_budget() -> None:
    text = "짧은 메시지"
    assert grapheme_truncate(text, 100) == text


# ---------------------------------------------------------------------------
# Fenced code blocks
# ---------------------------------------------------------------------------


def test_extract_and_restore_fenced_blocks_roundtrip() -> None:
    src = (
        "intro paragraph\n\n"
        "```python\n"
        "def f(x):\n"
        "    return x + 1\n"
        "```\n\n"
        "outro line"
    )
    redacted, blocks = extract_fenced_blocks(src)
    assert len(blocks) == 1
    assert "```python" not in redacted
    assert "HELM_FENCE_0" in redacted
    restored = restore_fenced_blocks(redacted, blocks)
    assert restored == src


def test_stage1_preserves_fenced_code_block_verbatim() -> None:
    src = (
        "안녕하세요 주인님,\n\n"
        "```python\n"
        "print('주인님, 안녕!')  # 한국어 주석\n"
        "```\n"
    )
    profile = WebFetchTextProfile()
    out = profile.canonicalize(src)
    assert "print('주인님, 안녕!')" in out.text
    assert out.code_blocks
    assert "한국어 주석" in out.code_blocks[0]


def test_stage2_replaces_fenced_block_with_marker_not_inline_edit() -> None:
    src = (
        "Intro.\n\n"
        "```python\n"
        "x = 1\n"
        "```\n\n"
        "Outro."
    )
    profile = WebFetchTextProfile()
    canonical = profile.canonicalize(src)
    summary = profile.summarize(canonical)
    assert "[code block #1]" in summary.summary
    # Stage 2 must not contain the raw code body (we replaced it).
    assert "x = 1" not in summary.summary


# ---------------------------------------------------------------------------
# URLs
# ---------------------------------------------------------------------------


def test_extract_urls_keeps_full_url() -> None:
    src = (
        "see https://example.com/path?utm_source=foo&id=42#frag for details, "
        "and also http://kor.example.kr/한글."
    )
    urls = extract_urls(src)
    assert "https://example.com/path?utm_source=foo&id=42#frag" in urls
    # Trailing period removed but path kept intact.
    assert any(u.startswith("http://kor.example.kr/") for u in urls)


def test_stage1_collects_urls_verbatim_no_shortening() -> None:
    raw = (
        "주인님께,\n"
        "참고 링크: https://github.com/JDeun/Helm?tab=readme-ov-file#install\n"
    )
    profile = WebFetchTextProfile()
    out = profile.canonicalize(raw)
    assert out.urls == [
        "https://github.com/JDeun/Helm?tab=readme-ov-file#install"
    ]
    assert "https://github.com/JDeun/Helm?tab=readme-ov-file#install" in out.text


# ---------------------------------------------------------------------------
# Emoji preservation
# ---------------------------------------------------------------------------


def test_stage1_preserves_emoji_including_zwj_and_skin_tone() -> None:
    raw = "ship it 🚀 with the family 👨‍👩‍👧 — wave 👋\U0001F3FD!"
    profile = WebFetchTextProfile()
    out = profile.canonicalize(raw)
    assert "🚀" in out.text
    assert "👨‍👩‍👧" in out.text
    assert "👋\U0001F3FD" in out.text


def test_stage2_truncation_does_not_split_emoji_zwj() -> None:
    raw = "👨‍👩‍👧 a b c d e f g h"
    profile = WebFetchTextProfile()
    canonical = profile.canonicalize(raw)
    summary = profile.summarize(canonical, budget=3)
    # The family emoji is one cluster: it should be either fully kept
    # or fully dropped, but never split between man / woman / girl.
    assert "👨‍👩" not in summary.summary.replace("👨‍👩‍👧", "")


# ---------------------------------------------------------------------------
# Korean honorifics preservation
# ---------------------------------------------------------------------------


def test_korean_honorifics_preserved_in_stage1_and_stage2() -> None:
    raw = "주인님, 김 부장님과 박 씨가 회의를 요청했습니다."
    profile = TelegramOutgoingProfile()
    canonical = profile.canonicalize(raw)
    assert "주인님" in canonical.text
    assert "부장님" in canonical.text
    assert " 씨" in canonical.text
    summary = profile.summarize(canonical, budget=200)
    assert "주인님" in summary.summary
    assert "부장님" in summary.summary


# ---------------------------------------------------------------------------
# Registry resolution
# ---------------------------------------------------------------------------


def test_registry_resolves_each_documented_alias() -> None:
    registry = CompressionRegistry()
    cases = {
        "web_fetch.html.article": WebFetchHtmlArticleProfile,
        "web_fetch_html": WebFetchHtmlArticleProfile,
        "web_fetch_text": WebFetchTextProfile,
        "browser_snapshot": BrowserSnapshotProfile,
        "github_repo_meta": GitHubRepoMetaProfile,
        "github_readme": GitHubReadmeProfile,
        "rss_feed": RSSFeedProfile,
        "atom_feed": AtomFeedProfile,
        "search_result": SearchResultProfile,
        "telegram_outgoing": TelegramOutgoingProfile,
        "llm_tool_output": LLMToolOutputProfile,
    }
    for input_kind, expected in cases.items():
        profile = registry.resolve(input_kind)
        assert isinstance(profile, expected), f"{input_kind!r} -> {profile!r}"


def test_registry_has_ten_distinct_profiles() -> None:
    registry = CompressionRegistry()
    ids = {p.profile_id for p in registry.profiles()}
    # The design enumerates 10 profile families.
    assert len(ids) == 10


def test_registry_strict_raises_on_unknown() -> None:
    registry = CompressionRegistry()
    try:
        registry.resolve("totally_made_up", strict=True)
    except KeyError:
        pass
    else:
        raise AssertionError("strict mode must raise on unknown input_kind")


def test_registry_non_strict_falls_back_to_text() -> None:
    profile = resolve("totally_made_up")
    assert isinstance(profile, WebFetchTextProfile)


# ---------------------------------------------------------------------------
# Profile-specific behavior
# ---------------------------------------------------------------------------


def test_github_repo_meta_signal_score() -> None:
    raw = {
        "full_name": "JDeun/Helm",
        "stargazers_count": 100,
        "forks_count": 7,
        "pushed_at": "2026-05-20T12:00:00Z",
        "readme": "# Helm\n\n주인님을 위한 도구.\n",
    }
    profile = GitHubRepoMetaProfile()
    out = profile.canonicalize(raw)
    assert out.facts["signal_score"] == 100 + 4 * 7
    assert out.facts["full_name"] == "JDeun/Helm"
    # README body comes through verbatim.
    assert "주인님" in out.text


def test_rss_feed_per_item_extraction() -> None:
    raw = {
        "items": [
            {
                "title": "Karpathy on RL",
                "link": "https://example.com/karpathy",
                "published": "2026-05-19T08:00:00Z",
                "summary": "내용 요약 with code: `x=1`",
            },
            {
                "title": "I/O recap",
                "link": "https://example.com/io",
                "published": "2026-05-20T08:00:00Z",
                "summary": "행사 요약.",
            },
        ]
    }
    profile = RSSFeedProfile()
    out = profile.canonicalize(raw)
    assert out.facts["item_count"] == 2
    assert "https://example.com/karpathy" in out.urls
    assert "https://example.com/io" in out.urls
    assert "Karpathy on RL" in out.text


def test_search_result_keeps_url_per_row() -> None:
    raw = {
        "results": [
            {"title": "A", "url": "https://a.example", "snippet": "snip a"},
            {"title": "B", "url": "https://b.example", "snippet": "snip b"},
        ]
    }
    profile = SearchResultProfile()
    out = profile.canonicalize(raw)
    assert out.facts["result_count"] == 2
    assert out.urls == ["https://a.example", "https://b.example"]


def test_browser_snapshot_strips_scripts_keeps_alt() -> None:
    raw = (
        "<html><head><style>x{}</style></head>"
        "<body><h1>제목</h1>"
        "<img src='x.png' alt=\"주인님의 사진\">"
        "<script>evil()</script>"
        "<p>본문</p></body></html>"
    )
    profile = BrowserSnapshotProfile()
    out = profile.canonicalize(raw)
    assert "evil()" not in out.text
    assert "[image: 주인님의 사진]" in out.text
    assert "본문" in out.text


def test_stage_outputs_are_serializable() -> None:
    profile = WebFetchTextProfile()
    result = profile.run("주인님, 안녕하세요. https://example.com")
    d1 = result.stage1.as_dict()
    d2 = result.stage2.as_dict()
    assert d1["stage"] == 1
    assert d2["stage"] == 2
    assert d2["display_only"] is True
    assert d1["profile_id"] == d2["profile_id"] == "web_fetch.text"


def test_stage2_summary_respects_grapheme_budget() -> None:
    raw = "주인님, " + ("안녕하세요 " * 200)
    profile = WebFetchTextProfile()
    canonical = profile.canonicalize(raw)
    summary = profile.summarize(canonical, budget=50)
    assert len(grapheme_clusters(summary.summary)) <= 50
