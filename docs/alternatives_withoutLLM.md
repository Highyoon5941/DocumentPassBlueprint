# alternatives_withoutLLM.md

> **LLM을 전혀 쓰지 않는 대안** — 전처리(S1~S4c)와 모델(S5)을 모두 비-LLM·비-SVM으로 대체하는 방안
> 성격: **제안서.** 채택하려면 승인이 필요하다(의존성 추가는 SETUP.md R5).
> 짝 문서: [`alternatives_withLLM.md`](alternatives_withLLM.md) · 현행 설계: [`Architecture.md`](Architecture.md)
> 작성 2026-10-07 · 모든 수치는 `Valeo_SVMtrial_sample_data/`(문서 82건·평가 410행)에서 **실측**

---

## 0. 이 문서의 전제

### 0.1 목표 재정의

목표는 **"이 문서종류 × 이 고객사 × 이 평가자 5명"에 대해 Pass 확률을 최대화하는 템플릿**이다.
모든 회사·모든 평가자에 통하는 보편 법칙이 아니다.

| 버릴 것 | 버리지 않을 것 |
|---|---|
| 평가자·고객사·문서종류를 넘나드는 보편성 | **새 문서**에 대한 일반화 |
| "보편적 참"을 묻는 유의성 검정 중심 사고 | 우연과 실제 패턴의 구분 |

> ⚠ "제공된 문서들에 대해서만"을 문자 그대로 받으면 최적해는 *어느 문서가 통과했는지 외우기*이고
> 템플릿의 가치가 0이 된다. 산출물이 **앞으로 쓸 템플릿**이므로 문서 방향의 일반화는 유지한다.
> 정확한 표현은 **"평가자 방향은 전이적(transductive), 문서 방향은 귀납적(inductive)"** 이다.

### 0.2 반드시 유지해야 하는 것

**Concept Bottleneck.** 특징은 사람이 읽을 수 있는 예/아니오여야 한다. 그래야 계수를
"이 요소를 넣어라"라는 템플릿 문장으로 바꿀 수 있다. 모델을 무엇으로 바꾸든 이건 유지한다.
(임베딩 기반은 성능이 올라도 목표 자체를 잃는다.)

### 0.3 현행 설계의 두 가지 손실

| # | 손실 | 근거 |
|---|---|---|
| **L1** | **390개 평가자 판정 → 80개 이진 라벨로 압축** | `y_unanimous`가 5명 판정을 1비트로 뭉갠다. 정보량 ~5배 손실 |
| **L2** | **평가자 개인차를 모델이 표현 못 함** | 풀링 SVM에 평가자 파라미터 자리가 없어 `per_rater`를 사후 분석으로만 본다 |

아래 대안들은 이 두 손실을 메우는 것이 핵심이다.

### 0.4 평가 기준

| 기준 | 의미 |
|---|---|
**해석성** | 결과를 템플릿 문장으로 바꿀 수 있는가
**소표본 적합** | n=80, 특징 10~30개에서 쓸 수 있는가
**신규 의존성** | 검증된 requirements 99개 밖의 패키지가 필요한가 (R5)
**난이도** | S=반나절 / M=1~2일 / L=3일 이상

---

## Part 1 — 전처리를 LLM 없이 (S1·S2·S4c 대체)

현행 S2는 페이지당 Gemini 1회(샘플 데이터 568회)를 쓴다. 이걸 로컬로 바꾼다.

### P1. ONNX 로컬 OCR 스택 ★★ 최우선

| | |
|---|---|
**아이디어** | `rapidocr`(한국어 인식) + `rapid-layout`(영역 검출) + `rapid-table`(표 셀, 선택)
**입력 → 출력** | 페이지 PNG → `PageLayout` (현행 스키마 그대로)
**신규 의존성** | `rapidocr`, `rapid-layout`, `onnxruntime` (+선택 `rapid-table`) — **R4 준수**: 시스템 바이너리 불필요
**난이도** | M

**왜 되는가 — 실측 (샘플 PDF, CPU)**

```
OCR (CUST_A_004, A4 흑백 스캔 + 기울기 ±0.8°)
  p002  3.8s  ★D4근본원인분석 ★발생원인-5Why분석 ★유출원인--5Why분석
               ★Why1 ★Why2 ★Why3 ★Why4 ★Why5
  p003  3.9s  ★[그래프]대책전/후불량률추이ppm ★ID7재발방지표준화 ★수평전개 ★차종 ★담당
  표지  2.2s  작성(1.00) 검토(0.99) 승인(1.00)        ← 평가자E가 보는 서명란
  평균 신뢰도 0.89~0.96

레이아웃 검출
  p002  1.3s  table 3, title 2, table_caption 2, plain text 1
  p003  1.1s  table 3, figure 1(0.95), title 3, table_caption 3   ← 그래프를 figure로
  표지  1.2s  table 2 (문서정보 표 + 서명란)
```

