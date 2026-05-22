#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from helm_workspace import get_workspace_layout
from scripts.env_flags import env_flag
from scripts.memory_capture import build_memory_capture_plan
from scripts.state_io import append_jsonl_atomic
from scripts.command_guard import CommandClassification, GuardDecision, evaluate_command_guard, decision_to_json
from scripts.state_snapshot import latest_snapshot_path, write_state_snapshot

# Module-top advisory_log import keeps the "advisory never raises" invariant
# (R6 Minor M1). Falls back to a noop counter if advisory_log itself fails.
try:
    from scripts.advisory_log import record_advisory_failure as _record_advisory_failure
except Exception:  # noqa: BLE001 - intentional last-resort fallback
    def _record_advisory_failure(channel: str, exc: BaseException) -> None:
        return None
from scripts.skill_manifest_lib import (
    load_skill_policies as load_manifest_policies,
    load_skill_contract_manifests,
    load_profiles as load_manifest_profiles,
    manifest_audit,
    manifest_quality_audit,
    validate_contract_manifest,
)
from scripts.skill_lifecycle_lib import record_runner_event
from scripts.time_helpers import utc_now_iso
from scripts.state_io import build_ledger_entry


EXIT_GUARD_REQUIRE_APPROVAL = 24
EXIT_GUARD_DENY = 25
EXIT_PAUSED = 26
EXIT_BROWSER_BLOCKED = 27
EXIT_CLEANUP_REQUIRED = 28  # OQ-7: finalization blocked — cleanup evidence missing

def _pause_gate_enabled() -> bool:
    """Return True only when OPENCLAW_PAUSE_GATE is truthy ('1', 'true', 'yes').

    All other values (including unset / '' / '0' / 'false' / 'no') return False.
    Delegates to ``scripts.env_flags.env_flag`` for shared semantics.
    """
    return env_flag("OPENCLAW_PAUSE_GATE")


def _browser_gate_enabled() -> bool:
    """Return True only when OPENCLAW_BROWSER_GATE is truthy ('1', 'true', 'yes').

    Default False (opt-in).  When disabled the verifier is called in shadow
    mode: decision is logged but the runner proceeds regardless.  When enabled
    the decision is enforced.  Delegates to ``scripts.env_flags.env_flag`` for
    shared semantics, mirroring the discipline of OPENCLAW_PAUSE_GATE.
    """
    return env_flag("OPENCLAW_BROWSER_GATE")


_BROWSER_ACTIONS = frozenset({
    "read", "navigate", "fetch_resource", "screenshot",
    "crawl_batch", "fillform", "interact", "submit",
})

# Single layout lookup at import time (was 4 separate calls; see 2026-05-21
# Helm full review issue #9).
_LAYOUT = get_workspace_layout()
WORKSPACE = _LAYOUT.root
PROFILE_FILE = WORKSPACE / "references" / "execution_profiles.json"
POLICY_FILE = WORKSPACE / "references" / "skill_profile_policies.json"
CHECKPOINT_SCRIPT = ROOT / "scripts" / "workspace_checkpoint.py"
TASK_LEDGER = _LAYOUT.state_root / "task-ledger.jsonl"
CHECKPOINT_INDEX = _LAYOUT.checkpoints_root / "index.json"
STATE_ROOT = _LAYOUT.state_root


_MINIMAL_ENV_KEYS = {
    "PATH", "HOME", "LANG", "LC_ALL", "TERM", "SHELL",
    "USER", "LOGNAME", "TMPDIR", "TMP", "TEMP",
    "SYSTEMROOT", "COMSPEC",
}

_WORKSPACE_ENV_KEYS = {
    "PWD", "OLDPWD", "VIRTUAL_ENV", "CONDA_DEFAULT_ENV", "CONDA_PREFIX",
}


def _minimal_env(*, extra_keys: set[str] | None = None) -> dict[str, str]:
    """Return a minimal environment dict with only safe, non-secret variables."""
    keep = _MINIMAL_ENV_KEYS | (extra_keys or set())
    env: dict[str, str] = {}
    for key, value in os.environ.items():
        if key in keep or key.startswith("HELM_") or key.startswith("OPENCLAW_"):
            env[key] = value
    return env


def iso_to_compact(value: str | None) -> str | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _load_json_object(path: Path, *, label: str) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SystemExit(f"Missing {label}: {path}")
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid {label}: {exc}")
    if not isinstance(payload, dict):
        raise SystemExit(f"Invalid {label}: expected JSON object")
    return payload


def _load_json_array(path: Path, *, label: str) -> list[object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SystemExit(f"Missing {label}: {path}")
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid {label}: {exc}")
    if not isinstance(payload, list):
        raise SystemExit(f"Invalid {label}: expected JSON array")
    return payload


def _load_json_object_lines(path: Path, *, label: str) -> list[dict]:
    if not path.exists():
        return []
    entries: list[dict] = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"Invalid {label} line {lineno}: {exc}")
        if not isinstance(payload, dict):
            raise SystemExit(f"Invalid {label} line {lineno}: expected JSON object")
        entries.append(payload)
    return entries


def load_profiles() -> dict[str, dict]:
    data = _load_json_object(PROFILE_FILE, label="execution profile file")
    profiles = data.get("profiles")
    if not isinstance(profiles, dict):
        raise SystemExit("Invalid execution profile file: missing `profiles` object")
    return profiles


