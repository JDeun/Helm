<p align="center">
  <img src="assets/helm-icon-v2.png" alt="Helm icon" width="108" />
</p>

<h1 align="center">Helm</h1>

<p align="center"><strong>반복되는 에이전트 작업을 더 안전하고, 추적 가능하고, 복구 가능하게.</strong></p>

<p align="center">Helm은 장기 실행 에이전트 workspace 주변에 profile, guardrail, checkpoint, audit trail, file-backed memory를 더하는 운영 레이어입니다.</p>

<p align="center"><strong>현재 릴리즈: v0.6.5</strong></p>

<p align="center">
  <a href="README.md">English README</a>
</p>

<p align="center">
  <img alt="License MIT" src="https://img.shields.io/badge/license-MIT-0f172a?style=flat-square">
  <img alt="Python 3.10+" src="https://img.shields.io/badge/python-3.10%2B-1d4ed8?style=flat-square">
  <img alt="Stability first" src="https://img.shields.io/badge/focus-stability--first-334155?style=flat-square">
  <img alt="Runtime agnostic" src="https://img.shields.io/badge/runtime-agnostic-475569?style=flat-square">
</p>

<p align="center">
  <a href="#quickstart">Quickstart</a> ·
  <a href="#왜-helm인가">왜 Helm인가</a> ·
  <a href="#helm이-더하는-것">Helm이 더하는 것</a> ·
  <a href="#워크플로우">워크플로우</a> ·
  <a href="#문서">문서</a>
</p>

## Quickstart

```bash
curl -fsSL https://raw.githubusercontent.com/JDeun/Helm/main/install.sh | bash
helm doctor --path ~/.helm/workspace
helm profile --path ~/.helm/workspace run inspect_local --task-name "first Helm inspection" -- git status --short
helm status --path ~/.helm/workspace --brief
helm dashboard --path ~/.helm/workspace
```

installer는 Helm을 설치하고 `~/.helm/workspace`를 만듭니다. 설치 후 `helm` 명령을 찾지 못하면 installer가 출력한 PATH 설정을 적용하세요.

다른 workspace 경로를 쓰려면:

```bash
curl -fsSL https://raw.githubusercontent.com/JDeun/Helm/main/install.sh \
  | bash -s -- --workspace ~/work/helm
```

## 왜 Helm인가

Helm은 또 하나의 agent runtime이 아닙니다. 이미 쓰고 있는 runtime 주변의 운영 레이어입니다.

OpenClaw/Hermes 스타일 workspace나 유사한 self-hosted agent service가 데모를 넘어 반복 운영 단계에 들어갔다면 Helm이 필요해집니다.

- 명시적 execution profile로 작업 범위 제한
- checkpoint 기반 복구 경로
- task log와 command log 기반 추적성
- chat history가 아니라 file state에서 이어지는 다음 실행
- skill contract와 local policy 기반 운영 규칙

에이전트가 일회성 데모만 수행한다면 Helm은 과할 수 있습니다.

## Helm이 더하는 것

핵심 개념:

- **Profile**: 명령 실행 전에 허용 범위를 정합니다. 예를 들어 조회 전용, workspace 수정, risky edit를 구분합니다.
- **Guardrail**: 실행 전에 명령 형태를 local policy와 비교해 위험하거나 profile을 벗어난 행동을 막습니다.
- **Checkpoint**: rollback이 필요할 수 있는 작업 전에 복구 지점을 눈에 보이게 남깁니다.
- **Audit trail**: 어떤 명령이 어떤 profile과 guard decision 아래 어떤 task로 실행됐는지 기록합니다.
- **File-backed memory**: 다음 실행이 chat history가 아니라 파일에 남은 durable state에서 이어지게 합니다.

