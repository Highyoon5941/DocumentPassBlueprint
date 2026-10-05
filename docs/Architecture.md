# Architecture.md

> Valeo_SVMtrial — 전체 아키텍처와 기본 개념
> 설계 기준 문서: [`Valeo_SVMtrial_SETUP.md`](Valeo_SVMtrial_SETUP.md) · 실행 방법: [`Runbook.md`](Runbook.md) · 사내망 전환: [`migration_vertexAI.md`](migration_vertexAI.md)
> 작성 2026-10-05

---

## 1. 이 시스템이 하는 일

### 1.1 문제

같은 종류의 문서(대책서 등) 여러 건이 있고, 평가자 여러 명이 각각 Pass/Fail 을 매겼다.
**사유는 남아 있지 않다.** 왜 어떤 문서는 평가자 5명 전원이 합격시켰고, 어떤 문서는 4명만 합격시켰는지 아무도 적어두지 않았다.

### 1.2 목표

Pass/Fail 예측기를 만드는 것이 **목적이 아니다.** Pass/Fail 데이터는 수단이다.
목표는 **평가자 전원을 통과할 표준 구조(템플릿)** 를 만드는 것이다.
템플릿에는 어떤 섹션·표·그림이 어떤 순서와 위계로 들어가야 하는지가 담긴다.

### 1.3 상사의 비유와 그 구현

> "평면에 Pass 와 Fail 점이 찍혀 있고 경계선의 방정식은 모른다.
> 결과로 경계선을 찾고, Fail 문서가 Pass 쪽으로 가려면 무엇을 바꿔야 하는지 가이드한다."

| 비유 | 구현 |
|---|---|
| 평면 위의 점 | 문서 1건 = 특징 벡터 **x** (예/아니오 개념들의 0/1 값 + 구조 수치) |
| 경계선의 방정식 | 선형 SVM 의 결정함수 `f(x) = w·x + b` |
| 경계선 찾기 | `w` 와 `b` 를 데이터에서 학습 (`modeling.py`) |
| "무엇을 바꿔야 하나" | 반사실(counterfactual): `f(x) > 0` 이 될 때까지 바꿀 개념을 고른다 (`counterfactual.py`) |
| 가이드 | 가중치 `w` 가 큰 요소 → "이것을 넣어라" 라는 템플릿 문장 (`template_spec.py`) |

**핵심은 `w` 가 사람이 읽을 수 있어야 한다는 것이다.** 그래서 다음 설계가 나온다.

---

## 2. 기본 개념 4가지

### 2.1 Text Concept Bottleneck — 특징을 사람이 읽을 수 있게 만든다

문장 임베딩(예: 768차원 벡터)을 특징으로 쓰면 성능은 올라갈 수 있지만 `w` 의 342번째 성분이 무슨 뜻인지 알 수 없다.
그러면 템플릿 문장을 만들 수 없다. 목표가 템플릿이므로 **해석 가능성이 성능보다 우선한다.**

그래서 특징을 **사람이 읽을 수 있는 예/아니오 질문**으로 둔다.

```
❌ 임베딩:  x = [0.0231, -0.842, 0.117, ...]   → w 를 문장으로 못 바꾼다
✅ 개념:    x = [C001=1, C002=0, C003=1, ...]
            C001 = 'D4 섹션에 "5Why 단계적 원인분석 표" 가 있는가?'
            → w[C001] = +0.23  →  "D4 에 5Why 표를 넣어라"
```

이 구조를 **Concept Bottleneck** 이라 한다. 모든 정보가 해석 가능한 개념 층을 반드시 통과한다.

```
[문서] ──Gemini 비전──▶ [구조 JSON] ──Gemini 채점──▶ [개념 0/1]  ──선형 SVM──▶ [Pass/Fail]
                                                       ↑
                                              여기가 bottleneck.
                                              사람이 읽고 수정할 수 있다 (🔒 관문)
```

### 2.2 사유 없는 라벨에서 사유 후보를 만든다 (HypoGeniC 방식)

