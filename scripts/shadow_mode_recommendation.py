"""Per-feature enforce-readiness recommendations for the shadow-mode report.

Wave 6 — harness-engineering rollout.

See also :mod:`scripts.shadow_mode_report` for the aggregation layer whose
output feeds into this module's :func:`recommend` function.

Decision rules
--------------
Each rule is documented below.  Thresholds are defined as module-level
constants so they can be tuned in code without hunting through prose.

browser_verifier
    - shadow_count == 0                                   → no_signal
    - shadow_count < MIN_BROWSER_SHADOW (10)              → needs_more_data
    - shadow_count >= 10 AND block_mutation_true / shadow_count < BLOCK_RATE_CAUTION (0.5)
                                                          → ready_to_enforce
    - else (block rate >= 50%)                            → caution

pause_gate
    - blocked_count == 0                                  → no_signal
    - blocked_count >= 1                                  → ready_to_enforce

model_repair
    - event_count < MIN_REPAIR_EVENTS (5)                 → needs_more_data
    - (abort + give_up) / event_count >= BAD_VERDICT_RATE (0.5)
                                                          → caution
    - else                                                → ready_to_enforce

synthetic_respond_inferred
    - terminal_without_tool_events == 0                   → no_signal
    - would_have_helped_estimate / terminal_without_tool_events >= HELP_RATE (0.30)
                                                          → ready_to_enforce
    - else                                                → caution

skill_promotion
    Operational / informational — not an enforce-style decision.
    Always returns no_signal.  Reason notes the number of pending candidates.

max_sessions_hits
    Operational / observational — not an enforce-style decision.
    Returns no_signal when count == 0; surfaces the profiles when count >= 1.

cleanup_evidence_gate
    - required_count == 0                                 → no_signal
    - missing_cleanup_count / required_count >= MISSING_CLEANUP_RATE (0.30)
                                                          → caution
    - else                                                → ready_to_enforce

Verdicts
--------
``ready_to_enforce`` — data shows the feature behaves correctly; flip to
  enforce mode.
``needs_more_data`` — too few events to decide; collect more signal.
``caution`` — signal suggests a configuration or workflow issue; investigate
  before enforcing.
``no_signal`` — the feature never fired in the window (or is not an
  enforce-style gate); safe to leave as-is.
"""

from __future__ import annotations

__all__ = ["recommend"]

# ---------------------------------------------------------------------------
# Tunable thresholds
# ---------------------------------------------------------------------------

MIN_BROWSER_SHADOW: int = 10
"""Minimum shadow_count needed to make a browser_verifier decision."""

BLOCK_RATE_CAUTION: float = 0.5
"""If block_mutation_true / shadow_count >= this, issue caution."""

MIN_REPAIR_EVENTS: int = 5
"""Minimum event_count needed to make a model_repair decision."""

BAD_VERDICT_RATE: float = 0.5
"""If (abort + give_up) / event_count >= this, issue caution."""

HELP_RATE: float = 0.30
"""If would_have_helped / terminal_events >= this, synthetic tool is ready."""

MISSING_CLEANUP_RATE: float = 0.30
"""If missing_cleanup / required >= this, cleanup gate is not ready."""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def recommend(report: dict) -> dict:
    """Per-feature enforce-readiness recommendations.

    Parameters
    ----------
    report:
        The dict returned by :func:`scripts.shadow_mode_report.generate_report`.

    Returns
    -------
    dict
        Mapping of feature name to recommendation dict::

            {
              "<feature_name>": {
                "verdict": "ready_to_enforce" | "needs_more_data" | "caution" | "no_signal",
                "reason": str,
                "next_step": str,
              },
              ...
            }
    """
    features = report.get("features", {})
    result: dict = {}

    if "browser_verifier" in features:
        result["browser_verifier"] = _rec_browser_verifier(features["browser_verifier"])

    if "pause_gate" in features:
        result["pause_gate"] = _rec_pause_gate(features["pause_gate"])

    if "model_repair" in features:
        result["model_repair"] = _rec_model_repair(features["model_repair"])

    if "synthetic_respond_inferred" in features:
        result["synthetic_respond_inferred"] = _rec_synthetic_respond(
            features["synthetic_respond_inferred"]
        )

    if "skill_promotion" in features:
        result["skill_promotion"] = _rec_skill_promotion(features["skill_promotion"])

    if "max_sessions_hits" in features:
        result["max_sessions_hits"] = _rec_max_sessions(features["max_sessions_hits"])

    if "cleanup_evidence_gate" in features:
        result["cleanup_evidence_gate"] = _rec_cleanup_evidence(
            features["cleanup_evidence_gate"]
        )

    return result


