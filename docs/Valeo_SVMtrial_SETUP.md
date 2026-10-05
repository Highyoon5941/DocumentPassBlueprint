# Valeo_SVMtrial_SETUP.md
> 문서 Pass 조건 역추적 → 표준 템플릿 생성 도구 (SVM + Gemini 하이브리드)
> 작성일 2026-10-05 · 대상: 로컬 Claude Code가 이 파일 하나만 보고 프로젝트 전체를 구성·구현한다.

---

## 0. 이 파일 사용법 (사람용)

1. Ubuntu 22.04 개발 PC에 빈 폴더 `valeo_svmtrial/`을 만들고 이 파일을 넣는다.
2. 그 폴더에서 Claude Code를 실행하고 이렇게 지시한다:
   ```
   @Valeo_SVMtrial_SETUP.md 를 읽고 §12 개발 순서의 Phase 0부터 진행해줘.
   각 Phase의 완료 기준을 통과하면 멈추고 결과를 보고해.
   ```
3. Phase가 끝날 때마다 결과를 확인하고 "다음 Phase 진행"이라고 지시한다. 사람의 확인이 필요한 단계(🔒 표시)에서는 Claude Code가 반드시 멈춘다.

---

## 1. Claude Code 작업 규칙 (반드시 준수)

- **R1. 데이터 외부 전송 금지.** 문서와 이미지는 `.env`에 설정된 회사 GCP 프로젝트의 Vertex AI(Gemini) 엔드포인트로만 보낸다. 다른 API, 웹 서비스, 텔레메트리로는 보내지 않는다.
- **R2. Gemini 호출 경로 단일화.** 모든 호출은 `src/svmtrial/gemini_client.py` 하나를 거친다. Gemini CLI는 사용하지 않는다(§3 참조).
- **R3. 비밀정보 커밋 금지.** `data/`, `work/`, `outputs/`, `.env`, `*.json` 인증키, `dist/`는 git과 배포 zip에서 항상 제외한다.
- **R4. Windows(GPU 없음) 호환성 유지.**
  - 경로는 `pathlib`, 파일 읽기·쓰기는 `encoding="utf-8"`로 한다.
  - GPU 전용 패키지와 시스템 바이너리(Poppler, Tesseract 등)에 의존하지 않는다.
  - 셸 명령 대신 Python으로 구현한다.
- **R5. 의존성 추가 시 사용자 승인.** `requirements.txt`에 없는 패키지가 필요하면 먼저 이유를 보고하고 승인을 받는다. 승인 후에는 Windows wheel 존재 여부(§5.4)를 확인한다.
- **R6. 테스트에서는 Gemini를 호출하지 않는다.** `pytest`는 Gemini를 mock으로 대체한다. 실제 호출은 `--live` 옵션이나 CLI 명령으로만 한다.
- **R7. 사람 검토 관문(🔒)을 자동 통과하지 않는다.** 개념 후보 승인, 최종 템플릿 승인은 사람이 한다.
- **R8. 사용자 메시지, 리포트, 템플릿 문구는 한국어로 쓴다.** 코드 식별자와 JSON 키는 영어로 쓴다.
- **R9. 재현성.** Gemini 호출은 `temperature=0`으로 하고, 프롬프트는 버전을 붙여 파일로 관리하며, 응답은 캐시하고, 실행할 때마다 `run_id` 로그를 남긴다.
- **R10. 생성 결과는 "템플릿"이다.** 문서 초안(실제 내용 작성)은 만들지 않는다. 플레이스홀더와 작성 가이드만 넣는다.

---

## 2. 프로젝트 개요

### 2.1 목적 (상사 미팅 2026-10-02 요지)
- Pass/Fail 예측기를 만드는 것이 목적이 **아니다**. Pass/Fail 데이터는 수단이다.
- "왜 이 문서는 평가자 5명이 모두 합격시켰고, 저 문서는 4명만 합격시켰나"를 역추적한다. 그 결과로 **평가자 전원을 통과할 표준 구조(템플릿)**를 만든다. 템플릿에는 어떤 섹션, 표, 그림이 어떤 순서와 위계로 들어가야 하는지가 담긴다.
- 문서 종류는 바뀔 수 있어야 한다(대책서, 요구사양서, RMP, 외주 계산서 등). 구조는 범용으로 만들고, 대책서를 첫 적용 대상으로 삼는다.
- 상사의 비유: "평면에 Pass와 Fail 점이 찍혀 있고 경계선의 방정식은 모른다. 결과로 경계선을 찾고, Fail 문서가 Pass 쪽으로 가려면 무엇을 바꿔야 하는지 가이드한다." 이것을 선형 SVM과 반사실(counterfactual) 분석으로 구현한다.

### 2.2 입력 / 출력
| 구분 | 내용 |
|---|---|
| 입력 1 | 같은 성격의 문서 PDF 여러 개. **이미지 기반(스캔본)을 기본 전제**로 하며, 텍스트 레이어는 무시한다. |
| 입력 2 | 평가 시트(xlsx). 문서별로 평가자 여러 명이 Pass/Fail을 매겼고, 사유는 없다. |
| 출력 1 | `template_spec.json` / `template_spec.md`: 섹션 구조, 필수 요소, 작성 가이드, 근거 통계 |
| 출력 2 | 원본 형식에 맞춘 템플릿 파일. 문서형이면 `template.docx`, 슬라이드형이면 `template.pptx`. 구글 Docs/Slides로 가져오기 가능 |
| 출력 3 | `analysis_report.md`: 평가자 일치도, Pass를 가르는 요인, 신뢰도, 한계 |
| 출력 4 (Phase 7) | `diagnose`: 새 문서 1건에 대해 Pass 쪽으로 가려면 무엇을 보완해야 하는지 진단 |

### 2.3 확정된 결정사항
| 항목 | 결정 |
|---|---|
| LLM | 사내 Gemini, **Vertex AI (GCP 프로젝트)**를 Python SDK `google-genai`로 호출 |
| Gemini CLI | **불필요** (§3) |
| OCR | **Gemini 비전**. 페이지를 PNG로 바꿔 전송하고, 레이아웃 구조를 JSON으로 받는다 |
| 개발 환경 | Ubuntu 22.04 + Anaconda, Python 3.11. GPU(RTX 4060 Ti)는 **사용하지 않음** |
| 배포 | zip으로 묶어 Windows(GPU 없음) PC에서 Anaconda로 실행 |
| 대안 실행 환경 | Colab Enterprise / Vertex AI Workbench (§10) |
| 출력 범위 v1 | 명세(json/md) + docx/pptx. 구글 API 직접 연동은 v2 |

### 2.4 비목표 (v1)
- 문서 내용 초안 작성, 기술적 타당성(근본원인이 실제로 맞는지) 판정, 구글 Docs/Slides API 직접 생성, 웹 UI.

---

## 3. Gemini 연결 방식: CLI 필요 여부 판정

**결론: Gemini CLI는 필요 없다.** Vertex AI는 Python SDK(`google-genai`)로 직접 호출하며, 자동화 파이프라인에는 이 방식이 더 적합하다.

| 필요 여부 | 도구 | 용도 |
|---|---|---|
| ❌ 불필요 | Gemini CLI (`gemini`) | 대화형 코딩 에이전트. 이 프로젝트에서는 쓰지 않는다 |
| ✅ 필요 | `google-genai` (pip) | 모든 Gemini 호출 |
| ⚠️ 인증 방식에 따라 필요 | **Google Cloud CLI (`gcloud`)** | ADC 로그인용(`gcloud auth application-default login`). 서비스계정 JSON 키로 인증하면 이것도 불필요 |

참고:
- Vertex AI는 2026년 현재 공식 명칭이 "Gemini Enterprise Agent Platform"으로 바뀌었다.
- SDK에서는 `genai.Client(vertexai=True, ...)`(구 명칭)와 `enterprise=True`가 같은 뜻이다. 이 프로젝트는 `vertexai=True`를 쓴다.

