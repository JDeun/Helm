# Model Repair Integration — Wave 2 (N-C + N-D)

**Branch:** `feat/wave2-repair-respond-2026-05-22`
**Date:** 2026-05-22

---

## 1. Why Library, Not In-Process Proxy

Earlier design notes suggested wiring repair logic into `scripts/intelligence_tier.py`
as an in-process integration.  After auditing the codebase, that scope was incorrect:

- `intelligence_tier.py` only **classifies tier** from a discovery snapshot
  (`mode`, `available_tiers`).  It makes no model calls.
- Actual model calls live in the **external runtime** (Claude Code, OpenClaw runner).
- Therefore Wave 2's real job is **library hardening**: expose clean entry points
  with feature-flag detection and shadow-mode defaults so runner-side code can
  consume the spike modules safely.

The two new orchestrator modules (`scripts/model_repair.py`,
`scripts/respond_tool_wiring.py`) are pure Python libraries with no HTTP layer,
no subprocesses, and no side effects beyond optional JSONL trace appends.

---

## 2. API Summary

### `scripts/model_repair.py`

**`repair_enabled() -> bool`**
Reads `HELM_MODEL_REPAIR` at call time.  Returns `True` iff the value is
`"1"`, `"true"`, or `"yes"` (case-insensitive, stripped).  All other values
including unset return `False`.  Opt-in default ensures no behavioral change
until an operator explicitly enables repair.

**`evaluate_response(payload, *, model, tool_required, attempt, policy, traces_dir) -> dict`**
One-shot repair decision.  Calls `validate_response` + `build_nudge` +
`should_retry` and returns a structured verdict dict with keys `verdict`,
`issues`, `nudge`, `next_attempt`, and `shadow_mode`.  When `policy=None`,
reads `references/local_model_proxy_policy.json` fresh on each call (policy
can change at runtime; the cost is negligible vs. a model round-trip).
Shadow logging via `record_proxy_event` fires whenever `traces_dir` is
provided, independent of `repair_enabled()`.

**`repair_loop(*, invoke_model_fn, tools, model, tool_required, ...) -> dict`**
Canonical integration shape for external runners.  Drives the
validate-nudge-retry loop internally.  Returns
`{"response": ..., "issues": ..., "attempts": ...}`.  In shadow mode
(flag off), invokes the model once and returns without retrying.

### `scripts/respond_tool_wiring.py`

**`synthetic_respond_enabled() -> bool`**
Reads `HELM_SYNTHETIC_RESPOND` at call time.  Same truthy semantics as
`repair_enabled()`.

**`prepare_tools(tools, *, model_tier) -> list[dict]`**
Returns an augmented tools list.  Appends the synthetic respond tool iff
`synthetic_respond_enabled()` is `True` AND `model_tier == "L3_local_model"`.
Pure — input list is never mutated.

**`finalize_response(response, *, tool_required) -> dict`**
Strips respond-tool calls from the response and validates enforcement.
Adds `_finalize_warning` when `tool_required=True` and no respond call was
found.  Never raises.

---

## 3. Typical Runner Integration

```python
from scripts.model_repair import repair_loop
from scripts.respond_tool_wiring import prepare_tools, finalize_response

def run_with_repair(raw_tools, model_tier, model_id, tool_required):
    tools = prepare_tools(raw_tools, model_tier=model_tier)

    def invoke(tools_list, nudge):
        # nudge is None on first call; a string on retries
        messages = build_messages(nudge)          # runner-defined
        return call_model(model_id, tools_list, messages)  # runner-defined

    result = repair_loop(
        invoke_model_fn=invoke,
        tools=tools,
        model=model_id,
        tool_required=tool_required,
        traces_dir=Path("~/.helm/state/traces").expanduser(),
        max_attempts=3,
    )

    return finalize_response(result["response"], tool_required=tool_required)
```

---

## 4. Feature Flag Matrix

| `HELM_MODEL_REPAIR` | `HELM_SYNTHETIC_RESPOND` | `model_tier`       | Effect                                         |
|---------------------|--------------------------|--------------------|------------------------------------------------|
| off (default)       | off (default)            | any                | Shadow: validate + log only, no intervention   |
| off                 | on                       | `L3_local_model`   | Respond tool injected; repair in shadow only   |
| off                 | on                       | `L4_cloud_provider`| No injection; repair in shadow only            |
| on                  | off                      | any                | Repair active; no respond tool injection       |
| on                  | on                       | `L3_local_model`   | Full mode: repair + respond tool both active   |
| on                  | on                       | `L4_cloud_provider`| Repair active; no respond tool injection       |

---

## 5. Shadow Mode → Enforce Mode Rollout Plan

**Phase 1 — Shadow (current default):**
Deploy with both flags unset.  `repair_loop` calls the model once, validates,
and logs to `traces_dir`.  No nudges sent, no retries.  Operators can inspect
`proxy-events.jsonl` to measure issue frequency without affecting model output.

**Phase 2 — Selective enable:**
Set `HELM_MODEL_REPAIR=1` on a single runner instance processing local-model
tasks.  Monitor `proxy-events.jsonl` for `nudge_and_retry` vs `abort` ratios.
Evaluate whether nudges actually improve subsequent call quality.

**Phase 3 — Respond tool (L3 only):**
Set `HELM_SYNTHETIC_RESPOND=1` on the same instance.  Confirm that
`prepare_tools` injects the tool only for `L3_local_model` tier tasks, and
that `finalize_response` strips it cleanly from responses.

**Phase 4 — Full enforce:**
Enable both flags globally.  Shadow mode can be re-engaged at any time by
unsetting `HELM_MODEL_REPAIR`.

---

## 6. Open Questions

**OQ-1 (from Task 15 spike, still open):**
In-process library vs. HTTP sidecar proxy?  The current design is in-process
(direct function calls from the runner).  An HTTP proxy would allow language
boundaries (non-Python runners) and independent deployment, but adds latency
and operational complexity.  This wave delivers the in-process shape; the HTTP
layer remains out of scope pending evidence of cross-language demand.

**OQ-2:**
Should `repair_loop` accept a `nudge_injection_fn` to allow callers to control
how nudges are inserted into the conversation history?  Currently the nudge
string is passed directly as a parameter to `invoke_model_fn`; the runner must
decide how to prepend it.  Documenting this as a convention (not enforced) is
the current approach.