숨겨진 3요소(5Why·그래프·수평전개)와 서명란이 **전부 복원**됐다.

**성능 비교**

| | 568쪽 처리 | 비용 | 데이터 |
|---|---|---|---|
| **로컬 (~3s/쪽)** | **28분** 단일 스레드 / 12코어 **3~5분** | 0 | 밖으로 안 나감 |
| Gemini 무료 티어(10 RPM) | 1시간+ | 0 | ⚠ 학습에 사용됨 |
| Vertex AI 유료 | 수 분 | 토큰 과금 | 사내 프로젝트 내 |

**`PageLayout` 필드 대응**

| 필드 | 구현 | 상태 |
|---|---|---|
`legibility` | OCR 평균 신뢰도 | ✅ 실측 0.89~0.96 — 가장 자연스러운 대응
`orientation`,`looks_like` | 이미지 비율 | ✅ 이미 로컬 (`offline_backend._aspect`)
`blocks.heading`+`level` | layout `title` + 박스 높이로 level | ✅
`blocks.table` | layout `table` | ✅
`table.columns` | 표 영역 내 OCR 박스 기하 정렬 또는 `rapid-table` | ⚠ 구현 필요
`blocks.chart`/`photo` | layout `figure` + 색/엣지 휴리스틱 | ⚠ 모델은 `figure`까지만
`blocks.signature` | "작성/검토/승인" 키워드 + 박스 | ✅ 실측 1.00
`page_role` | 키워드 규칙(8D Report+고객사→cover, 부록→appendix) | ✅

**구현 위치** — 기존 백엔드 추상화를 그대로 쓴다.

```
src/svmtrial/local_backend.py        신규 (~250줄)
  generate(model, prompt_id, parts, schema, media_resolution) -> (dict, Usage)
    prompt_id == "ocr_page.v1"      → OCR+레이아웃 → PageLayout
    prompt_id == "doc_format.v1"    → 비율 (이미 offline에 있음)
    prompt_id == "section_map.v1"   → 동의어 사전 (이미 offline에 있음)
    prompt_id == "concept_scoring.v1" → 인용 문구 키워드 매칭 (이미 offline에 있음)
config.py  SVMTRIAL_BACKEND 에 "local" 추가
```

`ocr.py` / `concepts.py` / `modeling.py` 는 **손대지 않는다.** 스키마가 같기 때문이다.

**사내망 주의** — 모델을 ModelScope에서 첫 실행 때 내려받는다(약 133MB).
폐쇄망이면 미리 받아 zip에 동봉하고 `Global.model_root_dir`로 경로를 고정한다
(`build_release.py --with-wheels`와 같은 패턴).

**모델 출처** — Baidu PaddleOCR 계열 ONNX. 사내 모델 출처 정책이 있으면 사전 확인.

---

### P2. 텍스트 레이어 직접 추출 (디지털 PDF일 때)

| | |
|---|---|
**아이디어** | 스캔본이 아닌 PDF는 OCR 없이 `pymupdf`로 글자·좌표·글꼴크기를 바로 읽는다
**신규 의존성** | 없음 (`pymupdf` 설치됨) / 표까지 원하면 `pdfplumber`(순수 파이썬, 0.1MB)
**난이도** | S

샘플 데이터는 `n_with_text_layer: 0`(전부 스캔본)이라 해당 없지만, **실데이터에는 섞여 있을 수 있다.**
`ingest.py`가 이미 `has_text_layer`를 기록하므로 분기만 넣으면 된다.

```python
# local_backend 안에서
if meta["has_text_layer"]:
    blocks = blocks_from_text_layer(pdf, page_no)   # 글꼴 크기 → heading level, 매우 정확
else:
    blocks = blocks_from_ocr(png)                   # P1
```

글꼴 크기로 제목 레벨을 정확히 뽑을 수 있어 **OCR보다 품질이 높다.** 가능하면 우선 쓴다.

---

### P3. 순수 CV 구조 특징 (OCR 없이)

