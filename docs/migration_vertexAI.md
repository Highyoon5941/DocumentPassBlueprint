# migration_vertexAI.md

> 이 개발 PC(사내망 밖, GCP 접근 없음) → **사내망 Vertex AI(Gemini)** 전환 가이드
> 관련 문서: [`Architecture.md`](Architecture.md) · [`Runbook.md`](Runbook.md) · [`Valeo_SVMtrial_SETUP.md`](Valeo_SVMtrial_SETUP.md)
> 작성 2026-10-05

---

## 0. 요약: 코드는 바꾸지 않는다

전환은 **`.env` 파일 한 개를 고치는 것**이다. 파이썬 코드는 한 줄도 바꾸지 않는다.

```dotenv
# 바꿀 것
SVMTRIAL_BACKEND=vertex                  # offline → vertex
GOOGLE_CLOUD_PROJECT=<사내 GCP 프로젝트 ID>
GOOGLE_CLOUD_LOCATION=<global 또는 asia-northeast3>
GEMINI_MODEL_FAST=<Flash 계열 모델 ID>
GEMINI_MODEL_PRO=<Pro 계열 모델 ID>
```

그 다음 **인증 1회**와 **캐시 정리**만 하면 된다.

이것이 가능한 이유는 Gemini 호출이 `gemini_client.py` 한 곳을 지나고(R2),
`vertex_backend.py` 와 `offline_backend.py` 가 **같은 pydantic 스키마**를 돌려주기 때문이다.
(구조: [`Architecture.md` §6](Architecture.md))

> **중요**: `google-genai` 를 import 하는 모듈은 `src/svmtrial/vertex_backend.py` 하나뿐이다.
> 확인: `grep -rl "from google import genai" src/` → `vertex_backend.py` 만 나와야 한다.

---

## 1. 🔴 사내망에서 **내가 직접 받아와야 / 입력해야** 하는 것

> **이 절이 이 문서의 핵심이다.** 아래 값들은 코드로 알아낼 수 없고, 사내 IT·상사·GCP 콘솔에서 받아와야 한다.
> 전부 모으기 전에는 `SVMTRIAL_BACKEND=vertex` 로 바꿔도 동작하지 않는다.

### 1.1 반드시 필요 — 이게 없으면 한 줄도 돌지 않는다

| # | 항목 | 어디서 받는가 | 어디에 넣는가 | 확인 방법 |
|---|---|---|---|---|
| **M1** | **GCP 프로젝트 ID** (예: `valeo-kr-mfg-ai-prod`) | 사내 IT 또는 GCP 콘솔 상단 프로젝트 선택기. **프로젝트 "이름"이 아니라 "ID"** 다 | `.env` → `GOOGLE_CLOUD_PROJECT` | `gcloud projects list` |
| **M2** | **리전(location)**: `global` 또는 `asia-northeast3`(서울) 등 | 사내 IT. **데이터 처리 위치 정책** 질문이다. 최신 모델은 `global` 에만 있을 수 있다 | `.env` → `GOOGLE_CLOUD_LOCATION` | §1.4 의 질문지 참고 |
| **M3** | **사용 가능한 Gemini 모델 ID 2개** (Flash 계열 1개, Pro 계열 1개) | `python scripts/check_gemini.py --list-only` 로 **목록을 뽑아 그중에서 고른다.** 권한이 없어 목록이 안 나오면 IT 에서 직접 받는다 | `.env` → `GEMINI_MODEL_FAST`, `GEMINI_MODEL_PRO` | `python scripts/check_gemini.py` |
| **M4** | **인증**: 방식 A(ADC) 또는 방식 B(서비스계정 JSON 키) 중 IT 가 허용하는 것 | 사내 IT 정책. 회사에서 키 생성이 막혀 있을 수 있다 | A: 명령 실행 / B: `.env` → `GOOGLE_APPLICATION_CREDENTIALS` | §3 |
| **M5** | **IAM 역할 `roles/aiplatform.user`** (Vertex AI 사용자) 를 내 계정 또는 서비스계정에 부여 | 사내 IT (GCP 권한 관리자) | 부여받기만 하면 됨 | `403 PERMISSION_DENIED` 가 안 나면 OK |
| **M6** | **`aiplatform.googleapis.com` API 사용 설정** | 사내 IT. 보통 이미 켜져 있다 | — | 위와 같음 |