def load_policies() -> dict[str, dict]:
    return load_manifest_policies(WORKSPACE, POLICY_FILE)


def ensure_ledger_dir() -> None:
    TASK_LEDGER.parent.mkdir(parents=True, exist_ok=True)


def append_ledger(entry: dict) -> None:
    append_jsonl_atomic(TASK_LEDGER, entry)
    _best_effort_index(entry)


def _best_effort_index(task: dict) -> None:
    try:
        from scripts.ops_db import index_task_entry
        index_task_entry(state_root=STATE_ROOT, entry=task, source_file="task-ledger.jsonl")
    except Exception:
        pass


def finalize_task(task: dict) -> None:
    task["memory_capture"] = build_memory_capture_plan(task)
    try:
        task["state_snapshot"] = write_state_snapshot(task, workspace=WORKSPACE, state_root=STATE_ROOT)
    except OSError as exc:
        task["state_snapshot_error"] = str(exc)
    append_ledger(task)


def record_guard_audit(task: dict) -> None:
    task["status"] = "guard_audit"
    task["finished_at"] = utc_now_iso()
    append_ledger(task)


def block_task(task: dict, *, reason: str, stage: str = "guard") -> None:
    task["status"] = "blocked"
    task["finished_at"] = utc_now_iso()
    task["failure_stage"] = stage
    task["failure_reason"] = reason
    append_ledger(task)


def fallback_guard_decision(command: list[str], args: argparse.Namespace, exc: Exception) -> GuardDecision:
    return GuardDecision(
        action="require_approval",
        risk_score=0.5,
        score_breakdown={"guard_error": 0.5},
        selected_profile=args.profile,
        recommended_profile=None,
        reasons=(f"guard evaluation error: {exc}",),
        matched_rules=tuple(),
        classification=CommandClassification(
            normalized_command=" ".join(command),
            argv=tuple(command),
            shell_wrapped=False,
            shell_inner_command=None,
            categories=("unknown",),
            matched_rules=tuple(),
            writes_detected=False,
            network_detected=False,
            destructive_detected=False,
            privilege_detected=False,
            remote_detected=False,
        ),
        approval_required=True,
        approval_hint="--approve-risk",
    )


def evaluate_guard_for_run(
    *,
    args: argparse.Namespace,
    command: list[str],
    profiles: dict[str, dict],
    guard_mode: str,
) -> GuardDecision | None:
    if guard_mode == "off":
        return None
    try:
        return evaluate_command_guard(
            command=command,
            selected_profile=args.profile,
            profiles=profiles,
            workspace=WORKSPACE,
            task_name=getattr(args, "task_name", None),
            task_goal=getattr(args, "task_goal", None),
        )
    except Exception as exc:
        print(f"WARNING: Guard evaluation failed: {exc}. Defaulting to require_approval.", file=sys.stderr)
        return fallback_guard_decision(command, args, exc)


def task_stub(profile: str, args: argparse.Namespace, command: list[str]) -> dict:
    config = load_profiles()[profile]
    if args.meta_json:
        try:
            meta = json.loads(args.meta_json)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"Invalid --meta-json payload: {exc}")
        if not isinstance(meta, dict):
            raise SystemExit("Invalid --meta-json payload: expected JSON object")
    else:
        meta = {}
    return {
        "task_id": args.task_id or str(uuid.uuid4()),
        "task_name": args.task_name or " ".join(command[:3]),
        "skill": args.skill,
        "profile": profile,
        "backend": config["backend"],
        "runtime_backend": config.get("runtime_backend", config["backend"]),
        "runtime_target_kind": config.get("runtime_target_kind"),
        "runtime_target": args.runtime_target,
        "runtime_note": args.runtime_note,
        "isolation": config.get("isolation"),
        "handoff_required": config.get("handoff_required", False),
        "command": command,
        "command_preview": shlex.join(command),
        "started_at": utc_now_iso(),
        "status": "queued",
        "checkpoint_label": args.label,
        "checkpoint_paths": args.path or [],
        "checkpoint_id": None,
        "delivery_mode": args.delivery_mode,
        "meta": meta,
    }


def validate_skill_profile(skill: str | None, profile: str) -> None:
    if not skill:
        return
    profiles = load_manifest_profiles(PROFILE_FILE)
    manifests = load_skill_contract_manifests(WORKSPACE)
    manifest = manifests.get(skill)
    if manifest:
        issues = validate_contract_manifest(skill, manifest, profiles)
        if issues:
            raise SystemExit("Invalid skill manifest: " + "; ".join(issues))
    policies = load_policies()
    policy = policies.get(skill)
    if not policy:
        return
    allowed = policy.get("allowed_profiles", [])
    if allowed and profile not in allowed:
        raise SystemExit(
            f"Skill `{skill}` does not allow profile `{profile}`. "
            f"Allowed profiles: {', '.join(allowed)}"
        )


def run_checkpoint(profile: str, args: argparse.Namespace) -> dict | None:
    profiles = load_profiles()
    config = profiles[profile]
    if config["checkpoint"] != "required":
        return None
    label = args.label or f"{profile}-checkpoint"
    paths = args.path or ["scripts", "skills", "docs", "references", "AGENTS.md", "TOOLS.md"]
    checkpoint_cmd = ["python3", str(CHECKPOINT_SCRIPT), "create", "--label", label]
    for path in paths:
        checkpoint_cmd.extend(["--path", path])
    try:
        result = subprocess.run(
            checkpoint_cmd, cwd=str(WORKSPACE), capture_output=True, text=True, timeout=60
        )
    except subprocess.TimeoutExpired:
        return {"error": "checkpoint timed out after 60 seconds"}
    if result.returncode != 0:
        return {"error": result.stderr.strip() or result.stdout.strip() or "checkpoint creation failed"}
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"error": "checkpoint output was not valid JSON", "raw_output": result.stdout.strip()}