| | |
|---|---|
**아이디어** | 글자를 읽지 않고 **레이아웃만**으로 구조 특징을 만든다
**신규 의존성** | `opencv-python`(P1 채택 시 `rapidocr`가 함께 설치) 또는 `numpy`+`PIL`만으로도 가능
**난이도** | S~M

| 특징 | 계산 방법 |
|---|---|
표 개수 | 수평·수직 선분 검출(모폴로지 / 허프) 후 격자 교차점 군집
그림·사진 영역 | 글자 없는 큰 연결성분 + 엣지 밀도
chart vs photo | 색 히스토그램 엔트로피 + 흰 배경 비율 (차트는 흰 배경·적은 색)
잉크 밀도 / 여백 비율 | 이진화 후 흑픽셀 비율 — "빽빽함"의 대리 지표
문단 수 | 수평 투영 프로파일의 골짜기 수
기울기(skew) | 허프 변환 지배 각도 → `legibility` 대리
도장·서명 | 색 분리(빨강/파랑 채널) 후 연결성분

**왜 중요한가** — 현행 샘플 실행에서 **유일한 `확정` 등급이 `STR_n_tables`(표 개수)** 였다.
즉 **글자를 전혀 읽지 않아도 실제 신호가 잡힌다.** P1이 어려우면 P3만으로도 1차 파이프라인이 선다.

---

### P4. 고정 양식 좌표 템플릿 매칭

| | |
|---|---|
**아이디어** | 대책서가 사내 표준 양식이면 셀 위치가 고정이다. 좌표로 영역을 잘라 그 안만 확인
**신규 의존성** | 없음
**난이도** | S (양식당 1회 설정)

```yaml
# config/form_templates/대책서_표준_v3.yaml
page1:
  signature_block: {x: 0.62, y: 0.88, w: 0.35, h: 0.09}   # 상대 좌표
  doc_number:      {x: 0.70, y: 0.12, w: 0.25, h: 0.04}
page_any:
  d4_root_cause:   {anchor_text: "D4", below: 0.0, h: 0.4}
```

OCR 호출을 **작은 크롭 몇 개**로 줄여 속도·정확도가 동시에 올라간다.
양식이 바뀌면 YAML만 고친다. 양식 버전이 여러 개면 첫 페이지 해시로 자동 선택.

---

### P5~P8 (짧게)

| # | 방안 | 메모 |
|---|---|---|
**P5** | 도장·서명 색상 분리 검출 | 빨간 인감·파란 서명은 색 채널로 매우 쉽게 잡힌다. `STR_has_signature`를 OCR 없이 |
**P6** | 이미지 품질 지표 → `legibility` | 기울기·노이즈·해상도·대비를 0~1로 합성. OCR 신뢰도가 없을 때의 대안 |
**P7** | 하이브리드(권장) | 영역은 레이아웃 모델, 텍스트는 **제목·표 헤더 크롭만** OCR. 전문 전사를 안 하므로 빠르고 정확 |
**P8** | 반자동 주석 도구 | 레이아웃 검출 결과를 사람이 몇 분에 확인·수정하는 간단한 HTML 뷰어. 초기 20~30건으로 규칙을 교정하면 이후 자동화 정확도가 크게 오른다 |

---

## Part 2 — 모델을 SVM 없이 (S5 대체)

### 실측 비교 — 어느 방법이 진짜 계수를 복원하는가

정답지의 생성 모형을 알고 있으므로 직접 채점할 수 있다(OCR은 완벽하다고 가정해 **방법론만** 비교).

```
참 생성 모형:  점수 = -1.6 + 1.9×why5 + 1.6×graph + 1.0×yokoten + 0.5×photo
                    + 문서 공통잡음 N(0,0.35) + 평가자 성향(A 0 / B +0.4 / C 0 / D -0.2 / E -0.2)
               평가자E만: 서명란 있으면 +1.2, 없으면 -1.8  (격차 3.0)
               미끼(효과 0): gantt, colorbox, appendix
```

