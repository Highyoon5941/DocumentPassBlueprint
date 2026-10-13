# autonomous_pipeline.md — 자율 기준 선정 + 로지스틱 + 형식 자유 출력

> **PDF → 이미지 → Claude가 기준 자체 선정 → 로지스틱 → Claude가 가이드라인 → 이미지+docx**
> 사람의 도메인 의견이 들어가지 않는다. 문서 형식에 얽매이지 않는다.
> 적용 방법: [`claude_backend_guide.md`](claude_backend_guide.md) · 배경: [`1007_night_alternatives.md`](1007_night_alternatives.md)
> 작성 2026-10-13 · 모든 수치는 실측

---

## 0. 요약

| 요구사항 | 구현 | 검증 |
|---|---|---|
사람 의견 배제 | `analysis.autonomous: true` — seed 미사용 + 🔒 자동통과 + 도메인 중립 v2 프롬프트 | 두 문서종류 모두 숨김 규칙 복원 |
무조건 이미지 입력 | 이미 그러함 (`ocr.py` 가 PNG 만 전송) | 코드로 확인 |
SVM → 로지스틱 | `method: logistic \| hierarchical`, 확률·오즈비 출력 | 계수 순서 복원, 평가자E 상호작용 검출 |
결과 기반 가이드라인 | 목적함수 직접 최적화 → 예산별 최적 조합 | `greedy_is_optimal: False` 실측 |
형식 자유 출력 | `LayoutSpec` → 이미지 + docx (공통 원천) | 렌더러에 문서종류 가정 0 (테스트로 고정) |

---

## 1. 사람 의견을 어디서 뺐나

세 군데에 들어가 있었다.

| # | 위치 | 들어있던 의견 | 자율 모드 |
|---|---|---|---|
**①** | `seeds.py` | 대책서 = 8D (D0~D8) 9개 섹션 | `seed_for(doc_type, autonomous=True)` → `[]` |
**②** | `concept_discovery.v1.md` | `좋음: D4 섹션에 "5Why 단계적 원인분석 표"…` | **v2** — 도메인 용어 제거, 플레이스홀더로 |
**③** | 🔒 개념/섹션 승인 관문 | 사람이 개념을 삭제·수정·추가 | 자동 통과 (**R7 를 명시적으로 끈 것** — 리포트에 기록) |

### 1.1 v2 프롬프트의 원칙

형식 지침은 남기고 도메인 내용만 뺐다. 형식까지 빼면 "작성자가 성실한가?" 같은
검증 불가능한 기준이 나온다.

```
유지:  "문서만 보고 예/아니오로 답할 수 있어야 한다"
       "내용의 기술적 타당성은 판단하지 않는다"
       "인용 문구는 입력 문서에 실제로 등장한 표현이어야 한다"

제거:  "5Why", "수평전개", "근본원인", "8D", "효과검증", "5W2H"
```

v2 에 금지어가 남아 있지 않은지 테스트로 고정했다
(`test_prompt_v2_variants_are_domain_neutral`).

### 1.2 ⚠ R7 을 껐다는 사실

`SETUP.md` R7 은 "사람 검토 관문을 자동 통과하지 않는다"를 요구한다. 자율 모드는 이를 끈다.
**사용자 승인을 받고 진행했으며**, 기본값은 `autonomous: false` 로 두어 의도적 선택이 되게 했다.

| | 🔒 켬 (기본) | 🔒 끔 (자율) |
|---|---|---|
사람 의견 | 들어감 | **안 들어감** |
검증 불가 기준 | 사람 + 통계가 거름 | **통계만 거름** |
미끼 | 사람 + 통계 | 통계만 |

승인 파일에 `_autonomous_auto_approved: true` 가 기록되어 사후에 구분할 수 있다.

---

## 2. 이미지 전용 입력

이미 그렇게 돼 있었다. `ocr.py` 가 LLM 에 보내는 것:

```python
parts=[P.text(프롬프트), P.image(img.read_bytes())]     # 이미지만
```

