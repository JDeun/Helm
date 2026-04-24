# Helm 0.6.0 Hardening — Handoff Document

**Date:** 2026-04-25
**Release:** v0.6.0
**Status:** Released (tag pushed, 173 tests passing)

---

## 잔여 과제 (MEDIUM priority)

### 1. `RiskCategory` Literal 타입 누락

**파일:** `scripts/command_guard.py:20-31`
**현상:** `_classify_argv()`에서 `"heredoc_input"`, `"base64_pipe"`, `"network_detected"`를 카테고리로 추가하지만, `RiskCategory` Literal 타입에 정의되지 않음.
**영향:** 런타임 동작에는 문제없으나 mypy/pyright 타입 검사에서 오류 발생.
**수정:** `RiskCategory` Literal에 세 값 추가.

```python
RiskCategory = Literal[
    ...,
    "heredoc_input",
    "base64_pipe",
    "network_detected",
]
```

**난이도:** 5분, 테스트 변경 불필요

---

### 2. 커스텀 Provider 레지스트리 미연결

**파일:** `scripts/model_provider_probe.py:122` (`probe_api_providers_from_env`)
**현상:** `_load_provider_registry()` 함수가 존재하고 정책 JSON 파싱도 동작하지만, `probe_api_providers_from_env()`는 하드코딩된 `_API_PROVIDER_ENV_REGISTRY`만 사용. 커스텀 정책 파일의 프로바이더가 실제 프로빙에 반영되지 않음.
**영향:** 사용자가 `model_provider_policy.json`에 커스텀 프로바이더를 추가해도 자동 감지되지 않음.
**수정 방향:**
  - `probe_api_providers_from_env()`에 `policy_path: Path | None = None` 매개변수 추가
  - 또는 모듈 초기화 시 `_load_provider_registry()`로 레지스트리 교체

**난이도:** 30분, 테스트 1-2개 추가 필요

---

### 3. AMD GPU (ROCm) 미지원

**파일:** `scripts/discovery.py:204-230` (`_detect_gpu`)
**현상:** 현재 NVIDIA (`nvidia-smi`)와 Apple Silicon만 GPU 감지. AMD GPU 사용 환경에서 GPU가 미감지됨.
**영향:** AMD GPU 사용자에게 GPU 정보가 표시되지 않음 (기능 누락, 보안 이슈 아님).
**수정 방향:**
  - `rocm-smi --showproductname --showmeminfo vram` 호출 추가
  - `_detect_gpu()` 내 NVIDIA 블록 이후 AMD 블록 추가
  - 멀티 GPU 환경도 고려 (현재 `splitlines()[0]`로 첫 번째만 보고)

**난이도:** 1시간, 테스트 2-3개 추가 필요

---

### 4. `init_db()` — `_connect()` 미사용

**파일:** `scripts/ops_db.py:85-97`
**현상:** `init_db()`가 `_connect()` 헬퍼 대신 직접 `sqlite3.connect()` 호출. `_DDL` 안의 PRAGMA 문으로 동일한 설정이 적용되긴 하지만 코드 일관성이 깨짐.
**영향:** 기능적 문제 없음. 코드 리뷰에서 반복 지적될 수 있음.
**수정:**

```python
def init_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = _connect(db_path)  # 변경
    try:
        conn.executescript(_DDL)
        ...
```

**난이도:** 5분, 기존 테스트로 검증 가능

---

### 5. `test_manual_remote_guard_decision_is_recorded` — 취약한 테스트 방식

**파일:** `tests/test_run_with_profile_guard.py:78-85`
**현상:** `inspect.getsource(cmd_run)` 문자열에서 "Guard evaluation" 위치가 "manual-remote" 위치보다 앞인지 검사. 코드 리팩토링 시 주석/변수명 변경만으로도 테스트가 깨질 수 있음.
**영향:** false negative/positive 가능성. 실제 실행 순서를 보장하지 못함.
**수정 방향:**
  - mock 기반 통합 테스트로 전환
  - `evaluate_command_guard`가 `manual-remote` 경로에서도 호출되는지 `mock.call_count`로 검증
  - 또는 guard decision이 task dict에 기록된 후 manual-remote가 반환하는지 검증

**난이도:** 30분, 기존 테스트 교체

---

## 추가 개선 제안 (LOW priority)

| 항목 | 파일 | 설명 |
|------|------|------|
| `/dev/tcp` 카테고리명 | command_guard.py:489 | `"network_detected"` → `"dev_tcp_bypass"` 전용 카테고리명 |
| state_io 바이너리 모드 | state_io.py:23 | 텍스트 `"a"` → 바이너리 `"ab"` 모드로 lock/write 일관성 |
| 동시 쓰기 스트레스 테스트 | test_state_io.py | 멀티스레드 동시 append 테스트 추가 |
| f-string SQL 주석 | commands/db.py:56 | 화이트리스트 안전성 주석 추가 |
| discovery 전용 테스트 | tests/ | GPU 테스트를 `test_discovery.py`로 분리 |

---

## 릴리즈 체크리스트

- [x] 173 tests passing
- [x] Version 0.6.0 in `setup.py` and `pyproject.toml`
- [x] CHANGELOG.md updated
- [x] README.md / README.ko.md updated
- [x] Tag v0.6.0 pushed
- [ ] GitHub Release created (manual: https://github.com/JDeun/Helm/releases/new?tag=v0.6.0)
- [ ] `gh` CLI 설치 후 향후 릴리즈 자동화 권장
