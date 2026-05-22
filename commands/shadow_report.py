"""Helm CLI command for the shadow-mode report (Wave 6).

``helm shadow-report [--since DAYS] [--feature NAME] [--format md|json]
                     [--with-recommendations] [--out PATH]``

Options
-------
--since DAYS
    Reporting window in days (default: 14).
--feature NAME
    Repeat to include specific features only.  Default: all features.
    Recognised values: browser_verifier, pause_gate, model_repair,
    synthetic_respond_inferred, skill_promotion, max_sessions_hits,
    cleanup_evidence_gate, all.
--format md|json
    Output format.  ``md`` (default) produces a markdown document.
    ``json`` produces the raw report dict as pretty-printed JSON.
--with-recommendations
    Append a Recommendations section (or JSON key) with per-feature verdicts.
--out PATH
    Write output to this path instead of stdout.

Exit codes
----------
0 — always, except when ``--out`` path is unwritable or ``--since`` / other
    argument parsing fails.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from scripts.shadow_mode_report import generate_report, to_markdown
from scripts.shadow_mode_recommendation import recommend


def cmd_shadow_report(args: argparse.Namespace) -> int:
    """Execute ``helm shadow-report``."""
    since_days: int = getattr(args, "since", 14) or 14
    features: list[str] | None = getattr(args, "feature", None) or None
    fmt: str = getattr(args, "format", "md") or "md"
    with_recs: bool = getattr(args, "with_recommendations", False)
    out_path_str: str | None = getattr(args, "out", None)

    # Validate since_days
    if since_days <= 0:
        print("error: --since must be a positive integer", file=sys.stderr)
        return 1

    report = generate_report(
        since_days=since_days,
        feature_filter=features if features else None,
    )

    if with_recs:
        recs = recommend(report)
    else:
        recs = None

    if fmt == "json":
        payload = dict(report)
        if recs is not None:
            payload["recommendations"] = recs
        output = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    else:
        output = to_markdown(report)
        if recs is not None:
            output += _recs_to_markdown(recs)

    if out_path_str:
        out_path = Path(out_path_str)
        try:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(output, encoding="utf-8")
        except OSError as exc:
            print(f"error: cannot write to {out_path}: {exc}", file=sys.stderr)
            return 1
    else:
        sys.stdout.write(output)

    return 0


def _recs_to_markdown(recs: dict) -> str:
    """Render recommendations as a markdown section."""
    lines: list[str] = ["", "## Recommendations", ""]
    for feature, rec in recs.items():
        verdict = rec.get("verdict", "?")
        reason = rec.get("reason", "")
        next_step = rec.get("next_step", "")
        lines.append(f"### {feature}")
        lines.append(f"- **Verdict:** {verdict}")
        lines.append(f"- **Reason:** {reason}")
        lines.append(f"- **Next step:** {next_step}")
        lines.append("")
    return "\n".join(lines)
