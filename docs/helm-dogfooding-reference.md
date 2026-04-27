# Helm Dogfooding Boundary

## Purpose

Helm is extracted from real long-lived agent operation, but it must remain a
portable operations layer rather than a copy of one private workspace.

This document defines how private OpenClaw-style environments should validate
Helm and how reusable behavior should be promoted back into Helm.

## Relationship

- A private agent workspace remains the personalized runtime, memory, and
  assistant environment.
- Helm remains the public, reusable safety, memory, audit, checkpoint, and
  operations layer.
- Private workspaces should validate Helm with real repeated work.
- Helm should receive only reusable, non-private patterns from those workspaces.

## What Private Workspaces Should Use Helm For

- Inspecting workspace state with `helm status --path <workspace> --brief`.
- Producing operational reports with `helm report --path <workspace>`.
- Testing task-ledger, checkpoint, and memory-capture compatibility.
- Validating whether a guard, profile, or reporting improvement belongs in
  reusable Helm core.
- Demonstrating integration paths for OpenClaw/Hermes-style agent workspaces.

## What Should Not Move Into Helm

- Personal memory or private ontology.
- Account tokens, OAuth clients, refresh tokens, or API keys.
- Schedules, family data, private obligations, or private automations.
- Assistant identity/persona files.
- User-specific model fallback preferences unless expressed as optional
  templates or examples.
- Runtime patch scripts for one local installation.

## Promotion Criteria

Promote a change only when all conditions are true:

- The behavior is useful outside one private workspace.
- The change can be explained without private context.
- It does not silently change a user's policy or fallback philosophy.
- It can be tested in Helm with local fixtures.
- It belongs to operations, safety, memory, context, checkpointing, reporting,
  or integration documentation.

## Rule

Private workspaces should be proving grounds. Helm should be the portable layer.

Do not make Helm more personal to fit one workspace. Do not make a private
workspace less personal just to fit Helm.
