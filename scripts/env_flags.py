"""Shared env-var truthy detection for Helm feature flags.

A single source of truth for which env-var values enable a feature flag.
All feature flags in scripts/ must use this helper to keep semantics
consistent across modules.
"""
from __future__ import annotations

import os

_TRUTHY_VALUES = frozenset({"1", "true", "yes"})


def is_truthy(raw: str | None) -> bool:
    """Return True iff raw is one of '1', 'true', 'yes' (case-insensitive, stripped).

    Other values (including None, empty string, '0', 'false', 'no', and any
    unknown string) return False.
    """
    if raw is None:
        return False
    return raw.strip().lower() in _TRUTHY_VALUES


def env_flag(name: str) -> bool:
    """Return True iff os.environ[name] is truthy per `is_truthy`."""
    return is_truthy(os.environ.get(name))
