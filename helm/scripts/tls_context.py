#!/usr/bin/env python3
"""Shared TLS/SSL context factory.

Nine+ scripts each defined the same certifi-backed SSL context (with a plain
default-context fallback when certifi is unavailable). This is the single copy so
the CA-bundle behavior can't drift between call sites.
"""
from __future__ import annotations

import ssl


def default_ssl_context() -> ssl.SSLContext:
    """An SSL context using certifi's CA bundle when available, else the system
    default. Never raises — falls back to the plain default context."""
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()