### 1.2 조건부 필요 — 사내망 환경에 따라

| # | 항목 | 언제 필요한가 | 어디에 넣는가 |
|---|---|---|---|
| **M7** | **프록시 주소** (예: `http://proxy.company:8080`) | 사내망이 프록시를 통해서만 외부에 나갈 때 | `.env` → `HTTPS_PROXY` (`HTTP_PROXY` 도 같이) |
| **M8** | **사내 CA 인증서 파일** (예: `C:\certs\corp-ca.pem`) | SSL 검사(SSL inspection)를 하는 사내망에서 | `.env` → `SSL_CERT_FILE` + pip/conda 설정 (§6) |
| **M9** | **서비스계정 JSON 키 파일** | 인증 방식 B 를 쓸 때만 | 별도 경로에 두고 `.env` → `GOOGLE_APPLICATION_CREDENTIALS`. **zip 에 넣지 않는다** |
| **M10** | **사내 pip 미러 주소** 또는 **오프라인 wheel** | 사내망에서 PyPI 가 막혀 있을 때 | `build_release.py --with-wheels` 로 zip 에 포함 (§6) |

### 1.3 정책·운영 확인 — 실데이터 투입 **전에** 답을 받아야 하는 것

| # | 질문 | 왜 중요한가 | 답을 못 받으면 |
|---|---|---|---|
| **M11** | **고객사 문서를 Vertex AI 로 보내도 되는가?** (고객사 NDA 포함) | 이 시스템은 문서 페이지 이미지를 Gemini 로 전송한다. 가장 중요한 질문이다 | **금지되면 OCR 을 로컬로 바꿔야 한다(v2 과제).** 전환 자체가 불가 |
| **M12** | **Vertex 할당량** (분당 요청 수, 분당 토큰) 과 **예산 한도** | `gemini.max_workers` 와 실행 시간을 정한다. 초과하면 `429` | `max_workers: 1~2` 로 보수적으로 시작 |
| **M13** | **토큰 단가** (입력/출력, 1M 토큰당) | `--dry-run` 비용 추정에 쓴다 | 비용 추정을 생략한다 (단가는 하드코딩하지 않는다) |
| **M14** | **평가 시트의 실제 열 이름과 평가자 명단** | `config.yaml` 의 `labels.columns` 매핑 | 시트를 열어 직접 확인 후 §4.2 처럼 수정 |
| **M15** | **문서 유형 × 고객사별 문서 수 (특히 Fail 수)** | 데이터 양 가드 판단. 소수 클래스가 적으면 결과가 "탐색적" 표기만 된다 | 일단 돌려보고 리포트의 경고를 확인 |
| **M16** | **로그 보관 정책** | `logs/<run_id>/gemini_calls.jsonl` 에 모델·토큰·호출 수가 남는다 (문서 내용은 남지 않는다) | 그대로 두되 사내 정책 확인 |

### 1.4 IT 에 보낼 질문지 (복사해서 쓰세요)

