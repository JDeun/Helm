"""CLI entry points for Helm architecture design Phase-A through Phase-E modules.

These commands expose the six new design modules (action_scope,
freshness_lib, memory_tree, helm_state_model, helm_frontmatter,
compression) to the ``helm`` CLI in an *advisory* form. Each command is
read-only or evaluates an in-memory check; none mutate state.

Adding these here addresses issue #6 from the 2026-05-21 Helm full
review ("Phase-A through Phase-E modules are dead code from production's
perspective") by giving each module at least one CLI surface so that:

* CI exercises them (``helm action-scope evaluate --message ...`` etc.).
* Operators can observe the gate outputs without invoking the Telegram
  pipeline.
* Integration drift surfaces as a broken command rather than silent dead
  code.

Each command keeps the existing per-module CLI entry point (``python3 -m
scripts.action_scope ...``) functional; this file is purely a wiring
layer.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def cmd_action_scope_evaluate(args: argparse.Namespace) -> int:
    """Evaluate the action-scope gate against a message.

    Thin wrapper that produces the same JSON payload as the module-level
    ``python3 -m scripts.action_scope`` CLI but is reachable via
    ``helm action-scope evaluate``.
    """
    from scripts.action_scope import (
        ActionScopeKind,
        MUTABLE_RESOURCES,
        attempted_action_allowed,
        evaluate,
    )

    decision = evaluate(
        args.message,
        explicit_targets=args.target or None,
        topics=args.topic or None,
    )
    payload: dict[str, object] = decision.as_dict()
    if args.attempt:
        attempted = ActionScopeKind(args.attempt)
        if args.resource is not None and args.resource not in MUTABLE_RESOURCES:
            print(
                f"error: unknown resource {args.resource!r}",
                file=sys.stderr,
            )
            return 2
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


def cmd_freshness_status(args: argparse.Namespace) -> int:
    """Print the connector freshness substrate as a JSON report.

    Reads the canonical Helm state at ``~/.helm/state/connector-freshness.json``
    (or the path passed via ``--state-path``) and lists each connector's
    last_seen, last_success, age, SLA budget, risk class, and freshness
    branch ("fresh" / "stale_low" / "stale_high").
    """
    from scripts.freshness_lib import (
        assess_record,
        list_records,
        load_state,
        utc_now,
    )

    state_path = Path(args.state_path).expanduser() if args.state_path else None
    state = load_state(state_path)
    records = list_records(state)
    now = utc_now()
    summary: list[dict[str, object]] = []
    for record in records:
        assessment = assess_record(
            record, now=now, strict_high_risk=args.strict_high_risk
        )
        summary.append(
            {
                "connector_id": record.connector_id,
                "risk_class": record.risk_class,
                "freshness_sla_minutes": record.freshness_sla_minutes,
                "last_seen": record.last_seen,
                "last_success": record.last_success,
                "stale_reason": assessment.stale_reason,
                "fresh": assessment.fresh,
                "age_seconds": assessment.age_seconds,
            }
        )
    payload = {
        "state_version": state.get("version", 1),
        "connectors": summary,
        "checked_at": now.isoformat(),
    }
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        if not summary:
            print("(no connector freshness records yet)")
            return 0
        print(f"checked_at: {payload['checked_at']}")
        for row in summary:
            mark = "fresh" if row["fresh"] else "STALE"
            print(
                f"  {row['connector_id']:<18}  {mark:<6}  "
                f"risk={row['risk_class']:<6}  "
                f"sla={row['freshness_sla_minutes']}m  "
                f"stale_reason={row['stale_reason']}  "
                f"last_success={row['last_success']}"
            )
    return 0


def cmd_state_lint(args: argparse.Namespace) -> int:
    """Lint outbound text against the Helm state machine assertion rules.

    Checks that text outbound to Telegram does not assert a stronger
    state than the note actually holds (e.g. claiming "saved to memory"
    when state is still CAPTURED).
    """
    from helm_state_model import PhraseLintError, State, lint_telegram_phrase

    try:
        state = State(args.state)
    except ValueError:
        valid = ", ".join(s.value for s in State)
        print(
            f"error: unknown state {args.state!r}; valid: {valid}",
            file=sys.stderr,
        )
        return 2

    if args.text == "-":
        text = sys.stdin.read()
    else:
        text = args.text

    try:
        lint_telegram_phrase(text, state)
    except PhraseLintError as exc:
        if args.json:
            print(
                json.dumps(
                    {"ok": False, "state": state.value, "reason": str(exc)},
                    indent=2,
                    ensure_ascii=False,
                )
            )
        else:
            print(f"lint failed: {exc}", file=sys.stderr)
        return 3

    if args.json:
        print(json.dumps({"ok": True, "state": state.value}, indent=2, ensure_ascii=False))
    else:
        print(f"ok (state={state.value})")
    return 0


def cmd_frontmatter_validate(args: argparse.Namespace) -> int:
    """Validate the vault folder layout against the design §2.1 standard.

    Lists any of the six required folders (00-Inbox, 10-Topics,
    20-Sources, 30-Decisions, 40-Audit, 90-Rejected) that are missing
    from the vault root, plus any informational "extra" folders that are
    present but not in the design.
    """
    from helm_frontmatter import validate_vault_layout

    vault_root = Path(args.vault_root).expanduser()
    if not vault_root.exists() or not vault_root.is_dir():
        print(
            f"error: vault root does not exist or is not a directory: {vault_root}",
            file=sys.stderr,
        )
        return 2

    report = validate_vault_layout(vault_root)
    if args.json:
        print(
            json.dumps(
                {
                    "vault_root": str(vault_root),
                    "missing": report["missing"],
                    "extra": report["extra"],
                },
                indent=2,
                ensure_ascii=False,
            )
        )
    else:
        print(f"vault_root: {vault_root}")
        if report["missing"]:
            print(f"missing: {', '.join(report['missing'])}")
        else:
            print("missing: (none)")
        if report["extra"]:
            print(f"extra:   {', '.join(report['extra'])}")
        else:
            print("extra:   (none)")
    return 0 if not report["missing"] else 3


def cmd_memory_tree_status(args: argparse.Namespace) -> int:
    """Show the on-disk state of the memory tree (sources / topics / global).

    Read-only: lists which source and topic summaries exist under
    ``~/.helm/memory/`` (or the path passed via ``--root``) and whether
    the global summary file is present. Does NOT trigger a refresh.
    """
    from memory_tree.tree import MemoryTree

    tree = MemoryTree(root=Path(args.root).expanduser() if args.root else None)
    paths = tree.paths
    sources: list[str] = []
    topics: list[str] = []
    if paths.source_dir.exists():
        sources = sorted(p.stem for p in paths.source_dir.glob("*.md"))
    if paths.topic_dir.exists():
        topics = sorted(p.stem for p in paths.topic_dir.glob("*.md"))
    global_exists = paths.global_file.exists()
    payload = {
        "root": str(paths.root),
        "sources": sources,
        "topics": topics,
        "global_summary_present": global_exists,
    }
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print(f"root: {paths.root}")
        print(f"sources ({len(sources)}): {', '.join(sources) if sources else '(none)'}")
        print(f"topics  ({len(topics)}): {', '.join(topics) if topics else '(none)'}")
        print(f"global summary: {'present' if global_exists else 'missing'}")
    return 0


def cmd_compression_profiles(args: argparse.Namespace) -> int:
    """List the compression profiles registered with the default registry.

    Prints each profile's id, input kinds it claims, and whether it has
    stage-1 / stage-2 output. Useful for verifying the registry is wired
    correctly without invoking a connector.
    """
    from scripts.compression import get_default_registry

    registry = get_default_registry()
    rows: list[dict[str, object]] = []
    for profile in registry.profiles():
        rows.append(
            {
                "profile_id": profile.profile_id,
                "input_kinds": list(profile.accepts_input_kinds),
                "default_budget_clusters": profile.default_budget_clusters,
            }
        )
    if args.json:
        print(json.dumps({"profiles": rows}, indent=2, ensure_ascii=False))
    else:
        for row in rows:
            kinds = ",".join(row["input_kinds"]) or "(none)"
            print(f"  {row['profile_id']:<36}  kinds={kinds}")
    return 0
