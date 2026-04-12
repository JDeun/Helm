# Helm

**오래 운영되는 개인 에이전트를 위한 안정성 우선 operations layer**

[English README](README.md)

Helm은 에이전트 런타임 위에 실행 프로파일, 컨텍스트 하이드레이션, 감사 추적, 롤백 가이드, 승인 기반 self-improvement를 얹는 운영 계층입니다.

즉, 에이전트가 추론과 툴 호출은 할 수 있지만 장기 운영 관점에서 아직 불안정한 부분을 보강하는 데 초점을 둡니다.

## Helm이 필요한 이유

많은 에이전트 스택은 툴 호출 자체는 잘하지만, 실제로 계속 굴리기 시작하면 아래 같은 부분이 약합니다.

- 실행 전에 위험도를 구분하는 규율
- 단순 채팅 히스토리 대신 운영 컨텍스트를 다시 읽는 구조
- 부모 task와 저수준 command를 함께 추적하는 감사 기록
- 위험한 변경 이후 되돌릴 수 있는 명시적 rollback 경로
- 반복 성공한 흐름을 skill로 바꾸되 uncontrolled self-modification은 막는 구조

Helm은 이 부분을 해결하려는 프로젝트입니다.

## Helm이 제공하는 것

- **Execution profiles**
  - `inspect_local`, `workspace_edit`, `risky_edit`, `service_ops`, `remote_handoff` 같은 실행 프로파일로 작업 성격을 먼저 선언합니다.
- **Context hydration**
  - 라우팅 전에 memory, ontology, task history, command failure, checkpoint를 다시 읽습니다.
- **Audit trails**
  - task와 command 실행 흔적을 함께 남깁니다.
- **Rollback guidance**
  - risky edit와 checkpoint를 연결해 복구 후보를 제시합니다.
- **Gated self-improvement**
  - 성공한 작업을 draft skill로 만들고, 평가 후 명시 승인으로만 승격합니다.
- **Operations reporting**
  - 최근 task 상태, 실패 command, handoff task, checkpoint, draft assessment를 요약합니다.

## 저장소 구조

- [`scripts/`](scripts)
  - 운영 코어 유틸리티
- [`docs/`](docs)
  - 실행 모델과 워크플로우 문서
- [`references/`](references)
  - 프로파일, 정책, 템플릿 기본 파일

## 설치 방법

현재 Helm은 패키징된 CLI보다는 파일 기반 코어로 배포됩니다.

### 1. 저장소 clone

```bash
git clone https://github.com/JDeun/Helm.git
cd Helm
```

### 2. Python 3.10+ 확인

```bash
python3 --version
chmod +x scripts/*.py scripts/*.sh 2>/dev/null || true
```

### 3. 기본 도구부터 실행

```bash
python3 scripts/run_with_profile.py list
python3 scripts/ops_memory_query.py --describe-modes
python3 scripts/ops_daily_report.py
```

### 4. 참조 파일을 자신의 환경에 맞게 수정

최소한 아래 파일은 자신의 환경에 맞게 확인/수정하는 것을 권장합니다.

- `references/execution_profiles.json`
- `references/skill_profile_policies.json`
- `references/skill-capture-template.md`

### 참고

tracked task를 실제로 돌리기 시작하면 workspace 내부에 `.openclaw/` 상태 디렉토리가 필요합니다. 이 디렉토리는 필요 시 runner가 자동으로 만듭니다.

## 핵심 스크립트

- [`run_with_profile.py`](scripts/run_with_profile.py)
- [`ops_memory_query.py`](scripts/ops_memory_query.py)
- [`workspace_checkpoint.py`](scripts/workspace_checkpoint.py)
- [`task_ledger_report.py`](scripts/task_ledger_report.py)
- [`command_log_report.py`](scripts/command_log_report.py)
- [`ops_daily_report.py`](scripts/ops_daily_report.py)
- [`skill_capture.py`](scripts/skill_capture.py)

## 현재 상태

이 저장소는 더 큰 private 운영 스택에서 재사용 가능한 Helm 코어를 1차로 추출한 공개 버전입니다.

현재 포함된 것:

- 안정성과 관측성을 위한 코어 스크립트
- execution-profile 모델
- context-hydration 가이드
- rollback / report 유틸리티
- gated skill-improvement 흐름

의도적으로 포함하지 않은 것:

- 개인 memory / ontology 데이터
- 개인 agent overlay
- task history, checkpoint, credential
- 모든 런타임과 워크플로우에 대한 패키징

## 기본 사용 흐름

1. `ops_memory_query.py`로 관련 컨텍스트를 읽습니다.
2. 작업 성격에 맞는 execution profile을 고릅니다.
3. `run_with_profile.py`로 작업을 실행합니다.
4. ledger / command report로 결과를 확인합니다.
5. risky edit는 checkpoint와 rollback 경로를 함께 봅니다.
6. 반복 성공 흐름은 `skill_capture.py`로 draft skill로 바꿉니다.

## 예시 명령

실행 프로파일 목록 확인:

```bash
python3 scripts/run_with_profile.py list
```

라우터용 프리셋 확인:

```bash
python3 scripts/ops_memory_query.py --describe-modes
python3 scripts/ops_memory_query.py --mode failures --limit 5
```

checkpoint가 붙는 risky task 실행:

```bash
python3 scripts/run_with_profile.py run risky_edit \
  --task-name "router refactor" \
  -- python3 -c 'print("hello")'
```

완료된 task에서 skill draft 생성 및 평가:

```bash
python3 scripts/skill_capture.py draft-from-task \
  --task-id <task-id> \
  --name example-skill \
  --description "Example reusable workflow"

python3 scripts/skill_capture.py assess-draft --name example-skill --json
```

## 포지셔닝

Helm은 다음이 아닙니다.

- 새로운 foundation model 프로젝트
- 범용 chat UI
- 완전 자율형 agent platform
- 모든 runtime을 대체하는 시스템

Helm은 다음입니다.

- operations layer
- governance / observability layer
- 로컬 및 개인 에이전트를 위한 안정성 중심 orchestration layer

## 라이선스

MIT
