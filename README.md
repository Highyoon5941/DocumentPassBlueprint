# DocumentPassBlueprint

> 문서 Pass 조건 역추적 → 표준 템플릿 생성 도구 (SVM + Gemini 하이브리드)
> 내부 코드명 `Valeo_SVMtrial` · Python 3.11 · CPU 전용

평가자 여러 명이 Pass/Fail 만 남긴 문서 데이터에서 **"무엇이 합격을 가르는가"** 를 역추적하고,
그 결과로 **평가자 전원을 통과할 표준 문서 템플릿**(`.docx` / `.pptx`)을 만든다.

---

## 무엇을 푸는 문제인가

같은 종류의 문서(대책서 등) 여러 건이 있고, 평가자 여러 명이 각각 Pass/Fail 을 매겼다.
**사유는 남아 있지 않다.** 왜 어떤 문서는 5명 전원이 합격시켰고 어떤 문서는 4명만 합격시켰는지 아무도 적어두지 않았다.

이 도구는 Pass/Fail **예측기를 만드는 것이 목적이 아니다.** Pass/Fail 데이터는 수단이다.

> "평면에 Pass 와 Fail 점이 찍혀 있고 경계선의 방정식은 모른다.
> 결과로 경계선을 찾고, Fail 문서가 Pass 쪽으로 가려면 무엇을 바꿔야 하는지 가이드한다."

| 비유 | 구현 |
|---|---|
| 평면 위의 점 | 문서 1건 = 특징 벡터 **x** (사람이 읽을 수 있는 예/아니오 개념 0/1 + 구조 수치) |
| 경계선의 방정식 | 선형 SVM 의 결정함수 `f(x) = w·x + b` |
| "무엇을 바꿔야 하나" | 반사실(counterfactual) — `f(x) > 0` 이 될 때까지 바꿀 항목의 **최소 집합** |
| 가이드 | 가중치 `w` 가 큰 요소 → "이것을 넣어라" 라는 템플릿 문장 |

### 핵심 설계: Text Concept Bottleneck

문장 임베딩을 특징으로 쓰면 `w` 의 342번째 성분이 무슨 뜻인지 알 수 없어 템플릿 문장을 만들 수 없다.
그래서 특징을 **사람이 읽고 수정할 수 있는 예/아니오 질문**으로 둔다.

```
❌ 임베딩:  x = [0.0231, -0.842, 0.117, ...]      → w 를 문장으로 못 바꾼다
✅ 개념:    C001 = 'D4 섹션에 "5Why 단계적 원인분석 표" 가 있는가?'
            w[C001] = +0.23   →   "D4 에 5Why 표를 넣어라"
```

목표가 템플릿이므로 **해석 가능성이 성능보다 우선한다.**

---

## 입력과 출력

| 구분 | 내용 |
|---|---|
| 입력 1 | 같은 성격의 문서 PDF 여러 개. **이미지 기반(스캔본)을 기본 전제**로 한다 |
| 입력 2 | 평가 시트(xlsx). 문서별로 평가자 여러 명이 Pass/Fail 을 매겼고 사유는 없다 |
| 출력 1 | `template_spec.json` / `.md` — 섹션 구조, 필수 요소, 작성 가이드, 근거 통계 |
| 출력 2 | `template.docx` 또는 `template.pptx` — 바로 쓰는 템플릿 (원본 형식에 맞춤) |
| 출력 3 | `analysis_report.md` — 평가자 일치도, Pass를 가르는 요인, 신뢰도, 한계 |
| 출력 4 | `diagnose_<doc_id>.md` — 새 문서 1건이 Pass 쪽으로 가려면 무엇을 보완해야 하는지 |

---

## 파이프라인

