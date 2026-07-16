#!/usr/bin/env python3
"""Deterministic skill router (P1: single-candidate direct route).

Helm already *validates* a caller-declared skill (see
``scripts.route_contract_lib.score_skill_relevance``) but has no way to
*select* a skill among candidates before an agent commits to one. This
module scores every installed skill manifest against a request and, when
exactly one clears the confidence threshold, returns a "direct" route --
letting a caller skip model-based classification entirely.

Deterministic and pure: no model calls, no randomness. ``route_skill`` takes
already-loaded manifests so it stays easily testable; ``load_installed_manifests``
is a thin convenience wrapper around
``scripts.skill_manifest_lib.load_skill_contract_manifests`` for callers that
want the on-disk installed set.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from helm_workspace import get_workspace_layout
from scripts.route_contract_lib import score_skill_relevance
from scripts.skill_manifest_lib import load_skill_contract_manifests

# 50 lines up with score_skill_relevance's own "strong" verdict boundary
# (route_contract_lib.py), i.e. a request needs clear, multi-signal support
# for a skill before the router will pick it directly with no human/model
# in the loop.
DEFAULT_MIN_SCORE = 50.0
DEFAULT_PROFILE = "inspect_local"
MAX_CANDIDATES = 8


def load_installed_manifests(workspace: Path | None = None) -> dict[str, dict]:
    """Load installed skill contract manifests (``skills/`` + ``skill_drafts/``).

    Thin wrapper around ``skill_manifest_lib.load_skill_contract_manifests``
    that defaults to the discovered workspace root when ``workspace`` is
    omitted.
    """
    root = workspace if workspace is not None else get_workspace_layout().root
    return load_skill_contract_manifests(root)


def route_skill(
    request: str,
    manifests: dict[str, dict],
    *,
    min_score: float = DEFAULT_MIN_SCORE,
    profile: str = DEFAULT_PROFILE,
    task_name: str | None = None,
    command: list[str] | None = None,
    limit: int = MAX_CANDIDATES,
) -> dict:
    """Deterministically route ``request`` to zero, one, or many skills.

    Scores every manifest in ``manifests`` against ``request`` using
    ``route_contract_lib.score_skill_relevance`` and buckets the result:

    - exactly one skill clears ``min_score`` ->
      ``{"decision": "direct", "skill": name, "score": score}``
    - more than one clears ->
      ``{"decision": "candidates", "candidates": [{"skill", "score"}, ...]}``,
      sorted by score descending (skill name ascending breaks ties), bounded
      to ``limit`` entries
    - none clear -> ``{"decision": "none", "candidates": []}``

    Pure and deterministic: no model calls, no I/O. ``manifests`` must
    already be loaded (see ``load_installed_manifests``).
    """
    command_list = list(command or [])
    scored: list[dict] = []
    for skill, manifest in manifests.items():
        contract = manifest if isinstance(manifest, dict) else {}
        try:
            result = score_skill_relevance(
                skill=skill,
                profile=profile,
                contract=contract,
                request=request,
                task_name=task_name,
                command=command_list,
            )
            score = float(result.get("score", 0))
        except Exception:  # noqa: BLE001 - one corrupt on-disk manifest must not disable the whole router
            continue
        scored.append({"skill": skill, "score": score})

    cleared = [item for item in scored if item["score"] >= min_score]
    # Deterministic ordering even on ties: score desc, then skill name asc.
    cleared.sort(key=lambda item: (-item["score"], item["skill"]))

    if len(cleared) == 1:
        return {"decision": "direct", "skill": cleared[0]["skill"], "score": cleared[0]["score"]}
    if len(cleared) > 1:
        return {"decision": "candidates", "candidates": cleared[:limit]}
    return {"decision": "none", "candidates": []}