개념 목록을 사람이 처음부터 다 적을 수는 없다. 그래서 LLM 이 **가설을 생성**한다.
Pass 문서 몇 건과 Fail 문서 몇 건의 구조 요약을 나란히 보여주고 "무엇이 다른가"를 예/아니오 질문으로 뽑게 한다.

중요한 제약 두 가지:
- 생성된 가설은 **가설일 뿐이다.** 통계 검증을 통과해야 템플릿에 들어간다.
- 가설은 **발견셋(60%)으로만** 만든다. 검증셋(40%)은 손대지 않는다. (§2.4)

### 2.3 평가자가 여럿이고 서로 다르다 — 라벨 자체를 먼저 분석한다

평가자 5명의 판단이 일치하지 않으면 "공통 템플릿"이라는 전제 자체가 약해진다.
그래서 모델을 만들기 **전에** 라벨의 성질을 먼저 측정한다 (`labels.py`).

| 측정값 | 무엇을 보는가 | 왜 이것인가 |
|---|---|---|
| **Fleiss' κ** | 평가자 여러 명의 일치도 | 표준 지표. 단, 모든 문서의 평가자 수가 같아야 해서 최빈값 부분집합에만 적용 |
| **Krippendorff's α** | 같은 일치도, **결측 허용** | 실제 데이터에는 평가 누락이 있다. α 는 이를 다룰 수 있어 주 지표로 쓴다 |
| **평가자 쌍별 Cohen's κ** | 누가 누구와 비슷한가 | 평가자 그룹이 갈리는지 본다 |
| **평가자별 Pass율** | 누가 엄격한가 | 엄격한 평가자가 템플릿 요구사항을 결정한다 |
| **Dawid-Skene** | "진짜 라벨"의 사후확률 + 평가자별 혼동행렬 | 다수결은 모든 평가자를 같은 무게로 센다. DS 는 평가자 정확도를 추정해 가중한다 |

**판정 관문:** α < 0.2 이면 리포트 맨 위에 경고를 띄운다 — *"평가자 간 기준이 거의 일치하지 않아 공통 템플릿의 근거가 약함."*
이때는 평가자별 분석이 주가 된다.

> **전원 통과 템플릿 = 각 평가자 요구사항의 합집합**
> 모든 평가자의 Pass 영역이 겹치는 곳에 들어가야 하므로, 요구사항은 교집합이 아니라 **합집합**이다.
> 한 명은 "있어야 Pass", 다른 한 명은 "없어야 Pass" 라고 하면 **충돌**이고, 이것은 리포트에 따로 표기한다.

### 2.4 통계적 누수를 막는다 — 그래야 결과를 믿을 수 있다

LLM 이 Pass/Fail 문서를 **보고** 가설을 만들었으므로, 같은 문서로 그 가설을 검증하면 당연히 잘 맞는다.
이것이 누수(leakage)다. 막는 장치가 네 겹 있다.

| 장치 | 내용 | 코드 |
|---|---|---|
| **1. 발견/검증 분할** | 타깃 기준 층화 분할. 발견셋 60% 에서만 가설 생성, 검증셋 40% 는 방향 확인용 | `concepts.make_split`, seed 고정, `work/concepts/<g>/split.csv` |
| **2. 다중비교 보정** | 특징이 많으면 우연히 유의한 것이 나온다 → Fisher 정확검정 p 에 BH-FDR 보정 → q 값 | `modeling.univariate` |
| **3. 순열검정** | 라벨을 500번 섞어 모델 성능 분포를 만들고 실제 성능과 비교. p ≥ 0.05 면 "모델 신호 없음" | `modeling.multivariate` |
| **4. 부트스트랩 안정성** | 200회 재표본으로 SVM 가중치 **부호**가 얼마나 일관된지 본다 | `modeling.bootstrap_stability` |

여기에 **데이터 양 가드**가 추가된다.
`소수 클래스 문서 수 < 10 × 특징 수` 이면 다변량 결과를 "탐색적"으로만 표기하고, 판정은 단변량과 안정성 중심으로 한다.

---

## 3. 전체 파이프라인

### 3.1 데이터 흐름