**인증 방식 (IT 확인 후 택1, §15):**
- **A. ADC (권장, 개인 계정)**
  - 각 PC에 `gcloud`를 설치하고 `gcloud auth application-default login`을 실행한다.
  - 이어서 `gcloud auth application-default set-quota-project <PROJECT_ID>`를 실행한다.
  - 사용자 계정에 IAM 역할 `roles/aiplatform.user`(Vertex AI 사용자)가 있어야 한다.
- **B. 서비스계정 JSON 키**
  - `.env`의 `GOOGLE_APPLICATION_CREDENTIALS`에 키 파일 경로를 넣는다.
  - 키 파일은 zip에 넣지 않고 별도 경로로 전달한다. 회사 정책상 키 생성이 막혀 있을 수 있다.

---

## 4. 아키텍처

```
[PDF(이미지)] ─S1 ingest──▶ 페이지 PNG + 메타(방향/비율/원본형식 추정)
                  │
                  └─S2 ocr (Gemini Flash, 페이지별)──▶ page JSON(제목계층·블록·표·그림·서명)
                                                    │
[평가 xlsx] ─S3 labels──▶ 평가자 일치도 + 타깃(y_unanimous / y_majority / pass_ratio) + 평가자별 라벨
                                                    │
        S4a sections : 제목 → 표준 섹션 분류체계(taxonomy) 매핑            (Gemini Pro + 🔒검토)
        S4b concepts : Pass vs Fail 대조 → 관찰 가능한 예/아니오 개념 후보 생성 (Gemini Pro, 발견셋만) → 🔒승인
        S4c score    : 문서 × 개념 0/1 행렬 + 결정론적 구조 특징                (Gemini Flash + 규칙)
                                                    │
        S5 model     : 단변량(Fisher+FDR) + 선형SVM/L1로지스틱/얕은트리/RuleFit + CV + 순열검정 + 부트스트랩 안정성
                       + 평가자별 모델 + 반사실(Pass까지 필요한 최소 보완)
                                                    │
        S6 template  : 검증된 개념 + Pass 문서의 섹션 골격 ─(Gemini Pro, 근거 ID 필수)─▶ TemplateSpec(JSON)
                       ─▶ docx / pptx 렌더 + analysis_report.md                      → 🔒최종 검토
        S7 diagnose  : 새 문서 → 점수 + 부족한 항목 목록 (피드백 루프)
```

**핵심 설계 원리 (Text Concept Bottleneck)**
- 문장 임베딩 같은 해석 불가능한 벡터는 쓰지 않는다. **사람이 읽을 수 있는 예/아니오 개념**(예: "D4에 5Why 표가 있다")을 특징으로 쓴다. 그래야 SVM 가중치 `w`를 "이 요소를 넣어라"라는 템플릿 문장으로 바꿀 수 있다.
- 사유가 없는 라벨에서 사유 후보를 만들 때는 LLM이 Pass와 Fail 문서를 대조해 가설을 생성한다(HypoGeniC 방식). 생성된 가설은 별도 검증셋에서 통계로 확인한다.

---

## 5. 개발 환경

### 5.1 Ubuntu 22.04 (개발)
```bash
conda env create -f environment.yml        # 최초
conda activate valeo_svm
python scripts/smoke_test.py               # ALL SMOKE TESTS PASSED 확인
cp .env.example .env && nano .env          # 프로젝트 ID 등 입력
gcloud auth application-default login      # 인증 방식 A일 때
python scripts/check_gemini.py --list-only # 사용 가능한 모델 ID 확인 → .env에 기입
python scripts/check_gemini.py             # 이미지+JSON 테스트 호출
```
- gcloud 설치(Ubuntu): https://cloud.google.com/sdk/docs/install 의 apt 설치 절차를 따른다.

### 5.2 `environment.yml`
```yaml
name: valeo_svm
channels:
  - conda-forge
dependencies:
  - python=3.11
  - pip
  - pip:
      - -r requirements.txt
```

### 5.3 `requirements.txt` (한 번에 설치)
> 2026-10-05에 Linux Python 3.11에서 설치하고 `scripts/smoke_test.py` 통과를 확인했다.
> 같은 버전 전체 의존성 99개가 **Windows(win_amd64, cp311) 바이너리 wheel로 모두 해결됨**도 확인했다. 따라서 Windows에서 컴파일러가 필요 없다.
> 설치 후 환경 크기는 약 0.6 GB이다.

```text
# ===== Valeo_SVMtrial requirements (Python 3.11, CPU only) =====
# --- 데이터/수치 ---
numpy==2.4.6
pandas==3.0.6
scipy==1.17.1
scikit-learn==1.9.1
statsmodels==0.15.0            # Fleiss kappa, FDR 보정
krippendorff==0.8.2            # 평가자 일치도(결측 허용)
imodels==3.0.2                 # RuleFit 등 해석 가능한 규칙 모델
matplotlib==3.11.2             # 리포트 차트
openpyxl==3.1.5                # xlsx 입출력
# --- PDF/이미지 (Poppler/Tesseract 불필요) ---
pymupdf==1.28.2                # PDF→PNG, 메타데이터
pillow==12.3.0
# --- Gemini (Vertex AI) ---
google-genai==2.28.0
google-auth==2.59.1
# --- 출력 문서 ---
python-docx==1.2.0
docxtpl==0.20.2
python-pptx==1.0.2
# --- 앱 공통 ---
pydantic==2.13.5               # JSON 스키마/검증, Gemini 구조화 출력
pyyaml==6.0.3
python-dotenv==1.2.4
jinja2==3.1.6                  # 프롬프트/리포트 템플릿
typer==0.27.2                  # CLI
rich==15.0.0
tqdm==4.70.1
tenacity==9.1.4                # 재시도
# --- 개발/테스트 ---
pytest==9.1.1
ruff==0.16.10
ipykernel==7.4.0               # Jupyter 탐색용(선택)
```

**고려했지만 기본에서 뺀 패키지** (승인 후 추가할 수 있음, R5):
| 패키지 | 뺀 이유 | 대체 |
|---|---|---|
| crowd-kit | transformers 등을 끌어와 환경이 1.8 GB로 커짐 | Dawid-Skene을 직접 구현(약 50줄, §8.3) |
| dice-ml | xgboost/lightgbm 의존 | 선형 모델 + 이진 특징에서는 탐욕 반사실이 정확한 최소해(§8.5) |
| shap | numba 의존 | 선형 모델은 `w·x`가 곧 기여도 |
| paddleocr / docling | GPU 또는 무거운 모델, 결정에 따라 Gemini 비전 사용 | 사내 정책상 외부 전송이 막히면 v2에서 검토 |

### 5.4 의존성 추가 시 Windows wheel 확인 명령
```bash
pip download --no-deps --platform win_amd64 --python-version 3.11 --only-binary=:all: <패키지>==<버전> -d /tmp/wcheck
```

### 5.5 `.env.example`
```dotenv
# --- Vertex AI ---
GOOGLE_CLOUD_PROJECT=your-gcp-project-id
GOOGLE_CLOUD_LOCATION=global            # IT 정책에 따라 asia-northeast3 등 (§15)
# 인증 방식 B일 때만:
# GOOGLE_APPLICATION_CREDENTIALS=C:\keys\svmtrial-sa.json
# --- 모델 ID: check_gemini.py --list-only 결과에서 골라 기입 ---
GEMINI_MODEL_FAST=                      # OCR/개념 채점용 Flash 계열 (예: 3.x Flash)
GEMINI_MODEL_PRO=                       # 가설 생성/템플릿 작성용 Pro 계열 (예: 3.x Pro)
# --- 사내 프록시 (필요 시) ---
# HTTPS_PROXY=http://proxy.company:8080
# SSL_CERT_FILE=C:\certs\corp-ca.pem
```
> 모델 ID는 하드코딩하지 않는다. 2026-10 기준 Vertex에는 Gemini 3.x Pro/Flash가 GA 상태이고, 2.5 계열은 2026-10-16 이후 종료될 수 있다. 사내 프로젝트에서 실제로 쓸 수 있는 ID는 `check_gemini.py`로 확인한다.