```
[Vertex AI (Gemini) 사용 요청 — 문서 템플릿 분석 도구]

1. GCP 프로젝트 ID 를 알려주세요. (프로젝트 이름이 아니라 ID)

2. Vertex AI 리전을 어디로 써야 합니까?
   - global 과 asia-northeast3(서울) 중 사내 데이터 처리 위치 정책상 허용되는 것
   - 최신 Gemini 모델이 global 에만 있는 경우가 있어 확인이 필요합니다.

3. 제 계정(또는 전용 서비스계정)에 IAM 역할 roles/aiplatform.user 를 부여해 주세요.
   그리고 aiplatform.googleapis.com API 사용 설정이 되어 있는지 확인 부탁드립니다.

4. 인증 방식은 어느 쪽을 써야 합니까?
   A) 개인 계정 ADC  — 각 PC 에서 `gcloud auth application-default login` 실행
   B) 서비스계정 JSON 키 — 키 파일을 전달받아 사용
   (사내 정책상 키 생성이 막혀 있다면 A 로 진행하겠습니다.)

5. 사용 가능한 Gemini 모델 ID 를 알려주세요.
   (제가 `client.models.list()` 로 조회할 권한이 있으면 직접 확인하겠습니다.)
   - 대량 처리용 Flash 계열 1개, 추론용 Pro 계열 1개가 필요합니다.

6. 사내망 프록시 주소와 사내 CA 인증서 파일이 필요합니까?
   pip/conda 설치와 Vertex AI HTTPS 호출 모두에 영향이 있습니다.

7. Vertex AI 할당량(분당 요청 수/토큰)과 예산 한도를 알려주세요.
   동시 호출 수를 그에 맞춰 설정하겠습니다.

8. [⚠ 가장 중요] 고객사 품질 문서(대책서 등)의 페이지 이미지를 사내 GCP 프로젝트의
   Vertex AI 로 전송해도 됩니까? 고객사 NDA 상 제약이 있는지 확인 부탁드립니다.
   - 전송 대상: 문서 페이지를 PNG 로 변환한 이미지, 그리고 그 구조 요약 텍스트
   - 전송 대상 아님: 평가자 이름·평가 결과 시트 (로컬에서만 처리)
   - 사내 GCP 프로젝트 외부(타사 API, 웹서비스)로는 어떤 데이터도 보내지 않습니다.

9. 토큰 단가(1M 토큰당 입력/출력)를 알려주시면 사전 비용 추정에 쓰겠습니다.
```

---

## 2. offline 백엔드가 무엇을 대체하고 있었는가

전환 후 각 단계가 **어떻게 달라지는지** 알고 있어야 결과를 비교할 수 있다.

| prompt_id | offline(개발 PC)이 하던 일 | 실데이터 가능? | vertex 전환 후 |
|---|---|---|---|
| `doc_format.v1` | 이미지 가로/세로 비율 다수결 | ✅ **유효** (픽셀에서 직접 계산) | Gemini 가 레이아웃까지 보고 판정. 대개 결과 동일 |
| `ocr_page.v1` | `make_dummy_data.py` 가 심은 fixture(`<doc_id>/p<page>`) 조회 | ❌ **불가** | **여기가 가장 크게 달라진다.** 실제 비전 OCR 로 제목 계층·표 열 이름·그림 종류를 읽는다 |
| `section_taxonomy.v1` | 8D seed + 제목 토큰 매칭 | ⚠ 한국어 동의어 일반화 약함 | Gemini PRO 가 "근본원인"/"Root cause"/"원인분석"을 하나로 묶는다 |
| `section_map.v1` | 토큰 Jaccard 최대 매칭 | ⚠ 동일 | 동의어 사전으로 1차 매칭, 실패분만 Gemini FAST |
| `concept_discovery.v1` | Pass/Fail 간 **줄 단위 문서빈도 차이**가 큰 줄을 가설로 | ⚠ 표현이 다양하면 약함 | Gemini PRO 가 의미 수준에서 가설을 만든다. **후보의 질이 크게 올라간다** |
| `concept_merge.v1` | 정규화 문구 기준 중복 제거 | ✅ 동작 | 의미 기준 병합 |
| `concept_scoring.v1` | 질문의 인용 문구를 개요 텍스트에서 부분일치 | ⚠ 표현 변형에 약함 | "5Why" 라는 말이 없어도 Why1~Why5 단계 표를 보면 `yes`. **재현율이 올라간다** |
| `template_compose.v1` | 골격 + 등급 개념으로 문구 기계 조립 | ✅ 동작(문구가 기계적) | 작성 가이드 문장이 자연스러워진다 |

### 2.1 ❌ `ocr_page.v1` — 전환이 필수인 이유

offline 스텁은 이미지를 **읽지 않는다.** `work/offline_fixtures/pages.json` 에서 `<doc_id>/p<page>` 키로 조회할 뿐이다.
실데이터에는 그 fixture 가 존재하지 않으므로:

- 모든 페이지가 `legibility = 0.0`, `blocks = []` 로 돌아온다
- 제목·표·그림이 하나도 안 잡히고, 모든 개념이 `no` 로 채점된다
- `diagnose` 는 이를 감지해 **"판정 불가"** 로 막고 이유를 출력한다 (조용히 틀린 답을 주지 않는다)
- `all` 파이프라인은 리포트의 `데이터 품질` 절에 판독 불량으로 기록한다

즉 **실데이터를 offline 로 돌리면 "아무 요소도 없는 문서들"로 분석된다.** 반드시 전환해야 한다.

---

## 3. 전환 절차

### 3.1 1단계 — 의존성 설치 (사내 PC)

이 개발 PC 에서 만든 환경과 **같은 버전**을 쓴다. `requirements.txt` 의 99개 의존성 전체가
Windows(win_amd64, cp311) 바이너리 wheel 로 해결됨을 확인했으므로 컴파일러가 필요 없다.

```bash
# Ubuntu / WSL
conda env create -f environment.yml          # 환경 이름 Valeo_SVM_Trial
conda activate Valeo_SVM_Trial
pip install -e .
python scripts/smoke_test.py                 # ALL SMOKE TESTS PASSED 확인
```

Windows 는 `windows\setup_windows.bat` 이 위 과정을 수행한다 ([`Runbook.md` §8](Runbook.md)).
사내망에서 pip 가 막혀 있으면 §6 의 오프라인 설치를 쓴다.

### 3.2 2단계 — `gcloud` 설치 (인증 방식 A 일 때만)

- Ubuntu: https://cloud.google.com/sdk/docs/install 의 apt 절차
- Windows: 같은 페이지의 설치 프로그램

방식 B(서비스계정 키)를 쓰면 `gcloud` 는 필요 없다.

### 3.3 3단계 — 인증

**방식 A — ADC (권장, 개인 계정)**

```bash
gcloud auth application-default login
gcloud auth application-default set-quota-project <M1 프로젝트 ID>
```

- 브라우저가 열리고 회사 계정으로 로그인한다.
- 두 번째 명령을 빠뜨리면 할당량 프로젝트가 없다는 경고나 오류가 날 수 있다.
- 계정에 `roles/aiplatform.user`(M5)가 있어야 한다.

**방식 B — 서비스계정 JSON 키**

```dotenv
# .env
GOOGLE_APPLICATION_CREDENTIALS=C:\keys\svmtrial-sa.json
```

- 키 파일은 **배포 zip 에 넣지 않는다.** `build_release.py` 가 `*-sa.json` 패턴을 감지하면 빌드를 실패시킨다.
- `.gitignore` 에도 들어 있다.

### 3.4 4단계 — `.env` 작성

```dotenv
# ── 백엔드 ──
SVMTRIAL_BACKEND=vertex

# ── Vertex AI ──
GOOGLE_CLOUD_PROJECT=valeo-kr-example-prod       # M1
GOOGLE_CLOUD_LOCATION=global                      # M2 (또는 asia-northeast3)
# GOOGLE_APPLICATION_CREDENTIALS=C:\keys\svmtrial-sa.json   # M9, 방식 B 일 때만

# ── 모델 ID (M3) — 5단계에서 채운다 ──
GEMINI_MODEL_FAST=
GEMINI_MODEL_PRO=

# ── 사내 프록시 (M7, M8) — 필요할 때만 ──
# HTTPS_PROXY=http://proxy.company:8080
# HTTP_PROXY=http://proxy.company:8080
# SSL_CERT_FILE=C:\certs\corp-ca.pem
```

### 3.5 5단계 — 모델 ID 확인 후 기입 (M3)

```bash
python scripts/check_gemini.py --list-only
```

사용 가능한 Gemini 모델 ID 목록이 나온다. 그중에서 고른다.

| `.env` 키 | 고르는 기준 | 쓰이는 곳 |
|---|---|---|
| `GEMINI_MODEL_FAST` | Flash 계열. **대량 호출**(페이지 수 × 문서 수)이므로 속도와 단가가 중요 | S2 ocr, S4c 채점, S4a 제목 매핑, S1 형식 판정 |
| `GEMINI_MODEL_PRO` | Pro 계열. 호출 수는 적고 **추론 품질**이 중요 | S4a 분류체계, S4b 가설 생성, S6 템플릿 작성 |