| 요인 | 참값 | 현행 SVM<br>(80건 이진화) | **M1 계층 로지스틱**<br>(390 판정) | M1+문서변량 | **M2 순서형**<br>(n_pass 0~5) |
|---|---|---|---|---|---|
why5 | 1.9 | 1.07 | **2.78** | 2.82 | 4.38
graph | 1.6 | 0.48 | **1.76** | 1.74 | 2.49
yokoten | 1.0 | 0.23 | **1.04** | 1.02 | 1.48
photo | 0.5 | **−0.09 ❌부호반대** | **0.89** (p=0.005) | 0.82 | 1.16 (p=0.020)
평가자E×서명란 | 3.0 | **표현 불가** | **+2.69** (p=0.0006) | +2.42 | 표현 불가
gantt (미끼) | 0 | — | 0.03 (p=0.92) ✅ | 0.06 | 0.04 ✅
appendix (미끼) | 0 | — | −0.06 (p=0.84) ✅ | −0.13 | −0.08 ✅
colorbox (미끼) | 0 | — | −0.96 (p=0.003) **❌오탐** | −0.99 | −1.39 **❌오탐**
signature (전체) | 0 | — | 0.19 (p=0.57) ✅ | 0.26 | 1.23 (p=0.017) ❌

**읽는 법**
- 계수 **순서**는 M1·M2 모두 정확히 복원. 크기는 참값보다 부풀지만(축소추정 없음) 비율은 맞다
- 정답지가 "40건으로는 검출 불가"라고 못 박은 **약한 요인 `photo`(0.5)가 검출**됨. 현행 SVM은 **부호가 반대**
- **평가자E 상호작용은 M1만 표현 가능.** M2·SVM은 풀링이라 구조적으로 못 한다
  (그래서 M2에서 `signature`가 전체 효과로 잘못 유의해졌다 — p=0.017)
- 힘이 세지면 **오탐도 는다**: 미끼 `colorbox`가 두 방법 모두에서 잘못 유의. → 축소추정/최소효과크기 문턱 필요

---

### M1. 평가자 수준 계층 로지스틱 ★★ 최우선

| | |
|---|---|
**아이디어** | 390개 평가자 판정을 그대로 쓰고, 평가자 고정효과 + 문서 변량절편 + 평가자×특징 상호작용을 모형화
**신규 의존성** | **없음** (`statsmodels 0.15` 설치됨)
**해석성** | ★★★ 계수가 로그오즈 → 템플릿 문장으로 직결
**소표본 적합** | ★★★ 관측치 80 → 390
**난이도** | M

```python
# modeling.py 에 추가
import statsmodels.api as sm, statsmodels.formula.api as smf
from statsmodels.genmod.bayes_mixed_glm import BinomialBayesMixedGLM

def fit_hierarchical(long, X, concepts, rater_interactions=("signature",)):
    """long: 평가자 수준 (doc_id, rater, y) / X: 문서 × 개념 행렬"""
    d = long.merge(X.reset_index(), on="doc_id").dropna(subset=["y"])
    terms = " + ".join(concepts)
    f = f"y ~ {terms} + C(rater)"
    for c in rater_interactions:                      # 평가자별로 다르게 보는 요소
        for r in sorted(d.rater.unique()):
            d[f"is_{r}"] = (d.rater == r).astype(int)
            f += f" + is_{r}:{c}"
    fixed = smf.glm(f, data=d, family=sm.families.Binomial()).fit()
    # 문서 공통잡음을 흡수하려면 변량절편
    mixed = BinomialBayesMixedGLM.from_formula(f, {"doc": "0 + C(doc_id)"}, d).fit_vb()
    return fixed, mixed
```

**왜 이게 1순위인가**
1. 정보량 80 → 390 (§0.3 L1 해소)
2. 평가자 개인차가 모델 안에 있다 (§0.3 L2 해소)
3. **확률을 직접 준다.** 목표가 "Pass 확률을 높이도록"인데 SVM은 결정값만 주고 확률이 아니다
4. `labels.py`가 이미 평가자 수준 `labels_long`을 저장한다 → **입력 데이터 변경 없음**
5. 이 모형은 공변량을 가진 **Rasch 모형(LLTM)** 과 같다 → 평가자 난이도를 정식으로 다룬다 (M13)

**전원 통과 확률** = `Π_r σ(η_r(x))` — 이게 M10의 목적함수가 된다.

---

### M2. 순서형 회귀 — `n_pass`(0~5)를 직접

| | |
|---|---|
**아이디어** | "5명 중 몇 명이 Pass"는 순서형 결과다. 이진화하지 말고 그대로 모형화
**신규 의존성** | 없음 (`statsmodels.miscmodels.ordinal_model.OrderedModel`)
**난이도** | S ← **가장 싸게 큰 개선**

```python
from statsmodels.miscmodels.ordinal_model import OrderedModel
om = OrderedModel(targets["n_pass"].astype(int), X[concepts], distr="logit").fit(method="bfgs")
```

