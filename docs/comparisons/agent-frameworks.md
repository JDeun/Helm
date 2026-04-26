# Helm Compared With Agent Frameworks

Helm is not an agent framework.

Agent frameworks usually help you define tools, agents, planners, graphs, and execution loops. Helm assumes you may already have one of those and focuses on what happens when you keep running it over time.

## Helm focuses on

- execution profiles
- command guard decisions
- task and command ledgers
- checkpoint and rollback visibility
- context hydration from files and prior operations
- memory finalization decisions
- model health and fallback state

## Helm does not try to own

- prompting strategy
- tool-calling protocol
- agent planning loops
- model routing inside a framework
- chat UI

## How to combine them

Use the framework to run the agent. Use Helm around commands, state, checkpoints, and reports so repeated work is inspectable and recoverable.