> 모델 ID 를 하드코딩하지 않는 이유: 모델 수명이 짧다. 2026-10 기준 Vertex 에는 Gemini 3.x Pro/Flash 가 GA 이고
> 2.5 계열은 2026-10-16 이후 종료될 수 있다. 사내 프로젝트에서 실제로 쓸 수 있는 ID 는 반드시 위 명령으로 확인한다.

**권한 때문에 목록 조회가 실패하면** IT 에서 받은 ID 를 직접 넣고 테스트 호출만 진행한다
(`check_gemini.py` 는 목록 실패를 치명적 오류로 보지 않는다).

### 3.6 6단계 — 연결 점검

```bash
python scripts/check_gemini.py
```

성공하면 다음이 순서대로 출력된다.

```
[OK] 인증 정보 발견 (Credentials), ADC 프로젝트=valeo-kr-example-prod
[OK] 사용 가능한 Gemini 모델 (valeo-kr-example-prod/global):
   - gemini-...
[OK] 응답: text_seen='D4 Root cause - 5 Why' is_slide_like=True
[OK] 토큰: 입력 1234 / 출력 56

GEMINI 연결 정상 — Gemini CLI 없이 Python SDK로 동작합니다.
```

### 3.7 7단계 — ⚠ 캐시 정리

**offline 로 만든 중간 산출물을 반드시 버린다.**

```bash
rm -rf work/ocr work/features work/concepts work/sections work/models
rm -rf work/offline_fixtures          # 더미 전용 fixture
```

| 왜 | 설명 |
|---|---|
| `work/cache/` 는 **그대로 둬도 된다** | 캐시 키에 백엔드 이름(`offline/v3` vs `vertex/<project>/<location>`)이 들어가므로 섞이지 않는다 |
| 하지만 `work/ocr/`, `work/features/` 등은 **버려야 한다** | 이 파일들은 캐시가 아니라 **최종 산출물**이다. 존재하면 재호출 없이 그대로 쓰인다 |
| `work/offline_fixtures/` 는 더미 전용 | 실데이터에서는 아무 의미가 없다 |
| 🔒 승인 파일(`*_approved.yaml`)은 | offline 데이터로 만든 것이므로 실데이터에서는 **다시 검토해야 한다**. 지우고 새로 승인하는 쪽을 권한다 |

`--force` 옵션은 캐시를 무시하지만 산출물 파일은 다시 쓴다. 확실하게 하려면 위처럼 디렉토리를 지운다.

### 3.8 8단계 — 비용·시간 추정 (실제 호출 전)

```bash
python -m svmtrial ingest --group 대책서__CUST_A      # S1 은 LLM 거의 없음
python -m svmtrial ocr --group 대책서__CUST_A --dry-run
```

출력 예:

```
--dry-run 추정
 calls       320
 by_prompt   {"ocr_page.v1": 320}
 est_tokens  576000
```

- `est_tokens_per_page`(config, 기본 1800)에 페이지 수를 곱한 대략치다.
- 단가(M13)를 `config.yaml` 의 `gemini.cost_per_1m_tokens` 에 넣으면 `est_cost` 도 함께 나온다.
  비어 있으면 **비용 추정을 생략한다** (단가를 하드코딩하지 않는다).

### 3.9 9단계 — 작은 그룹으로 먼저 실행

```bash
python -m svmtrial ocr --group 대책서__CUST_A --limit 5     # 문서 5건만
```

확인할 것:
- 제목 계층이 제대로 잡혔는가 → `work/ocr/<doc_id>/doc_outline.json` 의 `headings`
- 표의 열 이름이 들어왔는가 → 같은 파일의 `tables[].columns`
- `mean_legibility` 가 0.5 이상인가 → 낮으면 §5.2

괜찮으면 전체를 돌린다 ([`Runbook.md` §5](Runbook.md)).

---

