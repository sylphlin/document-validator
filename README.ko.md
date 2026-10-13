# document-validator

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Google GenAI SDK](https://img.shields.io/badge/Google%20GenAI%20SDK-v2.4+-4285F4.svg)](https://github.com/googleapis/python-genai)
[![Google ADK](https://img.shields.io/badge/Google%20ADK-v2.0+-orange.svg)](https://google.github.io/adk-docs/)
[![Gemini 3.8 Flash](https://img.shields.io/badge/Gemini%203.8-Flash-yellow.svg)](https://ai.google.dev/)

[English (en)](README.md) | [繁體中文 (zh-TW)](README.zh-TW.md) | [简体中文 (zh-CN)](README.zh-CN.md) | [日本語 (ja)](README.ja.md) | [한국어 (ko)](README.ko.md)

## 개요

![Document Processing Framework](images/workflow.png)

`document-validator`는 문서 적합성 검증 에이전트(agent)입니다. **기준 문서**(요구사항을 정의하는 문서 — 법규, 입찰 규격서, 심사위원회 의견, 내부 체크리스트 등)와 **검토 대상 문서**(검토받는 문서 — 신청서, 제안서, 사업계획서 등)가 주어지면, 기준 문서의 모든 요구사항이 검토 대상 문서에서 충족되는지 체계적으로 확인합니다.

이 에이전트는 단순 키워드 매칭으로 동작하지 않습니다. 기준 문서로부터 구조화된 체크리스트를 구성하고, 검토 대상 문서를 기준으로 각 요구사항을 평가하여, 무엇이 충족되었는지, 무엇이 부분적인지, 무엇이 누락되었는지를 명확히 보여주는 심사 보고서를 생성합니다 — 검토자가 모든 페이지를 직접 읽지 않고도 즉시 조치를 취할 수 있도록 합니다.

---

## 설계 철학과 로직

```mermaid
flowchart TD
    A([시작]) --> B

    B["Phase 0 — 접수\n0.0 직접 업로드된 파일 백업\n0.1 Drive 링크를 통한 문서 접수\n0.2 문서 목록 작성\n0.3 비정형 문서 확인\n0.4 PDF 문서에서 텍스트 추출"]
    B --> C

    C["Phase 1 — 기준 체크리스트 추출\n1.1 기준 문서 분석\n1.2 기준 체크리스트 작성\n1.3 체크포인트 — 체크리스트 확인"]
    C --> D{사용자가\n체크리스트를\n확인？}
    D -- 수정 요청 --> C
    D -- 확인됨 --> E

    E["Phase 2 — 문서 대조 및 채점\n2.1 충족도 점수 척도\n2.2 확인 방법별 채점 로직\n2.3 조건부 요구사항 처리\n2.4 출처 추적\n2.5 모호한 사례 처리"]
    E --> F

    F["Phase 3 — 보고서 생성\n요약 · 상세 결과\n격차 상세 · 수동 검토 대기열\n판정 규칙：승인 · 보정 요청 · 반송 · 수동 검토로 상향"]
    F --> G([종료])
```

### Phase 0 — 접수
에이전트는 제공된 모든 문서를 목록화하고, 보고서 전체에서 추적할 수 있도록 짧은 ID를 부여합니다(예: 기준 문서는 `C-1`, `C-2`; 검토 대상 문서는 `P-1`, `P-2`, `P-3`). 문서가 비정형이거나 이미지 위주인 경우, 에이전트는 이를 알리고 최선을 다해 추출을 진행합니다.

채팅에 직접 붙여넣기에는 너무 큰 문서는 대신 Google Drive 링크로 제공할 수 있습니다(예: "이것이 검토 대상 문서입니다: https://drive.google.com/file/d/.../view"). 에이전트는 [`skills/document-validator/scripts/fetch_drive_file.py`](skills/document-validator/scripts/fetch_drive_file.py)를 통해 Google Drive API를 직접 호출합니다 — 채팅 클라이언트의 connector가 필요 없으므로, 이 스킬이 다른 환경(예: Google Agent Engine)에 배포된 에이전트로 실행될 때도 동작합니다. 인증에는 Application Default Credentials를 사용하며, Google 네이티브 문서(Docs/Sheets/Slides)는 먼저 PDF로 내보내 모든 문서가 동일한 페이지 인용 규칙을 따르도록 하고, 일반 텍스트/Markdown 파일은 내용을 바로 읽어들이며, 폴더 링크는 그 안의 각 파일을 목록의 개별 항목으로 펼칩니다. 대상 파일은 해당 자격 증명이 가리키는 신원(예: 배포된 service account)에 공유되어 있어야 합니다 — "링크를 아는 누구나 접근 가능"한 공개 공유는 필요하지 않으며, 민감한 행정 문서에는 보통 사용하지 않아야 합니다.

PDF 입력의 경우, 에이전트는 [`skills/document-validator/scripts/extract_pdf_text.py`](skills/document-validator/scripts/extract_pdf_text.py)를 사용해 각 페이지를 Markdown으로 변환합니다 — 인용을 위해 페이지 번호를 보존하고, 표를 뒤섞인 텍스트가 아닌 실제 Markdown 표로 표현하며, 스캔/이미지 위주로 보이는 페이지에 표시를 남깁니다. 감지된 이미지는 기록되지만 내용이 추출되지는 않으므로, 검토자는 원본 PDF에서 도표를 확인해야 함을 알 수 있습니다. 처리 시간이 너무 오래 걸리는 페이지(큰 삽입 이미지)는 시간 초과되어 수동 검토 표시가 남습니다. 벡터 그래픽이 밀집된 페이지(CAD/3D 도면)는 다르게 처리됩니다 — 페이지의 텍스트(제목, 캡션, 주석)는 평소대로 추출되며, 도면 자체의 시각적 내용만 건너뜁니다. 어차피 사람이 원본 페이지를 직접 확인해야 하기 때문입니다. 큰 PDF는 한 번에 전부 읽어들이는 대신 페이지 범위 단위로 청크 처리되므로, 100페이지가 넘는 기준 문서도 한 번에 모두 로드할 필요가 없습니다.

### Phase 1 — 기준 체크리스트 추출
에이전트는 기준 문서를 분석하여 모든 요구사항을 추출하고, 다음과 같이 분류합니다:

| 유형 | 설명 |
|------|------|
| **결격 요건 (Disqualifying)** | 하나라도 충족되지 않으면 즉시 반송 |
| **필수 요건 (Mandatory)** | 무조건 존재하고 충족되어야 함 |
| **조건부 요건 (Conditional)** | 특정 발생 조건이 적용될 때만 필수 |
| **권고 요건 (Advisory)** | 권고되지만 필수는 아님; 채점 없이 기록만 함 |

기준 문서가 다층 구조(장 → 조 → 항)를 가질 경우, 각 요구사항의 ID는 점(dot) 표기로 그 계층을 반영합니다 — `REQ-1`, `REQ-1.2`, `REQ-1.2.3` — ID만 보아도 기준 문서 내 위치를 알 수 있어 별도로 찾아볼 필요가 없습니다.

다음 단계로 넘어가기 전에 체크포인트가 제시됩니다 — 사용자는 체크리스트를 확인하거나 누락된 요구사항을 추가해 달라고 요청할 수 있습니다. 사용자가 변경을 요청하면, 에이전트는 먼저 무엇이 바뀌었는지 설명하고, 업데이트된 전체 체크리스트를 다시 제시하여 확인을 받은 후에야 다음으로 진행합니다.

### Phase 2 — 문서 대조 및 채점
각 요구사항은 적절한 확인 방법(필드 존재 여부, 키워드 일치, 수치/형식 적합성, 논리적 일관성)을 사용하여 전체 검토 대상 문서를 기준으로 채점됩니다. 모호한 사례의 경우, 에이전트는 관련 문구를 인용하고, 자신의 해석을 명시하며, 필요한 경우 해당 항목을 수동 검토 대상으로 표시합니다.

충족도 점수:

| 점수 | 라벨 | 의미 |
|------|------|------|
| 90–100% | ✅ 충족 | 명확히 다루어짐; 내용이 완전함 |
| 70–89% | ⚠️ 부분 충족 | 언급은 있으나 불완전하거나 모호함 |
| 40–69% | ❌ 미약 | 간접적으로만 관련되거나 명백히 부족함 |
| 0–39% | 🚫 누락 | 해당 내용을 찾을 수 없음 |
| — | 🔍 판정 불가 | 사용 가능한 유일한 증거가 실제로 읽히지 않은 내용 — 이미지, 스캔/공백 페이지, 도면 자체의 시각적 내용(옆에 있는 텍스트가 아님), 또는 시간 초과된 페이지 — 인 경우 |

페이지가 이미지, 스캔본, 또는 읽을 수 없는 상태라는 것은 그 내용이 충족되었다는 증거가 아닙니다 — 이는 "증거의 부재"이며, "증거의 존재"가 아닙니다. 에이전트는 아무도 실제로 읽지 않은 내용을 근거로 "충족"(또는 다른 어떤 채점 라벨)으로 판정하는 일이 절대 없습니다; 이런 경우는 항상 "판정 불가"로 평가하고 수동 검토 대기열로 보내며, 검토자가 무엇을 확인해야 하는지 명확히 밝힙니다(예: "9페이지 — 도면, 내용 미추출; 필요한 부지 배치도가 포함되어 있는지 확인 필요"). 이렇게 특정 페이지를 인용하려면 문서상의 근거가 필요합니다 — 캡션이나 상호 참조(분석되지 않은 도면과 함께 추출되는 경우가 많음)가 그 페이지를 해당 요구사항과 연결해 줄 때만 인용할 수 있습니다; 그런 근거가 없다면 찾지 못한 요구사항은 그냥 "누락"으로 남겨두어야 하며, 관련 없는 도면 페이지를 "혹시 관련 있을지도" 라는 이유로 끌어와서는 안 됩니다. 점수와 "수동 검토 표시"는 절대 서로 모순되지 않습니다 — 표시가 되어 있다면 점수는 반드시 "판정 불가"이며, 자신감 있어 보이는 퍼센트 값이 될 수 없습니다.

### Phase 3 — 보고서 생성
에이전트는 현재 대화에서 사용되는 언어로 구조화된 보고서를 생성합니다(분석 대상 문서 자체의 언어와 반드시 같지는 않음). 내용은 다음을 포함합니다:

- **요약** — 전체 충족률과 판정 권고
- **상세 결과표** — 요구사항별 한 행, 점수·출처·비고 포함
- **격차 상세** — 90% 미만의 모든 항목과 모든 "판정 불가" 항목을 포함하며, 근본 원인별로 묶어 보정 제안을 첨부
- **수동 검토 대기열** — 결론을 내리기 전에 사람의 판단이 필요한 항목, 아무도 실제로 읽지 않은 내용만으로 뒷받침되는 항목도 포함

판정 옵션: *승인* / *보정 요청* / *반송* / *수동 검토로 상향*

---

## 사용 방법

### Antigravity 사용 방법 및 시나리오

Antigravity 플러그인 또는 스킬로 설치된 경우, 최소한의 `/` 슬래시 명령어와 `@` 파일 멘션, 또는 자연어 프롬프트로 `document-validator`를 호출할 수 있습니다:

| 시나리오 | 최소 명령어 (`/` + `@`) | 자연어 프롬프트 |
|---|---|---|
| **로컬 PDF 검증** | `/document-validator Criteria: @guidelines.pdf, Pending: @application.pdf` | `"@guidelines.pdf의 기준에 따라 @application.pdf가 요건을 충족하는지 검증해 주세요."` |
| **Google Drive 검증** | `/document-validator Criteria: https://drive.google.com/file/d/.../view, Pending: https://drive.google.com/file/d/.../view` | `"Drive에 있는 이 제안서가 우리 입찰 규격서의 모든 필수 요건을 충족하는지 확인해 주세요."` |

**1단계** — 무엇을 검증하려는지 설명하고 문서를 제공합니다 — PDF는 Google Drive 링크(또는 직접 업로드 / 로컬 `@` 파일 참조)로, 일반 텍스트/Markdown은 메시지에 직접 붙여넣습니다. 기준이 반드시 법규일 필요는 없습니다; 몇 가지 예시 시나리오:

### 시나리오: 보조금/지원금 신청 심사

> 이 신청 문서 묶음을 검증해 주세요:
>
> 기준 문서:
> - subsidy-program-guidelines.pdf — https://drive.google.com/file/d/1AbCdEfGhIjKlMnOpQrStUvWxYz/view
> - application-format-requirements.md
>
> 검토 대상 문서:
> - application-form-main.pdf — https://drive.google.com/file/d/1QwErTyUiOpAsDfGhJkLzXcVbNm/view
> - attachment-1-financial-statement.pdf — https://drive.google.com/file/d/1ZxCvBnMqWeRtYuIoPaSdFgHjKl/view
> - attachment-2-project-proposal.pdf — https://drive.google.com/file/d/1MnBvCxZaQwErTyUiOpLkJhGfDs/view

### 시나리오: 입찰 문서 심사

> 이 업체의 제안서가 우리 입찰 규격서의 모든 필수 요건을 충족하는지 확인해 주세요:
>
> 기준 문서:
> - tender-notice.pdf — https://drive.google.com/file/d/1TenderSpecAbCdEfGhIjKlMnOp/view
>
> 검토 대상 문서:
> - vendor-proposal.pdf — https://drive.google.com/file/d/1VendorBidAbCdEfGhIjKlMnOp/view

### 시나리오: 심사위원회 의견 반영 확인

> 심사위원회의 의견과 업체가 명시한 약속 사항을 기준으로 이 사업계획서를 확인해 주세요 — 약속한 모든 내용이 계획서에 명확히 반영되었는지 확인해 주세요.
>
> https://drive.google.com/file/d/1AbCdEfGhIjKlMnOpQrStUvWxYz/view

모든 시나리오에서 에이전트는 먼저 문서를 목록화하고, 채점을 시작하기 전에 기준 체크리스트를 사용자와 함께 확인합니다(위의 Phase 1 체크포인트 참고).

**2단계** — 검증 보고서를 받습니다. 위 보조금/지원금 시나리오의 예:

> **문서 검증 보고서**
>
> 검토 대상 문서: application-form-main.pdf (+첨부 2건)
> 기준: subsidy-program-guidelines.pdf (+보조 문서 1건)
> 심사일: 2026-06-18
>
> **요약**
>
> 전체 충족률: 72%
> - ✅ 충족: 11건
> - ⚠️ 부분 충족: 3건
> - ❌ 미약: 1건
> - 🚫 누락: 3건
>
> 판정: 보정 요청
>
> **상세 결과 — 필수 요건**
>
> | ID | 요구사항 | 결과 | 점수 | 출처 | 비고 |
> |----|------|------|------|------|------|
> | REQ-1 | 신청인 신원 확인됨 | ✅ | 98% | [P-1] §1.1 | |
> | REQ-2 | 사업 목적 명시됨 | ✅ | 95% | [P-1] §2.3 | |
> | REQ-3.1.1 | 예산 명세 제공 | ⚠️ | 74% | [P-3] p.4 | 지출 항목 분류 누락 |
> | REQ-4.1 | 재무제표 첨부됨 | ✅ | 100% | [P-2] | |
> | REQ-4.2 | 확인서 첨부 | 🚫 | 0% | — | 어떤 검토 대상 문서에서도 발견되지 않음 |
> | REQ-4.3 | 동의서 첨부 | 🚫 | 0% | — | 어떤 검토 대상 문서에서도 발견되지 않음 |
>
> **상세 결과 — 조건부 요건**
>
> | ID | 요구사항 | 발생 조건 적용? | 결과 | 점수 | 출처 | 비고 |
> |----|------|----------------|------|------|------|------|
> | REQ-5 | 환경영향평가서 | 예 | ⚠️ | 78% | [P-3] §5 | 요약만 있음; 전체 평가서 미첨부 |
> | REQ-6 | 공동신청인 위임장 | 아니오 | ➖ 해당 없음 | — | — | |
>
> **격차 상세**
>
> REQ-4.2, REQ-4.3: 확인서와 동의서가 어떤 검토 대상 문서에서도 발견되지 않음
> - 누락 내용: 두 문서 모두 검토 대상 문서에 존재하지 않음
> - 기준 근거: [C-1] 제4조 제2, 3항
> - 결함 유형: 보정 가능
> - 보정 제안: 두 문서를 첨부하여 재제출
>
> REQ-3.1.1: 사업계획서에 필요한 예산 명세가 포함되어 있지 않음
> - 누락 내용: 지출 항목 분류가 기재되지 않음
> - 증거 출처: [P-3] p.4 (부분적)
> - 기준 근거: [C-2] 부록 1
> - 결함 유형: 보정 가능
> - 보정 제안: [C-2] 부록 1에 따라 항목별 예산표 추가

---

## 프로젝트 구조

```
document-validator/
├── plugin.json                     # Agent Plugins 1.0 매니페스트 (skills 및 rules 등록)
├── AGENTS.md                       # 저장소 수준의 에이전트 규칙 및 엔지니어링 표준
├── rules/
│   └── AGENTS.md                   # plugin.json을 통해 배포되는 클라이언트 실행 불변 규칙(Invariants)
├── skills/                         # Agent Plugins 1.0을 준수하는 단일 진실 공급원(SSOT)
│   └── document-validator/
│       ├── SKILL.md                # 각 단계, 요구사항 유형, 보고서 형식, 실행 가이드라인
│       └── scripts/
│           ├── extract_pdf_text.py       # PDF → Markdown 추출 (병렬 워커 + CAD 고속 경로)
│           ├── fetch_drive_file.py       # Google Drive API 가져오기 (service account / ADC)
│           ├── gcs_state.py              # 직접 업로드된 파일 및 체크포인트 상태를 GCS에 백업/복원
│           ├── job_store.py              # 비동기 작업의 GCS 상태 기록 및 원자적 1회 전달(atomic deliver-once)
│           └── run_async_validation.py   # 분리형 백그라운드 기준 추출 및 체크리스트 생성 (기본값 gemini-3.8-flash)
├── gemini-enterprise/              # 격리된 Gemini Enterprise 클라우드 에이전트 패키지
│   ├── agent/                      # ADK wrapper — skill/SKILL.md를 시스템 프롬프트로 로드
│   │   ├── __init__.py             # ADK 로더용 root_agent 내보내기
│   │   ├── agent.py                # LlmAgent 구성 (기본값 gemini-3.8-flash)
│   │   ├── drive_tool.py           # fetch_drive_file_oauth — 사용자 개인 OAuth로 Drive 접근(배포 환경 전용)
│   │   ├── recall.py               # before_agent_callback을 통해 완료된 백그라운드 작업 결과 전달 또는 중단된 작업 재개
│   │   ├── skill_loader.py         # SKILL.md frontmatter 파서
│   │   └── tools.py                # start_job, check_job, read_asset 및 start_async_validation
│   ├── skill/                      # skills/document-validator/의 다운스트림 미러 (check_core_sync.py로 검증)
│   ├── tests/
│   │   └── eval/                   # `agents-cli eval`용 행동 수준 평가
│   │       ├── datasets/basic-dataset.json
│   │       └── eval_config.yaml
│   ├── agents-cli-manifest.yaml    # `agents-cli eval` 및 `agents-cli publish`용 매니페스트
│   ├── deployment_metadata.json    # deploy.sh가 자동 업데이트하며 `agents-cli publish gemini-enterprise`에서 사용
│   ├── deploy.sh                   # 루트 ./deploy.sh로 전달하는 편의 스크립트
│   ├── GEMINI.md                   # Gemini Enterprise 패키지 개발 및 평가 가이드
│   ├── requirements.txt            # 배포된 컨테이너에 설치되는 런타임 의존성
│   ├── pyproject.toml              # Gemini Enterprise 패키지 설정
│   ├── .env.example                # 클라우드 환경 변수 템플릿
│   └── .gitignore                  # 패키지 수준 Git 무시 규칙
├── tests/                          # 단위 테스트 및 SSOT 동기화 검증
│   ├── check_core_sync.py          # skills/document-validator/와 gemini-enterprise/skill/의 일치 여부 검증
│   └── test_*.py                   # Wrapper 및 스크립트 단위 테스트
├── setup.sh                        # GCP 인프라, GCS 버킷, IAM 권한 자동 프로비저닝 스크립트
├── deploy.sh                       # Vertex AI Agent Engine 배포 및 Gemini Enterprise 등록
├── .env.example                    # setup.sh / deploy.sh 실행 전 .env로 복사하여 사용
└── pyproject.toml                  # 로컬 개발 의존성 및 pytest 설정
```

이 저장소는 **[Agent Plugins 1.0 Specification](https://agent-plugins.org/specification)**을 준수하는 이중 타겟 패키지로 구성되어 있습니다:
1. **Google Antigravity 플러그인 & 스킬 (`skills/document-validator/`)**: 로컬 CLI / IDE 에이전트 실행을 위한 단일 진실 공급원(SSOT) 역할을 합니다.
2. **Gemini Enterprise 클라우드 에이전트 (`gemini-enterprise/`)**: ADK wrapper([`gemini-enterprise/agent/`](gemini-enterprise/agent/))와 동기화된 스킬 미러([`gemini-enterprise/skill/`](gemini-enterprise/skill/))를 분리하여 패키징합니다. `agent/` wrapper는 [`SKILL.md`](skills/document-validator/SKILL.md)를 에이전트의 시스템 프롬프트로 변환하고 `scripts/`를 `start_job`/`check_job` 도구로 노출합니다. `agent/` 안의 어떤 것도 문서 검증에 특화된 것이 아닙니다 — 에이전트의 동작을 바꾸려면 `skills/document-validator/SKILL.md`를 편집해야 하며, wrapper 코드를 건드릴 필요는 없습니다. 유일한 예외는 [`drive_tool.py`](gemini-enterprise/agent/drive_tool.py)입니다: 이는 OAuth 동의 흐름을 구동하기 위해 ADK의 `ToolContext`가 필요하며, `GOOGLE_OAUTH_CLIENT_ID`가 설정된 경우에만 등록됩니다; 그렇지 않으면 에이전트는 `fetch_drive_file.py`로 대체됩니다.

## 평가 및 테스트

### 단위 테스트 및 SSOT 동기화 확인

```bash
python3 tests/check_core_sync.py
pytest -v
```

### 행동 수준 평가 (`agents-cli eval`)

`tests/test_*.py`는 wrapper와 스크립트의 메커니즘(`start_job` 실행, 경로 탈출 거부, PDF 추출, 비동기 채점)을 검사합니다. 준수/격차 분석 로직 자체가 "정확한지" — 올바른 판정, 올바르게 식별된 격차 — 는 `gemini-enterprise/` 내의 [`google-agents-cli`](https://pypi.org/project/google-agents-cli/) 평가 도구로 검증됩니다:

```bash
cd gemini-enterprise
agents-cli eval generate   # tests/eval/datasets/basic-dataset.json에 대해 실제 에이전트를 실행
agents-cli eval grade      # tests/eval/eval_config.yaml의 지표에 따라 이 트레이스를 채점
```

`gcloud auth application-default login`과 `GOOGLE_CLOUD_PROJECT`(`--project`로 재정의 가능)가 필요합니다 — 실제 Gemini 모델(`gemini-3.8-flash`)을 호출합니다. 미리 마련된 세 가지 케이스는 승인, 필수 요건 누락, 결격 조건 케이스를 각각 다룹니다; 스킬이 발전함에 따라 `gemini-enterprise/tests/eval/datasets/` 아래에 더 추가하세요. 데이터셋 스키마, 지표 작성법, 실패 케이스 기반 반복 작업 흐름에 대해서는 `agents-cli eval --help`와 `google-agents-cli-eval` 스킬을 참고하세요.

---

## 설치 및 배포

실행 환경에 따라 아래 두 가지 배포 방법 중 하나를 선택하세요:

| 배포 방법 | 대상 환경 | 설정 도구 | 주요 인터페이스 |
| :--- | :--- | :--- | :--- |
| **방법 1: Google Antigravity & Agent Plugins** | 로컬 Antigravity IDE 및 Agent Skill | `pip` / `uv` + `./setup.sh` | Antigravity IDE 채팅 |
| **방법 2: Gemini Enterprise** | Cloud Vertex AI Agent Runtime | `./setup.sh` + `./deploy.sh` | Gemini Enterprise Web UI, Agent Engine |

### 방법 1: Google Antigravity 플러그인 & 스킬 설치

**Agent Plugins 1.0** 플러그인으로 설치하면 에이전트가 `skills/document-validator/`와 `rules/AGENTS.md`를 모두 자동으로 로드합니다:

```bash
# 옵션 A: 전역 플러그인 설치 (모든 워크스페이스에서 사용 가능)
mkdir -p ~/.gemini/config/plugins
git clone https://github.com/sylphlin/document-validator.git ~/.gemini/config/plugins/document-validator

# 옵션 B: 워크스페이스 플러그인 설치 (현재 프로젝트에만 적용)
mkdir -p .agents/plugins
git clone https://github.com/sylphlin/document-validator.git .agents/plugins/document-validator

# 옵션 C: 기존 단일 스킬 디렉터리 설치 (~/.gemini/config/skills/ 호환)
mkdir -p ~/.gemini/config/skills
ln -s ~/.gemini/config/plugins/document-validator/skills/document-validator ~/.gemini/config/skills/document-validator
```

로컬 Python 의존성을 설치하고 Google Cloud 환경을 초기화합니다(Google Drive 가져오기 또는 비동기 검증 사용 시):

```bash
cd ~/.gemini/config/plugins/document-validator
pip install -e ".[dev]"
./setup.sh --project <your-project-id>
```

### 방법 2: Gemini Enterprise 클라우드 배포

#### 1. 클라우드 인프라 자동 프로비저닝 (`setup.sh`)

`setup.sh`를 실행하면 필요한 GCP API 활성화, 세션 버킷(`gs://document-validator-sessions-${PROJECT_ID}`, 15일 수명 주기 자동 정리 포함) 및 스테이징 버킷(`gs://document-validator-staging-${PROJECT_ID}`) 생성 및 설정, Service Account IAM 권한 부여, `.env` 설정 기록이 자동으로 수행됩니다:

```bash
./setup.sh -p <your-project-id> -r us-central1
# 변경 없이 실행할 작업 미리보기:
./setup.sh --dry-run -p <your-project-id>
```

필요한 경우 `.env`를 편집합니다 — 기준 또는 검토 대상 PDF가 매우 큰 경우 `AGENT_MEMORY`(기본값 `32Gi`)를 조정하세요. 메모리가 부족하면 컨테이너가 에러 로그 없이 조용히 OOM-killed됩니다.

#### 2. Vertex AI Agent Engine 배포 및 Gemini Enterprise 등록 (`deploy.sh`)

```bash
# Vertex AI Agent Engine에 배포
./deploy.sh

# 배포 후 agents-cli publish를 통해 Gemini Enterprise에 자동 등록
./deploy.sh --project <your-project-id> --region us-central1 --ge <your-ge-app-id>

# 또는 위치 인자로 project/region 재정의:
./deploy.sh <project-id> <region>
```

`deploy.sh`는 `gemini-enterprise/agent`와 `gemini-enterprise/skill`을 함께 스테이징하고, `adk deploy agent_engine`으로 배포하며, `AGENT_ENGINE_ID`를 `.env`에 저장하고, `gemini-enterprise/deployment_metadata.json`을 작성합니다. 첫 실행 후 다시 배포하면 기존 Agent Engine 인스턴스가 제자리에서 업데이트됩니다.

#### 3. Google Drive 접근 권한 및 세션 영속성 참고 사항

- **Service Account Drive 접근 (기본값):** 기본 Google Drive 가져오기 경로(`fetch_drive_file.py`)는 배포된 service account가 검토자가 링크할 파일에 접근 권한을 가지고 있어야 합니다. 대상 파일/폴더를 service account의 이메일 주소로 공유하세요; "링크를 아는 누구나 접근 가능"한 공개 공유는 필요하지 않습니다.
- **사용자 개인 OAuth Drive 접근 (선택 사항):** `.env`에서 `GOOGLE_OAUTH_CLIENT_ID`와 `GOOGLE_OAUTH_CLIENT_SECRET`(`.env.example` 참고)을 설정하면 [`gemini-enterprise/agent/drive_tool.py`](gemini-enterprise/agent/drive_tool.py)가 활성화되어 Gemini Enterprise에 로그인한 사용자로서 Drive에 접근합니다. Google Cloud Console → APIs & Services → Credentials에서 OAuth 클라이언트(유형은 "웹 애플리케이션", Drive API 활성화, 동의 화면 범위는 `drive.readonly`)를 생성하세요. 둘 다 비워두면 service account 경로를 사용합니다.
- **임시 컨테이너 상태 백업 및 백그라운드 작업 복원 (`gcs_state.py`, `job_store.py` & `recall.py`):** Agent Engine 컨테이너 인스턴스는 일시적이며 같은 대화의 turn 사이에 교체될 수 있습니다. `gcs_state.py`는 직접 업로드된 파일과 체크포인트 상태를 `gs://document-validator-sessions-{GOOGLE_CLOUD_PROJECT}`(`./setup.sh`가 자동 생성)에 백업 및 복원하며, `job_store.py`와 `recall.py`는 분리형 백그라운드 기준 추출 작업(`run_async_validation.py`)을 추적하여 사용자의 다음 turn에서 완료된 체크리스트를 자동으로 전달하거나 중단된 작업을 재개합니다.