### 5.6 `.gitignore`
```gitignore
.env
*.json.key
*-sa.json
data/
work/
outputs/
dist/
logs/
__pycache__/
.ipynb_checkpoints/
.pytest_cache/
.ruff_cache/
```

---

## 6. 디렉토리 구조

```
valeo_svmtrial/
├─ Valeo_SVMtrial_SETUP.md        # 이 파일 (설계 기준)
├─ CLAUDE.md                      # Phase 0에서 생성: "모든 작업은 Valeo_SVMtrial_SETUP.md를 따른다" + 현재 Phase 기록
├─ environment.yml / requirements.txt / .env.example / .gitignore
├─ config/config.yaml
├─ prompts/                       # 프롬프트(버전 헤더 포함, Jinja2)
│   ├─ ocr_page.v1.md
│   ├─ doc_format.v1.md
│   ├─ section_taxonomy.v1.md
│   ├─ concept_discovery.v1.md
│   ├─ concept_merge.v1.md
│   ├─ concept_scoring.v1.md
│   └─ template_compose.v1.md
├─ src/svmtrial/
│   ├─ __init__.py  __main__.py  cli.py          # typer CLI: python -m svmtrial <cmd>
│   ├─ config.py        # config.yaml + .env 로드, pydantic Settings
│   ├─ gemini_client.py # 유일한 Gemini 진입점: 재시도·동시성·캐시·토큰 로깅
│   ├─ cache.py         # sha256 키 기반 JSON 캐시
│   ├─ schemas.py       # 모든 pydantic 스키마(§9)
│   ├─ ingest.py        # S1
│   ├─ ocr.py           # S2
│   ├─ labels.py        # S3 (+ dawid_skene)
│   ├─ sections.py      # S4a
│   ├─ concepts.py      # S4b, S4c
│   ├─ features.py      # 결정론적 구조 특징 + 행렬 조립
│   ├─ modeling.py      # S5
│   ├─ counterfactual.py
│   ├─ template_spec.py # S6 명세 생성
│   ├─ render_docx.py  render_pptx.py  report.py
│   └─ diagnose.py      # S7
├─ scripts/  smoke_test.py  check_gemini.py  make_dummy_data.py  build_release.py
├─ windows/  setup_windows.bat  run_windows.bat  README_windows.txt
├─ tests/    (Gemini mock 기반)
├─ data/     raw/pdfs/<doc_type>/<customer>/*.pdf , raw/labels/*.xlsx      (git 제외)
├─ work/     pages/ ocr/ sections/ concepts/ features/ models/ cache/      (git 제외)
└─ outputs/<run_id>/<doc_type>__<customer>/ template_spec.json|md, template.docx|pptx, analysis_report.md
```

---

## 7. 입력 데이터 규격

### 7.1 PDF
- 경로: `data/raw/pdfs/<doc_type>/<customer>/<doc_id>.pdf`. **파일명(확장자 제외)이 `doc_id`**이며 평가 시트의 `doc_id`와 일치해야 한다.
- 시트에 있는데 PDF가 없거나, PDF는 있는데 시트에 없으면 경고 목록(`work/ingest_issues.csv`)을 남기고 해당 문서는 제외한다.

### 7.2 평가 시트 (형식 자동 판별, `config.yaml`의 `labels.columns`로 열 이름 매핑)
**권장: long 형식 (평가 1건 = 1행)**
| doc_id | customer | doc_type | rater | result | (선택) eval_date |
|---|---|---|---|---|---|
| CUST_A_001 | 현대 | 대책서 | 윤정호 | Pass | 2025-03-02 |

**허용: wide 형식 (문서 1건 = 1행, 평가자별 열)** → 내부에서 long 형식으로 변환한다.
| doc_id | customer | doc_type | 평가자1 | 평가자2 | … |

**결과값 정규화:**
- Pass로 보는 값: `Pass, P, OK, O, ○, 합격, 1, Y`
- Fail로 보는 값: `Fail, F, NG, X, ×, 불합격, 0, N`
- 빈칸은 미평가로 처리한다(일치도 계산 시 결측 허용).
- 인식할 수 없는 값은 오류 목록에 넣고 중단한다.

### 7.3 그룹
- 분석 단위 = `(doc_type, customer)`. 고객사 양식이 섞이면 "고객사 A 양식 = Pass" 같은 가짜 신호가 생기므로 그룹별로 분석한다.
- `config.analysis.pool_customers: true`이면 `doc_type` 단위로 합치고, `customer`를 통제 변수로 넣는다(데이터가 적을 때).

---

## 8. 단계별 구현 명세

### 8.0 공통: `gemini_client.py`
```python
from google import genai
from google.genai import types

class GeminiClient:
    def __init__(self, settings):
        self.client = genai.Client(vertexai=True, project=settings.project, location=settings.location)
        # http_options=types.HttpOptions(retry_options=types.HttpRetryOptions(attempts=5)) 도 가능
    def generate_json(self, *, model: str, prompt_id: str, parts: list, schema: type[BaseModel],
                      cache_key_extra: str = "", media_resolution: str | None = None) -> BaseModel:
        """1) 캐시 키 = sha256(model + prompt_id(버전 포함) + 입력 바이트/텍스트 + extra)
           2) 캐시 적중 → 즉시 반환
           3) generate_content(config=GenerateContentConfig(temperature=0,
                 response_mime_type="application/json", response_schema=schema,
                 media_resolution=...))
           4) resp.parsed 검증 실패 → 1회 재요청(오류 메시지 첨부) → 그래도 실패하면 예외
           5) usage_metadata(입력/출력/thinking 토큰)를 logs/<run_id>/gemini_calls.jsonl 에 기록
        """
```
- 재시도: `tenacity`로 429/500/503에 지수 백오프(최대 6회, 최대 대기 60초)를 건다.
- 동시성: `ThreadPoolExecutor(max_workers=config.gemini.max_workers)`, 기본값 4.
- `--dry-run`: 호출 없이 페이지 수와 예상 호출 수, 예상 토큰(페이지당 대략치)만 출력한다. 단가는 하드코딩하지 않고 config의 `cost_per_1m_tokens`가 비어 있으면 생략한다.

### 8.1 S1 `ingest` — PDF → 페이지 PNG + 메타
- `pymupdf`로 페이지마다 `get_pixmap(dpi=config.ingest.dpi)`(기본 150, 표가 잘 안 읽히면 200)을 만들어 `work/pages/<doc_id>/p001.png`에 저장한다.
- 메타는 `work/pages/<doc_id>/meta.json`에 저장한다: 페이지 수, 페이지별 가로/세로 비율, PDF `creator`/`producer`, 텍스트 레이어 유무(참고용), 파일 sha256.
- **원본 형식(docs/slides) 판정** (`doc_format`):
  1. `creator`/`producer`에 PowerPoint, Keynote, Google Slides, Impress가 있으면 slides로, Word, Hangul/HWP, Google Docs, Writer가 있으면 docs로 본다.
  2. 그렇지 않으면(스캔본은 대개 스캐너 정보만 남음) 페이지의 과반이 가로형(비율 ≥ 1.25)이면 slides 후보, 세로형이면 docs 후보로 둔다.
  3. 2의 결과가 애매하거나(혼재) 확인이 필요하면 첫 2페이지를 Gemini Flash에 보내 `doc_format.v1.md`로 판정한다.
  4. 그룹의 출력 형식 = 그룹 내 다수결. 결과는 리포트에 표시한다.