def load_checkpoints() -> list[dict]:
    if not CHECKPOINT_INDEX.exists():
        return []
    checkpoints = _load_json_array(CHECKPOINT_INDEX, label="checkpoint index")
    valid: list[dict] = []
    for idx, item in enumerate(checkpoints, start=1):
        if not isinstance(item, dict):
            raise SystemExit(f"Invalid checkpoint index entry {idx}: expected JSON object")
        valid.append(item)
    return valid


def latest_task_entries() -> list[dict]:
    entries = _load_json_object_lines(TASK_LEDGER, label="task ledger")
    by_task: dict[str, dict] = {}
    for entry in entries:
        task_id = entry.get("task_id")
        if task_id:
            by_task[task_id] = entry
    return list(by_task.values())


def suggest_checkpoint_for_task(entry: dict) -> dict | None:
    checkpoints = load_checkpoints()
    explicit = entry.get("checkpoint_id")
    if explicit:
        for checkpoint in checkpoints:
            if checkpoint.get("checkpoint_id") == explicit:
                return checkpoint
    started_compact = iso_to_compact(entry.get("started_at"))
    if started_compact:
        older = [cp for cp in checkpoints if cp.get("created_at", "") <= started_compact]
        if older:
            return older[-1]
    return checkpoints[-1] if checkpoints else None


def cmd_list(_: argparse.Namespace) -> int:
    profiles = load_profiles()
    for name, config in profiles.items():
        print(
            f"{name}\t{config['backend']}\t"
            f"runtime={config.get('runtime_backend', config['backend'])}\t"
            f"checkpoint={config['checkpoint']}\t{config['description']}"
        )
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    profiles = load_profiles()
    if args.profile not in profiles:
        known = ", ".join(sorted(profiles.keys()))
        print(f"Unknown profile: {args.profile!r}. Known profiles: {known}", file=sys.stderr)
        return 2
    config = profiles[args.profile]
    print(json.dumps(config, indent=2))
    return 0


def cmd_policy(_: argparse.Namespace) -> int:
    policies = load_policies()
    if not policies:
        print("No skill profile policies configured.")
        return 0
    print(json.dumps(policies, indent=2, ensure_ascii=False))
    return 0


def cmd_validate_manifests(args: argparse.Namespace) -> int:
    payload = manifest_audit(WORKSPACE, POLICY_FILE, PROFILE_FILE)
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print(f"manifest_count={payload['manifest_count']}")
        print(f"missing_contract_skills={len(payload['missing_contract_skills'])}")
        for skill in payload["missing_contract_skills"][:50]:
            print(f"missing={skill}")
        for issue in payload["issues"][:100]:
            print(f"issue={issue}")
        print(f"ok={payload['ok']}")
    return 0 if payload["ok"] else 1


def cmd_audit_manifest_quality(args: argparse.Namespace) -> int:
    payload = manifest_quality_audit(WORKSPACE, PROFILE_FILE)
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print(f"manifest_count={payload['manifest_count']}")
        print(f"flagged_count={payload['flagged_count']}")
        for item in payload["items"][:100]:
            print(f"skill={item['skill']}")
            print(f"allowed_profiles={','.join(item['allowed_profiles'])}")
            print(f"default_profile={item['default_profile']}")
            for warning in item["warnings"]:
                print(f"warning={warning}")
    return 0 if payload["ok"] else 2


def cmd_ledger(args: argparse.Namespace) -> int:
    if not TASK_LEDGER.exists():
        print("No task ledger entries found.")
        return 0
    lines = TASK_LEDGER.read_text(encoding="utf-8").splitlines()
    count = args.limit or 20
    for line in lines[-count:]:
        print(line)
    return 0


def cmd_rollback(args: argparse.Namespace) -> int:
    entries = latest_task_entries()
    if not entries:
        print("No task ledger entries found.")
        return 0
    target = None
    if args.task_id:
        for entry in entries:
            if entry.get("task_id") == args.task_id:
                target = entry
                break
    else:
        risky = [entry for entry in entries if entry.get("profile") == "risky_edit"]
        if risky:
            risky.sort(key=lambda item: item.get("started_at", ""))
            target = risky[-1]
    if target is None:
        print("No matching risky task found.")
        return 0
    checkpoint = suggest_checkpoint_for_task(target)
    payload = {
        "task_id": target.get("task_id"),
        "task_name": target.get("task_name"),
        "status": target.get("status"),
        "profile": target.get("profile"),
        "runtime_backend": target.get("runtime_backend") or target.get("backend"),
        "runtime_target": target.get("runtime_target"),
        "checkpoint": checkpoint,
    }
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print(f"task_id={payload['task_id']}")
        print(f"task_name={payload['task_name']}")
        print(f"profile={payload['profile']}")
        print(f"status={payload['status']}")
        print(f"runtime_backend={payload['runtime_backend']}")
        print(f"runtime_target={payload['runtime_target'] or '-'}")
        if checkpoint:
            print(f"checkpoint_id={checkpoint.get('checkpoint_id')}")
            print(f"checkpoint_label={checkpoint.get('label')}")
            print(
                "restore_command="
                f"python3 {CHECKPOINT_SCRIPT} restore {checkpoint.get('checkpoint_id')}"
            )
        else:
            print("checkpoint_id=-")
    return 0


