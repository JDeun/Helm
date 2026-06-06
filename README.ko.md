<p align="center">
  <img src="assets/helm-icon-v2.png" alt="Helm icon" width="120" />
</p>

<h1 align="center">Helm</h1>

<p align="center"><strong>장기 실행 AI 에이전트를 위한 로컬 운영 레이어.</strong></p>

<p align="center">
  코딩, 운영, 리서치, 자동화 — 같은 workspace에서 몇 시간씩 도는 어떤 agent든.<br/>
  명령 전 profile · 위험 작업 전 checkpoint · chat이 사라진 뒤에도 남는 durable task history.
</p>

<p align="center">
  <a href="https://pypi.org/project/helm-agent-ops/"><img alt="PyPI" src="https://img.shields.io/pypi/v/helm-agent-ops?style=flat-square&color=0f172a"></a>
  <a href="https://pypi.org/project/helm-agent-ops/"><img alt="Python" src="https://img.shields.io/pypi/pyversions/helm-agent-ops?style=flat-square&color=334155"></a>
  <a href="https://github.com/JDeun/Helm/actions/workflows/publish.yml"><img alt="Publish" src="https://img.shields.io/github/actions/workflow/status/JDeun/Helm/publish.yml?branch=main&label=publish&style=flat-square"></a>
  <a href="https://github.com/JDeun/Helm/actions/workflows/ci.yml"><img alt="CI" src="https://img.shields.io/github/actions/workflow/status/JDeun/Helm/ci.yml?branch=main&label=tests&style=flat-square"></a>
  <img alt="License" src="https://img.shields.io/badge/license-MIT-475569?style=flat-square">
  <a href="https://arxiv.org/abs/2605.12129"><img alt="arXiv" src="https://img.shields.io/badge/arXiv-2605.12129-b31b1b?style=flat-square"></a>
</p>

<p align="center">
  <a href="https://v0-helm-agent-ops.vercel.app/">Landing</a> ·
  <a href="#quickstart">Quickstart</a> ·
  <a href="#helm이-하는-일">Helm이 하는 일</a> ·
  <a href="#워크플로우">워크플로우</a> ·
  <a href="#문서">문서</a> ·
  <a href="README.md">English</a>
</p>

---

## Quickstart

```bash
pip install helm-agent-ops
helm init --path ~/.helm/workspace
export HELM_WORKSPACE=~/.helm/workspace
```

선언된 risk profile 아래에서 첫 inspection 실행:

```bash
helm profile run inspect_local --task-name "first look" -- git status --short
helm status --brief
helm dashboard
```

첫 명령은 guard를 거친 실행 기록을 남깁니다. 두 번째 명령은 방금 일어난 일을 평이한 문장으로 보여줍니다. 세 번째 명령은 workspace 상태를 한 페이지로 보여줍니다.

> **PyPI 없이?** bootstrap installer 사용: `curl -fsSL https://raw.githubusercontent.com/JDeun/Helm/main/install.sh | bash`

---

## 왜 Helm인가

장기 실행 AI 에이전트는 drift 합니다. 이전 결정을 잊고, 막을 새도 없이 위험한 행동을 실행하고, 일주일 뒤에는 아무도 audit할 수 없는 chat log만 남깁니다 — agent가 코드 편집을 하든, ops를 돌리든, 노트를 정리하든, 사이트를 탐색하든, tool 체인을 호출하든.

Helm은 기존 agent runtime 주변을 감싸는 얇은 file-backed 운영 레이어입니다. agent를 **대체하지 않습니다**. agent의 작업이 **경계 안에서, 복구 가능하게, 검토 가능하게** 일어나도록 만듭니다.

모델은 행동을 제안하고, harness는 검증, 권한 판단, 실행, 기록, 관찰 반환을 담당합니다. 안전과 완료 주장은 prompt 조언이나 compact된 chat transcript가 아니라 실행 증거에서 나와야 합니다.