```
 PDF (스캔본)  ─S1 ingest───▶ 페이지 PNG + 메타 + 원본형식(docs/slides) 판정
                    │
                    └─S2 ocr (Gemini Flash, 페이지별)──▶ 제목계층·블록·표·그림·서명
                                                        │
 평가 xlsx ─S3 labels──▶ Fleiss κ / Krippendorff α / Dawid-Skene / 평가자 엄격도
                         타깃: y_unanimous | y_majority | y_ds | pass_ratio
                                                        │
        S4a sections : 제목 → 표준 섹션 분류체계 (8D seed)      Gemini Pro + 🔒
        S4b concepts : Pass/Fail 대조 → 예/아니오 개념 가설      Gemini Pro + 🔒
                       (발견셋 60% 에서만 — 누수 방지)
        S4c score    : 문서 × 개념 0/1 행렬 + 결정론 구조 특징   Gemini Flash
                                                        │
        S5 model     : 단변량(Fisher + BH-FDR) + 선형SVM/L1로지스틱/트리/RuleFit
                       + 교차검증 + 순열검정 + 부트스트랩 안정성
                       + 평가자별 모델 + 반사실(최소 보완 집합)
                                                        │
        S6 template  : 섹션 골격(결정론) + 명세 문구(Gemini Pro, 근거 ID 필수)
                       → docx / pptx + analysis_report.md        → 🔒 최종 검토
        S7 diagnose  : 새 문서 → 점수 + 부족한 항목 (피드백 루프)
```

### 통계적 누수를 막는 장치 4겹

| 장치 | 내용 |
|---|---|
| 발견/검증 분할 | 타깃 기준 층화. **발견셋 60% 에서만 가설 생성**, 검증셋 40% 는 방향 확인용 |
| 다중비교 보정 | Fisher 정확검정 p → BH-FDR 보정 → q 값으로 판단 |
| 순열검정 | 라벨을 500번 섞어 성능 분포와 비교. `p ≥ 0.05` 면 **"모델 신호 없음"** 으로 표기 |
| 부트스트랩 안정성 | 200회 재표본으로 SVM 가중치 **부호** 일관성 측정 |

여기에 **데이터 양 가드**가 더해진다. `소수 클래스 < 10 × 특징 수` 이면 다변량 결과를 "탐색적"으로만 표기한다.

### 사람 검토 관문 (🔒)

자동화하면 안 되는 판단 세 곳. **승인 파일이 없으면 다음 단계가 실행되지 않는다.**

1. 섹션 분류체계 — 이 문서 종류의 표준 섹션이 무엇인가
2. 개념 후보 — 각 가설이 문서만 보고 답할 수 있는가, 내용 타당성 판단이 섞이지 않았는가
3. 최종 템플릿 — 배포해도 되는가

`all` 파이프라인은 관문에서 **멈추고 안내 메시지를 출력한다.** 승인은 사람이 `approve` 명령으로 명시적으로 한다.

---

## 빠른 시작 (더미 데이터)

```bash
conda env create -f environment.yml      # 환경 이름: Valeo_SVM_Trial
conda activate Valeo_SVM_Trial
pip install -e .
cp .env.example .env
unset PYTHONPATH                         # ⚠ 아래 "알려진 함정" 참고

python scripts/smoke_test.py             # ALL SMOKE TESTS PASSED
python scripts/make_dummy_data.py --out data/dummy --n 40

# 더미 경로를 가리키는 config 사본
sed -e 's|data/raw/pdfs|data/dummy/pdfs|' -e 's|data/raw/labels|data/dummy/labels|' \
    config/config.yaml > /tmp/config_dummy.yaml
export CFG=/tmp/config_dummy.yaml  G=대책서__CUST_A

python -m svmtrial all -g $G --config $CFG          # 🔒 섹션 관문에서 멈춤
python -m svmtrial approve -g $G --gate sections --config $CFG
python -m svmtrial all -g $G --config $CFG          # 🔒 개념 관문에서 멈춤
python -m svmtrial approve -g $G --gate concepts --config $CFG
python -m svmtrial all -g $G --config $CFG          # 완주

python scripts/verify_dummy.py -g $G --config $CFG  # 숨김 정답 복원 검증
```

더미 데이터에는 **숨겨진 정답 규칙 3개**(5Why 표 / 효과검증 그래프 / 수평전개 표)와
**영향 없는 잡음 2개**(불량품 사진 / 팀 명단 표)가 섞여 있다.
파이프라인이 앞의 3개를 찾아내고 뒤의 2개를 걸러내면 정상 동작이다.

