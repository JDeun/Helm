"""Memory Tree (source / topic / global) for Helm.

Implements the three-layer summary tree from
``2026-05-21-helm-architecture-design.md`` §1.

Public API:

- :class:`MemoryTree`         high-level facade
- :class:`SourceSummary`,     :class:`TopicSummary`, :class:`GlobalSummary`
- :class:`RefreshTrigger`     enum of the 5 refresh triggers
- :func:`compute_hash`        stable content hash used by the ledger

The tree is filesystem-backed under a root directory (default
``~/.helm/memory``)::

    root/
      source/<source_id>.md        # one summary per connector / origin
      topic/<topic_id>.md          # one summary per topic of interest
      global/current.md            # compact injected memory candidate

Every refresh appends a ``kind=memory_refresh`` entry to the configured
task-ledger so "언제 어떤 근거로 메모리가 바뀌었는지" is reconstructable.
"""

from .tree import (
    GlobalSummary,
    MemoryTree,
    MemoryTreePaths,
    RefreshResult,
    RefreshTrigger,
    SourceSummary,
    TopicSummary,
    compute_hash,
)

__all__ = [
    "GlobalSummary",
    "MemoryTree",
    "MemoryTreePaths",
    "RefreshResult",
    "RefreshTrigger",
    "SourceSummary",
    "TopicSummary",
    "compute_hash",
]