`has_text_layer` 는 리포트에 숫자만 적고 어디에도 쓰지 않는다. 설계 전제가
"이미지 기반(스캔본)을 기본 전제로 하며 텍스트 레이어는 무시한다"(SETUP.md §2.2)이다.

> **대가**: 폰트나 "표 밖으로 밀림" 같은 기준은 좌표 계산이 아니라
> **비전 모델의 시각적 판정**으로만 가능하다. 정확도는 떨어지지만 모든 PDF 에 적용된다.

---

## 3. 로지스틱 계열 (SVM 대체)

`config.analysis.method` 로 고른다. 어느 쪽이든 **매 실행마다 적합하고 저장하지 않는다**(무상태).

| method | 함수 | 출력 | 쓰는 관측치 |
|---|---|---|---|
`svm` (기본) | `fit_svm_weights` | 결정값 `f(x)` | 문서 N |
`logistic` | `fit_logistic` | **확률 + 오즈비 + p값** | 문서 N |
`logistic` + `monotone: true` | `fit_monotone_logistic` | 위 + 계수 ≥ 0 제약 | 문서 N |
`hierarchical` | `fit_hierarchical` | 위 + **평가자 효과·상호작용** | **평가자 판정 N×R** |

### 3.1 실측 (대책서 샘플, 정답지 대조)

| 요인 | 참값 | 로지스틱 | 오즈비 | p | 단조 | 계층 |
|---|---|---|---|---|---|---|
why5 | 1.9 | **4.11** | 61.1 | 0.0001 | 3.52 | 2.80 |
graph | 1.6 | **2.28** | 9.8 | 0.0031 | 2.31 | 1.77 |
yokoten | 1.0 | **1.31** | 3.7 | 0.0953 | 1.20 | 1.06 |
photo | 0.5 | 0.32 | 1.4 | 0.6694 | 0.18 | 0.90 |
gantt (미끼) | 0 | −0.12 | 0.9 | 0.8696 | **0.00** | 0.02 |
colorbox (미끼) | 0 | −1.35 | 0.3 | 0.0903 | **0.00** | −0.97 |
appendix (미끼) | 0 | −0.29 | 0.7 | 0.7336 | **0.00** | −0.07 |

- 계수 **순서를 정확히 복원**한다
- **단조 제약이 미끼 3개를 전부 0 으로** 만든다 — "요소 추가가 해를 끼치지 않는다"는 제약의 부수 효과
- 계층 모형은 평가자별 차이를 잡는다:

```
기준 평가자: 평가자A
is_평가자B:signature  −0.47  p=0.6063
is_평가자C:signature  −0.70  p=0.4330
is_평가자D:signature  +0.50  p=0.5712
is_평가자E:signature  +2.56  p=0.0069   ← 평가자E 만 서명란 의존 (정답지와 일치)
```

### 3.2 구현에서 고친 두 가지

| # | 문제 | 수정 |
|---|---|---|
**F1** | `fit_regularized(alpha=1.0)` 가 계수를 0 근처로 뭉갰다 (why5 0.08) | statsmodels 는 로그우도를 표본 수로 나눈 뒤 alpha 를 곱한다 → **기본을 MLE 로** 바꾸고 완전분리·비수렴일 때만 약한 ridge(0.01) 로 폴백 |
**F2** | 평가자 전원의 상호작용 + 주효과 → **rank-deficient** (`SingularMatrixWarning`) | Σ_r is_r = 1 이라 주효과와 완전공선 → **기준 평가자의 상호작용을 뺀다**(C(rater) 와 같은 기준) |

둘 다 회귀 테스트로 고정했다.

---

## 4. 목적함수 직접 최적화

등급표 대신 **"N개까지 넣을 수 있다면 무엇을 넣어야 Pass 확률이 가장 높은가"** 에 답한다.

```
P(평가자 전원 Pass | x) = Π_r σ(w·x + b + β_r)
```

`objective: all_pass_prob` 로 켠다. 후보가 20개 이하면 전수탐색, 넘으면 beam search.

### 4.1 실측 — 예산별 최적 조합