## 4. 전환 후 조정이 필요할 수 있는 설정

### 4.1 `config/config.yaml`

| 키 | 기본값 | 언제 바꾸는가 |
|---|---|---|
| `gemini.max_workers` | 4 | `429 RESOURCE_EXHAUSTED` 가 나면 2 또는 1 로 낮춘다 (M12) |
| `gemini.cost_per_1m_tokens` | `{}` | 단가(M13)를 받으면 `{input: 0.3, output: 2.5}` 처럼 넣는다 |
| `gemini.est_tokens_per_page` | 1800 | 실제 호출 후 `logs/<run_id>/gemini_calls.jsonl` 의 평균으로 보정한다 |
| `ingest.dpi` | 150 | 표가 잘 안 읽히면 200. 토큰이 늘어난다 |
| `ingest.retry_dpi` | 250 | 판독성 낮은 페이지 재시도 dpi |
| `gemini.media_resolution_ocr` | `HIGH` | 비용을 줄이려면 `MEDIUM`. 표 인식률이 떨어질 수 있다 |
| `analysis.target` | `unanimous` | 전원 합격 비율이 15%~85% 밖이면 **자동으로** `majority` 로 바뀌고 리포트에 기록된다 |
| `analysis.pool_customers` | `false` | 고객사별 문서가 너무 적으면 `true` (doc_type 단위로 합침) |
| `analysis.concepts_per_scoring_call` | 25 | 응답이 잘리면 낮춘다 |
| `template.font` | `맑은 고딕` | 사내 표준 글꼴이 다르면 변경 |

### 4.2 평가 시트 열 이름 매핑 (M14)

실제 시트의 열 이름이 다르면 `config.yaml` 에서 매핑한다.

```yaml
labels:
  columns:
    doc_id: 문서번호          # 실제 열 이름 ← 왼쪽은 내부 이름(고정)
    customer: 고객사
    doc_type: 문서종류
    rater: 평가자
    result: 판정
  pass_values: [Pass, P, OK, O, "○", 합격, "1", Y, 적합]     # 사내에서 쓰는 표기 추가
  fail_values: [Fail, F, NG, X, "×", 불합격, "0", N, 부적합]
```

- `rater`/`result` 열이 없으면 **wide 형식**(문서 1행 + 평가자별 열)으로 자동 판별해 변환한다.
- **인식할 수 없는 결과값이 있으면 즉시 중단**하고 `work/labels/label_value_errors.csv` 를 남긴다.
  그 파일을 보고 위 목록에 추가한다. (조용히 결측으로 넘기지 않는다.)

---

## 5. 전환 검증 체크리스트

실데이터 분석을 시작하기 전에 전부 ✅ 가 되어야 한다.

### 5.1 환경

- [ ] `python -m svmtrial doctor` 의 `backend` 가 **`vertex`** 다
- [ ] 같은 출력의 `GOOGLE_CLOUD_PROJECT` 가 실제 프로젝트 ID 다 (`your-gcp-project-id` 가 아니다)
- [ ] `GEMINI_MODEL_FAST` / `GEMINI_MODEL_PRO` 가 비어 있지 않다
- [ ] `python scripts/smoke_test.py` → `ALL SMOKE TESTS PASSED`
- [ ] `python scripts/check_gemini.py` → `GEMINI 연결 정상`
- [ ] `grep -rl "from google import genai" src/` 가 `vertex_backend.py` 만 보여준다 (R2)

### 5.2 OCR 품질 — 전환에서 가장 크게 달라지는 부분

- [ ] `python -m svmtrial ocr --group <G> --limit 5` 가 성공한다
- [ ] 리포트의 `평균 판독성` ≥ 0.5 (낮으면 `ingest.dpi` 를 200으로, `media_resolution_ocr` 를 `HIGH` 로)
- [ ] `work/ocr/<doc_id>/doc_outline.json` 의 `headings` 에 실제 섹션 제목이 들어 있다
- [ ] 같은 파일 `tables[].columns` 에 표의 열 이름이 들어 있다
- [ ] `판독 불량 페이지` 수가 전체의 10% 미만이다

