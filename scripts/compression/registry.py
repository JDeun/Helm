"""Compression Profile Registry (§4.5).

Resolution order:

1. Exact ``profile_id`` match (when caller already knows the id).
2. ``input_kind`` lookup via :attr:`CompressionProfile.accepts_input_kinds`.
3. Fallback to :class:`WebFetchTextProfile` (loss-less plain text).

The fallback exists so that an unknown ``input_kind`` never silently
drops information — the worst that can happen is suboptimal token
budgeting.

A single default registry is exposed via :func:`get_default_registry`
and the module-level :func:`resolve` convenience function.

Ledger note (§4.5): callers MUST record ``profile_id`` on every entry
that travels through the registry. Helm's task ledger expects the field
``compression_profile_id``; OpenClaw connectors typically use the same
key inside their ``payload``. See :mod:`compression.__init__` for the
*sanity-check before quiet-day* contract that pairs with §4.4 (AI
briefing OpenHuman 누락 사건).
"""

from __future__ import annotations

from typing import Iterable

from .base import CompressionProfile
from .profiles import ALL_PROFILES, WebFetchTextProfile


class CompressionRegistry:
    """Lookup table mapping ``(input_kind, profile_id)`` to profiles."""

    def __init__(self, profiles: Iterable[CompressionProfile] | None = None) -> None:
        if profiles is None:
            profiles = [cls() for cls in ALL_PROFILES]
        self._profiles: list[CompressionProfile] = list(profiles)
        self._by_id: dict[str, CompressionProfile] = {
            p.profile_id: p for p in self._profiles
        }
        self._fallback: CompressionProfile = self._by_id.get(
            "web_fetch.text", WebFetchTextProfile()
        )

    # ------------------------------------------------------------------

    def register(self, profile: CompressionProfile) -> None:
        """Add (or replace by id) a profile. Useful for tests / plugins."""
        self._profiles = [p for p in self._profiles if p.profile_id != profile.profile_id]
        self._profiles.append(profile)
        self._by_id[profile.profile_id] = profile

    def profiles(self) -> list[CompressionProfile]:
        return list(self._profiles)

    def by_id(self, profile_id: str) -> CompressionProfile | None:
        return self._by_id.get(profile_id)

    # ------------------------------------------------------------------

    def resolve(
        self,
        input_kind: str,
        url: str | None = None,
        *,
        strict: bool = False,
    ) -> CompressionProfile:
        """Resolve a profile for ``(input_kind, url)``.

        * If ``input_kind`` matches a profile id, return that profile.
        * Otherwise, return the first profile whose
          :attr:`accepts_input_kinds` contains ``input_kind``.
        * Otherwise, in non-strict mode, fall back to
          :class:`WebFetchTextProfile`. In strict mode, raise
          :class:`KeyError` — useful in CI to catch typos in connector
          configs.
        """
        if input_kind in self._by_id:
            return self._by_id[input_kind]
        for profile in self._profiles:
            if profile.matches(input_kind, url):
                return profile
        if strict:
            raise KeyError(f"no compression profile registered for {input_kind!r}")
        return self._fallback


# Module-level convenience -------------------------------------------------

_DEFAULT: CompressionRegistry | None = None


def get_default_registry() -> CompressionRegistry:
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = CompressionRegistry()
    return _DEFAULT


def resolve(input_kind: str, url: str | None = None, *, strict: bool = False) -> CompressionProfile:
    """Module-level shortcut: ``compression.resolve('rss_feed')``."""
    return get_default_registry().resolve(input_kind, url, strict=strict)