```
 입력                        단계                                    산출물
──────────────────────────────────────────────────────────────────────────────────────
 PDF (이미지/스캔본)   ─S1 ingest──────▶ work/pages/<doc_id>/pNNN.png + meta.json
 data/raw/pdfs/                          · 페이지 비율, creator/producer, sha256
 <doc_type>/<customer>/                  · 원본 형식 판정: docs | slides (4단계)
   <doc_id>.pdf                │
                               ▼
                       ─S2 ocr──────────▶ work/ocr/<doc_id>/pNNN.json
                        Gemini FAST       + doc_outline.json
                        페이지 1장 = 1회   · 제목 계층(level 1~3), 블록 유형
                        media_res HIGH     · 표의 열 이름과 행 수, 그림 종류
                                           · 페이지 역할, 판독성 0~1
 평가 xlsx             ─S3 labels───────▶ work/labels/<group>/
 data/raw/labels/       (LLM 없음)         · labels_long.csv  doc_targets.csv
   *.xlsx                                  · agreement.json (κ/α/DS/엄격도)
   long 또는 wide                          · 타깃: y_unanimous | y_majority | y_ds | pass_ratio
                               │
            ┌──────────────────┴──────────────────┐
            ▼                                     ▼
    ─S4a sections─────────▶              ─S4b concepts discover──────▶
     Gemini PRO + 🔒                      Gemini PRO (발견셋만) + 🔒
     제목 → 표준 섹션 분류체계             Pass/Fail 대조 → 예/아니오 개념 가설
     대책서는 8D 를 seed 로                라운드 5회 × (Pass 4 + Fail 4) → 합치고 중복 제거
     taxonomy_candidates.yaml              concepts_candidates.yaml
       → 사람 검토 → _approved.yaml          → 사람 검토 → _approved.yaml
            └──────────────────┬──────────────────┘
                               ▼
                       ─S4c concepts score─▶ work/features/<group>/X.csv
                        Gemini FAST          행: doc_id, 열: 개념(0/1) + 구조 특징
                        문서 1건 = 1~N회      + scoring_meta.json (재채점 일치율)
                               │
                               ▼
                       ─S5 model────────────▶ work/models/<group>/
                        (LLM 없음)             · univariate.csv  concept_verdicts.csv
                        단변량 + 다변량         · stability.csv  results.json
                        + 안정성 + 검증셋       · counterfactual.json / _ranking.csv
                        + 평가자별 + 반사실
                               │
                               ▼
                       ─S6 template─────────▶ outputs/<run_id>/<group>/
                        Gemini PRO             · template_spec.json  template_spec.md
                        (문구만, 수치는 코드)    · template.docx  또는  template.pptx
                        + 결정론 골격           · analysis_report.md + charts/*.png
                        → 🔒 최종 검토
                               │
                               ▼
                       ─S7 diagnose─────────▶ outputs/<run_id>/<group>/diagnose_<doc_id>.md
                        새 문서 1건            · 결정값 f, Pass/Fail 쪽
                        S1→S2→S4c→SVM→반사실    · 보완 목록 (최소 변경 집합)
                                               · 피드백 루프 안내
```

### 3.2 각 단계가 LLM 을 쓰는지

| 단계 | LLM | 모델 tier | 호출 수 | 프롬프트 |
|---|---|---|---|---|
| S1 ingest | 조건부 | FAST | 형식이 애매한 문서당 1회 (대개 0) | `doc_format.v1` |
| S2 ocr | **예** | FAST | **페이지당 1회** ← 전체 비용의 대부분 | `ocr_page.v1` |
| S3 labels | 아니오 | — | 0 | — |
| S4a sections | 예 | PRO + FAST | 그룹당 1 + 미매칭 제목 1회 | `section_taxonomy.v1`, `section_map.v1` |
| S4b discover | 예 | PRO | 라운드 수(기본 5) + 합치기 1 | `concept_discovery.v1`, `concept_merge.v1` |
| S4c score | **예** | FAST | **문서당 ⌈개념수/25⌉회** + 재채점 10% | `concept_scoring.v1` |
| S5 model | 아니오 | — | 0 | — |
| S6 template | 예 | PRO | 그룹당 1~2 (근거 누락 시 재요청) | `template_compose.v1` |
| S7 diagnose | 예 | FAST | 문서 페이지 수 + 채점 | 위와 동일 |

