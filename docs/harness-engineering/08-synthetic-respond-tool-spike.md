# Synthetic Respond Tool — Spike

**Task 16** | Branch: `feat/harness-engineering-2026-05-22`

Companion spike to Task 15 (local model proxy). Adds a Forge-inspired
`respond(message=…)` tool that keeps small models in tool-calling mode
for their final turn.

---

## 1. Spike Scope

This spike implements three building-block functions and a schema file:

| Component | File |
|---|---|
| Canonical schema | `references/respond_tool_schema.json` |
| Schema loader + cache | `scripts/synthetic_respond_tool.py :: respond_tool_schema()` |
| Tool-list injection | `scripts/synthetic_respond_tool.py :: inject_respond_tool()` |
| Response stripping | `scripts/synthetic_respond_tool.py :: strip_respond_call()` |
| Terminal enforcement | `scripts/synthetic_respond_tool.py :: enforce_final_response()` |
| Tests | `tests/test_synthetic_respond_tool.py` (11 cases) |

Full runner integration, streaming support, and multi-turn coordination are
explicitly **out of scope** for this spike.

---

## 2. Why a Synthetic Respond Tool

### The small-model failure mode

Small local models (Ollama, llama.cpp, LM Studio) alternate between
**tool-call mode** and **text mode**.  The mode classifier is unreliable:
the model may emit a plain-text final answer when the runner expects a tool
call, breaking the agent loop; or it oscillates between modes across retries.

### The Forge mitigation (§5)

Inject a synthetic `respond(message=…)` tool into every request.  The model
never needs to leave tool-calling mode — it calls `respond` instead of
emitting free text.  The runner strips the call before surfacing the answer.
The model's "final answer" path becomes structurally identical to any other
tool call; the weak classifier is bypassed.

---

## 3. Component Diagram

```
Request path
────────────
caller tool list
    │
    ▼
inject_respond_tool()          ← appends respond schema if absent
    │
    ▼
local model (Ollama / llama.cpp / …)
    │  emits tool_calls=[…, respond(message="…")]
    ▼
strip_respond_call()            ← promotes respond.message → response.content
    │                             removes respond from tool_calls
    ▼
enforce_final_response()        ← optional guard: was respond actually used?
    │
    ▼
upstream runner / user
```

The three functions are pure and composable.  A caller may use any subset
depending on its policy.

---

## 4. Interaction with Task 15 (Local Model Proxy)

Task 15's `validate_response()` produces issue codes including
`terminal_without_tool` — raised when a model response has no tool calls but
the workflow still expects one.

This module provides the structural fix:

| Task 15 issue code | Task 16 remedy |
|---|---|
| `terminal_without_tool` | `inject_respond_tool()` ensures a tool is always available; `enforce_final_response()` confirms the model used it. |

Integration point: the proxy guard loop calls `inject_respond_tool()` before
forwarding the request, then calls `strip_respond_call()` and
`enforce_final_response()` on the response.  Neither module imports the other;
composition lives in the guard loop caller.

---

## 5. Edge Cases

### 5a. `tool_calls` key absent from response

`strip_respond_call` calls `response.get("tool_calls", [])` and returns the
original response unchanged if the list is empty or absent.
`enforce_final_response` does the same: absent `tool_calls` is treated as an
empty list, yielding `terminal_without_respond` when `required=True`.

### 5b. Respond present alongside other tool calls

Priority: respond wins the terminal slot.  Other calls are preserved in the
returned `tool_calls` list so the runner can dispatch them if needed.

### 5c. Multiple `respond` calls in one response

First call's message wins; extras are discarded with a `WARNING` log.  Using
the first message is more useful than raising an exception and crashing the
guard loop.

### 5d. Non-parseable JSON in `arguments`

`strip_respond_call` returns a copy of the original response with a
`_strip_warning` key.  Never raises.  Input dict is not mutated.  The caller
inspects `_strip_warning` to decide whether to retry or log the failure.

---

## 6. Out of Scope

- **Streaming** — `strip_respond_call` operates on a complete response dict.
  Streaming chunk assembly is a runner concern.
- **Real runner integration** — no modifications to the adaptive harness or
  any existing runner loop.  The functions are standalone utilities.
- **Multi-turn coordination** — respond injection is a per-request operation.
  Cross-turn state management belongs to the task-state layer (Task 2/3).
- **HTTP proxy wrapping** — Task 15.1 (future).  This module is called as a
  library, not as an HTTP middleware.
- **Schema versioning** — the schema is loaded from a single file.  A
  versioning or migration strategy is not addressed.

---

## 7. Open Questions for Kevin

1. **Respond-tool priority vs. multi-step plans** — If the model emits
   `respond` alongside a real tool call (e.g., `file_write`), should the
   runner execute the real tool call before delivering the respond message,
   or should respond always halt execution immediately?  Current behaviour:
   respond wins terminal, other calls are preserved but not dispatched.

2. **Schema evolution** — The respond schema is loaded from a static JSON
   file.  If the schema needs to change (e.g., add an `error` field), how
   should existing deployments be migrated?  Should the file be versioned, or
   should the loader accept a version parameter?

3. **Idempotency with other final-answer conventions** — Some runtimes use a
   `finish_reason: "stop"` signal instead of an empty `tool_calls` list to
   signal completion.  Should `enforce_final_response` also check
   `finish_reason`, or should it remain a pure `tool_calls` inspector?

4. **Respond tool visibility to the user** — The schema is injected into
   every request.  If the user or a downstream observer inspects the raw
   request, they will see the synthetic tool.  Is that acceptable, or should
   the injection be transparent (e.g., done inside the HTTP proxy after the
   client has already sent the request)?

5. **Warning channel for multiple respond calls** — Currently the warning is
   emitted via `logging.warning`.  Should it also be recorded as a proxy
   event (Task 15's `record_proxy_event`) so the pattern shows up in the ops
   JSONL log?

6. **Cache invalidation** — `respond_tool_schema()` caches the schema for the
   process lifetime.  In a long-running proxy process, a schema file update
   would require a restart.  Should the cache include a file-mtime check, or
   is a restart-to-update policy acceptable?