실측에서 `photo`를 p=0.020으로 검출했다(현행은 부호 반대). **단 10줄**이고 신규 의존성 0.
한계: 평가자 상호작용을 표현 못 한다 → `signature` 오탐. **M1의 간이판**으로 쓴다.

---

### M3. 베타-이항 / 이항 GLM + 문서 변량

| | |
|---|---|
**아이디어** | `n_pass ~ Binomial(5, p_doc)` 로 보고 과분산을 베타로 흡수 |
**신규 의존성** | 없음 (`sm.GLM(family=Binomial())` + `freq_weights`, 또는 `GEE`) |
**난이도** | S |

M2보다 평가자 수 결측(문서별 평가자 3~5명)을 자연스럽게 다룬다. 샘플에도 결측 10개가 있다.
`GEE(groups=doc_id)` 로 평가자 내 상관을 보정하는 변형도 가능하다(신규 의존성 0).

---

### M4. 단조 제약 로지스틱 ★ — 지금 실제로 나는 문제를 막는다

| | |
|---|---|
**아이디어** | "요소를 추가하는 것이 해를 끼치지 않는다"를 **제약**으로 넣는다 (존재 특징 계수 ≥ 0)
**신규 의존성** | 없음 (`scipy.optimize.minimize(bounds=...)`)
**난이도** | S

**해결하는 문제** — 현행 샘플 실행에서 이게 나왔다.

```
SEC_D6   유력   rd=-0.351   w=-0.379      → "D6 효과검증 섹션이 있으면 Pass율이 낮다"
```

거의 확실히 교란이고, 템플릿이 **"D6을 빼라"**고 권할 수 있다. 제약을 걸면 구조적으로 불가능해진다.

```python
from scipy.optimize import minimize
def fit_monotone_logistic(X, y, nonneg_cols, l2=1.0):
    Xm = np.c_[np.ones(len(X)), X.to_numpy(float)]
    lo = [-np.inf] + [0.0 if c in nonneg_cols else -np.inf for c in X.columns]
    def nll(w):
        z = Xm @ w
        return np.logaddexp(0, z).sum() - (y * z).sum() + l2 * (w[1:] ** 2).sum()
    r = minimize(nll, np.zeros(Xm.shape[1]), method="L-BFGS-B",
                 bounds=list(zip(lo, [np.inf] * Xm.shape[1])))
    return r.x
```

부수 효과: 비음 가중치에서는 반사실의 탐욕 최소성 논증이 더 깔끔해진다(제거 동작이 사라짐).

---

### M5. SLIM 정수 점수표 ★ — 현장 배포성 최고

| | |
|---|---|
**아이디어** | 정수 가중치 점수표를 학습. 사람이 **손으로 채점**할 수 있다
**신규 의존성** | 없음 (`imodels.SLIMClassifier` 설치됨)
**난이도** | S

**실측 결과 (샘플 데이터 그대로)**

```
점수 = -5  +4×why5  +2×graph  +1×yokoten  +1×signature  -1×colorbox
       → 점수 > 0 이면 '전원 통과' 예측
       훈련 balanced accuracy = 0.854
```

정수 4/2/1 이 참값 1.9/1.6/1.0 의 **순서를 그대로** 따라간다.
이건 그냥 **제출 전 체크리스트**다. SVM 가중치 `0.23312` 를 품질팀에 건네는 것과 비교해 보라.

```python
from imodels import SLIMClassifier
slim = SLIMClassifier(alpha=0.01).fit(X[concepts], y)     # 주의: feature_names 인자 없음
coef, intercept = np.ravel(slim.model_.coef_), float(np.ravel(slim.model_.intercept_)[0])
```

**템플릿 산출물에 직결** — `render_docx.py`의 "제출 전 체크리스트"를 점수표로 바꾸면
작성자가 스스로 점수를 세어 제출 여부를 판단할 수 있다.

---

### M6. 규칙 학습 — 현장 친화적 조건문

| | |
|---|---|
**신규 의존성** | 없음 (`imodels` 설치됨) |
**난이도** | S~M |

`imodels`에 바로 쓸 수 있는 분류기들이 있다(실측 확인).

| 분류기 | 산출물 | 메모 |
|---|---|---|
`FIGSClassifier` | 얕은 트리의 합 | **실측 동작**. 트리별 기여를 더하는 구조라 해석 가능
`BayesianRuleListClassifier` | "A이고 B면 Pass(확률 p)" 목록 | 확률이 붙은 규칙 목록. 소표본에 사전분포로 안정화
`SkopeRulesClassifier` | 정밀도/재현율 문턱을 만족하는 규칙 집합 | 오탐을 억제하고 싶을 때
`FastFrugalTreeClassifier` | 2~3단계 질문 트리 | 현장에서 몇 초에 판단하는 용도로 설계됨
`OneRClassifier` | 단일 최강 규칙 | 기준선(baseline)으로 유용
`TreeGAMClassifier` | 특징별 가법 형태 | 단조 제약과 결합하기 좋다

