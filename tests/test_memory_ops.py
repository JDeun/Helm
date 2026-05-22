from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def create_workspace(root: Path) -> None:
    (root / ".helm" / "checkpoints").mkdir(parents=True)
    (root / "references").mkdir()
    (root / "skills").mkdir()
    (root / "skill_drafts").mkdir()
    (root / "memory").mkdir()
    (root / ".helm" / "context_sources.json").write_text('{"sources": []}\n', encoding="utf-8")
    (root / ".helm" / "task-ledger.jsonl").write_text("", encoding="utf-8")
    (root / ".helm" / "command-log.jsonl").write_text("", encoding="utf-8")
    (root / ".helm" / "checkpoints" / "index.json").write_text("[]\n", encoding="utf-8")
    (root / "references" / "execution_profiles.json").write_text(
        json.dumps({"profiles": {"inspect_local": {}, "workspace_edit": {}, "service_ops": {}}}),
        encoding="utf-8",
    )
    (root / "references" / "skill_profile_policies.json").write_text(
        json.dumps({"skills": {}}),
        encoding="utf-8",
    )
    (root / "references" / "skill-capture-template.md").write_text("# Template\n", encoding="utf-8")
    (root / "references" / "skill-contract-template.json").write_text("{}\n", encoding="utf-8")


