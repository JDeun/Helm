# Helm Product Definition

## One-line Definition

Helm is a stability-first operations layer for long-lived agent workspaces.

## Problem

Agent runtimes are good at tool invocation and flexible prompting, but they
often stay weak in the parts that matter once repeated work begins:

- choosing how risky an action is before execution
- remembering operational context without relying on chat history
- tracing what task ran, what commands it triggered, and what changed
- recovering from mistakes with explicit rollback paths
- improving repeatable workflows without uncontrolled self-modification

Helm targets that operations gap.

## Product Thesis

The valuable layer is not another agent runtime. The valuable layer is an
agent-operations framework that sits above or beside an existing runtime and
makes repeated work governable, inspectable, and recoverable.

## Target User

Primary:

- developers and power users running local or self-hosted agents
- operators who want automation without full-autonomy chaos
- people who care about reproducibility, rollback, and human-in-the-loop
  boundaries

Secondary:

- teams prototyping internal agent workflows
- makers building personal assistant systems on top of OpenClaw/Hermes-style
  runtimes

## Core Promise

Helm helps an agent do more useful work without becoming opaque or reckless.

## Core Features

### Execution Profile Discipline

Every meaningful action can run under a declared profile such as
`inspect_local`, `workspace_edit`, `risky_edit`, `service_ops`, or
`remote_handoff`. This makes blast radius visible before execution.

### Context Hydration

Upper-layer workflows can hydrate context from durable memory, task history,
command failures, checkpoints, and state snapshots.

### Task and Command Observability

Helm records parent task metadata, low-level command traces, execution profile,
runtime target, and delivery mode. This creates a practical audit trail.

### Rollback-aware Workflows

Risky edits can be checkpointed and linked back to the task ledger so Helm can
suggest the nearest rollback candidate.

### Gated Self-improvement

Repeated successful work can become skill drafts. Drafts are assessed before
promotion. Promotion requires explicit approval.

### Operations Reporting

Helm can summarize recent outcomes, failed commands, handoff-required tasks,
checkpoints, and draft assessment state.

## Non-goals

Helm is not:

- a foundation model project
- a generic chat UI
- a universal MCP replacement
- a fully autonomous planner-executor with no human oversight
- a memory product for storing private life data in the open-source core

## Public Framing

Helm is an open-source agent-operations layer for local and self-hosted agents.

Recommended tagline:

`Operate agents with profiles, context, audit trails, rollback, and approval-gated self-improvement.`
