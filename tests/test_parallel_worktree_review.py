from __future__ import annotations

import inspect
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from parallel_worktree_review import _run, run_parallel_review, validate_candidates


def git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True, shell=False)


def make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "user.email", "test@example.com")
    (repo / "app.txt").write_text("base\n", encoding="utf-8")
    git(repo, "add", "app.txt")
    git(repo, "commit", "-qm", "base")
    return repo


def write_command(path: str, text: str) -> list[str]:
    return [sys.executable, "-c", f"from pathlib import Path; p=Path({path!r}); p.parent.mkdir(parents=True, exist_ok=True); p.write_text({text!r}, encoding='utf-8')"]


def assert_command(path: str, text: str) -> list[str]:
    return [sys.executable, "-c", f"from pathlib import Path; assert Path({path!r}).read_text(encoding='utf-8') == {text!r}"]


def test_two_candidates_are_isolated_patched_compared_and_cleaned(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    before_status = git(repo, "status", "--porcelain=v1").stdout
    before_head = git(repo, "rev-parse", "HEAD").stdout
    before_refs = git(repo, "for-each-ref", "--format=%(refname):%(objectname)", "refs/heads", "refs/tags").stdout
    candidates = [
        {
            "name": "small",
            "command": write_command("app.txt", "base\nsmall\n"),
            "test_command": assert_command("app.txt", "base\nsmall\n"),
        },
        {
            "name": "workflow",
            "command": write_command("scripts/new_tool.py", "print('ok')\n"),
            "test_command": [
                sys.executable,
                "-c",
                "from pathlib import Path; compile(Path('scripts/new_tool.py').read_text(), 'scripts/new_tool.py', 'exec')",
            ],
        },
    ]

    report = run_parallel_review(repo, candidates, tmp_path / "review", profile="risky_edit")

    assert report["ok"] is True
    assert report["original_unchanged"] is True
    assert report["automatic_merge"] is False
    assert report["human_review_required"] is True
    assert report["candidate_count"] == 2
    assert report["execution_mode"] == "parallel"
    assert report["max_workers"] == 2
    assert report["eligible_candidate_count"] == 2
    assert report["selection_blocked"] is False
    assert all(row["completion_evidence"]["eligible"] for row in report["candidates"])
    assert all(row["worktree_cleanup"]["ok"] for row in report["candidates"])
    assert all(row["home_cleanup"]["ok"] for row in report["candidates"])
    assert report["candidates"][0]["policy_risk"]["level"] == "low"
    assert report["candidates"][1]["policy_risk"]["level"] == "high"
    assert report["candidates"][1]["touched_files"] == ["scripts/new_tool.py"]
    assert all(Path(row["diff"]["path"]).stat().st_size > 0 for row in report["candidates"])
    assert report["matrix_readback"] == {"json": True, "markdown": True}
    assert (repo / "app.txt").read_text(encoding="utf-8") == "base\n"
    assert not (repo / "scripts").exists()
    assert git(repo, "status", "--porcelain=v1").stdout == before_status
    assert git(repo, "rev-parse", "HEAD").stdout == before_head
    assert git(repo, "for-each-ref", "--format=%(refname):%(objectname)", "refs/heads", "refs/tags").stdout == before_refs
    worktrees = git(repo, "worktree", "list", "--porcelain").stdout
    assert worktrees.count("worktree ") == 1


def test_dirty_original_worktree_fails_closed_without_creating_review_state(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    dirty = repo / "local-only.txt"
    dirty.write_text("dirty user state\n", encoding="utf-8")
    before_status = git(repo, "status", "--porcelain=v1").stdout
    output = tmp_path / "review"
    with pytest.raises(ValueError, match="must be clean"):
        run_parallel_review(
            repo,
            [{"name": "candidate", "command": write_command("app.txt", "changed\n")}],
            output,
        )
    assert dirty.read_text(encoding="utf-8") == "dirty user state\n"
    assert git(repo, "status", "--porcelain=v1").stdout == before_status
    assert not output.exists()
    assert git(repo, "worktree", "list", "--porcelain").stdout.count("worktree ") == 1


def test_two_candidate_commands_execute_concurrently(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    candidates = [
        {
            "name": name,
            "command": [
                sys.executable,
                "-c",
                f"import time; from pathlib import Path; time.sleep(0.8); Path({name!r}).write_text('done')",
            ],
            "test_command": [sys.executable, "-c", f"from pathlib import Path; assert Path({name!r}).read_text() == 'done'"],
        }
        for name in ("candidate-a", "candidate-b")
    ]
    started = time.monotonic()
    report = run_parallel_review(repo, candidates, tmp_path / "review")
    elapsed = time.monotonic() - started
    assert report["execution_mode"] == "parallel"
    assert all(row["completion_evidence"]["eligible"] for row in report["candidates"])
    assert elapsed < 1.45, f"two 0.8s candidates ran sequentially: {elapsed:.3f}s"


def test_missing_executable_and_failed_test_still_produce_ineligible_matrix(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    candidates = [
        {"name": "missing", "command": ["definitely-not-installed-helm-candidate"], "test_command": None},
        {
            "name": "badtest",
            "command": write_command("app.txt", "changed\n"),
            "test_command": [sys.executable, "-c", "raise SystemExit(7)"],
        },
    ]
    report = run_parallel_review(repo, candidates, tmp_path / "review")
    assert report["ok"] is True
    assert report["candidates"][0]["execution"]["status"] == "error"
    assert report["candidates"][0]["execution"]["exit_code"] == 127
    assert report["candidates"][1]["tests"]["status"] == "failed"
    assert not any(row["completion_evidence"]["eligible"] for row in report["candidates"])
    assert Path(report["matrix_json"]).exists()
    assert (repo / "app.txt").read_text(encoding="utf-8") == "base\n"


def test_timeout_is_recorded_and_worktree_is_removed(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    report = run_parallel_review(
        repo,
        [{"name": "slow", "command": [sys.executable, "-c", "import time; time.sleep(2)"], "timeout_seconds": 0.05}],
        tmp_path / "review",
    )
    row = report["candidates"][0]
    assert row["execution"]["status"] == "timeout"
    assert row["execution"]["exit_code"] == 124
    assert row["worktree_cleanup"]["ok"] is True
    assert row["completion_evidence"]["eligible"] is False


def test_minimal_environment_and_secret_redaction(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    real_home = tmp_path / "real-home"
    (real_home / ".openclaw").mkdir(parents=True)
    (real_home / ".openclaw" / "credentials.txt").write_text("must-not-be-readable\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(real_home))
    fake = "sk-supersecretvalue12345"
    monkeypatch.setenv("OPENAI_API_KEY", fake)
    code = (
        "import os; from pathlib import Path; "
        "print('inherited=' + str(os.getenv('OPENAI_API_KEY'))); "
        "print('home=' + os.environ['HOME']); "
        "print('real_secret_visible=' + str((Path.home()/'.openclaw/credentials.txt').exists())); "
        f"print('api_key={fake}'); Path('candidate.txt').write_text('api_key={fake}\\n')"
    )
    report = run_parallel_review(
        repo,
        [{"name": "redact", "command": [sys.executable, "-c", code], "test_command": [sys.executable, "-c", "raise SystemExit(0)"]}],
        tmp_path / "review",
    )
    row = report["candidates"][0]
    patch_text = Path(row["diff"]["path"]).read_text(encoding="utf-8")
    matrix_text = Path(report["matrix_json"]).read_text(encoding="utf-8")
    assert "inherited=None" in row["execution"]["stdout"]
    assert "real_secret_visible=False" in row["execution"]["stdout"]
    assert row["isolation"]["home"] != str(real_home)
    assert row["isolation"]["home_isolated"] is True
    assert row["isolation"]["os_sandbox"] is False
    assert row["home_cleanup"]["ok"] is True
    assert not Path(row["isolation"]["home"]).exists()
    assert fake not in row["execution"]["stdout"]
    assert fake not in patch_text
    assert fake not in matrix_text
    assert "[REDACTED]" in row["execution"]["stdout"]
    assert row["diff"]["redaction_count"] > 0
    assert row["policy_risk"]["level"] == "high"
    assert row["completion_evidence"]["eligible"] is False


def test_test_command_source_mutation_is_captured_and_blocks_completion(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    report = run_parallel_review(
        repo,
        [
            {
                "name": "toctou",
                "command": write_command("app.txt", "candidate\n"),
                "test_command": write_command("app.txt", "mutated-by-test\n"),
            }
        ],
        tmp_path / "review",
    )
    row = report["candidates"][0]
    assert row["tests"]["status"] == "passed"
    assert row["completion_evidence"]["tests_preserved_candidate_state"] is False
    assert row["completion_evidence"]["eligible"] is False
    assert "mutated-by-test" in Path(row["diff"]["path"]).read_text(encoding="utf-8")
    assert (repo / "app.txt").read_text(encoding="utf-8") == "base\n"


def test_original_worktree_references_are_rejected(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    command = [sys.executable, "-c", f"from pathlib import Path; Path({str(repo / 'escape.txt')!r}).touch()"]
    with pytest.raises(ValueError, match="original worktree"):
        run_parallel_review(repo, [{"name": "escape", "command": command}], tmp_path / "review")


@pytest.mark.parametrize(
    "command",
    [
        ["sh", "-c", "echo bad"],
        ["env", "python3", "-c", "print('bad')"],
        ["git", "-C", "/tmp/repo", "commit", "-m", "bad"],
    ],
)
def test_shell_wrappers_and_all_direct_git_commands_are_rejected(command: list[str]) -> None:
    with pytest.raises(ValueError, match="may not invoke"):
        validate_candidates([{"name": "bad", "command": command}])


def test_limits_paths_profile_and_output_location_are_enforced(tmp_path: Path) -> None:
    base = {"command": [sys.executable, "-c", "pass"]}
    with pytest.raises(ValueError, match="one or two"):
        validate_candidates([{**base, "name": "a"}, {**base, "name": "b"}, {**base, "name": "c"}])
    with pytest.raises(ValueError, match="unsafe"):
        validate_candidates([{**base, "name": "../escape"}])
    repo = make_repo(tmp_path)
    with pytest.raises(ValueError, match="risky_edit"):
        run_parallel_review(repo, [{**base, "name": "safe"}], tmp_path / "review", profile="workspace_edit")
    with pytest.raises(ValueError, match="outside"):
        run_parallel_review(repo, [{**base, "name": "safe"}], repo / "review")
    output = tmp_path / "occupied"
    output.mkdir()
    (output / "old.json").write_text("{}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="new or empty"):
        run_parallel_review(repo, [{**base, "name": "safe"}], output)


def test_execution_choke_point_explicitly_disables_shell() -> None:
    assert "shell=False" in inspect.getsource(_run)
