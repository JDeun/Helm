#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.model_health_lib import (
    higher_priority_models,
    launch_background_recovery_probe,
    load_policy,
    load_state,
    policy_models,
    save_state,
    select_model,
    utc_now_iso,
    update_state_with_probe,
)


def print_payload(payload: dict, *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return
    print(payload)


def probe_targets(args: argparse.Namespace, policy: dict) -> list[str]:
    if args.model:
        return [args.model]
    if args.current_model:
        return higher_priority_models(policy, args.current_model)
    return [str(item["ref"]) for item in policy_models(policy)]


def cmd_probe(args: argparse.Namespace) -> int:
    policy = load_policy()
    results = [update_state_with_probe(model, policy) for model in probe_targets(args, policy)]
    state = load_state(policy)
    print_payload({"results": results, "selected_model": state.get("selected_model")}, as_json=args.json)
    return 0 if all(item.get("status") == "healthy" for item in results) else 1


def cmd_watch(args: argparse.Namespace) -> int:
    policy = load_policy()
    deadline = time.monotonic() + max(1, args.duration_seconds)
    latest: list[dict] = []
    while True:
        latest = [update_state_with_probe(model, policy) for model in higher_priority_models(policy, args.current_model or None)]
        if time.monotonic() >= deadline:
            break
        time.sleep(max(1, args.interval_seconds))
    state = load_state(policy)
    print_payload({"results": latest, "selected_model": state.get("selected_model")}, as_json=args.json)
    return 0


def cmd_select(args: argparse.Namespace) -> int:
    policy = load_policy()
    state = load_state(policy)
    choice = select_model(policy, state)
    state["selected_model"] = {"model": choice.model, "reason": choice.reason, "source": choice.source, "checked_at": utc_now_iso()}
    save_state(state, policy)
    print_payload({"model": choice.model, "reason": choice.reason, "source": choice.source}, as_json=args.json)
    return 0


def cmd_state(args: argparse.Namespace) -> int:
    print_payload(load_state(load_policy()), as_json=args.json)
    return 0


def cmd_launch(args: argparse.Namespace) -> int:
    process = launch_background_recovery_probe(args.current_model or None)
    print_payload({"launched": bool(process), "pid": process.pid if process else None}, as_json=args.json)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Probe model health and select the best recovered model for the next turn.")
    subparsers = parser.add_subparsers(dest="command_name", required=True)

    probe = subparsers.add_parser("probe", help="Probe one model, higher-priority models, or the full policy chain.")
    probe.add_argument("--model")
    probe.add_argument("--current-model", help="Probe only models with higher priority than this model.")
    probe.add_argument("--json", action="store_true")
    probe.set_defaults(func=cmd_probe)

    watch = subparsers.add_parser("watch", help="Repeatedly probe models with higher priority than the current model.")
    watch.add_argument("--current-model", required=True)
    watch.add_argument("--interval-seconds", type=int, default=60)
    watch.add_argument("--duration-seconds", type=int, default=600)
    watch.add_argument("--json", action="store_true")
    watch.set_defaults(func=cmd_watch)

    select = subparsers.add_parser("select", help="Select the highest-priority fresh healthy model.")
    select.add_argument("--json", action="store_true")
    select.set_defaults(func=cmd_select)

    state = subparsers.add_parser("state", help="Show the persisted model health state.")
    state.add_argument("--json", action="store_true")
    state.set_defaults(func=cmd_state)

    launch = subparsers.add_parser("launch", help="Launch the background recovery prober.")
    launch.add_argument("--current-model")
    launch.add_argument("--json", action="store_true")
    launch.set_defaults(func=cmd_launch)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