### 5.3 라벨

- [ ] `python -m svmtrial labels --group <G>` 가 **중단 없이** 끝난다
      (중단되면 `work/labels/label_value_errors.csv` → §4.2)
- [ ] `agreement.json` 의 `n_ratings` 가 시트 행 수와 맞는다
- [ ] Krippendorff α 를 확인했다. **α < 0.2 면 경고가 뜬다** → 공통 템플릿의 근거가 약하다는 뜻이다
- [ ] 타깃이 자동 전환됐는지 확인했다 (`unanimous` → `majority`)

### 5.4 🔒 관문 — offline 승인을 그대로 쓰지 않는다

- [ ] `taxonomy_candidates.yaml` 을 **실데이터 기준으로 다시** 검토했다
- [ ] `concepts_candidates.yaml` 을 **실데이터 기준으로 다시** 검토했다
      (vertex 의 가설은 offline 보다 질이 다르다. 그대로 승인하지 말 것)
- [ ] 각 개념이 ① 문서만 보고 예/아니오로 답 가능 ② 내용 타당성 판단이 섞이지 않음 ③ actionable 이 맞음

### 5.5 결과 해석

- [ ] 리포트의 `탐색적` 표기 여부를 확인했다 (소수 클래스 < 10 × 특징 수)
- [ ] 순열검정 p < 0.05 인가. p ≥ 0.05 면 **다변량 결과를 쓰지 않는다**
- [ ] `유력` 중 `verdict_basis` 에 "안정성만"이 붙은 항목을 확인했다 (통계적 유의성 없음)
- [ ] 평가자 간 요구 충돌 목록을 확인했다
- [ ] 생성된 docx/pptx 를 Word/PowerPoint 에서 **직접 열어** 한글이 깨지지 않는지 확인했다 (🔒)

---

## 6. 사내망 특수 상황

### 6.1 pip / conda 가 SSL 오류를 낸다 (M8)

```bash
pip config set global.cert C:\certs\corp-ca.pem
conda config --set ssl_verify C:\certs\corp-ca.pem
```

### 6.2 사내망에서 pip 가 완전히 막혀 있다 (M10)

인터넷이 되는 PC 에서 wheel 을 미리 받아 zip 에 넣는다.

```bash
python scripts/build_release.py --with-wheels        # 약 160MB
```

사내 PC 에서 `windows\setup_windows.bat` 이 `wheelhouse/` 를 자동 감지해 오프라인 설치한다.
수동으로 하려면:

```bash
pip install --no-index --find-links wheelhouse -r requirements.txt
```

### 6.3 Gemini 호출이 SSL/프록시 오류를 낸다 (M7, M8)

```dotenv
HTTPS_PROXY=http://proxy.company:8080
HTTP_PROXY=http://proxy.company:8080
SSL_CERT_FILE=C:\certs\corp-ca.pem
```

`google-genai` 는 `httpx` 를 쓰고, `httpx` 는 이 환경변수를 따른다.

### 6.4 Colab Enterprise / Vertex AI Workbench 를 쓰는 경우

사내 GCP 프로젝트 안에서 돌기 때문에 **데이터가 회사 프로젝트 밖으로 나가지 않고**,
인증이 런타임 서비스계정으로 자동 처리된다(M4 불필요).

```bash
pip install -r requirements.txt && pip install -e .
# .env 에 SVMTRIAL_BACKEND=vertex, GOOGLE_CLOUD_PROJECT, 모델 ID 만 넣으면 된다
```

데이터는 회사 GCS 버킷에서 읽어 `data/raw/` 로 내려놓는다.

> **개인 Colab(무료/Pro)은 쓰지 말 것.** 고객사 문서가 개인 Drive 로 업로드되므로 보안상 부적절하다.

---

## 7. 문제 해결 (전환 시 자주 보는 오류)

