# Local Model Guard Proxy — Spike (Task 15)

**Branch:** `feat/harness-engineering-2026-05-22`

---

## 1. Spike Scope

**In scope:**

- `validate_response` — classify failure modes in a local model response payload.
- `build_nudge` — produce a priority-ordered retry prompt from the issue list.
- `should_retry` — decide whether to attempt another call based on attempt count and policy.
- `record_proxy_event` — append a structured JSONL event to a traces directory.
- `references/local_model_proxy_policy.json` — the default policy document.
- Unit tests covering all four functions with fakes; no live model required.

**Out of scope (this spike):**

- HTTP server, WSGI/ASGI wrapper, socket listener.
- Streaming (chunked) response handling.
- Real subprocess calls to Ollama, llama.cpp, LM Studio, or vLLM.
- OpenAI-compatible API surface (`/v1/chat/completions` endpoint).
- Authentication, TLS, or network configuration.

---

## 2. Why a Proxy (Forge Motivation)

Source: `~/Downloads/forge-openclaw-helm-development-direction-2026-05-20.md`,
section "Local Model Guard Proxy."

Small and self-hosted local models frequently fail tool-calling in ways
that cloud-hosted models handle reliably:

- Responding with plain prose instead of a tool call.
- Producing a tool call with missing or malformed fields.
- Embedding invalid JSON in the `arguments` field.
- Issuing a terminal text answer before completing required tool steps.
- Returning an empty response body.

Forge addresses these failures with a `ResponseValidator`, a retry nudge,
and an optional OpenAI-compatible proxy that clients reach by changing
only the base URL. The insight: tool-call reliability can be improved
*without modifying the agent loop* by inserting a thin guard layer between
the router and the local backend.

For OpenClaw and Helm, the proxy is optional and scoped to the
weak-model fallback path. It does not replace the agent loop; it adds a
reliability buffer in front of the models that need it most.

---

## 3. Components

### `validate_response(payload: dict) -> dict`

Takes a model response payload (OpenAI-style dict with `content`,
`tool_calls`, and optional `tool_required`). Returns
`{"valid": bool, "issues": list[str], "repair_hint": str | None}`.

Detected issue codes (exact strings):

| Code | Condition |
|---|---|
| `malformed_tool_call` | `tool_calls` entry is missing `name` or `arguments` |
| `non_json_when_tool_required` | `tool_required=True` but response has content and no tool call |
| `invalid_json_in_arguments` | `arguments` string is not parseable JSON |
| `terminal_without_tool` | `tool_required=True` and model gave a text final answer |
| `empty_response` | `content` is empty string AND `tool_calls` is absent |

### `build_nudge(issues: list[str]) -> str`

Takes the issue list from `validate_response`. Returns a non-empty
string by iterating issues in priority order (most actionable first) and
concatenating one nudge sentence per recognised code.

Priority order: `malformed_tool_call` → `invalid_json_in_arguments` →
`non_json_when_tool_required` → `terminal_without_tool` → `empty_response`.

### `should_retry(attempt: int, issues: list[str], policy: dict) -> bool`

Evaluates in order:

1. No issues → `False` (no retry needed).
2. Any issue in `abort_on` → `False` (abort immediately; retrying would
   not help a model that is deliberately terminating early).
3. `attempt >= max_retries` → `False` (budget exhausted).
4. Any issue in `nudge_on` → `True`.
5. Default → `False`.

### `record_proxy_event(traces_dir, model, issues, action, attempt) -> None`

Appends one JSON line to `<traces_dir>/proxy-events.jsonl`.

Fields: `timestamp` (ISO-8601 UTC), `model`, `issues`, `action`
(`"retry"` / `"abort"` / `"pass"`), `attempt` (zero-based).

Uses `O_APPEND` (Python `open(..., "a")`) for atomic single-line appends;
see the module docstring in `scripts/local_model_proxy.py` for the
full rationale and tradeoffs vs. tempfile-rename.

---

## 4. Where the Proxy Would Sit in OpenClaw's Provider Chain

The proxy is placed **between the fallback router and the local model
backend**. No other layer in the chain changes.

```
host runtime
  └── Helm profile / guard (validation_gate, command_guard)
        └── OpenClaw provider router
              └── [local_model_proxy — guard layer]    ← this spike
                    └── local model backend
                          (Ollama / llama.cpp / LM Studio / vLLM)
```