def _attach_advisory_action_scope(task: dict, args: argparse.Namespace) -> None:
    """Attach a Phase-A action-scope evaluation to ``task`` for observability.

    Advisory only — never blocks or modifies the run path. Failure of
    any kind degrades silently (the field is omitted from the task
    entry). Wired here so the task-ledger row carries the Phase-A
    module's view of the user-supplied task name + goal, which lets
    operators correlate guard decisions with the action-scope lock
    after the fact (R2 I1).
    """
    try:
        from scripts.action_scope import evaluate as _scope_evaluate
        parts = [s for s in (getattr(args, "task_name", None), getattr(args, "task_goal", None)) if s]
        if not parts:
            return
        decision = _scope_evaluate("\n".join(parts))
        payload = decision.as_dict()
        task["advisory_action_scope"] = {
            "locked_scope": payload.get("locked_scope"),
            "matched_verb": payload.get("matched_verb"),
            "allowed": payload.get("allowed"),
            "needs_live_source": payload.get("needs_live_source"),
            "advisory_only": True,
        }
    except (ImportError, AttributeError) as exc:
        # Module wiring failure (e.g. action_scope refactor in flight).
        # Advisory mode: never raise out of the production hot path,
        # but record so operators can spot a regression in the
        # advisory channel via helm doctor or the counter snapshot.
        _record_advisory_failure(
            "run_with_profile.action_scope.wiring", exc
        )
    except Exception as exc:
        # Unexpected raise from the evaluator itself. Still advisory:
        # leave the breadcrumb, then degrade silently.
        _record_advisory_failure(
            "run_with_profile.action_scope", exc
        )


def _attach_tool_grant(task: dict, profile: str) -> None:
    """Compute and attach the tool_grant block to a task dict (idempotent).

    The grant is recorded in the ledger for observability only.  Denied tools
    are NOT blocked at runner level — that is Task #4's responsibility.
    Calling this function again on a task that already has ``tool_grant`` is a
    no-op (the existing block is preserved unchanged).

    A default set of the eight canonical tool-group names is used because no
    ``--tool-grant`` CLI flag exists yet.  When that flag lands (future task),
    this call site can be made lazy/conditional.
    """
    if "tool_grant" in task:
        # Idempotent: already computed, do not overwrite.
        return
    _DEFAULT_REQUESTED_TOOLS = [
        "read_file",
        "apply_patch",
        "focused_test",
        "git_diff",
        "broad_shell",
        "external_network",
        "secrets_read",
        "destructive_git",
    ]
    try:
        from scripts.tool_groups import compute_grant
        grant = compute_grant(profile, _DEFAULT_REQUESTED_TOOLS)
        task["tool_grant"] = {
            "profile": profile,
            "granted": grant["granted"],
            "requires_approval": grant["requires_approval"],
            "denied": grant["denied"],
        }
    except Exception:  # noqa: BLE001 — advisory; never block the hot path
        pass


# Per-profile session cap constants for OQ-3 max_sessions enforcement.
# Mirrors _PROFILE_POLICIES in browser_work_verifier.py.
_BROWSER_MAX_SESSIONS: dict[str, int] = {
    "inspect_local": 5,
    "service_ops": 3,
    "risky_edit": 2,
}


def _count_active_browser_sessions(
    profile: str,
    window_minutes: int = 10,
) -> int:
    """Count open browser sessions for *profile* in the recent ledger window.

    OQ-3: Runner-side max_sessions enforcement via ledger counter.

    A session is "open" when:
    - A ledger entry has ``browser_recon`` set (verifier was called)
    - The entry's ``status`` is ``browser_recon`` or ``running`` or
      ``browser_approved_with_risk`` or ``browser_approved_by_site_note``
      (i.e. a session was authorised to open)
    - The entry's ``started_at`` is within ``window_minutes`` of now
    - No later entry for the same ``task_id`` has ``cleanup_status`` set

    Returns the count of such open sessions.  Returns 0 on any read/
    parse error (fail-open so that ledger corruption never blocks work).
    """
    from scripts.time_helpers import utc_now_iso
    try:
        if not TASK_LEDGER.exists():
            return 0

        lines = TASK_LEDGER.read_text(encoding="utf-8").splitlines()
        # Parse all entries, keep only the last N lines for performance
        # (10-minute window; tasks rarely exceed 1 line/second).
        tail_lines = lines[-2000:]

        now_ts = utc_now_iso()
        # Build a simple ISO-comparable window cutoff.
        import datetime as _dt
        now_dt = _dt.datetime.fromisoformat(now_ts.replace("Z", "+00:00"))
        cutoff_dt = now_dt - _dt.timedelta(minutes=window_minutes)

        _OPEN_STATUSES = frozenset({
            "browser_recon",
            "running",
            "browser_approved_with_risk",
            "browser_approved_by_site_note",
            "browser_recon_shadow",
        })

        # First pass: collect all entries.
        all_entries: list[dict] = []
        for line in tail_lines:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(entry, dict):
                all_entries.append(entry)

        # Identify task_ids that have cleanup evidence.
        tasks_with_cleanup: set[str] = set()
        for entry in all_entries:
            task_id = entry.get("task_id")
            if task_id and entry.get("cleanup_status"):
                tasks_with_cleanup.add(task_id)

        # Count open sessions.
        open_count = 0
        for entry in all_entries:
            if entry.get("profile") != profile:
                continue
            if not entry.get("browser_recon"):
                continue
            status = entry.get("status", "")
            if status not in _OPEN_STATUSES:
                continue
            started_at = entry.get("started_at", "")
            if not started_at:
                continue
            try:
                entry_dt = _dt.datetime.fromisoformat(
                    started_at.replace("Z", "+00:00")
                )
            except ValueError:
                continue
            if entry_dt < cutoff_dt:
                continue
            task_id = entry.get("task_id")
            if task_id and task_id in tasks_with_cleanup:
                continue
            open_count += 1

        return open_count
    except Exception:  # noqa: BLE001 — fail-open; never block on ledger errors
        return 0