# ---------------------------------------------------------------------------
# Per-feature rule implementations
# ---------------------------------------------------------------------------

def _rec_browser_verifier(data: dict) -> dict:
    shadow_count: int = data.get("shadow_count", 0)

    if shadow_count == 0:
        return {
            "verdict": "no_signal",
            "reason": "No browser tasks observed in the reporting window.",
            "next_step": "Wait for browser tasks to run, then re-evaluate.",
        }

    if shadow_count < MIN_BROWSER_SHADOW:
        return {
            "verdict": "needs_more_data",
            "reason": (
                f"Only {shadow_count} shadow tasks observed; "
                f"want at least {MIN_BROWSER_SHADOW} before enforcing."
            ),
            "next_step": "Continue collecting shadow data.",
        }

    bd = data.get("decision_breakdown", {})
    block_count = bd.get("block_mutation_true", 0)
    block_rate = block_count / shadow_count if shadow_count > 0 else 0.0

    if block_rate < BLOCK_RATE_CAUTION:
        return {
            "verdict": "ready_to_enforce",
            "reason": (
                f"{shadow_count} tasks observed; "
                f"block_mutation rate {block_rate:.0%} is below {BLOCK_RATE_CAUTION:.0%} threshold."
            ),
            "next_step": (
                "Set HELM_BROWSER_VERIFIER_SHADOW=0 (or equivalent flag) to enforce."
            ),
        }

    return {
        "verdict": "caution",
        "reason": (
            f"High block_mutation rate ({block_rate:.0%}) on {shadow_count} tasks "
            "suggests a config or skill issue."
        ),
        "next_step": (
            "Review browser_verifier decision_breakdown. Check profile policies "
            "before enforcing."
        ),
    }


def _rec_pause_gate(data: dict) -> dict:
    blocked_count: int = data.get("blocked_count", 0)

    if blocked_count == 0:
        return {
            "verdict": "no_signal",
            "reason": "Pause gate never fired; safe to enable but no signal yet.",
            "next_step": "Enable for profiles where pausing is expected to occur.",
        }

    return {
        "verdict": "ready_to_enforce",
        "reason": f"Gate produced expected blocks {blocked_count} time(s).",
        "next_step": "Flip HELM_PAUSE_GATE_ENFORCE=1 (or equivalent flag).",
    }


def _rec_model_repair(data: dict) -> dict:
    event_count: int = data.get("event_count", 0)

    if event_count < MIN_REPAIR_EVENTS:
        return {
            "verdict": "needs_more_data",
            "reason": (
                f"Only {event_count} repair events; "
                f"want at least {MIN_REPAIR_EVENTS} before deciding."
            ),
            "next_step": "Allow more tasks to run through model_repair.",
        }

    vbd = data.get("verdict_breakdown", {})
    bad = vbd.get("abort", 0) + vbd.get("give_up", 0)
    bad_rate = bad / event_count if event_count > 0 else 0.0

    if bad_rate >= BAD_VERDICT_RATE:
        return {
            "verdict": "caution",
            "reason": (
                f"High abort/give_up rate ({bad_rate:.0%}) on {event_count} events "
                "suggests model or policy issue."
            ),
            "next_step": (
                "Review top_issues in model_repair report and check nudge policy config."
            ),
        }

    return {
        "verdict": "ready_to_enforce",
        "reason": (
            f"{event_count} repair events with acceptable abort/give_up rate "
            f"({bad_rate:.0%})."
        ),
        "next_step": "Set HELM_MODEL_REPAIR=1 (or equivalent flag) to enforce.",
    }


