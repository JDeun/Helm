from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace


def create_workspace(root: Path) -> None:
    (root / ".helm" / "checkpoints").mkdir(parents=True)
    (root / ".helm" / "context_sources.json").write_text('{"sources": []}\n', encoding="utf-8")
    (root / ".helm" / "task-ledger.jsonl").write_text("", encoding="utf-8")
    (root / ".helm" / "command-log.jsonl").write_text("", encoding="utf-8")
    (root / ".helm" / "checkpoints" / "index.json").write_text("[]\n", encoding="utf-8")
    (root / "references").mkdir()
    (root / "references" / "execution_profiles.json").write_text('{"profiles": {}}\n', encoding="utf-8")
    (root / "references" / "model_recovery_policy.json").write_text('{"models": []}\n', encoding="utf-8")
    (root / "references" / "skill-capture-template.md").write_text("# Template\n", encoding="utf-8")
    (root / "references" / "skill-contract-template.json").write_text("{}\n", encoding="utf-8")
    (root / "skills").mkdir()
    (root / "skill_drafts").mkdir()
    (root / "memory").mkdir()


def write_lifecycle_candidate(root: Path) -> None:
    lifecycle = root / ".helm" / "skill-lifecycle"
    lifecycle.mkdir(parents=True)
    (lifecycle / "events.jsonl").write_text(
        json.dumps(
            {
                "ts": "2026-05-10T00:00:00+00:00",
                "event": "skill_failure",
                "skill_id": "alpha",
                "outcome": {
                    "schema_version": 2,
                    "task_id": "task-1",
                    "status": "failure",
                    "improvement_candidate": True,
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )


def test_run_script_returns_124_when_child_times_out(monkeypatch, capsys, tmp_path: Path) -> None:
    import commands

    create_workspace(tmp_path)

    def fail_with_timeout(*_: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd=[sys.executable], timeout=kwargs.get("timeout"))

    monkeypatch.setenv("HELM_SCRIPT_TIMEOUT_SECONDS", "0.01")
    monkeypatch.setattr(commands.subprocess, "run", fail_with_timeout)

    assert commands.run_script("memory_ops.py", ["--help"], tmp_path) == 124
    assert "script timed out" in capsys.readouterr().err


def test_script_timeout_can_be_disabled(monkeypatch) -> None:
    import commands

    monkeypatch.setenv("HELM_SCRIPT_TIMEOUT_SECONDS", "0")
    assert commands.script_timeout_seconds() is None


def test_promote_from_trajectory_apply_timeout_returns_124(monkeypatch, capsys, tmp_path: Path) -> None:
    from commands import skill_lifecycle

    create_workspace(tmp_path)
    write_lifecycle_candidate(tmp_path)

    def fail_with_timeout(*_: object, **__: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd=["skill_capture.py"], timeout=120, output="partial\n")

    monkeypatch.setattr(skill_lifecycle.subprocess, "run", fail_with_timeout)
    args = SimpleNamespace(
        path=str(tmp_path),
        task_id=None,
        name="alpha-improved",
        description="Improved alpha skill",
        limit=None,
        apply=True,
        json=True,
    )

    assert skill_lifecycle.cmd_skill_lifecycle_promote_from_trajectory(args) == 124
    payload = json.loads(capsys.readouterr().out)
    assert payload["returncode"] == 124
    assert "timed out" in payload["stderr"]