def _check_cleanup_required_satisfied(task_id: str) -> tuple[bool, str | None]:
    """Check whether cleanup evidence is recorded for *task_id*.

    OQ-7: Finalization gate.

    Returns ``(satisfied, reason)`` where:
    - ``satisfied=True`` means cleanup is not required OR evidence exists.
    - ``satisfied=False, reason=str`` means cleanup was required but no
      ``cleanup_status`` row exists for this task.

    Reads the task ledger and examines all rows for this task_id.
    Fail-open: any ledger read error returns ``(True, None)`` so that
    ledger corruption never permanently blocks task completion.
    """
    try:
        if not TASK_LEDGER.exists():
            return True, None

        entries_for_task: list[dict] = []
        for line in TASK_LEDGER.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(entry, dict) and entry.get("task_id") == task_id:
                entries_for_task.append(entry)

        if not entries_for_task:
            return True, None

        # Check if any entry required cleanup evidence.
        cleanup_required = any(
            entry.get("browser_recon", {}).get("require_cleanup_evidence")
            for entry in entries_for_task
        )
        if not cleanup_required:
            return True, None

        # Check if any entry has cleanup_status recorded.
        has_cleanup = any(
            entry.get("cleanup_status")
            for entry in entries_for_task
        )
        if has_cleanup:
            return True, None

        return False, (
            f"task {task_id} required browser cleanup evidence "
            "(require_cleanup_evidence=True) but no cleanup_status is recorded; "
            "record cleanup_status before marking complete"
        )
    except Exception:  # noqa: BLE001 — fail-open
        return True, None


def _evaluate_browser_gate(
    args: argparse.Namespace,
    task: dict,
) -> int | None:
    """Evaluate the browser verifier gate and mutate *task* with the result.

    Returns:
        None   — caller should proceed normally.
        int    — exit code; caller should exit immediately with that code.

    This function is called ONLY when ``--browser-action`` is present.
    It never raises: any exception from the verifier is caught and treated
    as a ``require_confirmation`` shadow-mode fallback.

    Behavior matrix
    ---------------
    gate OFF  → shadow mode: decision logged as ``browser_recon_shadow``,
                runner proceeds (returns None).
    gate ON   → enforce mode: decision honored per spec.
    """
    from scripts.browser_work_verifier import verify as _bv_verify

    request = {
        "url_pattern": getattr(args, "browser_url_pattern", None) or "",
        "intended_action": args.browser_action,
        "logged_in_account_required": bool(getattr(args, "browser_logged_in", False)),
        "parallel_requested": bool(getattr(args, "browser_parallel", False)),
        "execution_profile": args.profile,
    }
    site_note = getattr(args, "browser_site_note", None)
    if site_note:
        request["existing_site_note_path"] = site_note

    try:
        decision = _bv_verify(request)
    except Exception as exc:  # noqa: BLE001 — verifier errors degrade safely
        decision = {
            "allow_single_session": False,
            "allow_parallel": False,
            "require_user_login": False,
            "require_confirmation": True,
            "block_mutation": False,
            "pause_profile": False,
            "require_cleanup_evidence": False,
            "reason": f"verifier exception: {exc}",
            "checks": {},
        }

    enforce = _browser_gate_enabled()

    if not enforce:
        # Shadow mode: log and proceed.
        task["status"] = "browser_recon_shadow"
        task["browser_recon"] = decision
        task["finished_at"] = utc_now_iso()
        append_ledger(dict(task))
        # Reset status so the calling code can continue.
        task.pop("status", None)
        task.pop("browser_recon", None)
        task.pop("finished_at", None)
        return None

    # Enforce mode — honor the decision.

    # OQ-3: max_sessions check — BEFORE session open.
    # Only enforce when gate is on; shadow mode always proceeds.
    _profile = args.profile
    _max_sessions = _BROWSER_MAX_SESSIONS.get(_profile)
    if _max_sessions is not None:
        _active = _count_active_browser_sessions(_profile)
        if _active >= _max_sessions:
            task["status"] = "browser_blocked"
            task["finished_at"] = utc_now_iso()
            task["browser_recon"] = decision
            task["browser_block_reason"] = "max_sessions_reached"
            append_ledger(dict(task))
            print(
                f"BROWSER GATE BLOCKED: max_sessions reached for profile "
                f"{_profile!r} (active={_active}, cap={_max_sessions})",
                file=sys.stderr,
            )
            return EXIT_BROWSER_BLOCKED

    block = (
        decision.get("block_mutation") is True
        or decision.get("allow_single_session") is False
    )
    if block:
        task["status"] = "browser_blocked"
        task["finished_at"] = utc_now_iso()
        task["browser_recon"] = decision
        append_ledger(dict(task))
        print(
            f"BROWSER GATE BLOCKED: {decision.get('reason', 'blocked by verifier')}",
            file=sys.stderr,
        )
        return EXIT_BROWSER_BLOCKED

    if decision.get("require_confirmation"):
        # OQ-1: gated mutation gate is satisfied by EITHER --approve-risk OR an
        # existing site note (caller-supplied or auto-resolved by verifier).
        _site_note_in_decision = decision.get("checks", {}).get("existing_site_note")
        _site_note_present = (
            _site_note_in_decision == "present"
            or bool(request.get("existing_site_note_path"))
            or bool(getattr(args, "browser_site_note", None))
        )

        if _site_note_present:
            # Site note satisfies the gate (OQ-1).
            task["browser_approved_by_site_note"] = True
            task["status_browser"] = "browser_approved_by_site_note"
            _browser_recon_entry = dict(task)
            _browser_recon_entry["status"] = "browser_approved_by_site_note"
            _browser_recon_entry["browser_recon"] = decision
            append_ledger(_browser_recon_entry)
            # task continues; do NOT set status yet — normal flow takes over.
        elif getattr(args, "approve_risk", False):
            # --approve-risk satisfies the gate.
            task["browser_approved_with_risk"] = True
            task["status_browser"] = "browser_approved_with_risk"
            _browser_recon_entry = dict(task)
            _browser_recon_entry["status"] = "browser_approved_with_risk"
            _browser_recon_entry["browser_recon"] = decision
            append_ledger(_browser_recon_entry)
            # task continues; do NOT set status yet — normal flow takes over.
        else:
            # Neither approval nor site note — require human approval.
            task["status"] = "browser_requires_approval"
            task["finished_at"] = utc_now_iso()
            task["browser_recon"] = decision
            append_ledger(dict(task))
            print(
                f"BROWSER GATE REQUIRES APPROVAL: {decision.get('reason', '')}. "
                "Pass --approve-risk or provide a site note to proceed.",
                file=sys.stderr,
            )
            return EXIT_GUARD_REQUIRE_APPROVAL
        return None

    # All clear: embed recon into the task dict for the normal finalize path.
    task["browser_recon"] = decision
    return None