| Helm 없이 | Helm으로 |
| --- | --- |
| agent가 결정하는 즉시 위험한 명령 실행 | 선언된 execution profile + guard 검사를 거친 실행 |
| 다단계 / 다중 파일 변경 후 무엇이 바뀌었는지 추측 | 작업 전 checkpoint 생성, 명확한 rollback 지점 |
| "어제 agent가 뭐 했지?" → chat 스크롤 | 로컬 task ledger, command log, dashboard, markdown report |
| context가 chat window에만 존재 | file-backed memory + ranked retrieval로 다음 세션 재구성 |
| skill 규칙이 prompt 안에만 있음 | `SKILL.md` + `contract.json`이 실행 시점에 정책 적용 |

agent가 일회성 데모만 돌린다면 Helm 필요 없음. 같은 workspace에 몇 시간씩 돌린다면 — 코딩이든 ops든 지식 캡처든 — 필요함.

---

## Helm이 하는 일

<table>
<tr>
<td width="33%" valign="top">

### 🛡️ 실행 전 guard

- **Execution profile**로 blast radius 선언 (`inspect_local`, `workspace_edit`, `risky_edit`, `service_ops`, `remote_handoff`)
- **Command guard**가 destructive하거나 profile 벗어난 동작을 사전 차단
- **Tool-group grant**가 각 profile이 노출하는 capability 제한

</td>
<td width="33%" valign="top">

### 💾 사후 복구

- 광범위한 수정 전 **Checkpoint** 생성으로 명확한 rollback target
- **Task ledger** & **command log**가 chat과 무관하게 durable history 유지
- **Browser & profile gate**로 runaway 작업 중지 + cleanup 증거 요구

</td>
<td width="33%" valign="top">

### 🧭 시간에 걸친 운영

- **File-backed memory** + ranked retrieval (`helm context --explain-ranking`)
- **Skill lifecycle**이 skill 규칙의 promote / decay 관리
- **Adaptive harness**가 failure signature → policy transition 연결

</td>
</tr>
</table>

<p align="center">
  <img src="assets/helm-architecture-diagram.png" alt="Helm architecture" width="720" />
</p>

---

## 3분 데모

