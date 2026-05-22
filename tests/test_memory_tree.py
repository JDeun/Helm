"""Tests for memory_tree (helm-arch-2026-05-21 §1)."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


from memory_tree import (  # noqa: E402  (path adjusted above)
    GlobalSummary,
    MemoryTree,
    MemoryTreePaths,
    RefreshTrigger,
    SourceSummary,
    TopicSummary,
    compute_hash,
)
from memory_tree.tree import (  # noqa: E402
    LEDGER_KIND,
    _parse_frontmatter,
    _render_frontmatter,
    _sanitize_id,
    openclaw_mirror_paths,
)


def _make_tree(tmp: Path) -> MemoryTree:
    root = tmp / "memory"
    ledger = tmp / "task-ledger.jsonl"
    tree = MemoryTree(root=root, ledger_path=ledger)
    tree.ensure_directories()
    return tree


def _read_ledger(tree: MemoryTree) -> list[dict]:
    if not tree.ledger_path.exists():
        return []
    out = []
    for line in tree.ledger_path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            out.append(json.loads(line))
    return out


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def test_compute_hash_is_stable_and_short() -> None:
    assert compute_hash("hello") == compute_hash("hello")
    assert compute_hash("hello") != compute_hash("world")
    assert len(compute_hash("hello")) == 16


def test_sanitize_id_strips_unsafe_chars() -> None:
    assert _sanitize_id("Helm") == "helm"
    assert _sanitize_id("github-tinyhumansai/openhuman") == "github-tinyhumansai-openhuman"
    assert _sanitize_id("  sheets:household ledger  ") == "sheets-household-ledger"


def test_sanitize_id_rejects_empty() -> None:
    import pytest

    with pytest.raises(ValueError):
        _sanitize_id("")
    with pytest.raises(ValueError):
        _sanitize_id("   ")


def test_frontmatter_roundtrip() -> None:
    data = {
        "kind": "source_summary",
        "source_id": "telegram",
        "linked_sources": ["telegram", "calendar-personal"],
        "freshness_sla_minutes": 10,
    }
    text = _render_frontmatter(data) + "body line\n"
    parsed, body = _parse_frontmatter(text)
    assert parsed["source_id"] == "telegram"
    assert parsed["freshness_sla_minutes"] == 10
    assert parsed["linked_sources"] == ["telegram", "calendar-personal"]
    assert body.strip() == "body line"


def test_frontmatter_roundtrip_literal_bool_strings_stay_string() -> None:
    """String values that look like YAML literals (true/false/null) must
    survive round-trip as strings, not get coerced to bool/None."""
    data = {
        "kind": "topic_summary",
        "summary_token": "true",
        "other_token": "false",
        "null_like": "null",
    }
    parsed, _ = _parse_frontmatter(_render_frontmatter(data) + "\n")
    assert parsed["summary_token"] == "true"
    assert parsed["other_token"] == "false"
    assert parsed["null_like"] == "null"
    assert isinstance(parsed["summary_token"], str)


def test_frontmatter_roundtrip_leading_dash_stays_string() -> None:
    """A string starting with ``-`` (e.g. negative-looking task id) must
    not be reinterpreted as a numeric literal."""
    data = {"task_label": "-archive"}
    parsed, _ = _parse_frontmatter(_render_frontmatter(data) + "\n")
    assert parsed["task_label"] == "-archive"
    # also: pure negative-int strings stay strings
    data2 = {"task_label": "-1"}
    parsed2, _ = _parse_frontmatter(_render_frontmatter(data2) + "\n")
    assert parsed2["task_label"] == "-1"


def test_frontmatter_roundtrip_list_element_with_comma_preserves_element() -> None:
    """A list element containing ``,`` inside a quoted string must
    survive as a single element (not split across the comma)."""
    data = {
        "linked_tasks": ["helm-arch-2026-05-21#4", "helm, openclaw split"],
    }
    text = _render_frontmatter(data) + "\n"
    parsed, _ = _parse_frontmatter(text)
    assert parsed["linked_tasks"] == [
        "helm-arch-2026-05-21#4",
        "helm, openclaw split",
    ]


def test_frontmatter_roundtrip_list_element_with_colon_or_hash() -> None:
    data = {"items": ["a:b", "c#d", "plain"]}
    parsed, _ = _parse_frontmatter(_render_frontmatter(data) + "\n")
    assert parsed["items"] == ["a:b", "c#d", "plain"]


# ---------------------------------------------------------------------------
# layer writes
# ---------------------------------------------------------------------------


def test_refresh_source_writes_file_and_ledger() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        tree = _make_tree(Path(tmpdir))
        result = tree.refresh_source(
            "telegram",
            "최근 메시지 3건 수신.",
            trigger=RefreshTrigger.CRON,
            reason="cron auto-fetch",
        )
        assert result.layer == "source"
        assert result.target == "telegram"
        assert result.trigger is RefreshTrigger.CRON
        assert result.changed  # before was empty file -> different hash
        assert result.path.exists()

        text = result.path.read_text(encoding="utf-8")
        assert text.startswith("---")
        assert "source_id: telegram" in text
        assert "최근 메시지 3건 수신." in text

        ledger = _read_ledger(tree)
        assert len(ledger) == 1
        entry = ledger[0]
        assert entry["kind"] == LEDGER_KIND
        assert entry["layer"] == "source"
        assert entry["target"] == "telegram"
        assert entry["trigger"] == "cron"
        assert entry["reason"] == "cron auto-fetch"
        assert entry["before_hash"] != entry["after_hash"]
        assert entry["task_id"]


def test_refresh_topic_links_sources_and_tasks() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        tree = _make_tree(Path(tmpdir))
        summary = TopicSummary(
            topic_id="helm",
            summary_text="Helm Memory Tree 구현 진행 중.",
            linked_sources=["telegram", "github-trending"],
            linked_tasks=["helm-arch-2026-05-21#4"],
        )
        result = tree.refresh_topic(
            "helm",
            summary,
            trigger=RefreshTrigger.TASK_LEDGER_CHANGE,
            reason="task #4 progressed",
        )
        assert result.layer == "topic"
        assert result.target == "helm"
        text = result.path.read_text(encoding="utf-8")
        assert "topic_id: helm" in text
        assert "linked_sources: [telegram, github-trending]" in text
        # task id with `#` requires quoting
        assert "helm-arch-2026-05-21#4" in text

        readback = tree.read_topic("helm")
        assert readback is not None
        assert readback.topic_id == "helm"
        assert readback.linked_sources == ["telegram", "github-trending"]
        assert "구현 진행 중" in readback.summary_text


def test_refresh_global_composes_from_topics() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        tree = _make_tree(Path(tmpdir))
        tree.refresh_topic("helm", "Helm body.", reason="seed")
        tree.refresh_topic("openclaw", "OpenClaw body.", reason="seed")

        result = tree.refresh_for_global_compact(reason="weekly compact")
        assert result.layer == "global"
        assert result.target == "current"
        assert result.trigger is RefreshTrigger.GLOBAL_COMPACT

        gs = tree.read_global()
        assert gs is not None
        assert set(gs.included_topics) == {"helm", "openclaw"}
        assert "## helm" in gs.summary_text
        assert "## openclaw" in gs.summary_text


def test_global_summary_handles_empty_tree() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        tree = _make_tree(Path(tmpdir))
        result = tree.refresh_for_global_compact(reason="empty")
        assert result.path.exists()
        gs = tree.read_global()
        assert gs is not None
        assert gs.included_topics == []
        assert "(no topic summaries)" in gs.summary_text


# ---------------------------------------------------------------------------
# hashes / idempotency
# ---------------------------------------------------------------------------


def test_no_op_refresh_yields_same_hash() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        tree = _make_tree(Path(tmpdir))
        # write once with explicit fixed timestamps so a no-op refresh truly is one
        fixed = "2026-05-21T00:00:00Z"
        first = tree.refresh_source(
            "telegram",
            SourceSummary(
                source_id="telegram",
                summary_text="same body",
                last_seen=fixed,
                last_success=fixed,
            ),
            trigger=RefreshTrigger.CRON,
        )
        second = tree.refresh_source(
            "telegram",
            SourceSummary(
                source_id="telegram",
                summary_text="same body",
                last_seen=fixed,
                last_success=fixed,
            ),
            trigger=RefreshTrigger.CRON,
        )
        assert first.after_hash == second.after_hash
        assert not second.changed
        # ledger still records the attempt (auditable refresh, even if no-op)
        ledger = _read_ledger(tree)
        assert len(ledger) == 2
        assert ledger[1]["changed"] is False


def test_changed_refresh_yields_different_hashes() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        tree = _make_tree(Path(tmpdir))
        r1 = tree.refresh_source("telegram", "first body")
        r2 = tree.refresh_source("telegram", "second body different")
        assert r1.after_hash != r2.after_hash
        assert r2.before_hash == r1.after_hash


# ---------------------------------------------------------------------------
# trigger entry points (5 of them)
# ---------------------------------------------------------------------------


def test_five_refresh_triggers_recorded_in_ledger() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        tree = _make_tree(Path(tmpdir))

        # 1) cron — source only
        tree.refresh_for_cron("calendar-personal", "오늘 일정 2건.")

        # 2) telegram answer — source + topic
        tree.refresh_for_telegram_answer(
            "telegram",
            "household",
            source_summary="latest message",
            topic_summary="household state",
        )

        # 3) task ledger change — topic only
        tree.refresh_for_task_ledger_change("helm", "helm progressed")

        # 4) obsidian user edit — source + topic
        tree.refresh_for_obsidian_user_edit(
            "obsidian-vault",
            "career",
            source_summary="vault updated",
            topic_summary="career roadmap edited",
        )

        # 5) global compact
        tree.refresh_for_global_compact(reason="weekly")

        triggers = [entry["trigger"] for entry in _read_ledger(tree)]
        assert "cron" in triggers
        assert "telegram_answer" in triggers
        assert "task_ledger_change" in triggers
        assert "obsidian_user_edit" in triggers
        assert "global_compact" in triggers

        # cron must not touch the topic layer
        cron_entries = [e for e in _read_ledger(tree) if e["trigger"] == "cron"]
        assert all(e["layer"] == "source" for e in cron_entries)

        # task_ledger_change must only refresh topic
        tlc_entries = [e for e in _read_ledger(tree) if e["trigger"] == "task_ledger_change"]
        assert all(e["layer"] == "topic" for e in tlc_entries)


def test_telegram_answer_respects_action_scope() -> None:
    """Telegram answer trigger must not touch unrelated topics."""

    with tempfile.TemporaryDirectory() as tmpdir:
        tree = _make_tree(Path(tmpdir))
        # seed an unrelated topic
        seed = tree.refresh_topic("vehicle", "vehicle body untouched", reason="seed")
        seed_hash = seed.after_hash

        tree.refresh_for_telegram_answer(
            "telegram",
            "household",
            source_summary="msg",
            topic_summary="household answer",
        )

        # the vehicle topic must be byte-identical
        vehicle_after = compute_hash(tree.topic_path("vehicle").read_text(encoding="utf-8"))
        assert vehicle_after == seed_hash


# ---------------------------------------------------------------------------
# read paths / listing / mirror
# ---------------------------------------------------------------------------


def test_list_sources_and_topics() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        tree = _make_tree(Path(tmpdir))
        tree.refresh_source("telegram", "a")
        tree.refresh_source("calendar-personal", "b")
        tree.refresh_topic("helm", "x")

        assert tree.list_sources() == ["calendar-personal", "telegram"]
        assert tree.list_topics() == ["helm"]


def test_read_missing_returns_none() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        tree = _make_tree(Path(tmpdir))
        assert tree.read_source("does-not-exist") is None
        assert tree.read_topic("nope") is None
        assert tree.read_global() is None


def test_openclaw_mirror_paths_do_not_create_files() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        workspace = Path(tmpdir) / "ws"
        paths = openclaw_mirror_paths(workspace)
        assert paths.source_dir == workspace / "memory" / "source_summary"
        assert paths.topic_dir == workspace / "memory" / "topic_summary"
        assert paths.global_file == workspace / "memory" / "global_summary.md"
        # mirror helper must not touch the filesystem
        assert not paths.source_dir.exists()


def test_atomic_write_replaces_existing_file_without_partial_state() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        tree = _make_tree(Path(tmpdir))
        tree.refresh_source("telegram", "v1")
        # simulate concurrent read while we rewrite
        path = tree.source_path("telegram")
        original = path.read_text(encoding="utf-8")
        tree.refresh_source("telegram", "v2 new")
        new = path.read_text(encoding="utf-8")
        assert "v1" not in new
        assert "v2 new" in new
        assert original != new


def test_ledger_entries_are_valid_json_lines() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        tree = _make_tree(Path(tmpdir))
        tree.refresh_source("telegram", "a")
        tree.refresh_topic("helm", "b")
        tree.refresh_for_global_compact()
        raw = tree.ledger_path.read_text(encoding="utf-8").splitlines()
        for line in raw:
            if not line.strip():
                continue
            entry = json.loads(line)
            assert entry["kind"] == LEDGER_KIND
            assert "before_hash" in entry
            assert "after_hash" in entry
            assert "trigger" in entry
            assert "timestamp" in entry


def test_memory_tree_paths_from_root_resolves_user() -> None:
    paths = MemoryTreePaths.from_root("~/.helm/memory")
    assert paths.root.is_absolute()
    assert paths.source_dir.name == "source"
    assert paths.topic_dir.name == "topic"
    assert paths.global_file.name == "current.md"


def test_append_ledger_delegates_to_state_io(monkeypatch) -> None:
    """R5 M4: ``_append_ledger`` must go through ``state_io.append_jsonl_atomic``.

    Pre-R5 ``memory_tree._append_ledger`` inlined an ``fcntl.flock``
    block that omitted ``os.fsync``; the rest of Helm uses
    ``state_io.append_jsonl_atomic`` which fsyncs after every write.
    This test pins the centralization contract so a future refactor
    cannot quietly reintroduce the divergence.
    """
    import scripts.state_io as state_io_mod

    captured: list[tuple[Path, dict]] = []
    original = state_io_mod.append_jsonl_atomic

    def spy(path, entry):
        captured.append((path, dict(entry)))
        return original(path, entry)

    monkeypatch.setattr(state_io_mod, "append_jsonl_atomic", spy)

    with tempfile.TemporaryDirectory() as tmpdir:
        tree = _make_tree(Path(tmpdir))
        tree.refresh_source("telegram", "alpha")
        assert captured, "expected state_io.append_jsonl_atomic to be invoked"
        path, entry = captured[-1]
        assert path == tree.ledger_path
        assert entry["kind"] == LEDGER_KIND
        # File on disk must still hold the row (i.e. the delegation
        # actually performed the write).
        raw = tree.ledger_path.read_text(encoding="utf-8").splitlines()
        assert raw, "ledger should contain at least one row"
        last = json.loads(raw[-1])
        assert last["kind"] == LEDGER_KIND