| 증상 | 원인 | 조치 |
|---|---|---|
| `DefaultCredentialsError` | 인증 안 됨 | `gcloud auth application-default login` 또는 `GOOGLE_APPLICATION_CREDENTIALS` 경로 확인 (M4) |
| `403 PERMISSION_DENIED` | IAM 역할 또는 API 미설정 | `roles/aiplatform.user`(M5), `aiplatform.googleapis.com`(M6) 확인 |
| `404 model not found` | 모델 ID 또는 리전 불일치 | `check_gemini.py --list-only` 로 다시 확인 (M3, M2) |
| `429 RESOURCE_EXHAUSTED` | 할당량 초과 | `gemini.max_workers` 를 낮춘다. 재시도는 자동이고 캐시 덕분에 이어서 실행된다 (M12) |
| 할당량 프로젝트 경고 | `set-quota-project` 누락 | `gcloud auth application-default set-quota-project <PROJECT_ID>` |
| `.env 에 GEMINI_MODEL_FAST 가 비어 있습니다` | M3 미입력 | §3.5 |
| `vertex 백엔드인데 GOOGLE_CLOUD_PROJECT 가 없습니다` | M1 미입력 | §3.4 |
| 모든 페이지 판독성 0.0 | **`SVMTRIAL_BACKEND` 가 아직 `offline`** 이다 | `.env` 확인 → `doctor` 로 `backend=vertex` 확인 (§2.1) |
| 결과가 offline 때와 똑같다 | 산출물 파일이 남아 재호출이 안 됐다 | §3.7 캐시 정리 |
| 응답이 비어 있다 (빈 응답 오류) | 안전 필터 차단 가능성 | 해당 페이지를 직접 확인. `logs/<run_id>/gemini_calls.jsonl` 참고 |
| Windows 한글 깨짐 | 코드페이지 | bat 파일에 `chcp 65001` 과 `PYTHONUTF8=1` 이 있는지 확인 (이미 들어 있다) |

---

## 8. 되돌리기 (rollback)

전환 후 문제가 생기면 `.env` 한 줄로 되돌아간다.

```dotenv
SVMTRIAL_BACKEND=offline
```

그리고 더미 데이터로 파이프라인이 살아 있는지 확인한다.

```bash
python scripts/make_dummy_data.py --out data/dummy --n 40
# config 의 paths 를 data/dummy 로 바꾼 사본으로 실행 (Runbook.md §4)
python -m svmtrial all --group 대책서__CUST_A --config <더미 config>
python scripts/verify_dummy.py --group 대책서__CUST_A --config <더미 config>
```

캐시가 백엔드별로 분리돼 있으므로, 되돌린 뒤에도 vertex 로 받은 응답은 보존된다.
다시 vertex 로 바꾸면 이전 응답을 재사용해 **같은 호출을 다시 하지 않는다.**

---

## 9. 전환 요약 (한 장)

```
┌──────────────────────────── 사전 준비 (사람) ────────────────────────────┐
│  M1 프로젝트 ID    M2 리전    M3 모델 ID×2    M4 인증 방식                │
│  M5 IAM 역할       M6 API 설정                                            │
│  M7~M10 프록시/CA/키/미러 (사내망에 따라)                                 │
│  M11 ⚠ 고객사 문서 전송 승인   M12 할당량   M13 단가                      │
│  M14 시트 열 이름  M15 문서 수  M16 로그 정책                             │
└───────────────────────────────┬───────────────────────────────────────────┘
                                ▼
  1. conda env create -f environment.yml && pip install -e .
  2. gcloud 설치 (방식 A)
  3. gcloud auth application-default login
     gcloud auth application-default set-quota-project <M1>
  4. .env : SVMTRIAL_BACKEND=vertex, M1, M2  (모델 ID 는 아직 비움)
  5. python scripts/check_gemini.py --list-only   → 모델 ID 골라 .env 에 기입 (M3)
  6. python scripts/check_gemini.py               → "GEMINI 연결 정상"
  7. rm -rf work/ocr work/features work/concepts work/sections work/models
     rm -rf work/offline_fixtures
  8. python -m svmtrial ocr --group <G> --dry-run  → 호출 수·토큰 추정
  9. python -m svmtrial ocr --group <G> --limit 5  → OCR 품질 확인
 10. §5 체크리스트 전부 ✅ → 전체 실행 (Runbook.md §5)
```