![Helm three-minute demo terminal capture](https://raw.githubusercontent.com/JDeun/Helm/main/assets/helm-three-minute-demo.gif)

```bash
helm profile run inspect_local --task-name "inspect current repository" -- git status --short
helm checkpoint create --label before-risky-work --include $HELM_WORKSPACE
helm report --format markdown
helm dashboard
```

각 명령은 디스크에 구조화된 기록을 남깁니다: task ledger, command log, checkpoint 기록, dashboard 요약. agent가 아무것도 기억할 필요 없습니다.

---

## 워크플로우

<details>
<summary><b>Workspace 점검</b></summary>

```bash
helm doctor
helm status --brief
helm dashboard
```

</details>

<details>
<summary><b>선언된 profile 아래 명령 실행</b></summary>

```bash
helm profile run inspect_local --task-name "inspect repository state" -- git status --short
helm profile run workspace_edit --task-name "tighten typing in api/" -- ruff check api/
```

</details>

<details>
<summary><b>기존 시스템을 context source로 채택</b></summary>

```bash
helm survey
helm onboard --use-detected --dry-run
helm onboard --use-detected
```

</details>

<details>
<summary><b>Rollback 및 최근 상태 확인</b></summary>

```bash
helm checkpoint-recommend
helm checkpoint list
helm task list --status running
helm task doctor
helm report --format markdown
```

</details>

<details>
<summary><b>Inspectable ranking으로 durable context 조회</b></summary>

```bash
helm context --mode decisions --explain-ranking --json
helm context --mode timeline --since 2026-05-01
helm context --mode entity --entity project_helm
helm context --mode reflect-candidates
```

</details>

<details>
<summary><b>Privacy boundary preflight</b></summary>

```bash
helm privacy scan --text "Contact alice@example.com" --json
helm privacy tokenize --scope task-123 --text "Contact alice@example.com"
```

</details>

<details>
<summary><b>오래된 skill claim 검토</b></summary>

```bash
helm skill-lifecycle negative-claims --persist
helm skill-lifecycle revalidation-due
helm skill-lifecycle revalidate-claim \
  --skill old-skill \
  --claim-id sha256:abc123 \
  --status resolved \
  --note "command now exists"
```

</details>

<details>
<summary><b>Model health probe</b></summary>

```bash
helm health state --json
helm health select --json
```

</details>

> 모든 명령은 `--path /custom/workspace` 도 받습니다 — `$HELM_WORKSPACE` 안 쓸 때. `examples/demo-workspace`에 데모 workspace가 있어 안전하게 지정 가능.

---

## v0.10.0 — harness-engineering 레이어

*현재 릴리즈: v0.10.0 — 2026-05-22 릴리즈.* 모든 신규 기능은 기본 shadow mode — 결정은 기록되지만 enforce 안 됨. opt-in 시점은 누적 데이터 기반으로.

- **Failure signature 분류** — 모든 failure event를 `{component, tool, profile, error_class, target, fingerprint}`로 정규화, 같은 실패가 run을 가로질러 식별 가능.
- **Profile → tool-group grant** — 각 profile이 노출하는 도구 제한; runner가 매 ledger row에 grant 기록.
- **반복 실패 policy transition** — same-fingerprint / patch-failed / same-skill / credential-invalid 패턴이 자동으로 다음 action 선택 (stop / decompose / repair / re-auth).
- **Patch-first edit 정책 + validation gate** — 파일 수정은 patch 우선; 확장자별 validation 명령이 write 후 실행.
- **Task-state 컨트롤 컨테이너** — Forge "Control Flow Is Not Memory" 원칙: required_steps, completed_steps, blockers, approval, recovered_messages가 transcript와 분리된 구조화 상태로 존재.
- **Trace recorder → trace replay → skill candidate** — 각 run이 JSON trace 생성; 반복 성공 패턴은 skill draft 후보, 반복 실패는 repair 후보.
- **Profile pause / resume** — profile 단위 secret-token-gated hard stop, `OPENCLAW_PAUSE_GATE` flag.
- **Browser work verifier** — 사전 결정 (`allow_single_session`, `block_mutation`, `require_user_login`, `require_confirmation`, `pause_profile`, `require_cleanup_evidence`) + runner enforcement gate.
- **Model repair + synthetic respond hook** — 소형 모델 fallback proxy 위한 library 진입점; `HELM_MODEL_REPAIR` / `HELM_SYNTHETIC_RESPOND`.
- **Shadow-mode reporter** — `helm shadow-report --since 14d --with-recommendations`로 14일 신호 집계 + feature별 `ready_to_enforce / needs_more_data / caution / no_signal` 추천.

자세한 내용은 [v0.10.0 릴리즈 노트](docs/releases/0.10.0.md)와 13개 [`docs/harness-engineering/`](docs/harness-engineering/) 문서 참조.

---

## Workspace 모델

Helm은 dedicated workspace에서 동작하며, 기존 시스템을 read-only context source로 채택합니다.

- Helm 상태는 workspace 안 `.helm/`에 위치.
- Profile, note, 정책, skill 규칙은 explicit 파일로 유지.
- OpenClaw, Hermes, notes vault는 덮어쓰지 않고 **채택**.
- JSONL이 append-only source of truth, SQLite는 query index.

---

## 비교

| 카테고리 | 어떤 도구가 더 적합한가 | Helm이 더하는 것 |
| --- | --- | --- |
| **Agent framework** (LangChain, AutoGen 등) | prompt, planner, tool loop, agent graph | profile, guard 결정, checkpoint, task ledger |
| **Observability** (Langfuse, Helicone 등) | hosted trace, service metric | 실행 전 정책 + 로컬 복구 상태 |
| **Evaluation** (DeepEval, Phoenix 등) | 모델 출력 스코어링 | 반복 human-agent 작업 운영 history |
| **Shell wrapper** (명령 보조 도구) | 명령 편의성 | workspace 상태, memory 캡처, report, 복구 규율 |

더 깊은 비교는 [`docs/comparisons/`](docs/comparisons/) 참조.

---

## 문서

<table>
<tr>
<th align="left">시작하기</th>
<th align="left">핵심 개념</th>
<th align="left">심화</th>
</tr>
<tr>
<td valign="top">

- [3분 데모](docs/three-minute-demo.md)
- [첫 실행](docs/first-run.md)
- [온보딩](docs/onboarding.md)
- [데모 모음](docs/demos.md)
- [OpenClaw 통합](docs/integrations/openclaw.md)
- [기존 agent workspace](docs/integrations/existing-agent-workspace.md)

</td>
<td valign="top">

- [Execution profile](docs/execution-profiles.md)
- [Privacy boundary](docs/privacy-boundary.md)
- [Task state](docs/task-state.md)
- [Task finalization](docs/task-finalization.md)
- [Memory operations 정책](docs/memory-operations-policy.md)
- [Ops memory query](docs/ops-memory-query.md)
- [Adaptive harness](docs/adaptive-harness.md)
- [Skill quality & policy](docs/skill-quality-and-policy.md)

</td>
<td valign="top">

- [Harness engineering — 인덱스](docs/harness-engineering/)
- [Control Flow Is Not Memory](docs/harness-engineering/05-control-flow-is-not-memory.md)
- [Helm vs Forge](docs/harness-engineering/06-helm-vs-forge.md)
- [HITL 결정 패턴](docs/hitl-decision-patterns.md)
- [Evidence label convention](docs/evidence-label-convention.md)
- [Helm dogfooding 참고](docs/helm-dogfooding-reference.md)
- [연구 배경](docs/research-background.md)

</td>
</tr>
</table>

---

## 연구 배경

Helm의 설계 방향은 [Harness Design Determines Operational Stability in Small Language Models](https://arxiv.org/abs/2605.12129) 의 결과와 일치합니다. 이 논문은 planning, verification, recovery harness가 소형 언어 모델의 운영 안정성에 어떻게 영향을 주는지 실험적으로 연구합니다. Helm의 adaptive harness 방향은 [It's Not the Capability: Harness Sensitivity Is Non-Monotone Across LLM Agent Tiers](https://arxiv.org/abs/2605.26731) 의 결과와도 연결됩니다. 이 후속 논문은 harness strictness를 모든 모델에 일괄 적용하기보다 모델 타입과 실패 패턴에 맞춰 선택해야 함을 보여줍니다.

Helm 인용:

```bibtex
@software{helm_2026,
  title  = {Helm: A stability-first operations layer for long-lived agent workspaces},
  author = {Cho, Yong Eun},
  year   = {2026},
  url    = {https://github.com/JDeun/Helm},
  version = {0.10.0}
}
```

machine-readable 형식은 [`CITATION.cff`](CITATION.cff) 참조.

---

## Contributing

Issue와 PR 환영합니다.

- PR 전에 [`CONTRIBUTING.md`](CONTRIBUTING.md) 읽기.
- 테스트 실행: `python -m pytest -q` (현재 1,372 tests).
- Release 검사: `python scripts/release_version_check.py --version <next>`.
- 보안 보고: [`SECURITY.md`](SECURITY.md) 참조.

---

## 릴리즈 이력

- **최신**: [v0.10.0](docs/releases/0.10.0.md) — harness-engineering 레이어 (2026-05-22)
- **이전**: [v0.9.6](docs/releases/0.9.6.md), [v0.9.5](docs/releases/0.9.5.md), [v0.9.0](docs/releases/0.9.0.md)
- **전체 changelog**: [`CHANGELOG.md`](CHANGELOG.md) · [이전 릴리즈 노트](docs/releases/)

---

## Helm에 포함되지 **않는** 것

Helm은 public 운영 레이어만 ship합니다. 다음은 포함되지 **않습니다**:

- Private memory 내용
- Personal agent overlay
- Credential, secret
- 특정 workspace의 raw task 내용
- Live connector token

저장소는 fork, clone, 검토 모두 안전합니다.

---

## 라이선스

[MIT](LICENSE) © Yong Eun Cho ([JDeun](https://github.com/JDeun))
