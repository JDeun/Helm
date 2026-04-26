# Helm Compared With Eval Tools

Helm is not an eval framework.

Eval tools measure model or prompt quality. Helm governs repeated agent operations around a real workspace.

## Eval tools answer

- Did this model answer correctly?
- Did this prompt improve a benchmark?
- Did a regression appear in a fixed task suite?

## Helm answers

- Was the command safe for the selected profile?
- What task and command history should future runs see?
- Was a checkpoint available before risky work?
- Did the task produce durable memory or review follow-up?
- Which local or API model fallback is currently healthy?

## How to combine them

Run evals through Helm when the eval itself mutates files, invokes services, or should leave an auditable operational record.