| 반복 에이전트 운영 문제 | Helm이 더하는 것 |
| --- | --- |
| 에이전트가 이전 작업을 잊음 | notes, memory, tasks, commands, checkpoints 기반 context hydration |
| risky edit가 너무 빠르게 진행됨 | profile, command guard, checkpoint discipline |
| 나중에 실행 이유를 설명하기 어려움 | task ledger, command log, status, dashboard, report |
| skill 규칙이 프롬프트에만 남음 | `SKILL.md` guidance와 `contract.json` 실행 정책 |
| model fallback이 즉흥적으로 결정됨 | file-backed health check와 fallback selection |
| 운영 상태가 흩어짐 | workspace layout, adopted sources, SQLite query index |

Helm은 원칙적으로 runtime-agnostic이지만, state, memory, profiles, checkpoints, task history가 있는 persistent workspace를 1차 대상으로 설계되었습니다.

![Helm 설명 카툰](assets/helm-explainer-cartoon-ko.png)

## 워크플로우

workspace 점검.

```bash
helm doctor --path ~/.helm/workspace
helm status --path ~/.helm/workspace --brief
helm dashboard --path ~/.helm/workspace
```

명시 profile로 명령 실행.

```bash
helm profile --path ~/.helm/workspace run inspect_local \
  --task-name "inspect repository state" \
  -- git status --short
```

기존 시스템을 context source로 연결.

```bash
helm survey --path ~/.helm/workspace
helm onboard --path ~/.helm/workspace --use-detected --dry-run
helm onboard --path ~/.helm/workspace --use-detected
```

rollback 후보와 최근 운영 상태 확인.

```bash
helm checkpoint-recommend --path ~/.helm/workspace
helm checkpoint list --path ~/.helm/workspace
helm report --path ~/.helm/workspace --format markdown
```

model health 확인.

```bash
helm health --path ~/.helm/workspace state --json
helm health --path ~/.helm/workspace select --json
```

demo workspace 실행.

```bash
helm doctor --path examples/demo-workspace
helm dashboard --path examples/demo-workspace
```

## Workspace 모델

Helm은 전용 workspace에 두고, 기존 시스템은 먼저 read-only context source로 붙이는 것이 안전합니다.

- Helm state는 `.helm/` 아래에 둡니다
- profiles, notes, policies, skill rules는 명시 파일로 유지합니다
- OpenClaw, Hermes, notes vault는 overwrite하지 않고 adopt해서 연결합니다
- JSONL은 append-only 원본이고, SQLite는 query index입니다

## 문서

먼저 볼 문서:

- [`docs/first-run.md`](docs/first-run.md)
- [`docs/onboarding.md`](docs/onboarding.md)
- [`docs/demos.md`](docs/demos.md)
- [`docs/integrations/openclaw.md`](docs/integrations/openclaw.md)
- [`docs/integrations/existing-agent-workspace.md`](docs/integrations/existing-agent-workspace.md)

핵심 개념:

- [`docs/execution-profiles.md`](docs/execution-profiles.md)
- [`docs/memory-operations-policy.md`](docs/memory-operations-policy.md)
- [`docs/task-finalization.md`](docs/task-finalization.md)
- [`docs/adaptive-harness.md`](docs/adaptive-harness.md)
- [`docs/skill-quality-and-policy.md`](docs/skill-quality-and-policy.md)

포지셔닝:

- [`docs/comparisons/agent-frameworks.md`](docs/comparisons/agent-frameworks.md)
- [`docs/comparisons/observability-tools.md`](docs/comparisons/observability-tools.md)
- [`docs/comparisons/eval-tools.md`](docs/comparisons/eval-tools.md)

릴리즈:

- [`CHANGELOG.md`](CHANGELOG.md)
- [`docs/releases/0.6.5.md`](docs/releases/0.6.5.md)

## 현재 상태

Helm v0.6.5는 OpenClaw/Hermes 스타일 adoption, 장기 실행 workspace integration, command guard hardening, `status --brief`, `dashboard`, HTML report 기반 local operational visibility에 집중합니다.

Helm에는 private memory, personal agent overlay, credential, private task history가 포함되지 않습니다.

## 라이선스

MIT
