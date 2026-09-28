#!/usr/bin/env python3
"""Shared frontmatter parser for SKILL.md / Obsidian-style markdown.

Background
----------
Prior to this module the codebase carried three independent
``---``-delimited frontmatter parsers (see the 2026-05-21 Helm full
review §Duplication Findings):

* :mod:`memory_tree.tree._parse_frontmatter` — supports scalars,
  inline lists ``[a, b, c]``, and quoted strings; used for memory-tree
  summaries.
* :mod:`scripts.skill_lifecycle_lib._parse_frontmatter` — simple
  ``key: value`` regex-based parser used for SKILL.md headers.
* :class:`helm_frontmatter.Frontmatter` — strict 10-field dataclass
  schema for Obsidian note frontmatter; this one is a *schema*, not a
  parser, and is kept distinct because its purpose is validation, not
  parsing.

This module hosts a single lightweight parser that the first two can
delegate to without changing their on-disk format. The
:class:`helm_frontmatter.Frontmatter` schema validator is intentionally
left untouched — it consumes already-parsed dicts.

Public API
----------
* :func:`parse_frontmatter` — return ``(frontmatter_dict, body)``.
  Accepts the YAML-ish subset (scalars, ``[a, b]`` lists, ``"quoted"``
  strings, ``true`` / ``false`` / ``null``) that memory_tree already
  uses. Skill_lifecycle's simpler ``key: value`` use case is a
  strict subset and works transparently.
* :func:`parse_frontmatter_str_only` — convenience wrapper that returns
  a ``dict[str, str]`` (every value coerced to ``str``) to match the
  legacy ``skill_lifecycle_lib._parse_frontmatter`` shape.

Behavior on missing or malformed input
---------------------------------------
If the text does not start with ``---`` or the closing fence is missing
the function returns ``({}, text)``.  This matches both legacy
implementations' fail-open contract — a SKILL.md without frontmatter is
treated as a markdown body with no metadata.
"""

from __future__ import annotations

__all__ = [
    "parse_frontmatter",
    "parse_frontmatter_str_only",
    "split_inline_list",
]


def parse_frontmatter(text: str) -> tuple[dict[str, object], str]:
    """Parse the leading ``---``-delimited frontmatter block.

    Returns ``(frontmatter_dict, body)`` where ``body`` is the text
    *after* the closing ``---`` fence (with one newline consumed) and
    ``frontmatter_dict`` maps each key to a parsed scalar / list /
    ``None``.

    Recognises:

    * scalar ``key: value`` lines (int / float / quoted-string / bool /
      null detection via :func:`_parse_value`)
    * inline lists: ``key: [a, b, "c, d"]``
    * empty lines and ``# comment`` lines (skipped)

    Behavior on malformed lines mirrors :mod:`memory_tree.tree` — lines
    without a colon are silently dropped so a user-edited note never
    crashes the loader.
    """

    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    header = text[3:end].lstrip("\n")
    tail_start = end + len("\n---")
    if tail_start < len(text) and text[tail_start] == "\n":
        tail_start += 1
    body = text[tail_start:]

    out: dict[str, object] = {}
    for raw in header.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if ":" not in raw:
            continue
        key, _, value = raw.partition(":")
        key = key.strip()
        value = value.strip()
        out[key] = _parse_value(value)
    return out, body


def parse_frontmatter_str_only(text: str) -> dict[str, str]:
    """Parse frontmatter and coerce all values to strings.

    Matches the legacy
    :func:`scripts.skill_lifecycle_lib._parse_frontmatter` shape, which
    returns ``dict[str, str]`` with the raw textual value (no scalar
    coercion). The shared implementation re-renders parsed values as
    strings for that single caller.
    """
    parsed, _body = parse_frontmatter(text)
    out: dict[str, str] = {}
    for key, value in parsed.items():
        if value is None:
            out[key] = ""
        elif isinstance(value, (list, tuple)):
            out[key] = "[" + ", ".join(_format_scalar(v) for v in value) + "]"
        elif isinstance(value, bool):
            out[key] = "true" if value else "false"
        else:
            out[key] = str(value)
    return out


def split_inline_list(inner: str) -> list[str]:
    """Split the body of ``[a, b, "c, d"]`` honouring quoted commas.

    Returns the raw token fragments (still trimmed) so each can be
    passed back through :func:`_parse_value`. Exposed publicly because
    :mod:`memory_tree.tree` uses the same helper in its render path.
    """
    parts: list[str] = []
    buf: list[str] = []
    in_quote = False
    escape = False
    for ch in inner:
        if escape:
            buf.append(ch)
            escape = False
            continue
        if ch == "\\" and in_quote:
            buf.append(ch)
            escape = True
            continue
        if ch == '"':
            in_quote = not in_quote
            buf.append(ch)
            continue
        if ch == "," and not in_quote:
            parts.append("".join(buf).strip())
            buf = []
            continue
        buf.append(ch)
    tail = "".join(buf).strip()
    if tail or parts:
        parts.append(tail)
    return parts


def _parse_value(value: str) -> object:
    if value == "" or value.lower() == "null":
        return None
    if value.lower() == "true":
        return True
    if value.lower() == "false":
        return False
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        parts = split_inline_list(inner)
        return [_parse_value(p) for p in parts]
    if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        return value[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    try:
        if "." in value:
            return float(value)
        return int(value)
    except ValueError:
        return value


def _format_scalar(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)