### 8.2 S2 `ocr` — Gemini 비전으로 페이지 구조 추출
- 페이지 PNG 1장당 1회 호출한다(모델 FAST, `media_resolution` HIGH). 프롬프트는 `ocr_page.v1.md`, 스키마는 `PageLayout`(§9)이다.
- 추출 대상: 제목과 계층(level 1~3), 블록 유형(paragraph/table/image/photo/chart/diagram/signature/stamp/form_field), 표의 열 이름과 행 수, 페이지 역할(표지/본문/부록), 판독성(0~1).
- 결과는 `work/ocr/<doc_id>/p001.json`에 저장하고, 문서 단위로 `doc_outline.json`(제목 트리 + 블록 요약 + 전체 텍스트)으로 합친다.
- 판독성 < 0.5인 페이지는 dpi 250으로 다시 렌더링해 재시도하고, 그래도 낮으면 리포트에 "판독 불량"으로 표시한다.

### 8.3 S3 `labels` — 평가자 일치도와 타깃
- 산출물: `work/labels/labels_long.parquet`, `doc_targets.csv`, `agreement.json`
- 문서별 계산값: `n_raters`, `n_pass`, `pass_ratio`, `y_majority`(ratio ≥ 0.5), `y_unanimous`(ratio == 1.0, 평가자 ≥ 3명일 때만), `y_ds`(Dawid-Skene 사후확률 ≥ 0.5).
- 일치도:
  - Fleiss' κ(평가자 수가 같은 문서만), Krippendorff's α(nominal, 결측 허용), 평가자 쌍별 Cohen's κ 행렬을 구한다.
  - 평가자별 엄격도(Pass율)와 Dawid-Skene 혼동행렬도 구한다.
- **판정 관문:** α < 0.2이면 리포트 맨 위에 "평가자 간 기준이 거의 일치하지 않아 공통 템플릿의 근거가 약함"이라고 경고한다. 이 경우 평가자별 분석이 주가 된다.
- Dawid-Skene: 이진 EM(클래스 사전확률과 평가자별 2×2 혼동행렬, 다수결로 초기화, 50회 또는 수렴까지)을 `labels.py`에 직접 구현한다. 단위 테스트에는 합성 데이터를 쓴다.
- 기본 타깃은 `config.analysis.target`, 기본값 `unanimous`("5명 전원 합격 vs 나머지")다. 전원 합격 비율이 15% 미만이거나 85% 초과면 자동으로 `majority`로 바꾸고 리포트에 기록한다.

### 8.4 S4 개념 특징

**S4a `sections` — 표준 섹션 분류체계**
1. 그룹 내 모든 문서의 level 1~2 제목을 모은다.
2. Gemini PRO(`section_taxonomy.v1.md`)가 표준 섹션 목록을 만든다: `id`, 이름, 동의어, 설명.
3. 대책서는 8D(D0 긴급대응, D1 팀, D2 문제정의/5W2H, D3 임시조치/봉쇄, D4 근본원인(발생·유출), D5 영구대책, D6 실행·효과검증, D7 재발방지/표준화·수평전개, D8 종결)를 **초기값(seed)**으로 주되, 데이터에 맞게 수정할 수 있게 한다.
4. 🔒 `work/sections/<group>/taxonomy_candidates.yaml`을 사람이 검토해 `taxonomy_approved.yaml`로 저장한다.
5. 매핑: 동의어 사전으로 먼저 매칭하고, 실패한 제목만 Gemini FAST가 분류한다. 결과는 문서별 `sections.json`(섹션 존재 여부, 순서, 차지 페이지 수)이다.

**S4b `concepts discover` — Pass/Fail 대조로 가설 생성 (HypoGeniC 방식)**
- **누수 방지:** 그룹 문서를 타깃 기준으로 층화해 **발견셋 60% / 검증셋 40%**로 나눈다(seed 고정, `work/split.csv`). 가설은 발견셋으로만 만든다.
- 라운드 r = 1..R (기본 R=5):
  - 발견셋에서 Pass k개와 Fail k개(기본 k=4)를 무작위로 뽑는다.
  - 각 문서의 `doc_outline` 요약(섹션 구조, 표 열 이름, 그림 종류, 핵심 문장 일부)을 Gemini PRO에 준다(`concept_discovery.v1.md`).
  - Gemini는 **문서만 보고 판단할 수 있는 예/아니오 질문** 형태의 가설을 최대 15개 제안한다.
- 가설 필드:
  - `id`, `question`, `section_id`
  - `type`: structure(구조) / content_completeness(내용 완결성) / format(형식) / quantitative(정량 근거)
  - `actionable`: 템플릿으로 유도할 수 있는가
  - `rationale`
- `concept_merge.v1.md`로 라운드 결과를 합치고 중복을 제거해 30~60개로 줄인다.
- 결정론적 구조 특징은 자동으로 추가한다(LLM 불필요): 페이지 수, 표·사진·차트 개수, 섹션별 존재 여부(S4a), 섹션 순서가 표준 순서와 맞는 정도(Kendall τ), 서명·승인란 유무, 평균 판독성.
- 🔒 `work/concepts/<group>/concepts_candidates.yaml`을 사람이 검토(삭제, 수정, 추가)해 `concepts_approved.yaml`로 저장한다. **승인 파일이 없으면 S4c는 실행하지 않는다.**

**S4c `concepts score` — 문서 × 개념 행렬**
- 문서 1건마다 Gemini FAST를 호출해(`concept_scoring.v1.md`) 승인된 개념들(한 번에 최대 25개, 넘으면 나눠서)에 대해 `{id, value: yes|no|na, page, evidence_quote}`를 받는다.
- 입력은 `doc_outline.json` 텍스트를 기본으로 한다. 개념 type이 format이나 표/그림 관련이면 해당 페이지 PNG도 같이 보낸다.
- 신뢰도 점검: 무작위 10%를 다시 채점(다른 순서)해 일치율을 리포트한다. 85% 미만인 개념은 "채점 불안정"으로 표시하고 모델에서 제외한다.
- 출력은 `work/features/<group>/X.csv`(행: doc_id, 열: 개념 + 구조 특징, 0/1/수치)이다.

### 8.5 S5 `model` — 무엇이 Pass를 가르는가
**데이터 양 가드:** `n_minor`(소수 클래스 문서 수) < 10 × 특징 수이면 다변량 결과는 "탐색적"으로만 표기한다. 이때 판정은 단변량 결과와 안정성을 중심으로 한다.

1. **단변량(주 근거, 소량 데이터에 강함)**
   - 이진 특징마다: 있을 때와 없을 때의 Pass율, 위험차(RD), Fisher 정확검정 p, BH-FDR q를 구한다.
   - 타깃이 `pass_ratio`이면 Mann-Whitney 검정과 효과크기를 쓴다.
2. **다변량** (feature는 0/1 그대로, 수치형은 표준화)
   - `LinearSVC(class_weight="balanced")`, C ∈ {0.01, 0.1, 1}
   - L1 로지스틱(liblinear)
   - `DecisionTreeClassifier(max_depth=3)`
   - `imodels.RuleFitClassifier(max_rules=15)`
   - 평가: RepeatedStratifiedKFold(5×10, n < 40이면 LeaveOneOut)로 balanced accuracy와 ROC-AUC를 구하고 `DummyClassifier`와 비교한다.
   - `permutation_test_score`(n_permutations=500)로 p를 구한다. p ≥ 0.05이면 "모델 신호 없음"으로 표기한다.
3. **안정성**: 부트스트랩 200회로 SVM 가중치 부호의 일관성(%)과 상위 10위 진입 빈도를 구한다.
4. **검증셋 확인**: 발견셋에서 찾은 효과 방향이 검증셋(40%)에서도 같은지(RD 부호) 확인한다.
5. **개념 판정 등급**
   - `확정`: q < 0.1, 부호 일관성 ≥ 90%, 검증셋에서 같은 방향
   - `유력`: q < 0.25 또는 부호 일관성 ≥ 80%, 검증셋에서 같은 방향
   - `참고`: 나머지 중 RD > 0.15
   - `기각`: 그 외
