from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

import helm
from scripts import command_log_report, ops_daily_report, task_ledger_report
from scripts import run_with_profile


def test_command_log_report_skips_malformed_lines() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "command-log.jsonl"
        path.write_text('{"component":"runner"}\nnot-json\n{"label":"ok"}\n', encoding="utf-8")

        with patch.object(command_log_report, "COMMAND_LOG", path):
            rows = command_log_report.load_entries()

        assert len(rows) == 2


def test_task_ledger_report_skips_malformed_lines() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "task-ledger.jsonl"
        path.write_text('{"task_id":"1"}\nnot-json\n{"task_id":"2"}\n', encoding="utf-8")

        with patch.object(task_ledger_report, "TASK_LEDGER", path):
            rows = task_ledger_report.load_entries()

        assert [row["task_id"] for row in rows] == ["1", "2"]


def test_ops_daily_report_tolerates_invalid_checkpoint_and_assessment_json() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        workspace = Path(tmpdir)
        state_root = workspace / ".helm"
        drafts_root = workspace / "skill_drafts"
        (state_root / "checkpoints").mkdir(parents=True)
        (drafts_root / "draft-a" / "meta").mkdir(parents=True)
        (state_root / "checkpoints" / "index.json").write_text("{not-json\n", encoding="utf-8")
        (drafts_root / "draft-a" / "meta" / "assessment.json").write_text("{not-json\n", encoding="utf-8")

        with patch.object(ops_daily_report, "STATE_ROOT", state_root), patch.object(
            ops_daily_report, "DRAFTS_ROOT", drafts_root
        ):
            assert ops_daily_report.load_checkpoints() == []
            assert ops_daily_report.load_draft_assessments() == []


def test_helm_status_payload_tolerates_malformed_state_files() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        workspace = Path(tmpdir)
        state_root = workspace / ".helm"
        drafts_root = workspace / "skill_drafts"
        (state_root / "checkpoints").mkdir(parents=True)
        (drafts_root / "draft-a" / "meta").mkdir(parents=True)
        (state_root / "task-ledger.jsonl").write_text('{"task_id":"ok-1","status":"completed"}\nnot-json\n', encoding="utf-8")
        (state_root / "command-log.jsonl").write_text('{"label":"cmd","exit_code":0}\n', encoding="utf-8")
        (state_root / "memory-operations.jsonl").write_text('{"id":"memop-1"}\n', encoding="utf-8")
        (state_root / "crystallized-sessions.jsonl").write_text('{"id":"crystal-1"}\n', encoding="utf-8")
        (state_root / "checkpoints" / "index.json").write_text("{not-json\n", encoding="utf-8")
        (drafts_root / "draft-a" / "meta" / "assessment.json").write_text("{not-json\n", encoding="utf-8")

        with patch.object(helm, "configured_context_sources", return_value=[]):
            payload = helm.build_status_payload(workspace)

        assert payload["recent_tasks"][0]["task_id"] == "ok-1"
        assert payload["recent_checkpoints"] == []
        assert payload["draft_assessments"] == []


def test_run_with_profile_load_checkpoints_reports_invalid_json_cleanly() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "index.json"
        path.write_text("{not-json\n", encoding="utf-8")

        with patch.object(run_with_profile, "CHECKPOINT_INDEX", path):
            with pytest.raises(SystemExit, match="Invalid checkpoint index"):
                run_with_profile.load_checkpoints()


def test_run_with_profile_latest_task_entries_reports_non_object_line_cleanly() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "task-ledger.jsonl"
        path.write_text('"not-an-object"\n', encoding="utf-8")

        with patch.object(run_with_profile, "TASK_LEDGER", path):
            with pytest.raises(SystemExit, match="expected JSON object"):
                run_with_profile.latest_task_entries()