```
기준(요소 없음)   P = 0.0000
요소 1개씩:  why5 +0.0648 | graph +0.0052 | yokoten +0.0005 | photo +0.0003

1개 → 0.0648   why5
2개 → 0.5223   why5, graph
3개 → 0.7860   why5, graph, yokoten
4개 → 0.9131   why5, graph, yokoten, signature
5개 → 0.9634   why5, graph, yokoten, photo, signature
8개 → 0.9021   (미끼 colorbox 를 넣으면 오히려 떨어진다)
```

### 4.2 ⚠ 탐욕이 최적이 아니다 — 실측으로 확인

`Architecture.md` §4.4 는 "이진 특징에서 각 변경의 이득이 독립이므로 탐욕이 최소해"라고 적고 있다.
**선형 `w·x` 에서는 맞다.** 목적이 `Π_r σ(·)` 로 바뀌면 이득이 독립이 아니다 —
why5 단독은 0.065 인데 why5+graph 는 0.522 다(초모듈성).

```
greedy_is_optimal: False      ← optimize() 가 매번 계산해 결과에 넣는다
```

그래서 전수탐색/beam 을 쓴다. 이 값이 `False` 면 리포트에서 탐욕 결과를 믿으면 안 된다.

> `Architecture.md` 본문은 아직 SVM 기준으로 쓰여 있다. `objective: all_pass_prob` 를
> 기본으로 올릴 때 §4.4 를 함께 고쳐야 한다.

---

## 5. 형식 자유 출력 — `LayoutSpec`

### 5.1 무엇이 문제였나

`render_docx.py` 는 섹션 **내용**은 데이터에서 가져왔지만 **껍데기**가 파이썬에 박혀 있었다.

```
박혀 있던 것:  표지 4항목(문서 제목/고객사/문서번호/작성일)
               서명란 작성/검토/승인 (2곳)
               고정 제목 4개 (0.문서 전체 규칙 / 제출 전 체크리스트 / …)
               표 기본 열 항목/내용/비고
               레이아웃 순서 자체
```

완전히 다른 문서(외주 계산서, RMP)가 들어오면 맞지 않는다.
**출력을 이미지로 바꾸는 것만으로는 해결되지 않는다** — 고정성은 파일 형식이 아니라 렌더러에 있다.

### 5.2 해결 — 배치를 명세로 끌어냈다

```
TemplateSpec(무엇을)  +  관측 기하(Pass 문서의 페이지 크기·방향·분량)
        ↓  layout_compose.v1  (LLM)
LayoutSpec(어떻게 배치할지 — kind + rect 만)
        ↓
   ├─ render_image.py      → PDF + preview/*.png
   └─ render_docx_layout.py → .docx (좌표를 흐름 순서로 근사)
```

`LayoutBlock` 은 **문서 종류를 모른다**:

```python
kind: title | heading | paragraph | guidance | table
    | figure_placeholder | signature | checklist | field | divider
rect: [x0, y0, x1, y1]      # pt, 좌상단 원점
```

렌더러에 `대책서`·`8D`·`문서번호`·`고객사` 같은 문자열이 없음을 **테스트로 고정**했다
(`test_image_render_is_doc_type_agnostic`, `test_docx_layout_renderer_is_doc_type_agnostic`).

### 5.3 한글 렌더

| 순위 | 수단 | 비고 |
|---|---|---|
1 | 시스템 한글 폰트 (NanumGothic / Noto CJK / 맑은 고딕 / AppleSDGothic) | 자동 탐색 |
2 | PyMuPDF **내장 `korea` 폰트** | 폴백. 자간이 넓지만 글자는 정확 |

**신규 의존성 0** — `pymupdf` 는 입력 렌더링에 이미 쓰고 있다.
폰트 서브셋으로 PDF 를 4.83MB → 0.06MB 로 줄였다.
체크박스는 글리프(`☐`)에 의존하지 않고 사각형을 직접 그린다(두부 방지).

### 5.4 출력 선택

```yaml
template:
  output: both        # image | docx | both
  preview_dpi: 150
```

