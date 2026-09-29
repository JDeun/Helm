"""Canonical secret/PII scrubbers (shape-based redaction).

Two scrubbers, one rule set:
- `redact(text)` — full content-publishing scrubber (authorship trailers + paths + email
  + IP + blob on top of the secret rules). Extracted verbatim from content_draft_runner.
- `redact_secrets(text)` — secret/key TOKEN shapes ONLY (no path/email/IP/blob/trailer),
  for redacting execution output / evidence / state bundles where masking file paths, git
  SHAs (40-char blob), or emails would destroy useful context. Superset of the narrow
  per-file regexes that evidence_gatherer / verified_execution / parallel_worktree_review /
  task_state_bundle previously hand-rolled — closing e.g. their weaker/absent JWT coverage.

Input is capped so no regex can be driven into super-linear time (ReDoS).
"""
from __future__ import annotations

import re

# --- secret/key TOKEN shapes (shared by both scrubbers) ----------------------
# generic "secret/token/password/api_key = value" (catches unforeseen secret shapes);
# no leading \b: catch PREFIXED keys too (DB_PASSWORD=, openai_api_key=, access_token=)
# no-\b + \w* (content publishing): also catch PREFIXED keys (DB_PASSWORD=, openai_api_key=).
# Aggressive on purpose — fine for prose, but it over-matches debug identifiers like
# "real_secret_visible=False", so execution/state redaction uses _KV_ANCHORED instead.
_GENERIC_KV = (re.compile(r"(?i)(?:secret|token|passwd|password|api[_-]?key|access[_-]?key|access[_-]?token|"
                          r"refresh[_-]?token|secret[_-]?key|client[_-]?secret|auth[_-]?token|bearer)\w*\s*[=:]\s*\S+"), "[secret]")
# \b-anchored, no \w* sprawl (execution/state): matches "token=val"/"password: x" but NOT a
# variable merely CONTAINING a keyword (real_secret_visible=…) — preserves the old per-file behavior.
_KV_ANCHORED = (re.compile(r"(?i)\b(?:secret|token|passwd|password|api[_-]?key|access[_-]?key|access[_-]?token|"
                           r"refresh[_-]?token|secret[_-]?key|client[_-]?secret|auth[_-]?token|authorization)\s*[=:]\s*\S+"), "[secret]")