6. **평가자별 모델**: 평가자별 라벨이 30건 이상이면 같은 분석을 반복한다.
   - **전원 통과 템플릿 = 각 평가자의 Pass 요구사항의 합집합**이다. 모든 평가자의 Pass 영역이 겹치는 곳에 들어가야 하므로 요구사항은 합쳐진다.
   - 평가자 간 충돌(한 명은 +, 다른 한 명은 −)은 리포트에 따로 표기한다.
7. **반사실(`counterfactual.py`)**
   - Fail 문서마다 선형 SVM 결정함수 `f(x) = w·x + b`가 0을 넘을 때까지 바꿀 개념을 고른다. 대상은 `actionable=true`인 이진 개념이다.
   - 고르는 방법: 없는데 w > 0인 것을 추가하고, 있는데 w < 0인 것을 제거한다. **|w|가 큰 순서로 하나씩 바꾼다.**
   - 이진 특징에서 각 변경의 이득이 독립이므로 이 탐욕 방식이 변경 개수 기준 최소해다.
   - 결과는 "Pass까지 필요한 보완 목록"이며, 전체 Fail 문서에서 자주 등장하는 보완 항목 순위도 함께 낸다.
- 산출물: `work/models/<group>/results.json`, `concept_verdicts.csv`, 차트 PNG(효과크기 forest plot, 가중치 막대)

### 8.6 S6 `template` — 명세 → docx/pptx
1. **섹션 골격**(결정론)
   - Pass(타깃=1) 문서에서 섹션별 존재율과 중앙 순서 위치를 구한다.
   - 존재율 ≥ `config.template.section_min_presence`(기본 0.7)인 섹션, 그리고 `확정`/`유력` 개념이 붙은 섹션을 포함한다.
   - 섹션별 권장 분량(페이지·슬라이드 수 중앙값)과 대표 표 열 구성(Pass 문서에서 가장 흔한 열 이름 집합)도 구한다.
2. **명세 작성**(Gemini PRO, `template_compose.v1.md`)
   - 입력은 섹션 골격, 등급이 붙은 개념, 효과 통계, 평가자 충돌 목록이다.
   - 출력은 `TemplateSpec`(§9)이다. **모든 필수 요소와 가이드 문장에 `evidence_ids`(개념 ID 또는 "skeleton")가 있어야 한다.** 근거 없는 항목은 검증에서 실패 처리하고 재요청한다.
   - 실제 내용(가짜 수치, 가짜 원인)은 쓰지 않는다. `[예: 불량률 추이 그래프 — 대책 전/후 ppm]`처럼 플레이스홀더로만 쓴다.
   - Gemini의 `response_schema`에는 `sections`와 `global_rules`만 담은 축소 스키마(`TemplateSpecLLM`)를 넘긴다. 자유형 `dict` 필드는 구조화 출력에서 거부될 수 있다. `meta`, `evidence`, 통계 값은 코드가 채운다.
3. **렌더링**
   - `render_docx.py`:
     - 기본 글꼴은 맑은 고딕으로 하고, `w:eastAsia`도 지정한다.
     - 표지(제목, 고객사, 문서번호, 작성/검토/승인란)를 넣는다.
     - 섹션마다 Heading 1과 회색 이탤릭 `[작성 가이드]` 문단을 넣는다. 필수 요소는 ☐ 체크박스 줄로 넣는다.
     - 표 요소는 지정된 열 이름으로 빈 표(3행)를 만들고, 그림 요소는 점선 상자와 캡션 플레이스홀더로 만든다.
     - 마지막에 "제출 전 체크리스트"(확정/유력 개념)를 넣는다.
   - `render_pptx.py`:
     - 16:9로 만든다. 표지 슬라이드 다음에 섹션별로 `slides_hint`만큼 슬라이드를 만든다.
     - 각 슬라이드는 제목, 플레이스홀더 박스(표, 그림, 텍스트 영역 배치), 발표자 노트에 작성 가이드와 근거를 넣는다.
     - 마지막 슬라이드에 체크리스트를 넣는다.
   - `template_spec.md`는 사람이 읽는 버전으로, 섹션 표와 근거 통계를 담는다.
4. 🔒 사람이 최종 검토한 뒤 배포한다.

### 8.7 `report` — `analysis_report.md`
섹션 순서:
1. 요약(그룹, 문서 수, 타깃 정의, 핵심 결론 3줄)
2. 데이터 품질(판독 불량, 누락, 매칭 실패)
3. 평가자 일치도와 엄격도
4. Pass를 가르는 요인(등급별 표 + forest plot)
5. 모델 성능과 순열검정
6. 평가자별 차이와 충돌
7. 자주 필요한 보완 항목(반사실)
8. 한계와 주의사항: 상관≠인과, 내용 품질은 판정하지 않음, 표본 수

### 8.8 S7 `diagnose` (Phase 7)
- `python -m svmtrial diagnose --pdf new.pdf --group 대책서__CUST_A`
- 처리: S1 → S2 → S4c(해당 그룹의 승인 개념) → SVM 점수 → 반사실 보완 목록 → `diagnose_<doc_id>.md`
- 평가자들이 이 문서를 실제로 평가하면 그 결과를 평가 시트에 추가해 재학습한다(피드백 루프).

---

## 9. 핵심 스키마 (`schemas.py`, pydantic v2)

```python
from typing import Literal, Optional
from pydantic import BaseModel, Field

BlockType = Literal["heading","paragraph","bullet","table","image","photo","chart","diagram",
                    "signature","stamp","form_field","other"]

class TableInfo(BaseModel):
    columns: list[str] = []
    n_rows: int = 0
    caption: Optional[str] = None

class Block(BaseModel):
    type: BlockType
    level: Optional[int] = Field(None, description="heading일 때 1~3")
    text: str = ""                       # 표/그림이면 요약 설명
    table: Optional[TableInfo] = None

class PageLayout(BaseModel):
    page_role: Literal["cover","body","appendix","blank"]
    orientation: Literal["portrait","landscape"]
    looks_like: Literal["document","slide","form","spreadsheet","other"]
    blocks: list[Block]
    legibility: float = Field(ge=0, le=1)

class Concept(BaseModel):
    id: str                               # 예: C012
    question: str                         # "D4에 5Why(또는 동등한 단계적 원인분석) 표가 있는가?"
    section_id: Optional[str] = None
    type: Literal["structure","content_completeness","format","quantitative"]
    actionable: bool
    rationale: str

class ConceptAnswer(BaseModel):
    id: str
    value: Literal["yes","no","na"]
    page: Optional[int] = None
    evidence_quote: Optional[str] = None

class Evidence(BaseModel):
    concept_id: str
    verdict: Literal["확정","유력","참고"]
    pass_rate_with: float
    pass_rate_without: float
    n_with: int
    n_without: int
    q_value: Optional[float] = None
    svm_weight: Optional[float] = None
    sign_stability: Optional[float] = None

class Element(BaseModel):
    kind: Literal["text","table","figure","photo","chart","signature","checklist"]
    title: str
    required: bool
    guidance: str                         # 작성 가이드(플레이스홀더 수준, 실제 내용 금지)
    table_columns: Optional[list[str]] = None
    evidence_ids: list[str]               # 비면 검증 실패

class Section(BaseModel):
    order: int
    section_id: str
    title: str
    required: bool
    presence_in_pass: float
    guidance: str
    elements: list[Element]
    slides_hint: Optional[int] = None
    pages_hint: Optional[float] = None
    evidence_ids: list[str]

class TemplateSpec(BaseModel):
    group: str
    doc_format: Literal["docs","slides"]
    target_definition: str
    n_docs: int
    n_target_pass: int
    agreement_alpha: Optional[float]
    sections: list[Section]
    global_rules: list[Element]           # 문서 전체 규칙(분량, 서명란 등)
    rater_conflicts: list[str] = []
    caveats: list[str]
    evidence: list[Evidence]
    meta: dict                            # run_id, 모델 ID, 프롬프트 버전, 생성시각
```

