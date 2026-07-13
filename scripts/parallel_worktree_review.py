#!/usr/bin/env python3
"""Isolated two-candidate review for risky edits; never merges automatically."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
SHELLS = frozenset({"sh", "bash", "zsh", "fish", "dash", "ksh", "csh", "tcsh", "pwsh", "powershell", "cmd", "cmd.exe"})
WRAPPERS = frozenset({"env", "command", "nohup", "nice", "sudo", "xargs"})
SECRET_ENV_RE = re.compile(r"api.?key|token|secret|password|passwd|credential|authorization|cookie", re.IGNORECASE)
OUTPUT_LIMIT = 8000


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _run(
    command: list[str],
    *,
    cwd: Path,
    timeout: float,
    env: dict[str, str] | None = None,
    text: bool = True,
) -> subprocess.CompletedProcess[Any]:
    """The only execution choke point. Candidate and test commands use shell=False."""
    return subprocess.run(
        command,
        cwd=str(cwd),
        capture_output=True,
        text=text,
        check=False,
        timeout=timeout,
        shell=False,
        env=env,
    )


def _git(repo: Path, *args: str, timeout: float = 30, text: bool = True) -> subprocess.CompletedProcess[Any]:
    return _run(["git", "-C", str(repo), *args], cwd=repo, timeout=timeout, text=text)


def _require_git(repo: Path, *args: str, timeout: float = 30, text: bool = True) -> subprocess.CompletedProcess[Any]:
    result = _git(repo, *args, timeout=timeout, text=text)
    if result.returncode != 0:
        stderr = result.stderr if isinstance(result.stderr, str) else result.stderr.decode("utf-8", errors="replace")
        raise RuntimeError(f"git {' '.join(args)} failed: {stderr.strip()}")
    return result


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _validate_command(raw: object, *, label: str) -> list[str]:
    if not isinstance(raw, list) or not raw or not all(isinstance(item, str) and item and "\0" not in item for item in raw):
        raise ValueError(f"{label} must be a non-empty argv string list")
    command = list(raw)
    executable = Path(command[0]).name.casefold()
    if executable in SHELLS or executable in WRAPPERS:
        raise ValueError(f"{label} may not invoke a shell or command wrapper")
    if executable == "git":
        raise ValueError(f"{label} may not invoke git directly")
    return command


def validate_candidates(raw: object) -> list[dict[str, Any]]:
    if not isinstance(raw, list) or not 1 <= len(raw) <= 2:
        raise ValueError("parallel review requires one or two candidates")
    result: list[dict[str, Any]] = []
    names: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("candidate must be an object")
        name = str(item.get("name") or "")
        if not NAME_RE.fullmatch(name) or name in names:
            raise ValueError(f"unsafe or duplicate candidate name: {name!r}")
        names.add(name)
        timeout = float(item.get("timeout_seconds") or 300)
        if not 0 < timeout <= 1800:
            raise ValueError("candidate timeout_seconds must be between 0 and 1800")
        result.append(
            {
                "name": name,
                "command": _validate_command(item.get("command"), label=f"{name}.command"),
                "test_command": (
                    _validate_command(item.get("test_command"), label=f"{name}.test_command")
                    if item.get("test_command") is not None
                    else None
                ),
                "timeout_seconds": timeout,
            }
        )
    return result


def _state_snapshot(repo: Path) -> dict[str, str]:
    head = _require_git(repo, "rev-parse", "HEAD").stdout.strip()
    status = _require_git(repo, "status", "--porcelain=v1", "-z", "--untracked-files=all").stdout
    refs = _require_git(repo, "for-each-ref", "--format=%(refname)%00%(objectname)", "refs/heads", "refs/tags").stdout
    staged = _require_git(repo, "diff", "--cached", "--binary", text=False).stdout
    unstaged = _require_git(repo, "diff", "--binary", text=False).stdout
    digest = hashlib.sha256()
    for value in (head.encode(), status.encode(), refs.encode(), staged, unstaged):
        digest.update(value)
        digest.update(b"\0")
    return {"head": head, "status": status, "refs": refs, "digest": digest.hexdigest()}


def _truncate(value: str, limit: int = OUTPUT_LIMIT) -> str:
    if len(value) <= limit:
        return value
    half = limit // 2
    return f"{value[:half]}\n[... truncated {len(value) - limit} chars ...]\n{value[-half:]}"


def _secret_values() -> tuple[str, ...]:
    return tuple(
        sorted(
            {value for key, value in os.environ.items() if SECRET_ENV_RE.search(key) and len(value) >= 8},
            key=len,
            reverse=True,
        )
    )


def _redact(value: str, secrets: tuple[str, ...]) -> tuple[str, int]:
    text = value
    count = 0
    for secret in secrets:
        occurrences = text.count(secret)
        if occurrences:
            text = text.replace(secret, "[REDACTED]")
            count += occurrences
    patterns = (
        re.compile(r"(?i)\b(api[_-]?key|token|secret|password|authorization)(\s*[:=]\s*)([^\s,;]+)"),
        re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/-]{8,}"),
        re.compile(r"\b(?:sk-[A-Za-z0-9_-]{8,}|gh[pousr]_[A-Za-z0-9]{8,}|AIza[0-9A-Za-z_-]{20,})\b"),
        re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
    )
    for pattern in patterns:
        text, replaced = pattern.subn(
            (lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]") if pattern is patterns[0] else "[REDACTED]",
            text,
        )
        count += replaced
    return text, count


def _minimal_env(candidate: str, home: Path) -> dict[str, str]:
    allowed = ("PATH", "LANG", "LC_ALL", "LC_CTYPE")
    env = {key: os.environ[key] for key in allowed if os.environ.get(key)}
    tmpdir = home / "tmp"
    tmpdir.mkdir(parents=True, exist_ok=True)
    env.update(
        {
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0",
            "HOME": str(home),
            "TMPDIR": str(tmpdir),
            "XDG_CACHE_HOME": str(home / ".cache"),
            "XDG_CONFIG_HOME": str(home / ".config"),
            "XDG_DATA_HOME": str(home / ".local" / "share"),
            "XDG_STATE_HOME": str(home / ".local" / "state"),
            "PARALLEL_REVIEW": "1",
            "PARALLEL_REVIEW_CANDIDATE": candidate,
            "PARALLEL_REVIEW_NO_MERGE": "1",
        }
    )
    return env


def _redact_command(command: list[str], secrets: tuple[str, ...]) -> tuple[list[str], int]:
    redacted: list[str] = []
    count = 0
    for item in command:
        value, replacements = _redact(item, secrets)
        redacted.append(value)
        count += replacements
    return redacted, count


def _command_result(command: list[str], cwd: Path, timeout: float, env: dict[str, str], secrets: tuple[str, ...]) -> dict[str, Any]:
    started = time.monotonic()
    try:
        result = _run(command, cwd=cwd, timeout=timeout, env=env)
        stdout, stdout_redactions = _redact(result.stdout or "", secrets)
        stderr, stderr_redactions = _redact(result.stderr or "", secrets)
        redacted_command, command_redactions = _redact_command(command, secrets)
        return {
            "status": "passed" if result.returncode == 0 else "failed",
            "command": redacted_command,
            "exit_code": result.returncode,
            "duration_seconds": round(time.monotonic() - started, 3),
            "stdout": _truncate(stdout),
            "stderr": _truncate(stderr),
            "redaction_count": command_redactions + stdout_redactions + stderr_redactions,
        }
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout.decode("utf-8", errors="replace") if isinstance(exc.stdout, bytes) else exc.stdout or ""
        stderr = exc.stderr.decode("utf-8", errors="replace") if isinstance(exc.stderr, bytes) else exc.stderr or ""
        stdout, stdout_redactions = _redact(stdout, secrets)
        stderr, stderr_redactions = _redact(stderr, secrets)
        redacted_command, command_redactions = _redact_command(command, secrets)
        return {
            "status": "timeout",
            "command": redacted_command,
            "exit_code": 124,
            "duration_seconds": round(time.monotonic() - started, 3),
            "stdout": _truncate(stdout),
            "stderr": _truncate(stderr),
            "redaction_count": command_redactions + stdout_redactions + stderr_redactions,
        }
    except OSError as exc:
        error, redactions = _redact(str(exc), secrets)
        redacted_command, command_redactions = _redact_command(command, secrets)
        return {
            "status": "error",
            "command": redacted_command,
            "exit_code": 127,
            "duration_seconds": round(time.monotonic() - started, 3),
            "stdout": "",
            "stderr": _truncate(error),
            "redaction_count": command_redactions + redactions,
        }


def _reject_original_repo_references(specs: list[dict[str, Any]], repo: Path) -> None:
    """Reject obvious attempts to target the original worktree instead of the detached copy."""
    marker = str(repo.resolve())
    for spec in specs:
        for label in ("command", "test_command"):
            command = spec[label]
            if command is None:
                continue
            for argument in command:
                if marker in argument:
                    raise ValueError(f"{spec['name']}.{label} may not reference the original worktree")
                candidate = argument.split("=", 1)[-1]
                path = Path(candidate).expanduser()
                if path.is_absolute() and _inside(path, repo):
                    raise ValueError(f"{spec['name']}.{label} may not target the original worktree")


def _untracked(worktree: Path) -> list[str]:
    output = _require_git(worktree, "ls-files", "--others", "--exclude-standard", "-z").stdout
    return [item for item in output.split("\0") if item]


def _worktree_content_digest(worktree: Path, base_sha: str) -> str:
    """Hash the candidate-visible Git diff, including non-ignored untracked files."""
    digest = hashlib.sha256()
    digest.update(_require_git(worktree, "diff", "--binary", base_sha, "--", text=False).stdout)
    for relative in _untracked(worktree):
        path = worktree / relative
        digest.update(relative.encode("utf-8", errors="surrogateescape"))
        digest.update(b"\0")
        if path.is_symlink():
            digest.update(b"symlink\0")
            digest.update(os.readlink(path).encode("utf-8", errors="surrogateescape"))
        elif path.is_file():
            digest.update(b"file\0")
            digest.update(path.read_bytes())
        else:
            digest.update(b"missing-or-special\0")
        digest.update(b"\0")
    return digest.hexdigest()


def _cleanup_isolated_path(path: Path, secrets: tuple[str, ...]) -> dict[str, Any]:
    result: dict[str, Any] = {"attempted": os.path.lexists(path), "ok": True, "path": str(path), "redaction_count": 0}
    try:
        if path.is_symlink() or (os.path.lexists(path) and not path.is_dir()):
            path.unlink()
        elif path.exists():
            shutil.rmtree(path)
        result["ok"] = not os.path.lexists(path)
    except OSError as exc:
        error, redactions = _redact(str(exc), secrets)
        result.update({"ok": False, "error": _truncate(error), "redaction_count": redactions})
    return result


def _capture_patch(worktree: Path, base_sha: str, patch_path: Path, secrets: tuple[str, ...]) -> tuple[list[str], dict[str, Any]]:
    untracked = _untracked(worktree)
    if untracked:
        _require_git(worktree, "add", "-N", "--", *untracked)
    touched_output = _require_git(worktree, "diff", "--name-only", "-z", base_sha, "--").stdout
    touched = [item for item in touched_output.split("\0") if item]
    numstat = _require_git(worktree, "diff", "--numstat", base_sha, "--").stdout
    additions = deletions = binary_files = 0
    for line in numstat.splitlines():
        parts = line.split("\t", 2)
        if len(parts) < 3:
            continue
        if parts[0] == "-" or parts[1] == "-":
            binary_files += 1
        else:
            additions += int(parts[0])
            deletions += int(parts[1])
    patch = _require_git(worktree, "diff", "--binary", base_sha, "--", text=False).stdout
    patch_path.parent.mkdir(parents=True, exist_ok=True)
    decoded = patch.decode("utf-8", errors="replace")
    redacted, redaction_count = _redact(decoded, secrets)
    stored_patch = redacted.encode("utf-8") if redaction_count else patch
    patch_path.write_bytes(stored_patch)
    if untracked:
        _require_git(worktree, "reset", "--", *untracked)
    return touched, {
        "path": str(patch_path),
        "exists": patch_path.exists(),
        "bytes": len(stored_patch),
        "sha256": hashlib.sha256(stored_patch).hexdigest(),
        "additions": additions,
        "deletions": deletions,
        "binary_files": binary_files,
        "redaction_count": redaction_count,
    }


def _policy_risk(paths: Iterable[str], *, secret_redactions: int = 0) -> dict[str, Any]:
    touched = list(paths)
    reasons: list[str] = []
    score = 0
    high_patterns = ("scripts/", "skills/", ".github/", "cron", "auth", "secret", "credentials", "references/")
    medium_patterns = ("docs/", "config", "policy", "router")
    for path in touched:
        folded = path.casefold()
        if any(pattern in folded for pattern in high_patterns):
            score += 3
            reasons.append(f"shared/high-impact path: {path}")
        elif any(pattern in folded for pattern in medium_patterns):
            score += 1
            reasons.append(f"review-sensitive path: {path}")
    if len(touched) > 5:
        score += 2
        reasons.append(f"wide diff: {len(touched)} files")
    if secret_redactions:
        score += 10
        reasons.append(f"candidate patch contained {secret_redactions} redacted credential-like value(s)")
    level = "high" if score >= 3 else "medium" if score else "low"
    return {"level": level, "score": score, "reasons": reasons}


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _candidate_review(
    repo: Path,
    base_sha: str,
    spec: dict[str, Any],
    run_dir: Path,
    worktree_root: Path,
) -> dict[str, Any]:
    name = spec["name"]
    worktree = worktree_root / name
    patch_path = run_dir / "patches" / f"{name}.patch"
    candidate_path = run_dir / "candidates" / f"{name}.json"
    isolated_home = run_dir / "isolated-homes" / name
    worktree.parent.mkdir(parents=True, exist_ok=True)
    isolated_home.mkdir(parents=True, exist_ok=False)
    added = False
    row: dict[str, Any] | None = None
    cleanup: dict[str, Any] = {"attempted": False, "ok": False, "redaction_count": 0}
    home_cleanup: dict[str, Any] = {"attempted": False, "ok": False, "path": str(isolated_home), "redaction_count": 0}
    secrets = _secret_values()
    try:
        _require_git(repo, "worktree", "add", "--detach", str(worktree), base_sha, timeout=120)
        added = True
        env = _minimal_env(name, isolated_home)
        execution = _command_result(spec["command"], worktree, spec["timeout_seconds"], env, secrets)
        before_tests = _worktree_content_digest(worktree, base_sha)
        tests = (
            _command_result(spec["test_command"], worktree, spec["timeout_seconds"], env, secrets)
            if spec["test_command"] is not None
            else {
                "status": "skipped",
                "command": None,
                "exit_code": None,
                "duration_seconds": 0.0,
                "stdout": "",
                "stderr": "",
                "redaction_count": 0,
            }
        )
        after_tests = _worktree_content_digest(worktree, base_sha)
        touched, diff = _capture_patch(worktree, base_sha, patch_path, secrets)
        captured_state = _worktree_content_digest(worktree, base_sha)
        patch_readback = patch_path.exists() and hashlib.sha256(patch_path.read_bytes()).hexdigest() == diff["sha256"]
        completion = {
            "command_exit_zero": execution["exit_code"] == 0,
            "tests_present": spec["test_command"] is not None,
            "tests_passed": tests["status"] == "passed",
            "patch_readback": patch_readback,
            "has_diff": diff["bytes"] > 0,
            "tests_preserved_candidate_state": before_tests == after_tests,
            "patch_matches_tested_state": after_tests == captured_state,
            "no_secret_redactions": (
                execution["redaction_count"] + tests["redaction_count"] + diff["redaction_count"] == 0
            ),
        }
        completion["eligible"] = all(completion.values())
        row = {
            "candidate": name,
            "profile": "risky_edit",
            "isolation": {
                "kind": "detached_worktree",
                "branch": None,
                "path": str(worktree),
                "base_sha": base_sha,
                "home": str(isolated_home),
                "home_isolated": True,
                "os_sandbox": False,
            },
            "execution": execution,
            "tests": tests,
            "tested_state": {
                "before_tests_sha256": before_tests,
                "after_tests_sha256": after_tests,
                "captured_patch_state_sha256": captured_state,
            },
            "touched_files": touched,
            "diff": diff,
            "completion_evidence": completion,
            "policy_risk": _policy_risk(
                touched,
                secret_redactions=execution["redaction_count"] + tests["redaction_count"] + diff["redaction_count"],
            ),
            "rollback": {
                "strategy": "discard_unapplied_patch",
                "patch_path": str(patch_path),
                "reverse_if_applied": ["git", "-C", str(repo), "apply", "--reverse", "--check", str(patch_path)],
                "automatic_merge": False,
            },
        }
    finally:
        cleanup["attempted"] = added
        if added:
            removed = _git(repo, "worktree", "remove", "--force", str(worktree), timeout=120)
            cleanup_stderr, cleanup_redactions = _redact(removed.stderr or "", secrets)
            cleanup.update(
                {
                    "ok": removed.returncode == 0,
                    "exit_code": removed.returncode,
                    "stderr": _truncate(cleanup_stderr),
                    "redaction_count": cleanup_redactions,
                }
            )
        home_cleanup = _cleanup_isolated_path(isolated_home, secrets)
    if row is None:
        raise RuntimeError(f"candidate {name} produced no review row")
    row["worktree_cleanup"] = cleanup
    row["home_cleanup"] = home_cleanup
    row["completion_evidence"]["worktree_cleanup"] = cleanup["ok"]
    row["completion_evidence"]["isolated_home_cleanup"] = home_cleanup["ok"]
    row["completion_evidence"]["eligible"] = all(
        value for key, value in row["completion_evidence"].items() if key != "eligible"
    )
    _write_json(candidate_path, row)
    return row


def _render_matrix(report: dict[str, Any]) -> str:
    lines = [
        "# Parallel risky-edit evidence matrix",
        "",
        f"- Base: {report['base_sha']}",
        f"- Original unchanged: {str(report['original_unchanged']).lower()}",
        "- Automatic merge: false",
        "- Human review required: true",
        "",
        "| Candidate | Diff (+/-) | Files | Tests | Completion | Policy risk | Rollback |",
        "| --- | ---: | ---: | --- | --- | --- | --- |",
    ]
    for row in report["candidates"]:
        diff = row["diff"]
        lines.append(
            f"| {row['candidate']} | {diff['additions']}/{diff['deletions']} | {len(row['touched_files'])} | "
            f"{row['tests']['status']} | {str(row['completion_evidence']['eligible']).lower()} | "
            f"{row['policy_risk']['level']} | discard {Path(row['rollback']['patch_path']).name} |"
        )
    lines.extend(["", "No candidate was merged. Review the patch, test output, policy risk, and rollback metadata before any separate apply/merge action."])
    return "\n".join(lines) + "\n"


def run_parallel_review(
    repo: Path,
    candidates: list[dict[str, Any]],
    output_dir: Path,
    *,
    profile: str = "risky_edit",
) -> dict[str, Any]:
    repo = repo.expanduser().resolve()
    output_dir = output_dir.expanduser().resolve()
    if profile != "risky_edit":
        raise ValueError("parallel review is restricted to risky_edit")
    if not (repo / ".git").exists():
        raise ValueError(f"not a Git worktree: {repo}")
    if _inside(output_dir, repo):
        raise ValueError("output_dir must be outside the reviewed worktree")
    specs = validate_candidates(candidates)
    _reject_original_repo_references(specs, repo)
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError("output_dir must be new or empty")
    worktree_root = output_dir.parent / f".{output_dir.name}-worktrees"
    if _inside(worktree_root, repo):
        raise ValueError("worktree root must be outside the reviewed worktree")
    before = _state_snapshot(repo)
    if before["status"]:
        raise ValueError("original worktree must be clean; dirty state is never silently omitted from candidate review")
    output_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    try:
        with ThreadPoolExecutor(max_workers=len(specs), thread_name_prefix="risky-edit-candidate") as pool:
            rows = list(
                pool.map(
                    lambda spec: _candidate_review(repo, before["head"], spec, output_dir, worktree_root),
                    specs,
                )
            )
    finally:
        after = _state_snapshot(repo)
    unchanged = before == after
    risk_rank = {"low": 0, "medium": 1, "high": 2}
    review_order = [
        row["candidate"]
        for row in sorted(
            rows,
            key=lambda row: (
                not row["completion_evidence"]["eligible"],
                risk_rank[row["policy_risk"]["level"]],
                row["diff"]["additions"] + row["diff"]["deletions"],
                row["candidate"],
            ),
        )
    ]
    report: dict[str, Any] = {
        "schema_version": 1,
        "profile": "risky_edit",
        "repo": str(repo),
        "base_sha": before["head"],
        "created_at": utc_now_iso(),
        "candidate_limit": 2,
        "execution_mode": "parallel" if len(specs) > 1 else "single",
        "max_workers": len(specs),
        "candidate_count": len(rows),
        "eligible_candidate_count": sum(row["completion_evidence"]["eligible"] for row in rows),
        "candidates": rows,
        "review_order": review_order,
        "original_unchanged": unchanged,
        "before_digest": before["digest"],
        "after_digest": after["digest"],
        "automatic_merge": False,
        "human_review_required": True,
        "selection_blocked": not any(row["completion_evidence"]["eligible"] for row in rows),
        "ok": unchanged
        and len(rows) == len(specs)
        and all(row["worktree_cleanup"]["ok"] and row["home_cleanup"]["ok"] for row in rows),
    }
    matrix_json = output_dir / "evidence-matrix.json"
    matrix_md = output_dir / "evidence-matrix.md"
    report["matrix_json"] = str(matrix_json)
    report["matrix_markdown"] = str(matrix_md)
    _write_json(matrix_json, report)
    matrix_md.write_text(_render_matrix(report), encoding="utf-8")
    report["matrix_readback"] = {
        "json": matrix_json.exists() and matrix_json.stat().st_size > 0,
        "markdown": matrix_md.exists() and matrix_md.stat().st_size > 0,
    }
    if not unchanged:
        report["ok"] = False
        report["blocker"] = "original worktree changed during candidate review; no automatic restoration was attempted"
    _write_json(matrix_json, report)
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run up to two risky-edit candidates in isolated detached worktrees")
    parser.add_argument("--repo", required=True)
    parser.add_argument("--spec", required=True, help="JSON object with profile and candidates")
    parser.add_argument("--output-dir", required=True, help="Must be outside the reviewed repository")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        spec = json.loads(Path(args.spec).read_text(encoding="utf-8"))
        if not isinstance(spec, dict):
            raise ValueError("spec must be an object")
        report = run_parallel_review(
            Path(args.repo),
            spec.get("candidates") or [],
            Path(args.output_dir),
            profile=str(spec.get("profile") or ""),
        )
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["ok"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
