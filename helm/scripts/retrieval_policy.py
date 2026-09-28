#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json

from retrieval_policy_lib import build_retrieval_plan, classify_retrieval


def cmd_classify(args: argparse.Namespace) -> int:
    payload = classify_retrieval(
        status_code=args.status_code,
        error_text=args.error_text,
        body_hint=args.body_hint,
        browser_used=args.browser_used,
        network_discovery=args.network_discovery,
        auth_required=args.auth_required,
        unsafe=args.unsafe,
        human_approval_needed=args.human_approval_needed,
    )
    print(json.dumps(payload.__dict__, indent=2, ensure_ascii=False))
    return 0


def cmd_plan(args: argparse.Namespace) -> int:
    payload = build_retrieval_plan(
        current_stage=args.current_stage,
        status_code=args.status_code,
        error_text=args.error_text,
        body_hint=args.body_hint,
        browser_used=args.browser_used,
        network_discovery=args.network_discovery,
        auth_required=args.auth_required,
        unsafe=args.unsafe,
        human_approval_needed=args.human_approval_needed,
        browser_allowed=not args.no_browser,
    )
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Classify blocked retrieval failures and recommend escalation stages.")
    subparsers = parser.add_subparsers(dest="command_name", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--current-stage", choices=["cheap_fetch", "transformed_url", "browser_snapshot", "browser_network"])
    common.add_argument("--status-code", type=int)
    common.add_argument("--error-text")
    common.add_argument("--body-hint")
    common.add_argument("--browser-used", action="store_true")
    common.add_argument("--network-discovery", action="store_true")
    common.add_argument("--auth-required", action="store_true")
    common.add_argument("--unsafe", action="store_true")
    common.add_argument("--human-approval-needed", action="store_true")

    classify = subparsers.add_parser("classify", parents=[common])
    classify.set_defaults(func=cmd_classify)

    plan = subparsers.add_parser("plan", parents=[common])
    plan.add_argument("--no-browser", action="store_true")
    plan.set_defaults(func=cmd_plan)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