The proxy is transparent to all layers above it. The host runtime and
Helm guard continue to operate against the same OpenAI-compatible
interface. Only the local model path has the guard; cloud-provider paths
bypass it entirely.

The proxy does not see Helm's task-state, ledger, required steps, or
side-effect approvals — those concerns live above the LLM call boundary.

---

## 5. What This Spike Does NOT Do

- **No HTTP server.** The four functions are library functions, not
  request handlers. Wrapping them in a FastAPI or aiohttp server is
  Task #15.1.
- **No streaming.** Streaming requires a chunked-response reassembler
  before validation can run. Out of scope until the HTTP wrapper exists.
- **No real model call.** Tests use hard-coded dicts; no Ollama or
  llama.cpp process is launched.
- **No OpenAI-compatible shim.** The proxy does not parse
  `/v1/chat/completions` request envelopes or rewrite response headers.
- **No multi-turn context injection.** The nudge is a string; it is the
  caller's responsibility to prepend it to the next request's message
  history.

---

## 6. Decision Criteria: When to Graduate to a Real Proxy

Graduate from spike to production-ready proxy when **at least three** of
the following are true:

1. A confirmed local-model fallback path exists in OpenClaw's provider
   router (Ollama or llama.cpp endpoint configured and exercised in CI).
2. The spike's `validate_response` has been tested against real
   malformed payloads from at least one local model and confirmed to
   detect actual failure modes.
3. A stakeholder decision has been made on transport: in-process library
   call vs. out-of-process HTTP sidecar (see open question OQ-1 below).
4. Streaming is required by the fallback path (if not, the HTTP wrapper
   can be deferred further).
5. The nudge retry loop has been validated to improve tool-call
   reliability on at least one benchmark scenario (eval evidence, not
   just unit tests).
6. The proxy event log (`proxy-events.jsonl`) is being consumed by a
   downstream analysis tool (e.g. `ops_daily_report.py` or a new
   `proxy_report.py`).

---

## 7. Open Questions for Kevin

**OQ-1. In-process library vs. out-of-process HTTP sidecar.**
Should the guard be called as a Python library function inside the
provider router, or should it run as a separate HTTP process that
OpenClaw routes through? The HTTP sidecar is closer to Forge's design
and allows polyglot clients; the in-process call is simpler to deploy
and debug. Which deployment model fits OpenClaw's architecture?

**OQ-2. Streaming compatibility.**
Does the fallback local model path use streaming responses? If so, the
validator cannot run until the full response is assembled. Should the
proxy buffer the stream (adding latency), or should streaming be
disabled on the fallback path?

**OQ-3. Nudge injection mechanism.**
The `build_nudge` output is a plain string. Who is responsible for
injecting it into the next request — the proxy, the provider router, or
the agent loop? If the proxy is out-of-process HTTP, it would need to
modify the outbound request body, which requires knowing the message
schema.

**OQ-4. `tool_required` signal source.**
`validate_response` accepts a `tool_required` flag to detect
`non_json_when_tool_required` and `terminal_without_tool`. Where does
this flag come from in a real request? Does OpenClaw's router know
whether the current call requires a tool response, or does that
information live only in the system prompt / tool list?

**OQ-5. Abort vs. escalate on `terminal_without_tool`.**
The policy currently aborts on `terminal_without_tool`. Should abort
mean "return an error to the caller" or "escalate to the primary model"?
If OpenClaw has a primary/fallback pair, aborting the fallback could
trigger automatic escalation; is that the desired behavior?

**OQ-6. Policy per model vs. global policy.**
`local_model_proxy_policy.json` defines a single global policy.
Different local models may need different `max_retries` or `nudge_on`
sets (e.g. a model known to be unreliable on JSON might need more
retries). Should the policy support per-model overrides?

**OQ-7. Event log rotation and retention.**
`proxy-events.jsonl` grows unboundedly. Should the proxy rotate logs by
date or size? Is the `proxy-events.jsonl` format compatible with the
existing JSONL tooling in `scripts/jsonl_io.py` (it is, but it should
be confirmed as a consumer)?

**OQ-8. Integration with `failure_signature.py`.**
The proxy's issue codes (`malformed_tool_call`, etc.) are structurally
similar to the failure signatures in `scripts/failure_signature.py`. Should
proxy-detected failures be promoted into a `FailureSignature` and written
to the task ledger, or should the proxy event log remain a separate
low-level trace?
