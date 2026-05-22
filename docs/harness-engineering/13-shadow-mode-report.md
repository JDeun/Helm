# 13 — Shadow-Mode Report (Wave 6)

## 1. Purpose

Waves 1-3b shipped feature-flagged behaviors that default to **shadow mode**:
the decision is logged but not enforced.  After collecting two weeks of
shadow data, Kevin needs to decide which features to flip from shadow to
enforce.

The shadow-mode report automates that decision process by aggregating
signals from the task ledger, proxy events, and skill-promotion state,
and producing per-feature **enforce-readiness verdicts**.

Run it as a once-off or schedule it weekly to track progress.

---

## 2. Report Shape

The `generate_report()` function in `scripts/shadow_mode_report.py` returns
a nested dict.  See the module docstring for the full schema.  Top-level keys:

| Key | Description |
|-----|-------------|
| `generated_at` | ISO 8601 UTC timestamp of report generation |
| `window` | `{days, since, until}` — the reporting window |
| `data_freshness` | Lines scanned, skill-state presence, unparseable timestamps |
| `features` | Per-feature aggregation (see below) |
| `raw_filter_applied` | Echo of the `feature_filter` input |

### Feature keys

- **`browser_verifier`** — shadow/enforce counts, decision breakdown, samples
- **`pause_gate`** — blocked count, samples
- **`model_repair`** — event count, verdict breakdown, top issues, shadow count
- **`synthetic_respond_inferred`** — terminal-without-tool events, would-have-helped estimate
- **`skill_promotion`** — candidates notified, approved, rejected, pending
- **`max_sessions_hits`** — count, by-profile breakdown
- **`cleanup_evidence_gate`** — required count, missing cleanup count, exit-28 count

---

## 3. Recommendation Rules

The `recommend()` function in `scripts/shadow_mode_recommendation.py` applies
these rules (thresholds are tunable module-level constants):

| Feature | Condition | Verdict |
|---------|-----------|---------|
| `browser_verifier` | `shadow_count == 0` | `no_signal` |
| `browser_verifier` | `shadow_count < 10` (MIN_BROWSER_SHADOW) | `needs_more_data` |
| `browser_verifier` | `shadow_count >= 10` AND `block_mutation_true / shadow_count < 0.50` (BLOCK_RATE_CAUTION) | `ready_to_enforce` |
| `browser_verifier` | block rate >= 50% | `caution` |
| `pause_gate` | `blocked_count == 0` | `no_signal` |
| `pause_gate` | `blocked_count >= 1` | `ready_to_enforce` |
| `model_repair` | `event_count < 5` (MIN_REPAIR_EVENTS) | `needs_more_data` |
| `model_repair` | `(abort + give_up) / event_count >= 0.50` (BAD_VERDICT_RATE) | `caution` |
| `model_repair` | otherwise | `ready_to_enforce` |
| `synthetic_respond_inferred` | `terminal_without_tool_events == 0` | `no_signal` |
| `synthetic_respond_inferred` | `would_have_helped / terminal_events >= 0.30` (HELP_RATE) | `ready_to_enforce` |
| `synthetic_respond_inferred` | below help rate | `caution` |
| `skill_promotion` | always | `no_signal` (operational/informational) |
| `max_sessions_hits` | always | `no_signal` (observational) |
| `cleanup_evidence_gate` | `required_count == 0` | `no_signal` |
| `cleanup_evidence_gate` | `missing / required >= 0.30` (MISSING_CLEANUP_RATE) | `caution` |
| `cleanup_evidence_gate` | below missing rate | `ready_to_enforce` |

---

## 4. Sample Report Excerpt

> **EXAMPLE** — synthetic numbers for illustration only

```
# Shadow-Mode Report

**Generated:** 2026-05-22T10:00:00+00:00
**Window:** 14 days (2026-05-08T10:00:00+00:00 → 2026-05-22T10:00:00+00:00)

## Data Freshness

- Ledger lines scanned: 3821
- Proxy events lines scanned: 1204
- Skill state present: True

## Feature: browser_verifier

- Shadow events: 47
- Enforced blocks: 0
- Enforced approvals: 0

**Decision Breakdown:**
  - block_mutation_true: 8
  - require_cleanup_evidence_true: 12
  - require_confirmation_true: 5

**Samples (5):** task001, task002, task003, task004, task005

## Feature: pause_gate

- Blocked count: 3
**Samples (3):** pause01, pause02, pause03

## Feature: model_repair

- Total events: 22
- Shadow events: 22

**Verdict Breakdown:**
  - abort: 1
  - give_up: 0
  - nudge_and_retry: 7
  - ok: 14

**Top Issues:**
  - non_json_when_tool_required: 5
  - malformed_tool_call: 2

## Recommendations

### browser_verifier
- **Verdict:** ready_to_enforce
- **Reason:** 47 tasks observed; block_mutation rate 17% is below 50% threshold.
- **Next step:** Set HELM_BROWSER_VERIFIER_SHADOW=0 to enforce.
```

---

## 5. Operator Runbook

### Daily / Weekly Cron Suggestion

```bash
# Weekly shadow report with recommendations, saved to file
0 8 * * 1  cd ~/Helm && python3 helm.py shadow-report \
  --since 14 \
  --with-recommendations \
  --format md \
  --out ~/.openclaw/workspace/reports/shadow-$(date +%Y-%m-%d).md
```

### How to Read the Report

1. Check **Data Freshness** — confirm scanned line counts are reasonable.
   If `ledger_lines_scanned` is 0, the ledger path may not exist yet.
2. Review each **Feature** section:
   - `shadow_count` / `blocked_count` tells you how much signal exists.
   - `decision_breakdown` shows which policies are firing most often.
   - `samples` gives task IDs for spot-checking in the task ledger.
3. Read the **Recommendations** section (requires `--with-recommendations`):
   - Each feature shows `verdict`, `reason`, and `next_step`.

### When to Flip a Feature to Enforce

| Verdict | Action |
|---------|--------|
| `ready_to_enforce` | Set the feature's enforce flag and monitor for one more week. |
| `needs_more_data` | Wait; re-run report after more tasks have completed. |
| `caution` | Investigate the flagged issue before enforcing. Review `decision_breakdown` and `top_issues`. |
| `no_signal` | Feature never fired. Safe to enable if appropriate, but no data to validate against. |

---

## 6. Limitations

- **Tail-sampling** — the report reads at most `tail_lines` (default 5000) lines
  from the end of each JSONL file.  For high-traffic deployments where the
  ledger grows faster than the window, very old entries near the rolling-window
  edge may be missed.  Increase `--tail-lines` if needed (not yet a CLI flag;
  use the Python API directly).

- **`synthetic_respond_inferred`** — this feature is inferred from proxy-event
  issue codes, not directly observed from a dedicated instrumentation point.
  `would_have_helped_estimate` is a lower-bound estimate; false negatives are
  likely if `tool_required` is inconsistently set.

- **`skill_promotion`** — this is an operational/informational feature, not an
  enforce-style gate.  Its verdict is always `no_signal`.  Use
  `helm skill-promotion pending` to manage individual candidates.

- **Timestamp tolerance** — entries with no parseable timestamp are included in
  aggregation counts (included-on-missing policy) and flagged in
  `data_freshness.ledger_unparseable_timestamp_count`.  High counts here may
  indicate a ledger format change.

- **Path privacy** — task ID samples strip absolute path prefixes to avoid
  leaking local filesystem layout.  If a sample looks truncated, the full
  task ID can be found by searching the ledger directly.