자세한 실행 방법은 **[`docs/Runbook.md`](docs/Runbook.md)** 를 보세요.

### 한글 샘플 데이터 (권장)

`Valeo_SVMtrial_sample_data/` 에 **가상의 한글 8D 대책서 82건 + 5인 평가 시트 410행**과
채점용 정답지가 있다. 영문 더미보다 실데이터에 가깝다 — 이미지 전용 스캔본, 표기 혼재
(Pass/합격/P/OK, Fail/불합격/F/NG), 빈칸 10개, **의도된 매칭 오류 4건**.

```bash
cp -r Valeo_SVMtrial_sample_data/data/raw/. data/raw/
python -m svmtrial ingest && python -m svmtrial labels
```

PDF 바이너리(40MB)는 git 에서 제외돼 있고 생성 스크립트로 **같은 내용이 재현된다**.

```bash
python Valeo_SVMtrial_sample_data/tools/make_sample_data.py     --out Valeo_SVMtrial_sample_data --n 40 --seed 7
```

S1/S3 검증 결과와 채점 기준 대조표는 [`docs/Runbook.md` §4bis](docs/Runbook.md) 에 있다.
**S2 OCR 부터는 `vertex` 백엔드가 필요하다** (실 스캔 이미지는 offline 스텁이 읽지 못한다).

---

## 문서

| 문서 | 내용 |
|---|---|
| **[`docs/Architecture.md`](docs/Architecture.md)** | 전체 아키텍처와 기본 개념 — Concept Bottleneck, 통계 설계의 근거, 모듈 지도, 알고 있는 한계 |
| **[`docs/Runbook.md`](docs/Runbook.md)** | 실행 방법 — 명령 목록, 더미/실데이터 절차, 🔒 관문에서 무엇을 봐야 하는지, 산출물 읽는 법, 문제 해결 |
| **[`docs/migration_vertexAI.md`](docs/migration_vertexAI.md)** | 사내망 Vertex AI 전환 — **§1 에 사람이 직접 받아와야 하는 항목(M1~M16)** 과 IT 질문지 |
| [`docs/Valeo_SVMtrial_SETUP.md`](docs/Valeo_SVMtrial_SETUP.md) | 설계 기준 문서. 모든 결정의 출처 |
| [`docs/alternatives_withoutLLM.md`](docs/alternatives_withoutLLM.md) | **제안서** — LLM 없이: 로컬 OCR 스택 + SVM 대체 모델(계층 로지스틱·순서형·단조·SLIM 점수표·규칙) |
| [`docs/alternatives_withLLM.md`](docs/alternatives_withLLM.md) | **제안서** — LLM 사용 + SVM 제거: 평가자 페르소나 시뮬레이션, 루브릭, 쌍대비교, 사례기반 |
| [`CLAUDE.md`](CLAUDE.md) | 작업 규칙, 현재 Phase, 설계 기준과 달라진 점 |

---

## ⚠ 백엔드 — 이 저장소의 기본값은 실데이터용이 아니다

`.env` 의 `SVMTRIAL_BACKEND` 가 Gemini 호출 경로를 고른다.

| 값 | 동작 | 용도 |
|---|---|---|
| `offline` | Gemini 를 호출하지 않고 결정론 규칙으로 **같은 스키마**의 응답을 만든다 | **기본값.** GCP 접근이 없는 개발 환경에서 전체 파이프라인 검증 |
| `vertex` | `google-genai` 로 실제 Vertex AI 호출 | 사내망 실업무 |

두 백엔드가 같은 pydantic 스키마를 돌려주므로 **전환 시 코드는 한 줄도 바뀌지 않는다** (`.env` 만 수정).
캐시 키에 백엔드 이름이 들어가므로 offline 스텁 응답이 vertex 전환 후 재사용되는 일은 없다.