---

## 10. 실행 환경별 안내

| 환경 | 용도 | 비고 |
|---|---|---|
| Ubuntu 22.04 (개발) | 개발, 실데이터 분석 | GPU 불필요. 연산은 CPU 수 초~수 분 수준이고, 시간의 대부분은 Gemini 호출 대기 |
| Windows (GPU 없음) | 배포본 실행 | 아래 §11 |
| **Colab Enterprise / Vertex AI Workbench** | 대안 | 같은 GCP 프로젝트 안에서 돌아 데이터가 회사 프로젝트 밖으로 나가지 않는다. 인증은 런타임 서비스계정으로 자동 처리된다. `pip install -r requirements.txt`를 실행하고, 데이터는 회사 GCS 버킷에서 읽는다 |
| 개인 Colab(무료/Pro) | **사용 금지 권장** | 고객사 문서가 개인 Drive로 업로드되므로 보안상 부적절 |

---

## 11. Windows 배포 (zip)

### 11.1 `scripts/build_release.py` (Ubuntu에서 실행)
- 다음을 `dist/valeo_svmtrial_<version>.zip`으로 묶는다: `src/`, `prompts/`, `config/`, `requirements.txt`, `environment.yml`, `.env.example`, `windows/*`, 이 SETUP.md.
- 제외: R3의 모든 항목.
- 옵션 `--with-wheels`: 사내망에서 pip 설치가 막힌 Windows용으로, wheel을 미리 받아 `wheelhouse/`(약 160 MB)에 넣는다.
  ```bash
  pip download --platform win_amd64 --python-version 3.11 --only-binary=:all: -r requirements.txt -d build/wheelhouse
  ```
- 빌드 후 zip 목록을 출력하고, `.env`나 키 파일이 들어가지 않았는지 자동으로 검사한다(들어갔으면 실패 처리).

### 11.2 `windows/setup_windows.bat` (Anaconda Prompt에서 실행)
```bat
@echo off
chcp 65001 >nul
cd /d %~dp0..
call conda env list | findstr /b "valeo_svm " >nul
if %errorlevel%==0 (call conda env update -n valeo_svm -f environment.yml) else (
  if exist wheelhouse (
    call conda create -y -n valeo_svm python=3.11 pip
    call conda run -n valeo_svm pip install --no-index --find-links wheelhouse -r requirements.txt
  ) else (call conda env create -f environment.yml)
)
if not exist .env copy .env.example .env
call conda run -n valeo_svm python scripts\smoke_test.py
echo.
echo [다음 단계] 1) .env 편집  2) gcloud auth application-default login  3) run_windows.bat check
pause
```

### 11.3 `windows/run_windows.bat`
```bat
@echo off
chcp 65001 >nul
set PYTHONUTF8=1
cd /d %~dp0..
if "%1"=="check" (call conda run --no-capture-output -n valeo_svm python scripts\check_gemini.py & goto :eof)
call conda run --no-capture-output -n valeo_svm python -m svmtrial %*
```
사용 예: `run_windows.bat all --group 대책서__CUST_A`, `run_windows.bat diagnose --pdf C:\docs\new.pdf --group ...`

### 11.4 Windows 사용자 준비물
- Anaconda(또는 Miniconda)
- Google Cloud CLI(인증 방식 A일 때)
- IAM `roles/aiplatform.user`
- 경로에 한글이 있어도 동작해야 한다(PYTHONUTF8). 단, 260자 경로 제한에 주의한다.

---

## 12. 개발 순서 (Phase별 완료 기준)

| Phase | 작업 | 완료 기준 |
|---|---|---|
| **0 환경** | 폴더 구조, `CLAUDE.md`, requirements/env 파일, `.gitignore`, `git init`, `scripts/smoke_test.py`(§13.1) | `conda env create` 성공, smoke test 전체 OK |
| **1 Gemini** | `config.py`, `gemini_client.py`, `cache.py`, `scripts/check_gemini.py`(§13.2) | 모델 목록 출력, 이미지+JSON 테스트 호출 성공, 캐시 적중 시 재호출 없음 |
| **2 더미+S1** | `scripts/make_dummy_data.py`(§13.3), `ingest.py`, CLI `ingest` | 더미 PDF 80개 → PNG와 meta 생성. CUST_A=docs, CUST_B=slides로 판정 |
| **3 S2** | `ocr.py`, `prompts/ocr_page.v1.md`, `--dry-run` | 더미 5개 실호출 성공, 제목 계층과 표 추출 확인, 비용 추정 출력 |
| **4 S3** | `labels.py`(long/wide, 정규화, κ/α, Dawid-Skene) | 단위 테스트 통과, 더미에서 rater_E가 가장 엄격하게 나옴 |
| **5 S4** | sections, concepts discover → 🔒 → score | 더미에서 5Why / 효과검증 그래프 / 수평전개 표 개념이 후보에 포함됨 |
| **6 S5** | modeling, counterfactual, 차트 | 더미의 숨김 규칙 3개가 `확정` 또는 `유력`으로 판정됨, 순열검정 p < 0.05 |
| **7 S6+S7** | template_spec, render_docx/pptx, report, diagnose | docx와 pptx가 Word/PowerPoint에서 정상적으로 열림(한글 깨짐 없음), 모든 요소에 근거 ID 있음 |
| **8 배포** | build_release, bat 파일 | Windows PC에서 setup → check → 더미 `all` 실행 성공 |
| **9 실데이터** | 실제 대책서 그룹 1개로 전체 실행 | 리포트 검토 회의, 피드백 반영 |

CLI 명령 (typer):
`python -m svmtrial ingest|ocr|labels|sections|concepts discover|concepts score|model|template|report|diagnose|all [--group G] [--dry-run] [--force]`
- `all`은 🔒 관문에서 승인 파일이 없으면 그 지점에서 멈추고 안내 메시지를 출력한다.

---

## 13. 바로 쓰는 스크립트 (검증 완료)