실측 FIGS 출력(발췌): `why5 <= 0.5 → 0.054` / `why5 > 0.5 & graph > 0.5 → 0.8` 등 —
**"5Why가 없으면 거의 떨어진다"** 를 트리 구조로 그대로 보여준다.

---

### M7. 연관규칙 — 함께 나타나는 조합 찾기 (주의 필요)

| | |
|---|---|
**신규 의존성** | 없음 (`mlxtend` 설치됨 — `imodels`가 끌어옴) |
**난이도** | S |

```python
from mlxtend.frequent_patterns import apriori, association_rules
B = X[concepts].astype(bool); B["PASS"] = y.astype(bool)
rules = association_rules(apriori(B, min_support=0.05, use_colnames=True),
                          metric="confidence", min_threshold=0.6)
```

**실측 결과와 그 교훈**

```
선행조건                                       지지도  신뢰도  향상도
colorbox + graph + photo + why5 + yokoten     0.05   1.00   2.86
gantt + graph + why5 + yokoten                0.11   0.90   2.57
graph + photo + why5 + yokoten                0.10   0.89   2.54
```

신뢰도 1.00·향상도 2.86이 나오지만 **미끼(`colorbox`, `gantt`)가 섞여 있다.**
지지도 0.05 = 문서 4건이라 우연 공출현을 걸러내지 못한다.

> **결론: 단독 판정 근거로 쓰지 말 것.** 탐색 단계에서 "함께 나타나는 조합" 후보를 뽑아
> 🔒 사람 검토에 올리거나, M10 조합 최적화의 초기 후보 생성기로만 쓴다.

---

### M8. 단조 GBM — 상호작용까지

| | |
|---|---|
**신규 의존성** | 없음 (`HistGradientBoostingClassifier(monotonic_cst=...)`, sklearn 1.9 확인) |
**난이도** | S |

```python
mono = [1 if c in presence_cols else 0 for c in X.columns]     # 1 = 증가 단조
clf = HistGradientBoostingClassifier(monotonic_cst=mono, max_depth=2,
                                     max_iter=200, l2_regularization=1.0)
```

상호작용("5Why와 그래프가 같이 있을 때 특히 좋다")을 잡으면서 단조성을 보장한다.
한계: 계수가 없어 템플릿 문장 변환이 선형 모델보다 번거롭다 → **부분의존도(PDP)** 로 설명.

---

### M9. 평가자별 모델 → 요구사항 교집합 (집합 덮기)

| | |
|---|---|
**아이디어** | "전원 통과" = 각 평가자의 Pass 영역의 **교집합**. 평가자별 규칙을 학습해 교집합을 구성 |
**신규 의존성** | 없음 |
**난이도** | M |

```
평가자 A 규칙: why5 ∧ graph
평가자 B 규칙: why5                       (관대)
평가자 D 규칙: why5 ∧ graph ∧ yokoten     (엄격)
평가자 E 규칙: why5 ∧ signature            (서명란 의존)
──────────────────────────────────────────
전원 통과 필요조건 = why5 ∧ graph ∧ yokoten ∧ signature   (합집합 요구)
```

현행 `modeling.per_rater`가 이미 합집합·충돌을 계산한다. 여기에 **규칙 학습(M6)을 평가자별로**
돌리고 결과를 논리식으로 합치면 된다. 평가자당 80건이라 규칙은 얕게(깊이 2) 제한한다.

**장점** — 목표 정의("전원 통과")에 가장 직설적이고, 평가자 충돌을 논리적으로 드러낸다.

---

### M10. 목적함수 직접 최적화 ★★ — 산출물이 달라진다

| | |
|---|---|
**아이디어** | 등급표를 만들지 않고 **`P(전원 Pass)`를 최대화하는 요소 조합**을 직접 찾는다
**신규 의존성** | 없음 |
**난이도** | S (M1 또는 M2 이후)

