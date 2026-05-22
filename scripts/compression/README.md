# Compression Profile Registry

Implementation of §4 of the
*Helm Architecture Design — 2026-05-21* document.

## Two-stage contract

```
profile  = compression.resolve(input_kind, url)
canonical = profile.canonicalize(raw)            # stage 1: loss-less
summary   = profile.summarize(canonical, budget) # stage 2: judgment / display
```

**Stage 1 (loss-less)** is the only output authorized to back a factual
assertion. Money, dates, quotes, signal scores, candidate selection
must all use `Stage1Output.facts` / `Stage1Output.text`.

**Stage 2 (judgment)** is display-only. Telegram outbound, daily
briefing prose, global summary cards. It MUST never be used as a
factual ground truth.

## §4.4 — AI briefing OpenHuman 누락 사건 linkage

The 2026-05-19 briefing dropped the OpenHuman / Karpathy / I/O items
because the *stage 2 summary* was used for candidate selection — the
summary was short, so the candidates disappeared.

This package encodes the structural fix:

1. `Stage1Output` carries `facts`, `urls`, `code_blocks`,
   `raw_token_estimate` — every signal needed for candidate ranking
   without ever invoking stage 2.
2. `Stage2Output.display_only` is `True` by default. Downstream
   callers that mistakenly try to feed `summary` into a fact decision
   can at least be linted for that flag.
3. The companion **raw-candidate sanity check** that the design calls
   out lives in P1 (`connector_freshness` + the briefing cron) and is
   intentionally NOT modified here. The sanity check should run on
   the stage-1 raw candidate count *before* any "quiet day" verdict
   is allowed.

A future P-C / cron payload edit will wire the registry into
`ai_briefing_archive.py` so the candidate phase resolves a profile,
calls `canonicalize`, and records `profile_id` next to each entry.
Until then, the registry only ships the building blocks; no cron
payload is touched (per the team task constraints).

## Preservation rules (§4.2)

* NFC normalization only.
* Truncation in grapheme cluster units (Hangul / emoji safe).
* Fenced code blocks: never compressed in stage 1; replaced with
  `[code block #i]` markers in stage 2 (never edited inline).
* URLs: kept verbatim — no shortening, no tracking-param stripping.
* Korean honorifics: `-님`, `-씨`, `주인님` preserved.
* Numbers / currency / dates: kept verbatim. Unit conversions must be
  annotated.