### 13.1 `scripts/smoke_test.py` — 환경 점검 (Gemini 호출 없음)
```python
"""환경 점검: 모든 핵심 라이브러리 import + 최소 동작 확인 (Gemini 호출 없음)."""
import sys, tempfile, pathlib, warnings
warnings.filterwarnings("ignore")
ok = lambda m: print(f"[OK] {m}")
tmp = pathlib.Path(tempfile.mkdtemp())

assert sys.version_info[:2] == (3, 11), f"Python 3.11 필요, 현재 {sys.version}"
ok(f"python {sys.version.split()[0]}")

import numpy as np, pandas as pd
ok(f"numpy {np.__version__} / pandas {pd.__version__}")

# 1) PDF -> 이미지 (Poppler 불필요)
import pymupdf as fitz
from PIL import Image
doc = fitz.open(); page = doc.new_page(width=842, height=595)  # 가로형(슬라이드 비율)
page.insert_text((72, 72), "Dummy 8D report", fontsize=20)
pdf = tmp / "t.pdf"; doc.save(pdf); doc.close()
d = fitz.open(pdf); pix = d[0].get_pixmap(dpi=150); png = tmp / "p1.png"; pix.save(png)
w, h = Image.open(png).size
ok(f"PyMuPDF {fitz.VersionBind} render {w}x{h}, text_layer={bool(d[0].get_text().strip())}")

# 2) 평가자 일치도
from statsmodels.stats.inter_rater import fleiss_kappa, aggregate_raters
import krippendorff
r = np.array([[1, 1, 1, 0, 1], [0, 0, 1, 0, 0], [1, 1, 1, 1, 1], [0, 1, 0, 0, 0]])
k = fleiss_kappa(aggregate_raters(r)[0])
a = krippendorff.alpha(reliability_data=r.T, level_of_measurement="nominal")
ok(f"fleiss_kappa={k:.3f}, krippendorff_alpha={a:.3f}")

# 3) 분류·규칙 모델
from sklearn.svm import LinearSVC
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import RepeatedStratifiedKFold, cross_val_score
from scipy.stats import fisher_exact
from statsmodels.stats.multitest import multipletests
rng = np.random.default_rng(0)
X = pd.DataFrame(rng.integers(0, 2, (60, 8)), columns=[f"c{i}" for i in range(8)])
y = ((X.c0 + X.c3 + rng.integers(0, 2, 60)) >= 2).astype(int)
svm = LinearSVC(C=0.5, class_weight="balanced").fit(X, y)
s = cross_val_score(LinearSVC(class_weight="balanced"), X, y, cv=RepeatedStratifiedKFold(n_splits=5, n_repeats=2, random_state=0), scoring="balanced_accuracy")
LogisticRegression(penalty="l1", solver="liblinear", class_weight="balanced").fit(X, y)
p = [fisher_exact(pd.crosstab(X[c], y))[1] for c in X]
multipletests(p, method="fdr_bh")
ok(f"LinearSVC cv bal_acc={s.mean():.2f}, top weight={X.columns[np.argmax(svm.coef_[0])]}")
from imodels import RuleFitClassifier
rf = RuleFitClassifier(max_rules=10, random_state=0).fit(X.values, y.values, feature_names=list(X.columns))
ok(f"imodels RuleFit rules={len(rf._get_rules())}")

# 4) 출력 문서
from docx import Document
from docx.oxml.ns import qn
dx = Document(); st = dx.styles["Normal"]; st.font.name = "Malgun Gothic"
st.element.rPr.rFonts.set(qn("w:eastAsia"), "맑은 고딕")
dx.add_heading("1. 문제 정의 (D2)", level=1); dx.add_paragraph("[작성 가이드] 예시")
t = dx.add_table(rows=2, cols=3); t.style = "Table Grid"; dx.save(tmp / "t.docx")
from pptx import Presentation
from pptx.util import Inches
pr = Presentation(); pr.slide_width, pr.slide_height = Inches(13.333), Inches(7.5)
sl = pr.slides.add_slide(pr.slide_layouts[5]); sl.shapes.title.text = "근본원인 분석"; pr.save(tmp / "t.pptx")
import docxtpl, openpyxl  # noqa: F401
ok("python-docx / python-pptx / docxtpl / openpyxl")

# 5) Gemini SDK (호출 없이 타입만)
from google import genai
from google.genai import types
from pydantic import BaseModel
class Probe(BaseModel):
    ok: bool
cfg = types.GenerateContentConfig(temperature=0, response_mime_type="application/json", response_schema=Probe)
part = types.Part.from_bytes(data=png.read_bytes(), mime_type="image/png")
ok(f"google-genai {genai.__version__ if hasattr(genai, '__version__') else ''} types OK")

import typer, rich, tenacity, yaml, dotenv, jinja2, matplotlib  # noqa: F401
ok("typer / rich / tenacity / yaml / dotenv / jinja2 / matplotlib")
print("\nALL SMOKE TESTS PASSED")
```

### 13.2 `scripts/check_gemini.py` — Vertex AI 연결 점검
```python
"""Vertex AI(Gemini) 연결 점검: 인증 → 모델 목록 → 이미지+구조화 출력 1회 호출.
사용: python scripts/check_gemini.py            (모델 목록 + 테스트 호출)
      python scripts/check_gemini.py --list-only
"""
import argparse, io, os, sys
from dotenv import load_dotenv
from pydantic import BaseModel

load_dotenv()
PROJECT = os.getenv("GOOGLE_CLOUD_PROJECT")
LOCATION = os.getenv("GOOGLE_CLOUD_LOCATION", "global")
MODEL = os.getenv("GEMINI_MODEL_FAST")


class Probe(BaseModel):
    text_seen: str
    is_slide_like: bool


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--list-only", action="store_true"); a = ap.parse_args()
    if not PROJECT:
        sys.exit("[X] .env 에 GOOGLE_CLOUD_PROJECT 가 없습니다.")
    import google.auth
    try:
        creds, detected = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        print(f"[OK] 인증 정보 발견 ({type(creds).__name__}), ADC 프로젝트={detected}")
    except Exception as e:
        sys.exit(f"[X] 인증 실패: {e}\n  → 'gcloud auth application-default login' 실행 또는 "
                 "GOOGLE_APPLICATION_CREDENTIALS(서비스계정 JSON 경로) 설정")

    from google import genai
    from google.genai import types
    client = genai.Client(vertexai=True, project=PROJECT, location=LOCATION)  # vertexai= 는 enterprise= 와 동일(구 명칭)
    try:
        names = sorted({m.name.split("/")[-1] for m in client.models.list(config={"query_base": True, "page_size": 200})
                        if m.name and "gemini" in m.name})
        print(f"[OK] 사용 가능한 Gemini 모델 ({PROJECT}/{LOCATION}):"); [print("   -", n) for n in names]
    except Exception as e:
        print(f"[!] 모델 목록 조회 실패(권한 부족일 수 있음, 호출은 될 수도 있음): {e}")
    if a.list_only:
        return
    if not MODEL:
        sys.exit("[X] .env 에 GEMINI_MODEL_FAST 를 위 목록 중 하나로 넣고 다시 실행하세요.")

    from PIL import Image, ImageDraw
    img = Image.new("RGB", (1600, 900), "white"); ImageDraw.Draw(img).text((100, 400), "D4 Root cause - 5 Why", fill="black")
    buf = io.BytesIO(); img.save(buf, "PNG")
    resp = client.models.generate_content(
        model=MODEL,
        contents=[types.Part.from_bytes(data=buf.getvalue(), mime_type="image/png"),
                  "이미지에 보이는 글자와, 슬라이드(가로형) 형태인지 판단해 JSON으로 답하라."],
        config=types.GenerateContentConfig(temperature=0, response_mime_type="application/json", response_schema=Probe),
    )
    print("[OK] 응답:", resp.parsed)
    u = resp.usage_metadata
    print(f"[OK] 토큰: 입력 {u.prompt_token_count} / 출력 {u.candidates_token_count}")
    print("\nGEMINI 연결 정상 — Gemini CLI 없이 Python SDK로 동작합니다.")


if __name__ == "__main__":
    main()
```
> 모델 목록 조회가 권한 문제로 실패하면, IT에서 받은 모델 ID를 `.env`에 직접 넣고 테스트 호출만 진행한다.