```python
def p_all_pass(model, cfg, raters, features):
    rows = [{**{c: 0.0 for c in features}, **cfg, "rater": r} for r in raters]
    return float(np.prod(model.predict(pd.DataFrame(rows))))

# 요소 수가 적으므로(보통 5~30) 전수탐색 또는 beam search
best = max(((p_all_pass(m, {c: 1.0 for c in combo}, R, F), combo)
            for r in range(len(ACT) + 1) for combo in itertools.combinations(ACT, r)))
```

**실측 결과**

```
기준(요소 없음)   P(전원 통과) = 0.0000
요소 1개씩:  why5 0.0713 | graph 0.0058 | yokoten 0.0005 | photo 0.0003 | signature 0.0002

요소 수별 최적 조합 (분량 예산이 있을 때):
  1개 → 0.0713   why5
  2개 → 0.5343   why5 + graph
  3개 → 0.7891   why5 + graph + yokoten
  4개 → 0.9075   why5 + graph + yokoten + signature
  5개 → 0.9604   전부
```

> ### ⚠ 현행 설계의 오류 정정
> `Architecture.md` §4.4는 "이진 특징에서 각 변경의 이득이 독립이므로 탐욕이 최소해"라고 적고 있다.
> **선형 `w·x`에서는 맞다.** 그러나 목적이 `Π_r σ(·)`로 바뀌면 이득이 **독립이 아니다** —
> 위 표에서 why5 단독은 0.071인데 why5+graph는 0.534다. 여러 요소가 함께 들어가야
> 각 평가자가 문턱을 넘기 때문이다(초모듈성).
>
> 따라서 M10에서는 **탐욕이 최적이 아니고**, 요소 수가 적으니 **전수탐색 / beam search / ILP**로 푼다.
> 위 32가지 탐색은 즉시 끝났다. 30개 요소면 beam search(폭 100) 또는 상위 k개만 탐색.

**이게 질문에 가장 정확히 대응하는 산출물이다.** "분량 예산이 3개뿐이면 무엇을 먼저 넣는가"에 바로 답한다.

---

### M11. Conformal prediction — 정직한 예측 구간

| | |
|---|---|
**아이디어** | 분포 가정 없이 "새 문서의 전원통과 확률 0.62 [0.41, 0.80]" 같은 보증된 구간 |
**신규 의존성** | 없음 (수십 줄로 직접 구현 가능) |
**난이도** | M |

소표본에서 점추정만 내놓는 것보다 정직하다. FDR/순열검정을 대체하는 **새 검증 축**(§Part 3).

---

### M12~M14 (짧게)

| # | 방안 | 메모 | 신규 의존성 |
|---|---|---|---|
**M12** | Firth 벌점 로지스틱 / 정확검정 | 완전분리·희귀사건에서 MLE가 발산하는 문제를 막는다. 소표본 표준 처방. 직접 구현 ~40줄 | 없음 |
**M13** | Rasch / IRT 관점 | 문서=응답자(능력 θ), 평가자=문항(난이도·변별도). **M1이 이미 Rasch(LLTM)와 동형**이므로 추가 구현 없이 "평가자 변별도"를 해석으로 얻는다. 2PL(변별도)까지 가려면 평가자별 기울기를 넣는다 | 없음 |
**M14** | 완전 베이지안 계층 (PyMC) | 축소추정으로 **오탐(`colorbox`)을 줄이는 가장 원리적인 방법**. 사후분포로 `P(효과>0)`을 직접 보고. 느리고 환경이 커진다 | `pymc`(+`arviz`) |

---

## Part 3 — 검증을 바꾼다

모집단을 좁혔으니 "보편적 참"을 묻는 검정은 목적에 맞지 않는다.

| 현행 | 교체 | 이유 |
|---|---|---|
BH-FDR 보정 | **유지(약화)** 또는 축소추정(M14)으로 대체 | 오탐 억제는 여전히 필요(실측 `colorbox` 오탐) |
순열검정 (모델 신호 유무) | **문서 단위 교차검증 Brier / log-loss** | 확률 모형이므로 보정도까지 평가 가능 |
부트스트랩 부호 일관성 | **유지** | 소표본에서 여전히 유용한 안정성 지표 |
— | **Conformal 구간** (M11) | 새 문서 예측의 정직한 불확실성 |
— | **다중 seed 재현성** | 샘플 README의 시뮬레이션 표처럼 seed를 바꿔 반복. 지금 결과는 seed 7 하나 |
— | **보정 곡선(calibration curve)** | "확률 0.8이라 했을 때 실제로 80% 통과하는가" |
등급표(확정/유력/참고/기각) | **보조로 격하** | 주 산출물은 M10의 최적 조합 + 예산 곡선 |

