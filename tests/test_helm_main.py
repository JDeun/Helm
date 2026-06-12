"""Smoke tests for ``helm.main`` and the REMAINDER-style passthrough.

These tests pin two behaviors that the rest of the CLI relies on but
that were previously only exercised indirectly:

1. The top-level subparser (built by ``build_parser``) accepts the
   stable subcommand verbs and forwards them to a ``func`` callable.
2. The passthrough dispatch in ``main`` splits ``--path`` from the
   forwarded child-script arguments correctly.

Tests use ``unittest.mock.patch`` to replace the underlying ``cmd_*``
handlers so we never invoke real subprocesses or touch the filesystem.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import helm as helm_module  # noqa: E402  (ROOT injected above)
from scripts import run_with_profile  # noqa: E402  (ROOT injected above)


def _ok(_args: argparse.Namespace) -> int:
    return 0


def test_main_passthrough_routes_profile() -> None:
    """``helm profile run skill-x`` must hit cmd_profile with forwarded args."""
    seen: dict[str, object] = {}

    def capture(args: argparse.Namespace) -> int:
        seen["path"] = args.path
        seen["args"] = list(args.args)
        return 0

    with patch.object(helm_module, "cmd_profile", side_effect=capture):
        rc = helm_module.main(["profile", "run", "skill-x", "--dry-run"])

    assert rc == 0
    assert seen["path"] is None
    assert seen["args"] == ["run", "skill-x", "--dry-run"]


def test_main_passthrough_extracts_path_before_forwarded() -> None:
    """``--path X`` immediately after the verb is consumed; rest is forwarded."""
    seen: dict[str, object] = {}

    def capture(args: argparse.Namespace) -> int:
        seen["path"] = args.path
        seen["args"] = list(args.args)
        return 0

    with patch.object(helm_module, "cmd_memory", side_effect=capture):
        rc = helm_module.main(["memory", "--path", "/tmp/ws", "list", "--limit", "5"])

    assert rc == 0
    assert seen["path"] == "/tmp/ws"
    assert seen["args"] == ["list", "--limit", "5"]


def test_main_passthrough_path_after_forwarded_is_not_consumed() -> None:
    """``--path`` that appears AFTER forwarded args is treated as a child arg.

    Pins the documented contract that ``--path`` is positional-after-verb
    only. This guards against accidental "greedy" consumption that would
    swallow child-script flags.
    """
    seen: dict[str, object] = {}

    def capture(args: argparse.Namespace) -> int:
        seen["path"] = args.path
        seen["args"] = list(args.args)
        return 0

    with patch.object(helm_module, "cmd_ops", side_effect=capture):
        rc = helm_module.main(["ops", "report", "--path", "/tmp/ws"])

    assert rc == 0
    # First forwarded token "report" precedes --path, so --path is forwarded.
    assert seen["path"] is None
    assert seen["args"] == ["report", "--path", "/tmp/ws"]


def test_run_with_profile_help_lists_readme_run_command() -> None:
    """README examples use ``helm profile run ...``; the child CLI help must expose it."""
    parser = run_with_profile.build_parser()

    assert "run" in parser.format_help()

    args = run_with_profile.parse_run_args(["inspect_local", "--task-name", "readme smoke", "--", "true"])
    assert args.profile == "inspect_local"
    assert args.task_name == "readme smoke"
    assert args.command == ["true"]


def test_main_passthrough_missing_path_value_raises() -> None:
    """``--path`` without a following value must SystemExit (argparse-style)."""
    with patch.object(helm_module, "cmd_skill", side_effect=_ok):
        with pytest.raises(SystemExit):
            helm_module.main(["skill", "--path"])


def test_main_non_passthrough_uses_full_parser() -> None:
    """Non-passthrough verbs (e.g. ``detect``) flow through build_parser.

    The ``--json`` flag must be parsed into argparse's namespace, not
    forwarded as raw argv, distinguishing this path from passthrough.
    """
    captured: dict[str, object] = {}

    def capture(args: argparse.Namespace) -> int:
        captured["json"] = args.json
        captured["path"] = args.path
        return 0

    with patch.object(helm_module, "cmd_detect", side_effect=capture):
        rc = helm_module.main(["detect", "--path", "/tmp/x", "--json"])

    assert rc == 0
    assert captured["json"] is True
    assert captured["path"] == "/tmp/x"


def test_main_unknown_verb_errors() -> None:
    """Unknown verbs must fail through argparse, not be passed through silently."""
    with pytest.raises(SystemExit):
        helm_module.main(["this-verb-does-not-exist"])


def test_build_parser_lists_expected_subcommands() -> None:
    """Pin the set of top-level verbs we expect to exist.

    A regression here flags any accidental removal from build_parser
    that would break shell scripts / docs hard-coded against these
    names.
    """
    parser = helm_module.build_parser()
    # Extract subparser names by walking the argparse internals once.
    subparsers_action = next(
        action for action in parser._actions if isinstance(action, argparse._SubParsersAction)
    )
    names = set(subparsers_action.choices.keys())
    # Core surface — must always exist.
    for expected in ("detect", "init", "doctor", "status", "dashboard", "validate"):
        assert expected in names, f"missing top-level verb: {expected}"
