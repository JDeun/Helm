# Helm vs Forge

**Task 17** | Branch: `feat/harness-engineering-2026-05-22`

---

## 1. One-Line Definitions

**Forge:** A self-hosted LLM tool-call reliability layer — runner, proxy, and
middleware that corrects malformed tool calls, enforces step order, and
retries premature completions in per-request/response scope.

**Helm:** A long-lived personal and operational agent governance layer —
profiles, audit trail, rollback guidance, task ledger, memory, and
side-effect discipline that operate across task lifecycles, sessions, and
compaction boundaries.

---

## 2. Side-by-Side Comparison

| Dimension | Forge | Helm |
|---|---|---|
| **Scope** | Per-call request/response cycle | Long-running task lifecycle across sessions |
| **Primary concerns** | Tool-call validity, retry nudge, terminal step enforcement | Profile, audit trail, rollback, task ledger, memory, side-effect approval |
| **Layer** | Runner / proxy / middleware | Runtime-agnostic operating layer |
| **Compaction handling** | In-call retries before the context window fills | Structured control state persisted outside the transcript |
| **State owner** | WorkflowRunner holds loop state for the current call | state_io ledger holds task-state across calls, sessions, and restarts |
| **Target runtime** | Self-hosted / local LLM backends with weak tool-call support | Any agent runtime (runtime-agnostic by design) |
| **Memory boundary** | Not addressed — Forge does not define a memory model | Explicit: memory (transcript) vs control state (task-state container) |
| **Side-effect discipline** | Not in scope | `external_side_effect_approvals` and promotion gate |

---

## 3. What Helm Absorbs from Forge

Forge is a reference implementation, not a competitor. Three Forge design
principles are directly applicable to Helm and have either already been
implemented or are scheduled as near-term work.

### 3a. Control-flow state separation (Task 6 — already implemented, `25d1983`)

Forge's core design sentence: "Control flow is not memory."

Helm implemented this in Task 6 as the task-state container in
`helm_state_model.py`: `required_steps`, `completed_steps`, `blockers`,
`external_side_effect_approvals`, `finalization_state`, and
`recovered_messages`. The container survives transcript compaction and
remains authoritative for completion checks.

See `docs/harness-engineering/05-control-flow-is-not-memory.md` for the
full principle note.

### 3b. Guardrails middleware (candidate components)

Forge exposes guardrail components independently of its runner. The
components with the closest Helm counterparts are:

- **ResponseValidator** — validates tool-call format before the call
  reaches the executor. Helm analog: `command_guard` evaluates command
  shape against `guard_policy.json` before subprocess launch.
- **StepEnforcer** — blocks terminal steps until prerequisites are met.
  Helm analog: `is_finalized()` dual-condition gate (see Task 6).
- **ErrorTracker** — records and classifies tool-call failures across
  retries. Helm candidate: a structured `failure_signature` field in the
  ledger (see `01-inventory.md` Section 6 for the failure signature draft).

These are candidate components for a future Helm middleware layer, not
current implementations. The design principle — decouple the guardrail
from the runner so either can be replaced — is directly compatible with
Helm's runtime-agnostic positioning.

### 3c. Ablation-based eval (Task 7 scaffold)

Forge's eval harness separates plumbing, model quality, advanced
reasoning, compaction, and stateful scenarios and runs ablation presets
to measure each guardrail's contribution to reliability.

Helm's Task 7 scaffolds an equivalent reliability eval suite with
Helm-specific failure scenarios: action-boundary violations,
recovered-context handling, task finalization with partial completions,
rollback path reachability, and external side-effect approval gaps.
The ablation structure — turn off one guardrail at a time and measure
failure rate — is taken directly from Forge's approach.

### 3d. Synthetic respond tool (Task 16 spike)

Forge injects a synthetic `respond(message=...)` tool to keep models
in tool-calling mode and prevent premature text responses. The outbound
handler strips the respond call before delivery.

Helm's Task 16 spike evaluates this for local fallback model paths:
structured final response enforcement and a `finalize` tool gate before
task completion. This is not yet in production; it is a candidate for the
weak-model fallback path.

---

## 4. What Forge Does That Helm Does Not Do

### 4a. Replace the agent loop

Forge's `WorkflowRunner` directly owns the LLM call loop: system prompt
construction, LLM invocation, tool-call parsing, execution, retry, and
context compaction. This is intentional — Forge's goal is a reliable loop
for self-hosted models.

Helm is explicitly runtime-agnostic. It does not own or replace the agent
loop. The runner that calls the LLM is the host runtime (OpenClaw, Hermes,
Claude Code, a custom orchestrator). Helm wraps the invocation with
profiles and guards and records the result in the ledger, but it does not
control the LLM call sequence.

### 4b. Run an OpenAI-compatible proxy as a core feature

Forge provides an OpenAI-compatible proxy so existing clients can route
through Forge's guardrail stack by changing only the base URL.

Helm does not offer this as a core feature. A proxy operates at the
per-request level and has no visibility into task state, required steps,
or side-effect approvals. For Helm's governance concerns, a proxy is
insufficient.

---

## 5. Integration Sketch

The only Helm path where a Forge-style proxy adds value without creating
a governance gap is in front of a local fallback model (Task 15 spike).
When the primary model is unavailable and OpenClaw falls back to a local
Ollama or llama.cpp endpoint, the fallback model may produce malformed
tool calls or premature text answers. A lightweight guard proxy between
the fallback router and the local model backend can intercept and retry
those failures before they reach Helm's runner, keeping the fallback
path at parity with the primary path without modifying the host runtime.

Placement: `host runtime -> Helm guard/profile -> [proxy] -> local model`

The proxy is optional and scoped to the weak-model path. It does not
see Helm's task-state, ledger, or side-effect approvals — those concerns
live above the LLM call layer and are covered by Section 4.

---

See also:
- `docs/harness-engineering/05-control-flow-is-not-memory.md` — the principle note
- `docs/harness-engineering/01-inventory.md` — ledger schema and runner surface
- `helm_state_model.py` — task-state container (Task 6, `25d1983`)