---

## Part 4 — 추천 조합과 단계별 계획

### 추천: 3단계

| 단계 | 내용 | 신규 의존성 | 난이도 | 얻는 것 |
|---|---|---|---|---|
**1** | **M2 순서형 + M4 단조 + M5 SLIM** | **0개** | S | 즉시 개선. `photo` 검출, D6 음수 제거, 손 채점 점수표 |
**2** | **M1 계층 로지스틱 + M10 목적함수 최적화** | **0개** | M | 평가자E 상호작용, 전원통과 확률, 예산별 최적 조합 |
**3** | **P1 로컬 OCR 스택** | 3개 (R5 승인) | M | LLM 의존 완전 제거. 568쪽 3~5분, 비용 0, 데이터 외부 유출 0 |

1·2단계는 **신규 의존성이 전혀 없어** 지금 바로 할 수 있다. 3단계만 승인이 필요하다.

### 병행 운영 제안

기존 경로를 지우지 말고 설정으로 고른다.

```yaml
# config/config.yaml
analysis:
  method: hierarchical        # svm | ordinal | hierarchical | slim | rules
  objective: all_pass_prob    # decision_value | all_pass_prob
  monotone: true              # 존재 특징 계수 ≥ 0
```

같은 데이터에 여러 방법을 돌려 `_answer_key` 대조로 비교한다.
**중요: 지금 실측은 seed 7 하나다.** 결론을 세우려면 `make_sample_data.py --seed` 를 바꿔 반복해야 한다.

---

## Part 5 — 모듈 변경 맵

```
변경 없음 ✅
  labels.py          평가자 수준 labels_long 이미 저장 — M1 입력 그대로 사용
  features.py        특징 행렬 그대로
  sections.py        그대로
  schemas.py         TemplateSpec 구조 유지 (evidence 의미만 확장)
  cli.py             명령 이름 유지

추가
  modeling.py        + fit_ordinal()        M2
                     + fit_hierarchical()   M1
                     + fit_monotone()       M4
                     + fit_scoring_system() M5 (SLIM)
                     + fit_rules()          M6
  objective.py       신규 — M10 (Π_r σ 전수탐색/beam). counterfactual.py 를 흡수
  validation.py      신규 — Part 3 (Brier/log-loss/conformal/보정곡선)
  local_backend.py   신규 — P1 (SVMTRIAL_BACKEND=local)

수정
  template_spec.py   입력이 '등급' → '최적 조합 + 한계이득'
  render_docx.py     체크리스트를 SLIM 점수표로
  report.py          §4 등급표 → 예산 곡선 + 평가자별 요구 분해 + 보정 곡선
  config.py          analysis.method / objective / monotone, backend "local"
```

---

## Part 6 — 한계와 주의사항

1. **실측은 seed 7, 샘플 데이터 1종이다.** 정답지도 "이 seed는 평균보다 신호가 잘 보이는 편"이라고
   밝히고 있다. 여러 seed·실데이터로 재확인해야 한다.
2. **OCR은 완벽하다고 가정했다.** Part 2 비교는 정답지 특징을 그대로 썼다. 실제로는 P1의 OCR 오차가
   특징에 섞이므로 계수가 희석된다(측정오차에 의한 감쇠).
3. **힘이 세지면 오탐도 는다.** M1·M2 모두 미끼 `colorbox`를 잘못 유의하게 판정했다.
   축소추정(M14) 또는 최소효과크기 문턱을 반드시 함께 둔다.
4. **인과가 아니다.** 방법을 바꿔도 "Pass 문서에 더 자주 있었다"는 사실만 강해진다.
   "넣으면 합격한다"는 보장이 아니라는 경고는 계속 리포트에 남겨야 한다.
5. **LLM 없이 개념을 만드는 문제.** S4b 가설 생성은 LLM의 영역이다. 비-LLM 경로에서는
   **사람이 `concepts_approved.yaml`을 직접 작성**한다(🔒 관문이 이미 이를 허용한다).
   대책서 개념은 알려진 도메인 지식이다(5Why 표, 전/후 그래프, 수평전개 표, 서명란).
   품질팀이 작성하면 되고, M7 연관규칙을 후보 생성 보조로 쓸 수 있다.
6. **평가자가 바뀌면 다시 학습해야 한다.** 평가자 파라미터를 명시적으로 넣은 대가다.
   새 평가자는 과거 판정이 없으면 모집단 평균으로 시작할 수밖에 없다.