비용은 **S2(페이지 수) 와 S4c(문서 수 × 개념 수)** 가 지배한다. 두 단계 모두 캐시되므로 재실행은 공짜다.

---

## 4. 통계 설계 — 왜 이 방법인가

### 4.1 단변량이 주 근거다

문서 수가 수십 건, 특징이 수십 개인 상황에서는 다변량 모델이 과적합한다.
그래서 **"이 요소가 있을 때 vs 없을 때 Pass율"** 이라는 단변량 비교를 주 근거로 둔다.

| 값 | 의미 |
|---|---|
| `rate_with` / `rate_without` | 요소가 있을 때 / 없을 때의 Pass율 |
| **`rd` (위험차)** | `rate_with − rate_without`. 효과크기. 이것이 템플릿 문장의 근거가 된다 |
| `p_value` | Fisher 정확검정 (소표본에 정확. 카이제곱의 근사를 쓰지 않는다) |
| **`q_value`** | BH-FDR 보정 후 값. 특징이 여러 개이므로 p 대신 q 로 판단한다 |

수치형 특징(페이지 수 등)은 Mann-Whitney U 검정을, 연속 타깃(`pass_ratio`)에는 Spearman 상관을 쓴다.

### 4.2 다변량은 보조다

| 모델 | 역할 |
|---|---|
| `LinearSVC` (C ∈ 0.01, 0.1, 1) | **주 모델.** `w` 가 반사실과 템플릿 가중치의 근거 |
| L1 로지스틱 | 희소 선택 — 어떤 특징이 살아남는지 교차 확인 |
| 깊이 3 결정트리 | 상호작용(조합 조건)이 있는지 본다 |
| RuleFit (`imodels`) | "A 이고 B 이면 Pass" 형태의 규칙을 뽑는다 |
| `DummyClassifier` | **기준선.** 이것을 못 이기면 신호가 없다 |

평가는 `RepeatedStratifiedKFold(5×10)`, 문서 40건 미만이거나 소수 클래스가 적으면 `LeaveOneOut` 으로 자동 전환한다.
지표는 `balanced_accuracy`(클래스 불균형 대응)와 `ROC-AUC`.

### 4.3 개념 판정 등급

네 가지 증거를 조합해 등급을 매긴다 (`modeling.verdicts`).

| 등급 | 조건 | 템플릿에서 |
|---|---|---|
| **확정** | q < 0.1 **그리고** 부호 일관성 ≥ 90% **그리고** 검증셋 동일 방향 | `required = true` (필수 요소) |
| **유력** | (q < 0.25 **또는** 부호 일관성 ≥ 80%) **그리고** 검증셋 동일 방향 | 권장 요소 |
| **참고** | 위에 못 들지만 \|RD\| > 0.15 | 선택 요소 또는 제외 |
| **기각** | 그 외 | 템플릿에 넣지 않는다 |

> **`유력` 의 주의점:** 조건이 "q 또는 안정성"의 **OR** 이므로, 통계적 유의성 없이 부호 안정성만으로도 올라올 수 있다.
> 그래서 각 판정에 `verdict_basis` 를 함께 남기고, 그런 경우 **"안정성만(통계적 유의성 없음)"** 이라고 적는다.
> 리포트의 `한계와 주의사항` 에도 해당 항목이 모여 나온다. 데이터가 늘면 재확인해야 한다.

### 4.4 반사실 — 왜 탐욕 방식이 최소해인가

Fail 문서 하나의 결정값이 `f(x) = w·x + b < 0` 이다. 이것을 0 보다 크게 만들어야 한다.

바꿀 수 있는 것은 `actionable=true` 인 **이진** 개념이다.
- 없는데(0) `w > 0` → 추가하면 `f` 가 `+w` 만큼 오른다
- 있는데(1) `w < 0` → 제거하면 `f` 가 `+|w|` 만큼 오른다