def cmd_run(args: argparse.Namespace) -> int:
    profiles = load_profiles()
    if args.profile not in profiles:
        known = ", ".join(sorted(profiles.keys()))
        print(f"Unknown profile: {args.profile!r}. Known profiles: {known}", file=sys.stderr)
        return 2
    config = profiles[args.profile]
    command = args.command
    if not command:
        raise SystemExit("No command supplied. Use `-- <command> ...`")

    # --- Pause gate (OPENCLAW_PAUSE_GATE) ---
    # Feature flag check FIRST: no side effects when disabled.
    if _pause_gate_enabled():
        from scripts.profile_pause_resume import check_can_start, _default_path as _pause_default_path
        can_start, pause_reason = check_can_start(args.profile, _pause_default_path())
        if not can_start:
            print(f"profile {args.profile} paused: {pause_reason}", file=sys.stderr)
            ensure_ledger_dir()
            blocked_entry = build_ledger_entry(
                {
                    "status": "blocked_by_pause",
                    "profile": args.profile,
                    "reason": pause_reason,
                    "updated_at": utc_now_iso(),
                },
            )
            append_ledger(blocked_entry)
            return EXIT_PAUSED
    # --- End pause gate ---

    validate_skill_profile(args.skill, args.profile)

    task = task_stub(args.profile, args, command)
    # Advisory Phase-A wiring (R2 I1): best-effort, silent on failure.
    _attach_advisory_action_scope(task, args)
    # Tool-grant wiring (Task #3): records grant for the profile before execution.
    # NOTE: denied tools are NOT blocked here — that is Task #4's responsibility.
    _attach_tool_grant(task, args.profile)
    append_ledger(task)

    # --- Browser gate (OPENCLAW_BROWSER_GATE + --browser-action) ---
    # Only active when --browser-action is explicitly supplied with a known action
    # string. The isinstance(str) guard prevents a MagicMock attribute from
    # accidentally triggering the gate in tests that don't set browser_action=None.
    # Pause gate always wins (already returned EXIT_PAUSED above if paused).
    _browser_action = getattr(args, "browser_action", None)
    if isinstance(_browser_action, str) and _browser_action:
        _browser_rc = _evaluate_browser_gate(args, task)
        if _browser_rc is not None:
            return _browser_rc
    # --- End browser gate ---

    checkpoint = run_checkpoint(args.profile, args)
    if checkpoint and checkpoint.get("error"):
        task["status"] = "failed"
        task["finished_at"] = utc_now_iso()
        task["failure_stage"] = "checkpoint"
        task["failure_reason"] = checkpoint["error"]
        finalize_task(task)
        return 1
    if checkpoint:
        task["checkpoint_id"] = checkpoint.get("checkpoint_id")
        task["checkpoint_label"] = checkpoint.get("label")

    # --- Guard evaluation (before any backend check) ---
    guard_mode_source = "cli" if getattr(args, "guard_mode", None) else "env"
    guard_mode = getattr(args, "guard_mode", None) or os.environ.get("HELM_GUARD_MODE", "enforce")
    guard_decision = None

    if guard_mode == "off" and guard_mode_source == "env":
        print("WARNING: Guard disabled via HELM_GUARD_MODE environment variable", file=sys.stderr)

    guard_decision = evaluate_guard_for_run(args=args, command=command, profiles=profiles, guard_mode=guard_mode)

    task["guard"] = (
        decision_to_json(guard_decision) if guard_decision
        else {"enabled": False, "mode": guard_mode}
    )
    task["guard"]["source"] = guard_mode_source

    if guard_decision and getattr(args, "guard_json", False):
        print(json.dumps(decision_to_json(guard_decision), indent=2, ensure_ascii=False))
        record_guard_audit(task)
        return 0

    if guard_mode == "enforce" and guard_decision:
        if guard_decision.action == "deny":
            block_task(task, reason="guard deny")
            print(f"GUARD DENY: {', '.join(guard_decision.reasons)}", file=sys.stderr)
            return EXIT_GUARD_DENY

        if guard_decision.action == "require_approval" and not getattr(args, "approve_risk", False):
            block_task(task, reason="approval required")
            hint = guard_decision.approval_hint or "Use --approve-risk to proceed."
            print(f"GUARD APPROVAL REQUIRED: {', '.join(guard_decision.reasons)}", file=sys.stderr)
            print(f"Hint: {hint}", file=sys.stderr)
            return EXIT_GUARD_REQUIRE_APPROVAL

        if getattr(args, "approve_risk", False) and guard_decision.action == "require_approval":
            task["guard"]["approved"] = True
    # --- End guard evaluation ---

    # --- Backend-specific handling ---
    if config["backend"] == "manual-remote":
        if not args.runtime_target:
            task["status"] = "failed"
            task["finished_at"] = utc_now_iso()
            task["failure_stage"] = "handoff"
            task["failure_reason"] = "remote_handoff requires --runtime-target"
            finalize_task(task)
            print("remote_handoff requires --runtime-target", file=sys.stderr)
            return 2
        task["status"] = "handoff_required"
        task["finished_at"] = utc_now_iso()
        finalize_task(task)
        print(
            json.dumps(
                {
                    "task_id": task["task_id"],
                    "status": task["status"],
                    "runtime_target": args.runtime_target,
                    "runtime_note": args.runtime_note,
                    "command_preview": task["command_preview"],
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return 0

    task["status"] = "running"
    task["started_execution_at"] = utc_now_iso()
    append_ledger(task)
    record_runner_event(
        WORKSPACE,
        skill_id=task.get("skill"),
        event="skill_used",
        extra={"task_id": task["task_id"], "profile": task["profile"]},
    )

    writes_allowed = config.get("writes_allowed", True)
    network_allowed = config.get("network_allowed", True)
    if not writes_allowed and not network_allowed:
        child_env = _minimal_env()
    elif not network_allowed:
        child_env = _minimal_env(extra_keys=_WORKSPACE_ENV_KEYS)
    else:
        child_env = os.environ.copy()
    child_env["HELM_TASK_ID"] = task["task_id"]
    child_env["HELM_TASK_PROFILE"] = str(task["profile"])
    child_env["OPENCLAW_TASK_ID"] = task["task_id"]
    previous_snapshot = latest_snapshot_path(STATE_ROOT)
    if previous_snapshot:
        child_env["HELM_PREVIOUS_STATE_SNAPSHOT"] = str(previous_snapshot)
        child_env["OPENCLAW_PREVIOUS_STATE_SNAPSHOT"] = str(previous_snapshot)
        task["previous_state_snapshot"] = str(previous_snapshot)
    if task.get("skill"):
        child_env["HELM_TASK_SKILL"] = str(task["skill"])
        child_env["OPENCLAW_TASK_SKILL"] = str(task["skill"])
    if task.get("task_name"):
        child_env["HELM_TASK_NAME"] = str(task["task_name"])
        child_env["OPENCLAW_TASK_NAME"] = str(task["task_name"])
    child_env["OPENCLAW_TASK_PROFILE"] = str(task["profile"])

    raw_timeout = getattr(args, "timeout", 1800)
    if raw_timeout is not None and raw_timeout < 0:
        raw_timeout = 0
    timeout_seconds = raw_timeout or None  # 0 → None (no limit)
    try:
        result = subprocess.run(command, cwd=str(WORKSPACE), env=child_env, timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        task["finished_at"] = utc_now_iso()
        task["status"] = "timeout"
        task["failure_stage"] = "execution"
        task["failure_reason"] = f"command timed out after {timeout_seconds}s"
        finalize_task(task)
        record_runner_event(
            WORKSPACE,
            skill_id=task.get("skill"),
            event="skill_failure",
            extra={"task_id": task["task_id"], "reason": "timeout"},
        )
        print(
            f"TIMEOUT: command exceeded {timeout_seconds}s limit: {shlex.join(command)}",
            file=sys.stderr,
        )
        return 1

    task["finished_at"] = utc_now_iso()
    task["exit_code"] = result.returncode

    # OQ-7: Finalization gate — check cleanup evidence before marking complete.
    # Only enforced when gate is on AND the task requires cleanup evidence.
    # Fail-open: if _check_cleanup_required_satisfied errors it returns True.
    if result.returncode == 0 and _browser_gate_enabled():
        _cleanup_ok, _cleanup_reason = _check_cleanup_required_satisfied(
            task["task_id"]
        )
        if not _cleanup_ok:
            task["status"] = "browser_cleanup_required"
            task["failure_stage"] = "finalization"
            task["failure_reason"] = _cleanup_reason or "cleanup evidence required"
            _cleanup_entry = dict(task)
            _cleanup_entry["status"] = "browser_cleanup_required"
            append_ledger(_cleanup_entry)
            print(
                f"BROWSER CLEANUP REQUIRED: {_cleanup_reason}",
                file=sys.stderr,
            )
            return EXIT_CLEANUP_REQUIRED

    task["status"] = "completed" if result.returncode == 0 else "failed"
    finalize_task(task)
    record_runner_event(
        WORKSPACE,
        skill_id=task.get("skill"),
        event="skill_success" if result.returncode == 0 else "skill_failure",
        extra={"task_id": task["task_id"], "exit_code": result.returncode},
    )
    return result.returncode


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a shell command under a declared execution profile.")
    subparsers = parser.add_subparsers(dest="command_name", required=True)

    listing = subparsers.add_parser("list", help="List configured execution profiles.")
    listing.set_defaults(func=cmd_list)

    show = subparsers.add_parser("show", help="Show one execution profile.")
    show.add_argument("profile", type=str)
    show.set_defaults(func=cmd_show)

    policy = subparsers.add_parser("policy", help="Show skill-to-profile policy mappings.")
    policy.set_defaults(func=cmd_policy)

    manifests = subparsers.add_parser("validate-manifests", help="Validate skill contract manifests.")
    manifests.add_argument("--json", action="store_true")
    manifests.set_defaults(func=cmd_validate_manifests)

    quality = subparsers.add_parser("audit-manifest-quality", help="Flag overly generic or weak manifest policies.")
    quality.add_argument("--json", action="store_true")
    quality.set_defaults(func=cmd_audit_manifest_quality)

    ledger = subparsers.add_parser("ledger", help="Show recent task-ledger entries.")
    ledger.add_argument("--limit", type=int, default=20)
    ledger.set_defaults(func=cmd_ledger)

    rollback = subparsers.add_parser("rollback", help="Suggest the checkpoint to use for a risky task.")
    rollback.add_argument("--task-id", help="Specific task id to inspect. Defaults to latest risky_edit task.")
    rollback.add_argument("--json", action="store_true")
    rollback.set_defaults(func=cmd_rollback)

    return parser


def parse_run_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a command with a declared execution profile.")
    parser.add_argument("command_name")
    parser.add_argument("profile", type=str)
    parser.add_argument("--task-name", help="Human-readable task name, recommended for service_ops.")
    parser.add_argument("--task-id", help="Explicit task id override for harness-controlled runs.")
    parser.add_argument("--skill", help="Owning skill slug for policy enforcement.")
    parser.add_argument("--meta-json", help="Structured metadata JSON to embed in the task ledger.")
    parser.add_argument("--label", help="Checkpoint label when the profile requires one.")
    parser.add_argument("--path", action="append", help="Checkpoint path override. May be repeated.")
    parser.add_argument("--runtime-target", help="Named runtime target such as local, ssh:host, container:name, or node label.")
    parser.add_argument("--runtime-note", help="Short note for backend/runtime handoff context.")
    parser.add_argument(
        "--delivery-mode",
        choices=["inline", "background", "announce", "none"],
        default="inline",
        help="Delivery mode for task-ledger context.",
    )
    parser.add_argument(
        "--guard-mode",
        choices=["enforce", "audit", "off"],
        default=None,
        help="Guard evaluation mode. Default: enforce (or HELM_GUARD_MODE env).",
    )
    parser.add_argument(
        "--approve-risk",
        action="store_true",
        help="Approve commands that require_approval. Does not override deny.",
    )
    parser.add_argument(
        "--guard-json",
        action="store_true",
        help="Print guard decision as JSON and exit without running the command.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=1800,
        help="Subprocess timeout in seconds (default: 1800 / 30 minutes). 0 disables the timeout.",
    )
    # --- Browser gate flags (Wave 3a) ---
    parser.add_argument(
        "--browser-action",
        choices=sorted(_BROWSER_ACTIONS),
        default=None,
        help=(
            "Treat this command as a browser task and consult the verifier. "
            "One of: read, navigate, fetch_resource, screenshot, crawl_batch, "
            "fillform, interact, submit. When absent all other --browser-* flags "
            "are ignored and the verifier is NOT called."
        ),
    )
    parser.add_argument(
        "--browser-url-pattern",
        default=None,
        help="URL pattern for the browser task (required when --browser-action is set).",
    )
    parser.add_argument(
        "--browser-logged-in",
        action="store_true",
        default=False,
        help="Indicate the browser session requires a logged-in account.",
    )
    parser.add_argument(
        "--browser-parallel",
        action="store_true",
        default=False,
        help="Indicate the browser task may run in parallel with other sessions.",
    )
    parser.add_argument(
        "--browser-site-note",
        default=None,
        help="Path to an existing site note for the target URL (optional).",
    )
    # --- End browser gate flags ---
    args, remainder = parser.parse_known_args()
    if remainder and remainder[0] == "--":
        remainder = remainder[1:]
    args.command = remainder
    return args


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "run":
        return cmd_run(parse_run_args())

    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