> **`offline` 로는 실데이터를 분석할 수 없다.** OCR 스텁이 더미 데이터의 fixture 를 조회할 뿐 실제 이미지를 읽지 못한다.
> 실데이터에서는 모든 페이지가 판독성 0 이 되고, `diagnose` 는 **"판정 불가"** 로 막는다.
> 전환 절차: **[`docs/migration_vertexAI.md`](docs/migration_vertexAI.md)**

### 데이터 취급

- 문서와 이미지는 **`.env` 에 설정된 회사 GCP 프로젝트의 Vertex AI 엔드포인트로만** 보낸다. 다른 API·웹서비스·텔레메트리로는 보내지 않는다
- 모든 Gemini 호출은 `src/svmtrial/gemini_client.py` 한 곳을 지난다. `google-genai` 를 import 하는 모듈은 `vertex_backend.py` 하나뿐이다
- 평가 시트(평가자 이름·판정)는 **로컬에서만** 처리하며 외부로 보내지 않는다
- `data/` `work/` `outputs/` `logs/` `.env` 인증키는 git 과 배포 zip 에서 항상 제외된다 (빌드 시 자동 검사, 위반 시 **빌드 실패**)

---

## 주요 명령

```bash
python -m svmtrial <명령> [--group G] [--dry-run] [--force] [--config PATH]
```

| 명령 | 단계 | 하는 일 |
|---|---|---|
| `doctor` | — | 환경·설정 점검, 그룹 탐색 |
| `ingest` | S1 | PDF → 페이지 PNG + 메타 + 형식 판정 |
| `ocr` | S2 | 페이지 구조 추출 (`--limit N` 으로 일부만) |
| `labels` | S3 | 평가자 일치도, Dawid-Skene, 타깃 결정 |
| `sections` | S4a | 섹션 분류체계 후보 🔒 + 제목 매핑 |
| `concepts discover` | S4b | Pass/Fail 대조로 개념 가설 생성 🔒 |
| `concepts score` | S4c | 문서 × 개념 행렬 |
| `model` | S5 | 통계 분석 + 등급 판정 + 반사실 |
| `template` | S6 | 템플릿 명세 + docx/pptx 🔒 |
| `report` | — | `analysis_report.md` + 차트 |
| `diagnose` | S7 | 새 문서 1건 진단 |
| `all` | S1~S6 | 전체 실행 (🔒 에서 멈춤) |
| `approve` | 🔒 | 후보 → 승인 (**사람이 직접 실행**) |

`--dry-run` 은 Gemini 호출 없이 **호출 수와 예상 토큰만** 출력한다. 비용 추정 전에 쓴다.

---

## 프로젝트 구조

```
├─ src/svmtrial/        28개 모듈 — 파이프라인 + Gemini 경계 + 렌더러
├─ prompts/             8개 (파일명에 버전 포함, Jinja2 + 기계판독 데이터 블록)
├─ scripts/             smoke_test / check_gemini / make_dummy_data
│                       build_release / verify_dummy / run_tests.sh
├─ tests/               10개 모듈, 139개 — Gemini 는 전부 mock
├─ windows/             setup_windows.bat / run_windows.bat / README
├─ config/config.yaml   모든 하이퍼파라미터
├─ docs/                Architecture / Runbook / migration_vertexAI / SETUP
├─ data/                raw/pdfs/<doc_type>/<customer>/*.pdf , raw/labels/*.xlsx   (git 제외)
├─ work/                중간 산출물 + Gemini 응답 캐시                              (git 제외)
└─ outputs/<run_id>/<group>/   최종 산출물                                          (git 제외)
```

---

## 검증 상태

offline 백엔드 + 더미 데이터(PDF 80건 / 평가 400행) 기준:

| 항목 | 결과 |
|---|---|
| `python scripts/smoke_test.py` | ALL SMOKE TESTS PASSED |
| `./scripts/run_tests.sh` | **139 passed** (Gemini 호출 없음) |
| `ruff check src/ scripts/ tests/` | All checks passed |
| `python scripts/verify_dummy.py` | 두 그룹 모두 **합격** — 숨김 규칙 3개 전부 `유력` 이상, 순열검정 p=0.002, 잡음은 `확정` 아님 |
| 출력물 | `CUST_A` → `template.docx`, `CUST_B` → `template.pptx` (16:9). 모든 요소에 근거 ID 존재 (누락 0개) |

