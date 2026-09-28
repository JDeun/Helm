"""Helm — a stability-first operations layer for long-lived agent workspaces.

The CLI lives in :mod:`helm.cli`. Attribute access is forwarded there lazily so
that importing a leaf such as ``helm.scripts.state_io`` does not pull in the whole
subcommand tree. Use ``importlib.import_module`` rather than ``from . import cli``:
the latter re-enters this ``__getattr__`` through ``_handle_fromlist`` and recurses
until the stack is exhausted.
"""
from __future__ import annotations

import importlib

__version__ = "0.13.0"


def __getattr__(name: str) -> object:
    if name.startswith("__"):
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    cli = importlib.import_module(f"{__name__}.cli")
    try:
        value = getattr(cli, name)
    except AttributeError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    import importlib as _importlib

    return sorted(set(globals()) | set(dir(_importlib.import_module(f"{__name__}.cli"))))