### 13.3 `scripts/make_dummy_data.py` — 가짜 이미지 PDF + 평가 시트
```python
"""가짜 '이미지 전용' PDF + 평가 시트를 만든다 (실데이터 없이 파이프라인 시험용).

정답 규칙(숨김): 5Why 근본원인 / 효과검증 그래프 / 수평전개 표 가 있을수록 Pass 확률↑.
파이프라인이 이 3가지를 다시 찾아내면 정상 동작으로 본다.
사용: python scripts/make_dummy_data.py --out data/dummy --n 40
"""
import argparse, io, random
from pathlib import Path
import pandas as pd
import pymupdf
from PIL import Image, ImageDraw, ImageFont

SECTIONS = ["D1 Team", "D2 Problem description (5W2H)", "D3 Containment action",
            "D4 Root cause", "D5 Corrective action", "D6 Effectiveness check",
            "D7 Prevent recurrence", "D8 Closure"]
HIDDEN = {"why5": "5-Why analysis table: Why1 > Why2 > Why3 > Why4 > Why5",
          "graph": "[Chart] Defect rate before/after (ppm) - trend graph",
          "yokoten": "Horizontal deployment table: Line | Model | Applied date | Owner"}
RATERS = ["rater_A", "rater_B", "rater_C", "rater_D", "rater_E"]


def page_image(lines, landscape):
    w, h = (1600, 900) if landscape else (1240, 1754)
    img = Image.new("RGB", (w, h), "white"); d = ImageDraw.Draw(img)
    try:
        font = ImageFont.load_default(size=28)
    except TypeError:  # 구버전 Pillow
        font = ImageFont.load_default()
    y = 60
    for ln in lines:
        if ln.startswith("[Chart]"):
            d.rectangle([80, y, w - 80, y + 220], outline="black", width=3)
            d.line([100, y + 200, w // 2, y + 60, w - 100, y + 180], fill="black", width=3)
        d.text((80, y), ln, fill="black", font=font); y += 260 if ln.startswith("[Chart]") else 50
    buf = io.BytesIO(); img.save(buf, "PNG"); return buf.getvalue()


def make_pdf(path, landscape, feats, rnd):
    doc = pymupdf.open()
    secs = [s for s in SECTIONS if rnd.random() > 0.15 or s.startswith(("D2", "D4", "D5"))]
    per_page = 2 if landscape else 4
    for i in range(0, len(secs), per_page):
        lines = []
        for s in secs[i:i + per_page]:
            lines += [s, "  lorem ipsum dolor sit amet " * 2]
            if s.startswith("D4") and feats["why5"]: lines.append(HIDDEN["why5"])
            if s.startswith("D6") and feats["graph"]: lines.append(HIDDEN["graph"])
            if s.startswith("D7") and feats["yokoten"]: lines.append(HIDDEN["yokoten"])
        png = page_image(lines, landscape)
        pw, ph = (842, 474) if landscape else (595, 842)
        page = doc.new_page(width=pw, height=ph)
        page.insert_image(page.rect, stream=png)  # 텍스트 레이어 없음 = 스캔본과 동일 조건
    doc.save(path); doc.close()


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--out", default="data/dummy"); ap.add_argument("--n", type=int, default=40)
    ap.add_argument("--seed", type=int, default=0); a = ap.parse_args()
    rnd = random.Random(a.seed); out = Path(a.out); rows = []
    for customer, doc_type, landscape in [("CUST_A", "대책서", False), ("CUST_B", "대책서", True)]:
        pdir = out / "pdfs" / doc_type / customer; pdir.mkdir(parents=True, exist_ok=True)
        for k in range(a.n):
            feats = {f: rnd.random() < 0.5 for f in HIDDEN}
            doc_id = f"{customer}_{k:03d}"; make_pdf(pdir / f"{doc_id}.pdf", landscape, feats, rnd)
            p = 0.15 + 0.25 * sum(feats.values())          # 숨김 규칙
            for r in RATERS:
                strict = 0.1 if r == "rater_E" else 0.0        # 한 명은 더 깐깐함
                rows.append({"doc_id": doc_id, "customer": customer, "doc_type": doc_type, "rater": r,
                             "result": "Pass" if rnd.random() < p - strict else "Fail"})
    lab = out / "labels"; lab.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_excel(lab / "labels_long.xlsx", index=False)
    print(f"PDF {2 * a.n}개, 평가 {len(rows)}행 → {out}")


if __name__ == "__main__":
    main()
```
> 숨김 정답(feats)은 Phase 6 검증용으로 `data/dummy/truth.csv`에도 저장하도록 Claude Code가 확장한다.

---

## 14. 설정 파일 `config/config.yaml` (초기값)
```yaml
paths:
  raw_pdfs: data/raw/pdfs
  labels: data/raw/labels
  work: work
  outputs: outputs
labels:
  columns: {doc_id: doc_id, customer: customer, doc_type: doc_type, rater: rater, result: result}
  pass_values: [Pass, P, OK, O, "○", 합격, "1", Y]
  fail_values: [Fail, F, NG, X, "×", 불합격, "0", N]
ingest:
  dpi: 150
  landscape_ratio: 1.25
gemini:
  model_fast: ${GEMINI_MODEL_FAST}
  model_pro: ${GEMINI_MODEL_PRO}
  max_workers: 4
  max_retries: 6
  media_resolution_ocr: HIGH
  cost_per_1m_tokens: {}          # 사내 단가 확인 후 입력(선택)
analysis:
  target: unanimous               # unanimous | majority | ratio | ds
  pool_customers: false
  split: {discovery: 0.6, seed: 42}
  discovery: {rounds: 5, k_per_class: 4, max_hypotheses: 15}
  min_rater_labels: 30
  cv: {n_splits: 5, n_repeats: 10}
  n_permutations: 500
  n_bootstrap: 200
  scoring_recheck_ratio: 0.1
  scoring_min_agreement: 0.85
template:
  section_min_presence: 0.7
  font: 맑은 고딕
  pptx_size: 16x9
```

---

## 15. 확인 필요 사항 (IT/상사, 실데이터 투입 전)

| # | 항목 | 왜 필요한가 |
|---|---|---|
| 1 | GCP 프로젝트 ID, 사용 가능한 Gemini 모델 ID | `.env` 기입 |
| 2 | 리전: `global` vs `asia-northeast3`(서울) | 데이터 처리 위치 정책. 최신 모델은 global에만 있을 수 있음 |
| 3 | 인증 방식 A(ADC) 또는 B(서비스계정 키) | Windows 배포 절차가 달라짐 |
| 4 | **고객사 문서를 Vertex AI로 보내도 되는지** (고객사 NDA 포함) | 금지되면 OCR을 로컬로 바꿔야 함(v2) |
| 5 | Vertex 할당량(분당 요청 수)과 예산 | `max_workers`, 실행 시간 |
| 6 | 사내 프록시/SSL 검사 여부 | pip, conda, Gemini 호출 설정 (§16) |
| 7 | 평가 시트 열 구성과 평가자 명단 | `config.labels.columns` |
| 8 | 문서 유형×고객사별 문서 수(특히 Fail 수) | 데이터 양 가드(§8.5) 판단 |

---

## 16. 문제 해결

| 증상 | 조치 |
|---|---|
| pip/conda 설치 시 SSL 오류 | `pip config set global.cert C:\certs\corp-ca.pem`, `conda config --set ssl_verify C:\certs\corp-ca.pem` |
| 사내망에서 pip가 차단됨 | `build_release.py --with-wheels`로 만든 zip 사용(오프라인 설치) |
| Gemini 호출 시 SSL/프록시 오류 | `.env`에 `HTTPS_PROXY`, `SSL_CERT_FILE` 설정 |
| `DefaultCredentialsError` | `gcloud auth application-default login`을 실행하거나 `GOOGLE_APPLICATION_CREDENTIALS` 경로 확인 |
| `403 PERMISSION_DENIED` | 계정에 `roles/aiplatform.user`가 있는지, API(`aiplatform.googleapis.com`)가 사용 설정되어 있는지 확인 |
| `404 model not found` | 모델 ID나 리전이 맞지 않음 → `check_gemini.py --list-only` 실행 |
| `429 RESOURCE_EXHAUSTED` | `max_workers`를 낮춤. 재시도는 자동이며 캐시 덕분에 이어서 실행됨 |
| Windows에서 한글이 깨짐 | bat 파일에 `chcp 65001`과 `PYTHONUTF8=1`이 있는지 확인. 파일 쓰기에 `encoding="utf-8"`을 지정했는지 확인 |
| docx 한글 글꼴이 이상함 | `rFonts w:eastAsia`가 지정됐는지 확인(§13.1 예시) |

---

## 17. 참고 자료
- HypoGeniC (LLM 가설 생성): https://github.com/ChicagoHAI/hypothesis-generation
- D5 (두 텍스트 집합 차이 설명): https://github.com/ruiqi-zhong/D5
- Text Concept Bottleneck: https://arxiv.org/abs/2310.19660
- Google Gen AI SDK: https://github.com/googleapis/python-genai
- Vertex AI 모델 버전·수명: https://docs.cloud.google.com/vertex-ai/generative-ai/docs/learn/model-versions
- Gemini CLI 인증 문서(CLI 불필요 판정 근거): https://geminicli.com/docs/get-started/authentication/
- imodels: https://github.com/csinva/imodels
- python-docx / python-pptx / PyMuPDF 공식 문서