한글 샘플 데이터(PDF 82건 / 평가 410행) 기준 — 정답지 대조:

| 단계 | 결과 |
|---|---|
| S1 ingest | ✅ PDF 82개, 매칭 오류 4건, CUST_A→docs / CUST_B→slides, 텍스트 레이어 0 |
| S3 labels | ✅ 표기 8종+빈칸 10개 정규화, wide 형식 동일 결과, 전원합격률 0.350 |
| S3 엄격도 | ✅ B 0.747 > C 0.704 > E 0.675 > A 0.671 > **D 0.630(가장 엄격)** — 정답지와 일치 |
| S2~S6 | ⏸ offline 로는 검증 불가 (실 스캔 이미지). `vertex` 전환 후 수행 — `ocr --dry-run` = 568회 |

테스트는 **Gemini 를 호출하지 않는다.** 네트워크를 쓰려 하면 즉시 실패한다.
실제 호출 점검은 `scripts/check_gemini.py` 로만 한다.

---

## 알려진 함정

### `PYTHONPATH` 오염

개발 PC 에 ROS Humble 이 설치돼 있으면 `PYTHONPATH` 에 **python3.10** 경로가 주입된다.
python3.11 환경에서 그 패키지가 import 되면 재현 불가능한 오류가 난다.

```bash
unset PYTHONPATH          # 또는 PYTHONPATH= python -m svmtrial ...
```

- 일반 실행: 경고가 뜨지만 동작한다 (`config.check_interpreter_hygiene` 가 감지)
- **`pytest` 는 깨진다** (ROS 플러그인 자동 로드) → `./scripts/run_tests.sh` 를 쓸 것
- Windows 배포본의 `.bat` 에는 `set PYTHONPATH=` 가 들어 있다

### 방법론의 한계 (리포트에도 항상 함께 출력된다)

1. **상관관계는 인과관계가 아니다.** "Pass 문서에 더 자주 있었다" 가 "넣으면 합격한다" 를 뜻하지 않는다
2. **내용의 기술적 타당성은 판정하지 않는다.** 근본원인이 실제로 맞는지는 보지 않는다. 구조와 요소의 유무만 본다
3. **표본 수.** 문서 수십 건에 특징 수십 개면 FDR 보정이 강해 `확정` 등급이 나오기 어렵다
4. **평가자 일치도가 낮으면** 공통 템플릿의 전제가 약해진다. Krippendorff α < 0.2 면 경고가 뜬다
5. `유력` 등급 일부는 통계적 유의성 없이 부호 안정성만으로 올라온다 → `verdict_basis` 에 명시된다

---

## 환경

| 항목 | 값 |
|---|---|
| Python | 3.11 (CPU 전용, GPU 불필요) |
| conda 환경 | `Valeo_SVM_Trial` |
| 의존성 | 99개 — 전부 Windows(win_amd64, cp311) 바이너리 wheel 로 해결됨 (컴파일러 불필요) |
| 개발 | Ubuntu 22.04 + Anaconda |
| 배포 | zip → Windows(GPU 없음) PC 에서 Anaconda 로 실행 |
| 대안 실행 | Colab Enterprise / Vertex AI Workbench (같은 GCP 프로젝트 안) |

Poppler, Tesseract 등 시스템 바이너리에 의존하지 않는다. 경로는 `pathlib`, 파일 입출력은 `encoding="utf-8"`.

### Windows 배포

```bash
python scripts/build_release.py                # dist/valeo_svmtrial_<version>.zip
python scripts/build_release.py --with-wheels  # 사내망 오프라인 설치용 (약 160MB)
```

빌드는 `.env`·인증키·데이터가 포함되면 **실패한다** (수집 단계와 zip 내부에서 두 번 검사).

---

## 비목표 (v1)

문서 내용 초안 작성, 기술적 타당성 판정, 구글 Docs/Slides API 직접 생성, 웹 UI.