def run_cli(workspace: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["HELM_WORKSPACE"] = str(workspace)
    env["PYTHONPATH"] = str(REPO_ROOT)
    return subprocess.run(
        [sys.executable, str(REPO_ROOT / "helm.py"), *args],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def test_memory_op_history_round_trip() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        create_workspace(root)

        op = run_cli(
            root,
            "memory",
            "op",
            "write",
            "--subject",
            "router policy",
            "--scope",
            "private",
            "--reason",
            "record a write op",
            "--evidence",
            "manual verification",
        )
        assert op.returncode == 0, op.stderr

        history = run_cli(root, "memory", "history", "--json")
        assert history.returncode == 0, history.stderr
        payload = json.loads(history.stdout)
        assert payload["count"] == 1
        assert payload["items"][0]["operation"] == "write"


def test_memory_crystallize_persists_artifact() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        create_workspace(root)
        ledger_entry = {
            "task_id": "task-1",
            "task_name": "router fix",
            "profile": "workspace_edit",
            "status": "completed",
            "memory_capture": {
                "claim_state": {"confidence_hint": "high"},
                "supersession": {"state": "none", "supersedes_task_ids": []},
                "review_flags": [],
                "crystallization": {
                    "question": "What changed?",
                    "action": "Edited router",
                    "result": "router updated",
                    "lesson": "keep policy explicit",
                    "affected_entities": ["skill:router"],
                },
            },
        }
        (root / ".helm" / "task-ledger.jsonl").write_text(json.dumps(ledger_entry) + "\n", encoding="utf-8")

        result = run_cli(root, "memory", "crystallize", "--task-id", "task-1")
        assert result.returncode == 0, result.stderr

        artifact = root / ".helm" / "crystallized-sessions.jsonl"
        assert artifact.exists()
        rows = [json.loads(line) for line in artifact.read_text(encoding="utf-8").splitlines() if line.strip()]
        assert rows[0]["task_id"] == "task-1"


def test_review_queue_surfaces_partial_and_missing_follow_up() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        create_workspace(root)
        entries = [
            {
                "task_id": "task-1",
                "task_name": "refresh router policy",
                "profile": "workspace_edit",
                "status": "completed",
                "memory_capture": {
                    "relevant": True,
                    "finalization_status": "capture_partial",
                    "claim_state": {"confidence_hint": "low"},
                    "review_flags": [{"type": "truth_resolution_review", "severity": "medium"}],
                    "supersession": {"state": "none", "supersedes_task_ids": []},
                },
            },
            {
                "task_id": "task-2",
                "task_name": "rerun router policy refresh",
                "profile": "workspace_edit",
                "status": "completed",
                "memory_capture": {
                    "relevant": True,
                    "finalization_status": "capture_written",
                    "claim_state": {"confidence_hint": "high"},
                    "review_flags": [],
                    "supersession": {"state": "refreshes_prior_state", "supersedes_task_ids": ["task-1"]},
                },
            },
        ]
        (root / ".helm" / "task-ledger.jsonl").write_text(
            "\n".join(json.dumps(entry) for entry in entries) + "\n",
            encoding="utf-8",
        )

        result = run_cli(root, "memory", "review-queue", "--json")
        assert result.returncode == 0, result.stderr
        payload = json.loads(result.stdout)
        assert payload["count"] == 2
        assert payload["items"][0]["task_id"] == "task-2"
        assert "missing_crystallization" in payload["items"][0]["blockers"]
        assert "missing_supersede_op" in payload["items"][0]["blockers"]
        assert "finalization=capture_partial" in payload["items"][1]["blockers"]


def test_review_queue_hides_task_closed_by_supersede_operation() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        create_workspace(root)
        entries = [
            {
                "task_id": "task-old",
                "task_name": "retry router refresh",
                "profile": "workspace_edit",
                "status": "failed",
                "memory_capture": {
                    "relevant": True,
                    "finalization_status": "capture_written",
                    "claim_state": {"confidence_hint": "low"},
                    "review_flags": [{"type": "low_confidence_review", "severity": "low"}],
                    "supersession": {"state": "not_applicable", "supersedes_task_ids": []},
                },
            },
            {
                "task_id": "task-new",
                "task_name": "retry router refresh",
                "profile": "workspace_edit",
                "status": "completed",
                "memory_capture": {
                    "relevant": True,
                    "finalization_status": "capture_written",
                    "claim_state": {"confidence_hint": "high"},
                    "review_flags": [],
                    "supersession": {"state": "refreshes_prior_state", "supersedes_task_ids": ["task-old"]},
                    "crystallization": {"question": "q", "action": "a", "result": "r", "lesson": "l", "affected_entities": []},
                },
            },
        ]
        (root / ".helm" / "task-ledger.jsonl").write_text(
            "\n".join(json.dumps(entry) for entry in entries) + "\n",
            encoding="utf-8",
        )
        (root / ".helm" / "memory-operations.jsonl").write_text(
            json.dumps(
                {
                    "id": "memop-1",
                    "timestamp": "2026-04-20T00:00:00+00:00",
                    "operation": "supersede",
                    "subject": "router retry resolved",
                    "scope": "private",
                    "task_id": "task-new",
                    "supersedes": ["task-old"],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        (root / ".helm" / "crystallized-sessions.jsonl").write_text(
            json.dumps(
                {
                    "id": "crystal-1",
                    "task_id": "task-new",
                    "crystallization": {"question": "q", "result": "r"},
                }
            )
            + "\n",
            encoding="utf-8",
        )

        result = run_cli(root, "memory", "review-queue", "--json")
        assert result.returncode == 0, result.stderr
        payload = json.loads(result.stdout)
        assert payload["count"] == 0


def test_review_queue_ignores_no_capture_low_confidence_tasks() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        create_workspace(root)
        ledger_entry = {
            "task_id": "task-smoke",
            "task_name": "smoke probe",
            "profile": "inspect_local",
            "status": "completed",
            "memory_capture": {
                "relevant": False,
                "finalization_status": "no_capture_needed",
                "claim_state": {"confidence_hint": "low"},
                "review_flags": [{"type": "low_confidence_review", "severity": "low"}],
                "supersession": {"state": "not_applicable", "supersedes_task_ids": []},
            },
        }
        (root / ".helm" / "task-ledger.jsonl").write_text(json.dumps(ledger_entry) + "\n", encoding="utf-8")

        queue = run_cli(root, "memory", "review-queue", "--json")
        assert queue.returncode == 0, queue.stderr
        assert json.loads(queue.stdout)["count"] == 0

        audit = run_cli(root, "memory", "audit-coherence", "--json")
        assert audit.returncode == 0, audit.stderr
        assert json.loads(audit.stdout)["issue_count"] == 0


def test_memory_coherence_audit_reports_cross_layer_gaps() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        create_workspace(root)
        ledger_entry = {
            "task_id": "task-1",
            "task_name": "write durable note",
            "profile": "workspace_edit",
            "status": "completed",
            "memory_capture": {
                "relevant": True,
                "finalization_status": "capture_written",
                "claim_state": {"confidence_hint": "high"},
                "review_flags": [],
                "supersession": {"state": "refreshes_prior_state", "supersedes_task_ids": ["task-old"]},
            },
        }
        (root / ".helm" / "task-ledger.jsonl").write_text(json.dumps(ledger_entry) + "\n", encoding="utf-8")
        (root / ".helm" / "memory-operations.jsonl").write_text(
            json.dumps(
                {
                    "id": "memop-1",
                    "operation": "supersede",
                    "task_id": "task-missing",
                    "supersedes": ["task-old"],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        (root / ".helm" / "crystallized-sessions.jsonl").write_text(
            json.dumps({"id": "crystal-1", "task_id": "task-missing"}) + "\n",
            encoding="utf-8",
        )

        result = run_cli(root, "memory", "audit-coherence", "--json")

        assert result.returncode == 1
        payload = json.loads(result.stdout)
        kinds = {issue["kind"] for issue in payload["issues"]}
        assert "memory_review_queue_blocker" in kinds
        assert "memory_operation_unknown_task" in kinds
        assert "memory_operation_supersedes_unknown_task" in kinds
        assert "crystallized_session_unknown_task" in kinds


def test_memory_ops_append_jsonl_is_concurrent_safe(tmp_path: Path) -> None:
    """Regression guard for the fcntl.LOCK_EX added to ``_append_jsonl``.

    Background
    ----------
    The 2026-05-21 Helm full review §Critical#2 flagged that
    ``scripts.memory_ops._append_jsonl`` previously used a plain
    ``open("a")`` and could interleave bytes with concurrent writers
    (memory_tree, run_with_profile, etc.) on the same shared file.
    The fix takes a POSIX exclusive lock around each write. This test
    spawns many threads that pound on the same path and asserts every
    line round-trips as a valid JSON object — i.e. no torn writes.

    If someone removes the lock, the threadpool will (probabilistically)
    interleave one line inside another and ``json.loads`` will raise.
    """
    from concurrent.futures import ThreadPoolExecutor

    # Lazy import: don't drag fcntl in on Windows test runs.
    sys.path.insert(0, str(REPO_ROOT))
    from scripts.memory_ops import _append_jsonl

    target = tmp_path / "shared.jsonl"
    n_threads = 8
    n_writes_per_thread = 25
    # Use a moderately-long payload so torn writes will straddle line
    # boundaries detectably (a short payload often fits in a single
    # atomic write even without locking).
    payload_template = {"filler": "x" * 256}

    def writer(thread_id: int) -> None:
        for i in range(n_writes_per_thread):
            row = dict(payload_template, thread=thread_id, seq=i)
            _append_jsonl(target, row)

    with ThreadPoolExecutor(max_workers=n_threads) as pool:
        futures = [pool.submit(writer, t) for t in range(n_threads)]
        for f in futures:
            f.result()

    lines = target.read_text(encoding="utf-8").splitlines()
    assert len(lines) == n_threads * n_writes_per_thread

    seen: set[tuple[int, int]] = set()
    for line in lines:
        entry = json.loads(line)  # would raise on a torn write
        assert entry["filler"] == "x" * 256
        key = (entry["thread"], entry["seq"])
        assert key not in seen, f"duplicate entry detected: {key}"
        seen.add(key)
    assert len(seen) == n_threads * n_writes_per_thread


def test_memory_tree_append_ledger_is_concurrent_safe(tmp_path: Path) -> None:
    """Same race-coverage as the memory_ops test, but on the memory_tree path.

    ``MemoryTree._append_ledger`` is the second writer of
    ``~/.helm/task-ledger.jsonl``; the review flagged the same fcntl gap
    here. We invoke the private writer directly with a minimal
    ``RefreshResult`` stub so the test never touches the rest of the
    memory_tree pipeline.
    """
    from concurrent.futures import ThreadPoolExecutor

    sys.path.insert(0, str(REPO_ROOT))
    from memory_tree.tree import MemoryTree, RefreshResult, RefreshTrigger

    root = tmp_path / "memtree-root"
    root.mkdir()
    ledger = tmp_path / "task-ledger.jsonl"
    tree = MemoryTree(root=root, ledger_path=ledger)

    n_threads = 6
    n_writes_per_thread = 20

    def writer(thread_id: int) -> None:
        for i in range(n_writes_per_thread):
            stub = RefreshResult(
                layer="source",
                target=f"src-{thread_id}",
                trigger=RefreshTrigger.CRON,
                reason="concurrent-race-regression-test",
                before_hash="0" * 8,
                after_hash="1" * 8,
                path=root / f"src-{thread_id}.md",
                task_id=f"t-{thread_id}-{i}",
                timestamp="2026-05-21T00:00:00Z",
            )
            tree._append_ledger(stub)  # type: ignore[attr-defined]

    with ThreadPoolExecutor(max_workers=n_threads) as pool:
        futures = [pool.submit(writer, t) for t in range(n_threads)]
        for f in futures:
            f.result()

    lines = ledger.read_text(encoding="utf-8").splitlines()
    assert len(lines) == n_threads * n_writes_per_thread
    ids: set[str] = set()
    for line in lines:
        entry = json.loads(line)
        ids.add(entry["task_id"])
    assert len(ids) == n_threads * n_writes_per_thread