`both` 를 권한다 — 이미지는 보여주기용, docx 는 작성자가 채우는 용도다.
둘 다 **같은 LayoutSpec** 에서 나오므로 배치가 어긋나지 않는다.
레이아웃 생성이 실패하면 기존 고정 렌더러로 자동 폴백한다(전체가 멈추지 않게).

---

## 6. 검증 결과

### 6.1 자율 모드 — 두 문서종류 모두 합격

```
######## 자율 모드 — 대책서 ########
  5Why 근본원인 표      1.0  C001  유력  +0.333 ✓
  효과검증 그래프        1.0  C003  유력  +0.125 ✓
  수평전개 표           1.0  C004  유력  +0.125 ✓
  현물 사진(미끼)        0.0  C005  유력  -0.108 ✓
  팀 명단 표(미끼)       0.0  C002  참고  +0.182 ✓
  순열검정 p = 0.006 ✓
  → 합격

######## 자율 모드 — 요구사양서 (완전히 다른 문서종류) ########
  합격판정기준 표        2.0  C002  확정  +0.519 ✓
  추적성 표             1.3  C003  유력  +0.215 ✓
  규격 상·하한 표        0.9  C005  유력  +0.177 ✓
  용어정의 표(미끼)       0.0  C001  기각  +0.038 ✓
  개정이력 표(미끼)       0.0   —    —      —   ✓
  순열검정 p = 0.002 ✓
  → 합격
```

**seed 없이, 🔒 없이, 도메인 예시 없이** 각 문서종류의 숨김 규칙을 복원했다.
대책서에서는 seed 를 끈 결과 분류체계가 9개(8D) → **8개**(데이터 관측값)로 줄었다 —
한 번도 나타나지 않은 D0 이 빠졌다.

### 6.2 산출물

```
outputs/<run_id>/<group>/
├─ template.pdf            ← LayoutSpec 그대로
├─ preview/p001.png …      ← 페이지별 이미지
├─ template.docx           ← 같은 LayoutSpec 에서
├─ layout_spec.json        ← 배치 명세 (근거 ID 포함)
├─ template_spec.json/.md
└─ analysis_report.md + charts/
```

### 6.3 테스트

```
./scripts/run_tests.sh   →  183 passed  (163 + 자율/로지스틱/목적함수/레이아웃 20)
ruff check               →  All checks passed
smoke_test               →  ALL SMOKE TESTS PASSED
```

---

## 7. 설정 요약

```yaml
analysis:
  autonomous: false          # true 면 seed 미사용 + 🔒 자동통과 + v2 프롬프트 (R7 off)
  method: svm                # svm | logistic | hierarchical
  objective: decision_value  # decision_value | all_pass_prob
  monotone: false            # 존재 특징 계수 >= 0
template:
  output: both               # image | docx | both
  preview_dpi: 150
```

**완전 자율 + 확률 기반** 구성:

```yaml
analysis: {autonomous: true, method: hierarchical, objective: all_pass_prob, monotone: true}
template: {output: both}
```

---

## 8. 한계

| # | 내용 |
|---|---|
**1** | **자율 모드는 미끼를 통계로만 거른다.** 대책서에서 미끼 `현물 사진`이 `유력` 로 올라왔다(RD는 음수라 템플릿에서 필수가 되진 않음). 사람 검토가 있으면 걸러진다 |
**2** | **실측은 합성 데이터 + offline 백엔드.** 자율 모드의 진짜 시험은 Claude 로 실데이터를 돌릴 때다 |
**3** | **LayoutSpec 을 LLM 이 만든 적이 없다.** offline 스텁(위→아래 흘려 배치)으로만 검증했다. Claude 가 만든 배치의 품질은 미지수 |
**4** | **계층 모형은 평가자가 바뀌면 재적합**이 필요하다 |
**5** | `Architecture.md` §4.4(탐욕 최소성)는 SVM 기준이라 목적함수를 기본으로 올릴 때 수정해야 한다 |
**6** | 이미지 입력 전용이므로 폰트·레이아웃 깨짐 기준은 **비전 모델의 시각 판정**에 의존한다 |