**이진 특징에서는 각 변경의 이득이 서로 독립이다** (`w·x` 가 선형이므로 교차항이 없다).
따라서 "필요한 상승폭을 가장 적은 개수로 채우기" 문제가 되고, 이득이 큰 것부터 담는 탐욕이 **변경 개수 기준 최소해**다.
(`tests/test_modeling.py::test_counterfactual_greedy_is_minimal` 이 완전탐색과 비교해 이를 고정한다.)

`dice-ml` 같은 반사실 라이브러리를 쓰지 않은 이유가 이것이다. 이 조건에서는 50줄이 정확한 답을 준다.

---

## 5. 모듈 지도

```
src/svmtrial/                                                               (약 5,600줄)
│
├─ 설정·기반
│   config.py          config.yaml + .env 로드, ${VAR} 치환, 인터프리터 위생 점검
│   schemas.py         모든 pydantic 스키마 (SETUP.md §9)
│   seeds.py           문서 종류별 섹션 분류체계 seed (대책서 = 8D)
│   groups.py          분석 단위 (doc_type, customer) 와 경로 규칙
│   textutil.py        문자열 정규화, 제목↔섹션 매칭, 인용 문구 추출
│   io_utils.py        표 저장 (parquet 엔진이 있으면 parquet, 없으면 CSV)
│
├─ Gemini 경계 (R2: 호출은 모두 이 층을 지난다)
│   gemini_client.py   ★ 유일한 진입점. 캐시·재시도·동시성·토큰 로깅·dry-run
│   parts.py           백엔드 중립 입력 조각 (text / image)
│   payload.py         프롬프트 안의 기계판독 JSON 블록
│   vertex_backend.py  실제 Vertex AI (google-genai 를 import 하는 유일한 모듈)
│   offline_backend.py 결정론 스텁 — 개발 PC 전용. 실데이터 불가
│
├─ 파이프라인
│   ingest.py          S1  PDF → PNG + 메타 + 형식 판정
│   ocr.py             S2  페이지 구조 추출 + doc_outline 조립
│   labels.py          S3  정규화 + κ/α + Dawid-Skene(직접 구현) + 타깃 결정
│   sections.py        S4a 분류체계 생성(🔒) + 제목 매핑 + Kendall τ
│   concepts.py        S4b 발견/검증 분할 + 가설 생성(🔒) · S4c 채점
│   features.py        결정론 구조 특징 + 특징 행렬 조립
│   modeling.py        S5  단변량 + 다변량 + 안정성 + 검증셋 + 등급 + 평가자별
│   counterfactual.py  S5-7 최소 보완 집합
│   template_spec.py   S6  섹션 골격(결정론) + 명세 작성(LLM) + 통계 채우기
│   render_docx.py     S6  TemplateSpec → .docx (맑은 고딕 + w:eastAsia)
│   render_pptx.py     S6  TemplateSpec → .pptx (16:9 + 발표자 노트)
│   report.py          analysis_report.md / template_spec.md / 차트
│   diagnose.py        S7  새 문서 1건 진단
│
└─ cli.py              typer CLI — 모든 명령의 조립

prompts/*.md           8개. 파일명에 버전이 들어간다 (R9). Jinja2 + 데이터 블록
scripts/               smoke_test / check_gemini / make_dummy_data / build_release
                       / verify_dummy / run_tests.sh
tests/                 10개 모듈, 136개. Gemini 는 전부 mock (R6)
windows/               setup_windows.bat / run_windows.bat / README_windows.txt
```

---

## 6. 백엔드 추상화 — 이 개발 PC와 사내망의 차이

이 개발 PC 에는 사내 GCP 접근이 없다. 그래서 Gemini 호출 경로를 **두 개**로 두고 `.env` 로 고른다.

```
             호출부 (ingest / ocr / sections / concepts / template_spec / diagnose)
                                      │
                                      │  gemini_client.generate_json(
                                      │      model=, prompt_id=, parts=, schema= )
                                      ▼
                        ┌──────── GeminiClient ────────┐
                        │ 캐시 · 재시도 · 동시성        │
                        │ 토큰 로깅 · dry-run           │
                        │ temperature=0 (R9)            │
                        └───────────────┬───────────────┘
                     SVMTRIAL_BACKEND   │
              ┌─────────────────────────┴─────────────────────────┐
              ▼                                                   ▼
      vertex_backend.VertexBackend                   offline_backend.OfflineBackend
      google-genai → Vertex AI                       결정론 규칙 (네트워크 없음)
      ✅ 실업무                                       ⚠ 개발 PC 전용
```

