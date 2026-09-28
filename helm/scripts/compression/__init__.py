"""Compression Profile Registry (Helm Architecture Design §4).

Two-stage compression pipeline used by Helm and OpenClaw connectors:

* **Stage 1 - loss-less canonicalization.** Used as the *sole* factual
  ground truth for downstream decisions (briefing candidates, money /
  date / quote assertions, sanity checks). Token count is trimmed by
  removing structural noise (nav, sidebar, tracking parameters) but no
  facts are dropped. Fenced code blocks, URLs, emoji, Korean honorifics
  (-님 / -씨 / 주인님 등), numerics, dates and currency are preserved
  verbatim.

* **Stage 2 - judgment summary.** Used only for display surfaces (global
  summary, Telegram outbound). May elide information. Must never be
  used as a factual ground truth.

The registry resolves a :class:`CompressionProfile` from an
``input_kind`` (and optional URL), then provides two methods::

    profile = registry.resolve(input_kind, url)
    canonical = profile.canonicalize(raw)            # loss-less
    summary   = profile.summarize(canonical, budget) # judgment

Result objects expose ``profile_id`` so the task ledger can record
exactly which profile shaped a given record. This is essential for
postmortem traceability (see *AI 브리핑 OpenHuman 누락 사건* in §4.4 of
the design): the missing OpenHuman / Karpathy / I/O items disappeared
because the *stage 2* judgment output was used for candidate selection.
The registry encodes the rule **"candidate selection MUST use the
stage-1 raw output (and a raw candidate count sanity check before any
'quiet day' verdict)"**. The actual sanity check lives in P1
(``connector_freshness`` + the briefing cron), and is intentionally not
touched here.

Korean / CJK safety:

* All text is NFC normalized before any slicing.
* Truncation is performed in grapheme cluster units so that Hangul
  jamo, combining marks and ZWJ-joined emoji sequences are never
  split mid-cluster (e.g. "안녕" and "👨‍👩‍👧" stay intact).
"""

from __future__ import annotations

from .base import (
    CompressionProfile,
    ProfileResult,
    Stage1Output,
    Stage2Output,
    nfc_normalize,
    grapheme_clusters,
    grapheme_truncate,
    extract_fenced_blocks,
    restore_fenced_blocks,
    extract_urls,
)
from .registry import CompressionRegistry, get_default_registry, resolve

__all__ = [
    "CompressionProfile",
    "ProfileResult",
    "Stage1Output",
    "Stage2Output",
    "CompressionRegistry",
    "get_default_registry",
    "resolve",
    "nfc_normalize",
    "grapheme_clusters",
    "grapheme_truncate",
    "extract_fenced_blocks",
    "restore_fenced_blocks",
    "extract_urls",
]
