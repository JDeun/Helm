"""Smoke tests for ``commands/phase_modules.py`` — the Phase A-E CLI wiring.

The Phase A-E modules (action_scope, freshness_lib, helm_state_model,
helm_frontmatter, memory_tree, compression) are well-covered in their
own unit tests, but the argparse → handler wiring in
``commands/phase_modules.py`` is a 282-line layer with no direct
coverage (R2 I3). A regression in argument naming or ``args.attempt`` /
``args.resource`` handling would not be caught by the underlying unit
tests.

This file exercises every subcommand through ``helm.main([...])`` so
the full argparse path is exercised:

* ``helm action-scope evaluate``
* ``helm freshness status``
* ``helm state lint-phrase``
* ``helm frontmatter validate-vault``
* ``helm memory-tree status``
* ``helm compression profiles``

stdout is captured via :func:`capsys` and parsed when the command emits
JSON. Tests assert observable shape, not exact byte equality, so
formatting tweaks elsewhere do not break this layer.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import helm as helm_module  # noqa: E402  (ROOT injected above)


# ---------------------------------------------------------------------------
# action-scope evaluate
# ---------------------------------------------------------------------------


def test_action_scope_evaluate_locks_edit_on_korean_edit_verb(capsys) -> None:
    """``수정합니다`` (Korean ``edit``) must produce locked_scope=edit."""
    rc = helm_module.main(["action-scope", "evaluate", "--message", "수정합니다"])
    out = capsys.readouterr().out
    payload = json.loads(out)
    # Decision shape from action_scope.GateDecision.as_dict().
    assert payload["locked_scope"] == "edit"
    # `evaluate` returns 0 when allowed; the locked-scope determination
    # is the meaningful assertion here.
    assert rc in {0, 3}


def test_action_scope_evaluate_handles_no_verb_message(capsys) -> None:
    """A message with no Korean / English verb should still parse cleanly."""
    rc = helm_module.main([
        "action-scope",
        "evaluate",
        "--message",
        "hello there",
    ])
    out = capsys.readouterr().out
    payload = json.loads(out)
    # The exact lock decision depends on heuristics; verifying that the
    # CLI returns a dict-shaped JSON payload is enough as smoke coverage.
    assert isinstance(payload, dict)
    assert "locked_scope" in payload
    assert rc in {0, 3}


def test_action_scope_evaluate_attempt_block_includes_resource(capsys) -> None:
    """``--attempt`` populates the ``attempt`` block with resource handling."""
    rc = helm_module.main([
        "action-scope",
        "evaluate",
        "--message",
        "삭제할게요",
        "--attempt",
        "delete",
        "--resource",
        "google_calendar",
    ])
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert "attempt" in payload
    assert payload["attempt"]["scope"] == "delete"
    assert payload["attempt"]["resource"] == "google_calendar"
    assert "allowed" in payload["attempt"]
    assert rc in {0, 3}


def test_action_scope_evaluate_unknown_resource_returns_2(capsys) -> None:
    """An unknown ``--resource`` value must exit 2 with a stderr error."""
    rc = helm_module.main([
        "action-scope",
        "evaluate",
        "--message",
        "삭제합니다",
        "--attempt",
        "delete",
        "--resource",
        "no_such_resource_xyz",
    ])
    err = capsys.readouterr().err
    assert rc == 2
    assert "unknown resource" in err


# ---------------------------------------------------------------------------
# freshness status
# ---------------------------------------------------------------------------


def test_freshness_status_emits_empty_text_when_no_state(tmp_path, capsys) -> None:
    """A missing/empty state file must not crash; text mode reports zero rows."""
    rc = helm_module.main([
        "freshness",
        "status",
        "--state-path",
        str(tmp_path / "freshness.json"),
    ])
    out = capsys.readouterr().out
    assert rc == 0
    assert "no connector freshness records yet" in out or out.strip() == ""


def test_freshness_status_json_mode_returns_connectors_key(tmp_path, capsys) -> None:
    rc = helm_module.main([
        "freshness",
        "status",
        "--state-path",
        str(tmp_path / "freshness.json"),
        "--json",
    ])
    out = capsys.readouterr().out
    assert rc == 0
    payload = json.loads(out)
    assert "connectors" in payload
    assert "checked_at" in payload


# ---------------------------------------------------------------------------
# state lint-phrase
# ---------------------------------------------------------------------------


def test_state_lint_ok_when_phrase_matches_state(capsys) -> None:
    """A neutral text under captured state is OK and returns rc=0."""
    rc = helm_module.main([
        "state",
        "lint-phrase",
        "--state",
        "captured",
        "--text",
        "메모를 저장했습니다.",
    ])
    out = capsys.readouterr().out
    assert rc == 0
    assert "ok" in out


def test_state_lint_fails_when_phrase_overstates_state(capsys) -> None:
    """``운영 규칙으로 반영`` requires state=promoted; under captured it fails."""
    rc = helm_module.main([
        "state",
        "lint-phrase",
        "--state",
        "captured",
        "--text",
        "운영 규칙으로 반영했습니다.",
    ])
    err = capsys.readouterr().err
    assert rc == 3
    assert "lint failed" in err


def test_state_lint_invalid_state_returns_2(capsys) -> None:
    rc = helm_module.main([
        "state",
        "lint-phrase",
        "--state",
        "no_such_state",
        "--text",
        "noop",
    ])
    err = capsys.readouterr().err
    assert rc == 2
    assert "unknown state" in err


def test_state_lint_json_payload_shape(capsys) -> None:
    rc = helm_module.main([
        "state",
        "lint-phrase",
        "--state",
        "captured",
        "--text",
        "메모를 저장했습니다.",
        "--json",
    ])
    out = capsys.readouterr().out
    assert rc == 0
    payload = json.loads(out)
    assert payload["ok"] is True
    assert payload["state"] == "captured"


# ---------------------------------------------------------------------------
# frontmatter validate-vault
# ---------------------------------------------------------------------------


def test_frontmatter_validate_missing_vault_returns_2(tmp_path, capsys) -> None:
    """A non-existent vault root must exit 2."""
    rc = helm_module.main([
        "frontmatter",
        "validate-vault",
        str(tmp_path / "no_such_vault"),
    ])
    err = capsys.readouterr().err
    assert rc == 2
    assert "does not exist" in err


def test_frontmatter_validate_vault_reports_missing(tmp_path, capsys) -> None:
    """An empty directory reports all six design folders as missing."""
    vault = tmp_path / "vault"
    vault.mkdir()
    rc = helm_module.main([
        "frontmatter",
        "validate-vault",
        str(vault),
        "--json",
    ])
    out = capsys.readouterr().out
    assert rc == 3  # missing folders → non-zero per phase_modules contract
    payload = json.loads(out)
    assert "missing" in payload
    assert "extra" in payload
    assert payload["missing"]  # at least one design folder reported missing


# ---------------------------------------------------------------------------
# memory-tree status
# ---------------------------------------------------------------------------


def test_memory_tree_status_empty_root(tmp_path, capsys) -> None:
    """An empty root reports zero sources and zero topics, no global summary."""
    rc = helm_module.main([
        "memory-tree",
        "status",
        "--root",
        str(tmp_path / "memory"),
        "--json",
    ])
    out = capsys.readouterr().out
    assert rc == 0
    payload = json.loads(out)
    assert payload["sources"] == []
    assert payload["topics"] == []
    assert payload["global_summary_present"] is False


def test_memory_tree_status_text_mode(tmp_path, capsys) -> None:
    rc = helm_module.main([
        "memory-tree",
        "status",
        "--root",
        str(tmp_path / "memory"),
    ])
    out = capsys.readouterr().out
    assert rc == 0
    assert "root:" in out
    assert "sources" in out
    assert "topics" in out


# ---------------------------------------------------------------------------
# compression profiles
# ---------------------------------------------------------------------------


def test_compression_profiles_json_listing(capsys) -> None:
    """The default registry must list at least one profile and parse as JSON."""
    rc = helm_module.main(["compression", "profiles", "--json"])
    out = capsys.readouterr().out
    assert rc == 0
    payload = json.loads(out)
    assert "profiles" in payload
    assert isinstance(payload["profiles"], list)
    # Each profile entry has the expected shape.
    for row in payload["profiles"]:
        assert "profile_id" in row
        assert "input_kinds" in row


def test_compression_profiles_text_listing(capsys) -> None:
    rc = helm_module.main(["compression", "profiles"])
    # text mode produces zero or more lines; the wiring must not crash.
    capsys.readouterr()
    assert rc == 0