**두 백엔드는 같은 pydantic 스키마를 돌려준다.** 그래서 백엔드를 바꿔도 호출부 코드는 한 줄도 바뀌지 않는다.

offline 백엔드가 각 호출을 어떻게 대체하는지, 그리고 그중 무엇이 실데이터에서 쓸 수 없는지는
[`migration_vertexAI.md` §2](migration_vertexAI.md) 에 표로 정리돼 있다. 요약하면:

- **픽셀에서 직접 계산하므로 실데이터에도 유효:** `doc_format.v1`(가로/세로 비율)
- **문자열 규칙이라 약하지만 동작:** 섹션 매칭, 개념 채점(인용 문구 부분일치), 템플릿 문구 조립
- **❌ 실데이터 불가:** `ocr_page.v1` — `make_dummy_data.py` 가 심은 fixture 를 조회할 뿐 이미지를 읽지 못한다.
  실데이터에서는 판독성 0 이 되고, `diagnose` 는 **"판정 불가"** 로 막는다.

캐시 키에 백엔드 이름과 스텁 규칙 버전이 들어간다. 그래서 offline 스텁 응답이 vertex 전환 후 재사용되는 일은 없다.

---

## 7. 사람 검토 관문 (🔒)

자동화하면 안 되는 판단이 세 군데 있다. 승인 파일이 없으면 다음 단계가 **실행되지 않는다** (R7).

| # | 관문 | 사람이 판단하는 것 | 통과 방법 |
|---|---|---|---|
| 1 | 섹션 분류체계 | 이 문서 종류의 표준 섹션이 무엇인가. seed(8D)를 데이터에 맞게 고칠지 | `work/sections/<g>/taxonomy_candidates.yaml` 검토 → `taxonomy_approved.yaml` |
| 2 | 개념 후보 | 각 가설이 ① 문서만 보고 예/아니오로 답할 수 있는가 ② 내용 타당성 판단이 섞이지 않았는가 ③ actionable 이 맞는가 ④ 중복이 없는가 | `work/concepts/<g>/concepts_candidates.yaml` 검토 → `concepts_approved.yaml` |
| 3 | 최종 템플릿 | 생성된 docx/pptx 를 배포해도 되는가 | 사람이 열어보고 배포 |

`svmtrial approve --group G --gate sections|concepts` 는 후보를 승인 파일로 복사하는 **명시적** 명령이다.
`all` 파이프라인은 이것을 **절대 자동으로 호출하지 않는다.** 관문에 닿으면 멈추고 안내 메시지를 출력한다.

---

## 8. 주요 설계 결정과 근거

| 결정 | 근거 |
|---|---|
| OCR 을 Gemini 비전으로 | 입력이 스캔본 전제. Poppler/Tesseract 같은 시스템 바이너리에 의존하지 않아 Windows 배포가 단순해진다 (R4) |
| Gemini CLI 미사용 | 자동화 파이프라인에는 Python SDK(`google-genai`)가 맞다. CLI 는 대화형 에이전트용 |
| `temperature=0` + 프롬프트 버전 + 응답 캐시 | 재현성 (R9). 같은 입력이면 같은 결과, 재실행은 공짜 |
| 프롬프트에 기계판독 JSON 블록 | Gemini 에는 구조화된 입력이 정확도에 유리하고, offline 백엔드는 같은 블록을 프로그램으로 파싱한다. 호출부가 백엔드에 따라 갈라지지 않는다 |
| 섹션 골격은 결정론, 문구만 LLM | LLM 이 통계 수치를 만들어내면 안 된다. `presence_in_pass` 등은 코드가 계산하고 코드가 덮어쓴다 |
| 모든 요소에 `evidence_ids` 필수 | 근거 없는 템플릿 항목을 막는다. 비어 있으면 오류를 붙여 재요청하고, 2회 실패 시 예외 |
| Dawid-Skene 직접 구현 (약 60줄) | `crowd-kit` 은 transformers 를 끌어와 환경이 1.8 GB 로 커진다 |
| 반사실 탐욕 직접 구현 | 선형 모델 + 이진 특징에서는 정확한 최소해다 (§4.4). `dice-ml` 은 xgboost/lightgbm 의존 |
| SHAP 미사용 | 선형 모델에서는 `w·x` 가 곧 기여도다. numba 의존이 불필요 |
| 그룹 = (doc_type, customer) | 고객사 양식이 섞이면 "고객사 A 양식 = Pass" 같은 가짜 신호가 생긴다 |
| `na` 를 0 으로 채움 | "해당 없음"은 요소 부재와 같게 다룬다. 결측 보간을 하지 않아 해석이 단순해진다 |
| parquet 대신 CSV 기본 | parquet 엔진(`pyarrow`)은 검증된 의존성 목록에 없다 (R5). 표가 작고 사람이 열어볼 수 있다. 엔진이 있으면 parquet 를 쓴다 |

