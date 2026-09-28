"""Helm — a stability-first operations layer for long-lived agent workspaces.

The CLI lives in :mod:`helm.cli`. Attribute access is forwarded there lazily so
that importing a leaf such as ``helm.scripts.state_io`` does not pull in the whole
subcommand tree. Use ``importlib.import_module`` rather than ``from . import cli``:
the latter re-enters this ``__getattr__`` through ``_handle_fromlist`` and recurses
until the stack is exhausted.
"""
from __future__ import annotations

import importlib
import importlib.metadata as _metadata
import tomllib
from pathlib import Path


def _read_version() -> str:
    """Report the installed distribution's version, or derive it from
    ``pyproject.toml`` in a source checkout where no distribution is installed.

    Deliberately does not touch :mod:`importlib.import_module` on ``helm.cli``
    or anything else in this package — only stdlib metadata/tomllib — so it
    cannot regress the lazy-import guarantee this module exists to provide.
    """
    try:
        return _metadata.version("helm-agent-ops")
    except _metadata.PackageNotFoundError:
        pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
        try:
            data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
            return data["project"]["version"]
        except (FileNotFoundError, KeyError):
            return "0.0.0+unknown"


__version__ = _read_version()


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