def _rec_synthetic_respond(data: dict) -> dict:
    terminal_count: int = data.get("terminal_without_tool_events", 0)

    if terminal_count == 0:
        return {
            "verdict": "no_signal",
            "reason": "No terminal-without-tool events observed in the window.",
            "next_step": "Check proxy event capture is wired correctly.",
        }

    would_have_helped: int = data.get("would_have_helped_estimate", 0)
    help_rate = would_have_helped / terminal_count if terminal_count > 0 else 0.0

    if help_rate >= HELP_RATE:
        return {
            "verdict": "ready_to_enforce",
            "reason": (
                f"Tool would have helped in {help_rate:.0%} of {terminal_count} "
                "terminal-without-tool events (threshold: "
                f"{HELP_RATE:.0%})."
            ),
            "next_step": "Enable synthetic_respond_tool injection.",
        }

    return {
        "verdict": "caution",
        "reason": (
            f"Tool would have helped in only {help_rate:.0%} of {terminal_count} events "
            f"(below {HELP_RATE:.0%} threshold)."
        ),
        "next_step": (
            "Investigate whether terminal-without-tool events are benign before enabling."
        ),
    }


def _rec_skill_promotion(data: dict) -> dict:
    pending: int = data.get("pending", 0)

    reason = (
        f"Skill promotion is operational/informational. "
        f"{pending} candidate(s) currently pending review."
    )
    return {
        "verdict": "no_signal",
        "reason": reason,
        "next_step": (
            "Run `helm skill-promotion pending` to review candidates. "
            "This is not an enforce-style gate."
        ),
    }


def _rec_max_sessions(data: dict) -> dict:
    count: int = data.get("count", 0)

    if count == 0:
        return {
            "verdict": "no_signal",
            "reason": "No max_sessions hits observed.",
            "next_step": "No action needed; monitor on an ongoing basis.",
        }

    by_profile = data.get("by_profile", {})
    profile_summary = ", ".join(
        f"{p}={c}" for p, c in sorted(by_profile.items(), key=lambda x: -x[1])
    )
    return {
        "verdict": "no_signal",
        "reason": (
            f"{count} max_sessions hit(s) observed. "
            f"Affected profiles: {profile_summary or 'unknown'}."
        ),
        "next_step": (
            "Review max_sessions limits per profile. "
            "This is observational — not an enforce-style gate."
        ),
    }


def _rec_cleanup_evidence(data: dict) -> dict:
    required_count: int = data.get("required_count", 0)

    if required_count == 0:
        return {
            "verdict": "no_signal",
            "reason": "No cleanup-evidence requirements observed in the window.",
            "next_step": "No action needed.",
        }

    missing_count: int = data.get("missing_cleanup_count", 0)
    missing_rate = missing_count / required_count if required_count > 0 else 0.0

    if missing_rate >= MISSING_CLEANUP_RATE:
        return {
            "verdict": "caution",
            "reason": (
                f"Workflow not yet cleaning up consistently: "
                f"{missing_rate:.0%} of {required_count} required entries have no "
                f"subsequent cleanup_status (threshold: {MISSING_CLEANUP_RATE:.0%})."
            ),
            "next_step": (
                "Ensure cleanup hooks run after browser tasks. "
                "Do not enforce until missing rate drops below "
                f"{MISSING_CLEANUP_RATE:.0%}."
            ),
        }

    return {
        "verdict": "ready_to_enforce",
        "reason": (
            f"Cleanup evidence present for {1 - missing_rate:.0%} of {required_count} "
            "required entries (missing rate below threshold)."
        ),
        "next_step": (
            "Enable HELM_CLEANUP_EVIDENCE_ENFORCE=1 (or equivalent flag)."
        ),
    }
