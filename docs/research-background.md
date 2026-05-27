# Research Background

Helm is a practical operations layer for long-lived agent workspaces. Its design is grounded in a simple observation: repeated agent work does not fail only because a model is too weak. It often fails because the surrounding harness does not make planning, verification, recovery, and auditability explicit.

That framing is aligned with two arXiv papers:

> Yong Eun Cho, ["Harness Design Determines Operational Stability in Small Language Models"](https://arxiv.org/abs/2605.12129), arXiv:2605.12129, 2026.

> Yong Eun Cho, ["It's Not the Capability: Harness Sensitivity Is Non-Monotone Across LLM Agent Tiers"](https://arxiv.org/abs/2605.26731), arXiv:2605.26731, 2026.

The first paper studies how different harness conditions affect the operational performance of small language models. It compares raw model-only prompting, minimal wrapper tags, and a structured `plan -> execute -> verify -> recover` pipeline across multiple 2-3B parameter models and task types.

The follow-up paper widens that question across model tiers and shows that harness sensitivity is non-monotone: stricter harnesses can hurt a frontier chat model, help a frontier reasoning model, and interact differently with constrained models depending on instruction-following quality and failure mode.

Key takeaways for Helm:

- **Harness design is operational infrastructure.** Agent performance depends not only on the model call, but also on the surrounding workflow that shapes planning, execution, validation, and recovery.
- **Minimal scaffolding is not automatically safer.** A thin wrapper can fail to improve stability, and in some cases can perform worse than model-only prompting.
- **Verification and recovery need first-class representation.** Reliable agent work needs explicit checks, catch points, and recovery paths rather than best-effort prompt instructions.
- **Small models benefit from structured operating loops.** Smaller models can become more usable when the runtime supplies a disciplined task loop and state boundary.
- **Harness policy should be adaptive, not one-size-fits-all.** The right amount of structure depends on the model's failure pattern: format violations, wrong-file edits, missing changes, and unresolved verifier failures call for different runtime responses.

Helm applies those lessons at the workspace level. Instead of trying to be another agent runtime, it provides the operational substrate around existing runtimes:

- execution profiles before commands
- guard decisions before risky actions
- checkpoints before broad edits
- task and command logs after work finishes
- context hydration from durable local state
- memory capture and validation after task completion
- approval-gated workflow improvement

The paper should be read as research background, not as a claim that Helm is the experimental system studied in the paper. Helm is a related open-source implementation direction: a local-first tool for making agent work more bounded, recoverable, and inspectable.

## Citation

```bibtex
@misc{cho2026harnessdesign,
  title        = {Harness Design Determines Operational Stability in Small Language Models},
  author       = {Yong Eun Cho},
  year         = {2026},
  eprint       = {2605.12129},
  archivePrefix= {arXiv},
  primaryClass = {cs.SE},
  doi          = {10.48550/arXiv.2605.12129},
  url          = {https://arxiv.org/abs/2605.12129}
}

@misc{cho2026harnesssensitivity,
  title        = {It's Not the Capability: Harness Sensitivity Is Non-Monotone Across LLM Agent Tiers},
  author       = {Yong Eun Cho},
  year         = {2026},
  eprint       = {2605.26731},
  archivePrefix= {arXiv},
  primaryClass = {cs.AI},
  doi          = {10.48550/arXiv.2605.26731},
  url          = {https://arxiv.org/abs/2605.26731}
}
```
