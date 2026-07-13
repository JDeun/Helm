# OMFM free-router integration

Helm treats `oh-my-free-models` (`omfm`) as one optional local
OpenAI-compatible provider below its existing model recovery policy. The
existing Ollama, OpenAI, and Gemini order is unchanged; `omfm/balanced` is the
last candidate and is selected only from fresh healthy state or live provider
discovery.

Install and configure the upstream service separately:

```bash
npm install -g oh-my-free-models
omfm model
omfm start --daemon
python3 scripts/omfm_status.py --json
python3 scripts/model_health_probe.py probe --model omfm/balanced --json
```

Provider credentials stay in `~/.oh-my-free-models/.env`; Helm uses only the
synthetic local token `omfm-local`. The normalized status never reads or emits
credential values. The official installation guide is
<https://github.com/hakilee/oh-my-free-models/blob/main/docs/INSTALLATION.md>.

The context guard requires known context metadata for every selected model in
the requested group:

- `fast`: 32,768 tokens minimum.
- `balanced`: 131,072 tokens minimum.
- `capable`: 1,000,000 tokens minimum.

Unknown request or model sizes fail closed. Call `resolve_runtime_model(...)`
with a concrete `context_tokens` value; `omfm/balanced` is skipped above its
window minus a 16,384-token output/safety reserve. OMFM does not compact or
truncate long conversations.

Runtime agent registration opts in with `model_policy.runtime_recovery=true`,
`allow_free_router=true`, and a concrete `context_tokens` value. The selected
model and selection evidence are recorded in the agent registry. OMFM is
accepted only for `inspect_local` or `research`; mutable profiles fall through
to the established stable chain. The same gate is available through
`model_health_probe.py select --profile ... --context-tokens ...
--allow-free-router`. Helm retains individual provider discovery/probe paths.

High-impact or mutable-surface execution must map `model_tier=free_router` to
strict harness enforcement. A stopped daemon, invalid group, context overflow,
or unavailable endpoint must fall through to the next stable candidate.