---

## 9. 알고 있는 한계

### 9.1 방법론의 한계 (실데이터에서도 남는다)

1. **상관관계는 인과관계가 아니다.** "Pass 문서에 더 자주 있었다"가 "넣으면 합격한다"를 뜻하지 않는다.
   예: 5Why 표를 넣는 작성자가 원래 꼼꼼한 사람이고, 평가자는 그 꼼꼼함 전체를 본 것일 수 있다.
2. **내용의 기술적 타당성은 판정하지 않는다.** 근본원인이 실제로 맞는지는 보지 않는다. 구조와 요소의 유무만 본다.
3. **표본 수.** 문서 수십 건에 특징 수십 개면 FDR 보정이 강하게 걸려 `확정` 등급이 나오기 어렵다.
   이때 판정은 단변량 효과크기와 부호 안정성 중심이 되고, 리포트에 "탐색적"이라고 표기된다.
4. **평가자 일치도가 낮으면** 공통 템플릿의 전제가 약해진다. α < 0.2 면 경고가 뜨고 평가자별 분석이 주가 된다.
5. **`유력` 중 일부는 통계적 유의성이 없다** (§4.3). `verdict_basis` 로 구분된다.
6. **Dawid-Skene 의 전제.** 문서마다 "참 라벨"이 하나 있다고 가정한다. 평가 난이도가 문서별로 연속적으로 다르면
   DS 사후확률이 극단값으로 수렴할 수 있다. 그래서 기본 타깃은 `unanimous`/`majority` 이고 `y_ds` 는 참고값이다.

### 9.2 이 개발 환경의 한계 (사내망 전환으로 해소)

7. **offline 백엔드의 OCR 은 fixture 조회일 뿐이다.** 실제 이미지를 읽지 못한다. → `SVMTRIAL_BACKEND=vertex`
8. **offline 의 개념 발견은 문자열 규칙**이라 표현이 다양한 실문서에서는 약하다. → vertex 전환 시 Gemini PRO 가 담당
9. **모델 ID·리전·할당량이 미확정.** → [`migration_vertexAI.md` §1](migration_vertexAI.md) 의 수동 입력 항목

### 9.3 v1 비목표

문서 내용 초안 작성, 기술적 타당성 판정, 구글 Docs/Slides API 직접 생성, 웹 UI.

---

## 10. 확장 지점

| 하고 싶은 것 | 손댈 곳 |
|---|---|
| 새 문서 종류 추가 (요구사양서, RMP, 외주 계산서) | `seeds.py` 에 seed 항목 추가. 나머지는 그대로 동작한다 |
| 새 결정론 특징 추가 | `features.py::structural_features` |
| 프롬프트 개선 | `prompts/<name>.v2.md` 를 새로 만들고 호출부의 `prompt_id` 를 올린다. 캐시가 자동 분리된다 |
| 새 모델 추가 | `modeling.py::_make_models` |
| 등급 기준 조정 | `config.yaml` 의 `analysis.verdict` |
| 다른 LLM 제공자 | `vertex_backend.py` 와 같은 `generate()` 인터페이스로 모듈 추가 후 `gemini_client.backend` 에 분기 |
