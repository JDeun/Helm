"""Pluggable checkpoint backends for non-file runtime state.

`scripts/workspace_checkpoint.py` snapshots workspace *files* (tar.gz). A risky
runtime op — a dependency/interpreter bump, a migration — has no file-level
before-state, so there is nothing to diff or point a restore hint at. These
backends capture that non-file state.

The first backend, ``pip_freeze``, records the installed-distribution
fingerprint (name → version) via ``importlib.metadata`` (no subprocess, no
network). ``diff_fingerprints`` reports what a runtime bump changed. Backends
are registered in ``BACKENDS`` so new state sources (a DB dump, an env
snapshot) are added by data, and ``capture_runtime_state`` runs a selected set
drift-tolerantly — an unknown or failing backend is reported, never fatal.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from scripts.io_utils import atomic_write_json


def pip_freeze_fingerprint() -> dict[str, str]:
    """Return {distribution_name: version} for the current interpreter.

    Uses ``importlib.metadata`` rather than shelling ``pip freeze`` so it is
    hermetic, fast, and needs no network.
    """
    from importlib.metadata import distributions

    out: dict[str, str] = {}
    for dist in distributions():
        try:
            name = dist.metadata["Name"]
        except Exception:
            continue
        if name:
            out[name] = dist.version or ""
    return out


def diff_fingerprints(before: dict[str, str], after: dict[str, str]) -> dict:
    """Report added / removed / version-changed distributions between two
    fingerprints (e.g. before vs after a runtime bump)."""
    added = {k: after[k] for k in after if k not in before}
    removed = {k: before[k] for k in before if k not in after}
    changed = {
        k: {"before": before[k], "after": after[k]}
        for k in before
        if k in after and before[k] != after[k]
    }
    return {
        "added": added,
        "removed": removed,
        "changed": changed,
        "changed_any": bool(added or removed or changed),
    }


# Registry: backend name → zero-arg callable returning JSON-serializable state.
BACKENDS: dict[str, Callable[[], object]] = {
    "pip_freeze": pip_freeze_fingerprint,
}


def capture_runtime_state(
    dest_dir: Path | str,
    backends: tuple[str, ...] = ("pip_freeze",),
) -> dict:
    """Run the selected backends, writing each captured state to
    ``dest_dir/<backend>.json``. Drift-tolerant: an unknown backend or a
    backend that raises is recorded as ``{"ok": False, "error": ...}`` and
    does not abort the others."""
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    results: dict[str, dict] = {}
    for name in backends:
        fn = BACKENDS.get(name)
        if fn is None:
            results[name] = {"ok": False, "error": "unknown_backend"}
            continue
        try:
            data = fn()
            path = dest / f"{name}.json"
            atomic_write_json(path, data)
            results[name] = {
                "ok": True,
                "path": str(path),
                "count": len(data) if hasattr(data, "__len__") else None,
            }
        except Exception as exc:  # backend failure must not abort the checkpoint
            results[name] = {"ok": False, "error": str(exc)}
    return {
        "dest_dir": str(dest),
        "backends": results,
        "ok": all(entry["ok"] for entry in results.values()) if results else True,
    }