_CONNSTRING = (re.compile(r"://[^/@\s:]+:[^/@\s]+@"), "://[redacted]@")  # creds in a connection-string URL
_BOT = (re.compile(r"\bbot\d{5,}:[A-Za-z0-9_-]{15,}\b"), "[secret]")
# sk-/gh?_ (ghp/gho/ghu/ghs/ghr)/github_pat/xox — {8,} to superset the per-file copies
_TOKEN_PREFIX = (re.compile(r"\b(sk|ghp|gho|ghu|ghs|ghr|github_pat|xox[baprs])[-_][A-Za-z0-9_-]{8,}\b"), "[secret]")
_AKIA = (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[secret]")  # AWS access key id
_GOOGLE_KEY = (re.compile(r"\bAIza[0-9A-Za-z_-]{20,}\b"), "[secret]")  # Google API key
# `Authorization: Bearer <token>` uses a SPACE (not =/:).
# STRICT (content publishing): lookahead requires a non-alpha char so plain prose
# ("bearer standing tall") isn't clobbered — real tokens carry a digit/symbol.
_BEARER_STRICT = (re.compile(r"(?i)\bbearer\s+(?=[A-Za-z0-9._~+/=-]*[0-9._~+/=-])[A-Za-z0-9._~+/=-]{8,}"), "[secret]")
# BROAD (execution/state secret redaction): mask ANY token after Bearer, incl. pure-alpha —
# these contexts aren't prose, so fail-secure > prose-preservation (matches the old per-file rule).
_BEARER_BROAD = (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+"), "[secret]")
# `Basic <base64>` is the same shape and the same secret -- an HTTP auth scheme
# carrying a credential after the scheme word. Both scrubbers leaked it: the
# bearer rules only matched the word "bearer", and _BLOB's 40-char floor is above
# a typical base64 user:pass. Same pattern, different scheme word.
_BASIC_AUTH = (re.compile(r"(?i)\bbasic\s+[A-Za-z0-9+/=]{8,}"), "[secret]")
# JWT by STRUCTURE (base64url.base64url.base64url, always starts `eyJ`) — no keyword needed
_JWT = (re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}"), "[jwt]")
# whole PEM block (input is capped below, so the bounded lazy body is ReDoS-safe) + orphan header
_PEM_BLOCK = (re.compile(r"(?i)-----BEGIN[A-Z0-9 ]{0,40}-----[\s\S]{0,4000}?-----END[A-Z0-9 ]{0,40}-----"), "[key]")
_PEM_HEADER = (re.compile(r"-----(?:BEGIN|END)[^-\n]{0,60}-----"), "[key]")

SECRET_PATTERNS = [
    # _BEARER_BROAD MUST precede _KV_ANCHORED: otherwise "Authorization: Bearer <token>" is eaten
    # by the KV rule (its `\S+` grabs the word "Bearer" after "authorization:"), leaving the opaque
    # token orphaned — _BEARER_BROAD then finds no "bearer" keyword and the token LEAKS. Bearer-first
    # masks the whole "Bearer <token>" before the KV rule touches the header.
    _BEARER_BROAD, _BASIC_AUTH, _KV_ANCHORED, _CONNSTRING, _BOT, _TOKEN_PREFIX, _AKIA, _GOOGLE_KEY, _JWT, _PEM_BLOCK, _PEM_HEADER,
]

# Compiled patterns only (no baked replacement) — for consumers that apply their own
# marker ("[REDACTED]") and/or count replacements while reusing the canonical shape set.
SECRET_REGEXES = [pat for pat, _ in SECRET_PATTERNS]

# --- content-publishing extras (redact() only) -------------------------------
_TRAILERS = [
    (re.compile(r"^(Co-Authored-By|Claude-Session|Signed-off-by|Reviewed-by):.*$", re.M | re.I), ""),
    (re.compile(r"🤖.*$", re.M), ""),
    (re.compile(r"https?://claude\.ai/\S+", re.I), "[link]"),
]
# Home-directory shapes that identify a person, on every OS this package declares
# support for. It used to be macOS (/Users/) and ~/ only, while pyproject declares
# "OS Independent" and the comment claimed "any machine" -- so on Linux or Windows
# redact() masked no home path at all. Anchored on the separator after the username
# so ordinary prose ("homeward bound", "see users table") is untouched.
_PATHS = [
    (re.compile(r"/Users/[^\s\"'`]+"), "[path]"),                  # macOS
    (re.compile(r"/home/[^\s\"'`]+"), "[path]"),                   # Linux
    (re.compile(r"(?i)[A-Z]:\\Users\\[^\s\"'`]+"), "[path]"),      # Windows
    (re.compile(r"~/[^\s\"'`]+"), "[path]"),
]

# Deployment-specific directory names are NOT defined here. An earlier attempt kept
# them as a module default, which moved the problem without solving it: a module that
# ships a default naming one particular install still names that install, and still
# redacts the wrong thing for anyone else. Callers pass their own.
#
# The workspace's own names live in redaction_local.py, which is not published.
DEFAULT_EXTRA_PATH_NAMES: "tuple[str, ...]" = ()


def _extra_path_pattern(names):
    # Substring match, deliberately no \b: these are dotted names and \b does not
    # break before a leading dot.
    if not names:
        return None
    return re.compile("(" + "|".join(re.escape(n) for n in names) + ")")


def redact_paths(text: str, extra_names=DEFAULT_EXTRA_PATH_NAMES) -> str:
    """Replace personal path shapes, plus any caller-supplied directory names."""
    for pattern, replacement in _PATHS:
        text = pattern.sub(replacement, text)
    extra = _extra_path_pattern(extra_names)
    if extra is not None:
        text = extra.sub("[path]", text)
    return text
_IP = (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"), "[ip]")
# bounded quantifiers → no catastrophic backtracking on "a.a.a…"
_EMAIL = (re.compile(r"\b[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,255}\.[A-Za-z]{2,24}\b"), "[email]")
_BLOB = (re.compile(r"\b[A-Za-z0-9+/]{40,}={0,2}\b"), "[blob]")  # 40+ (was 60) — AWS secrets etc.

# Full scrubber rule list. Order matters, and got this wrong for a long time:
# _GENERIC_KV's value pattern is \S+, which stops at whitespace -- so on
# `access_token: Bearer QQQtoken`, it consumed the word "Bearer" and left the token
# orphaned in the output. SECRET_PATTERNS documents that exact failure and fixes it by
# running the bearer rules FIRST; this list did not, so the publishing scrubber leaked
# a token the evidence scrubber caught. Bearer rules now lead here too.
#
# _KV_ANCHORED carries `authorization`, which _GENERIC_KV does not, so without it
# redact() left `Authorization: <token>` completely untouched while redact_secrets()
# masked it. The module's docstring calls redact() the full scrubber "on top of the
# secret rules"; it has to actually be a superset for that to be true.
REDACT_PATTERNS = [
    *_TRAILERS,
    # _BEARER_STRICT only, NOT _BEARER_BROAD: broad matches any word after
    # "bearer", so leading with it clobbers ordinary prose ("flag bearer standing
    # tall" -> "flag [secret] tall"). Strict requires a token-shaped run of 8+.
    _BEARER_STRICT, _BASIC_AUTH,
    _KV_ANCHORED, _GENERIC_KV, _CONNSTRING,
    *_PATHS,
    _BOT, _TOKEN_PREFIX, _AKIA, _GOOGLE_KEY, _JWT, _PEM_BLOCK, _PEM_HEADER,
    _IP, _EMAIL, _BLOB,
]

_INPUT_CAP = 8000


def redact(text: str) -> str:
    """Full content-publishing scrubber: secrets + paths + IPs + emails + authorship
    trailers. Input is capped so no regex can be driven into super-linear time (ReDoS)."""
    text = (text or "")[:_INPUT_CAP]
    for pat, repl in REDACT_PATTERNS:
        text = pat.sub(repl, text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def redact_secrets(text: str) -> str:
    """Secret/key TOKEN shapes only — no path/email/IP/blob/trailer masking, so file
    paths, git SHAs, and emails in execution output/evidence survive. ReDoS-capped."""
    text = (text or "")[:_INPUT_CAP]
    for pat, repl in SECRET_PATTERNS:
        text = pat.sub(repl, text)
    return text
