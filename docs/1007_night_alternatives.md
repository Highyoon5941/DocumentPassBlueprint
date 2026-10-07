# 1007_night_alternatives.md — SVM 외 대안과 로지스틱 구현 방향

> **질문**: SVM 말고 다른 방식이 있는가, SVM과 무엇이 다른가. 로지스틱이 있는 건 아는데 구현 방향을 모르겠다.
> **전제**: 제약 해제 — 사내망 한정 아님, 문서 반출 가능, **LLM(Claude) 사용 가능.** LLM 활용 방향으로 좁힌다.
> 짝 문서: [`1007_night_SVM.md`](1007_night_SVM.md) · 더 넓은 목록: [`alternatives_withLLM.md`](alternatives_withLLM.md)
> 작성 2026-10-07 · 모든 수치는 실측

---

## 0. 한 장 요약

| | SVM (현행) | **로지스틱 (권장)** | 계층 로지스틱 (최종형) |
|---|---|---|---|
출력 | 결정값 `f(x)` — 확률 아님 | **확률 `P(Pass)`** | **`P(평가자 r 통과)` 평가자별** |
"Pass 확률을 높이도록" | ✗ 직접 답 못 함 | ✅ 직접 답 | ✅ + 전원통과 확률 `Π_r` |
평가자 개인차 | ✗ 구조적으로 표현 불가 | △ 평가자 고정효과로 일부 | ✅ 상호작용까지 |
쓰는 관측치 | 80건 (라벨 이진화) | 80건 또는 **390건** | **390건** |
적합 시간 | 1.1 ms | ~5 ms | **12.4 ms** |
신규 의존성 | 0 | **0** | **0** |
학습 모델 저장 | 안 함 | 안 함 | 안 함 |

**결론**: 목표가 "확률"이면 로지스틱이 구조적으로 맞다. SVM을 버리지 말고 `analysis.method` 설정으로 병행한다.

---

## 1. SVM과 로지스틱의 차이 — 무엇이 실제로 다른가

둘 다 **선형 경계면 `w·x + b`** 를 찾는다. 특징 공학(Concept Bottleneck), 무상태 적합, 문서종류 불변성은 **완전히 동일**하다.
다른 것은 **"무엇을 최소화하는가"** 와 **"무엇을 출력하는가"** 둘뿐이다.

### 1.1 손실함수

```
SVM (hinge loss):        Σ max(0, 1 − y·f(x))  + λ‖w‖²
  → 경계 근처의 점만 신경 쓴다(support vector). 멀리 있는 점은 손실 0
  → 출력 f(x) 는 "경계면에서의 거리". 확률이 아니다

로지스틱 (log loss):     Σ −[y·log σ(f) + (1−y)·log(1−σ(f))]  + λ‖w‖²
  → 모든 점이 손실에 기여. 확신 있게 틀리면 크게 벌점
  → 출력 σ(f) = 1/(1+e^(−f)) 가 바로 확률
```

### 1.2 실무적으로 갈리는 지점

| 항목 | SVM | 로지스틱 |
|---|---|---|
**확률** | 없음. Platt scaling 같은 사후 보정이 따로 필요 | **모형의 출력 자체** |
**계수 해석** | "경계면을 미는 방향" — 단위가 모호 | **로그오즈.** `exp(w)` = 오즈비 → "있으면 Pass 오즈 ×16" |
**이상치** | 강건함 (경계 밖은 무시) | 민감함 (확신 있게 틀린 점이 계수를 끌어당김) |
**통계적 추론** | p값·신뢰구간이 표준이 아니다 | **Wald/LR 검정, 표준오차, 신뢰구간이 기본 제공** |
**계층·혼합 모형** | 확장이 어렵다 | **변량효과·상호작용으로 자연스럽게 확장** |
**소표본 분리** | 상대적으로 안정 | 완전분리 시 계수 발산 → Firth 벌점으로 해결 |

### 1.3 이 프로젝트에서 왜 로지스틱이 맞는가

목표 문장이 **"Pass를 받을 확률이 가장 높은 가이드라인"** 이다.

```
SVM:       f(x) = +0.0073    → "Pass 쪽"   (얼마나? 모른다)
로지스틱:  P(Pass) = 0.62     → "62%"      (예산 3개면 79%까지 올라간다 — §4)
```

그리고 **전원 통과**는 확률의 곱으로만 표현된다.

```
P(평가자 5명 전원 Pass | x) = Π_r σ(w·x + b + β_r)
```

SVM의 결정값으로는 이 식을 쓸 수 없다.

---

## 2. 로지스틱 구현 방향 — 3단계

### 단계 1 — 기본 로지스틱 (반나절, 신규 의존성 0)

현행 `modeling.py` 에 함수 하나를 추가한다. SVM과 **같은 입력**(`X`, `y`)을 받는다.

```python
# src/svmtrial/modeling.py 에 추가
import statsmodels.api as sm

def fit_logistic(s: Settings, X: pd.DataFrame, y: pd.Series, l2: float = 1.0) -> dict:
    """기본 로지스틱. SVM 과 같은 입력, 출력은 '확률'.

    소표본 완전분리에 대비해 L2 벌점(ridge)을 기본으로 둔다.
    반환: weights(로그오즈), odds_ratio, intercept, p_values, 그리고 predict_proba
    """
    Xd = sm.add_constant(X.to_numpy(float), has_constant="add")
    model = sm.GLM(y.to_numpy(float), Xd, family=sm.families.Binomial())
    res = model.fit_regularized(alpha=l2, L1_wt=0.0) if l2 else model.fit()
    names = ["_intercept", *X.columns]
    coef = dict(zip(names, res.params, strict=False))
    return {
        "weights": {k: v for k, v in coef.items() if k != "_intercept"},
        "intercept": coef["_intercept"],
        "odds_ratio": {k: float(np.exp(v)) for k, v in coef.items() if k != "_intercept"},
        "p_values": dict(zip(names, getattr(res, "pvalues", [np.nan] * len(names)), strict=False)),
        "predict_proba": lambda Z: 1.0 / (1.0 + np.exp(-(sm.add_constant(
            Z.to_numpy(float), has_constant="add") @ res.params))),
    }
```

> `fit_regularized` 는 `pvalues` 를 주지 않는다. p값이 필요하면 `l2=0` 으로 `fit()` 을 쓰거나,
> 벌점 적합의 계수와 비벌점 적합의 p값을 함께 보고한다(§6 주의사항).

**산출물 변화**: `report.py` 의 등급표에 `오즈비` 열이 추가되고, `diagnose` 가 확률을 낸다.

```
C001 D4 5Why 표   w=+2.78  오즈비 ×16.1   "있으면 Pass 오즈가 16배"
```

### 단계 2 — 단조 제약 + 정수 점수표 (반나절, 신규 의존성 0)

**해결하는 실제 문제**: 현행 샘플 실행에서 `SEC_D6 w=-0.379` 가 나왔다 —
"D6 효과검증 섹션이 **있으면** Pass율이 낮다". 거의 확실히 교란이고,
템플릿이 **"D6을 빼라"** 고 권할 수 있다.

```python
from scipy.optimize import minimize

def fit_monotone_logistic(X, y, nonneg_cols, l2=1.0):
    """존재 특징의 계수를 ≥ 0 으로 제약한다 ('요소 추가가 해를 끼치지 않는다')."""
    Xm = np.c_[np.ones(len(X)), X.to_numpy(float)]
    lo = [-np.inf] + [0.0 if c in nonneg_cols else -np.inf for c in X.columns]
    def nll(w):
        z = Xm @ w
        return np.logaddexp(0, z).sum() - (y.to_numpy(float) * z).sum() + l2 * (w[1:] ** 2).sum()
    r = minimize(nll, np.zeros(Xm.shape[1]), method="L-BFGS-B",
                 bounds=list(zip(lo, [np.inf] * Xm.shape[1], strict=False)))
    return dict(zip(["_intercept", *X.columns], r.x, strict=False))
```

그리고 **사람이 손으로 채점할 수 있는 정수 점수표**(`imodels.SLIMClassifier`, 이미 설치됨):

```
실측 (대책서 샘플):
  점수 = -5  +4×why5  +2×graph  +1×yokoten  +1×signature  -1×colorbox
         → 점수 > 0 이면 '전원 통과' 예측,  훈련 balanced accuracy 0.854
```

SVM 가중치 `0.23312` 를 품질팀에 건네는 것과 비교해 보라. 이건 그냥 **제출 전 체크리스트**다.

### 단계 3 — 계층 로지스틱 (1~2일, 신규 의존성 0) ★ 최종형

현행 설계의 가장 큰 손실은 **390개 평가자 판정을 80개 이진 라벨로 뭉개는 것**이다.
`y_unanimous` 는 5명의 판단을 1비트로 압축한다. 평가자 수준에서 모형을 세우면 된다.

```python
from statsmodels.genmod.bayes_mixed_glm import BinomialBayesMixedGLM
import statsmodels.formula.api as smf

def fit_hierarchical(s, long, X, concepts, rater_interactions=("C002",)):
    """평가자 수준 계층 로지스틱.

    long: labels.py 가 이미 저장하는 평가자 수준 테이블 (doc_id, rater, y)
    X   : 문서 × 개념 행렬 (현행 그대로)
    → 입력 데이터 변경이 필요 없다
    """
    d = long.merge(X.reset_index(), on="doc_id").dropna(subset=["y"])
    terms = " + ".join(concepts)
    f = f"y ~ {terms} + C(rater)"                      # 평가자 고정효과(엄격도)
    for c in rater_interactions:                        # 평가자별로 다르게 보는 요소
        for r in sorted(d.rater.unique()):
            d[f"is_{r}"] = (d.rater == r).astype(int)
            f += f" + is_{r}:{c}"
    fixed = smf.glm(f, data=d, family=sm.families.Binomial()).fit()
    mixed = BinomialBayesMixedGLM.from_formula(      # 문서 공통잡음 흡수
        f, {"doc": "0 + C(doc_id)"}, d).fit_vb()
    return fixed, mixed
```

**측정된 효과** — 정답지 생성 모형을 알고 있으므로 직접 채점했다
(OCR은 완벽하다고 가정해 **방법론만** 비교. 대책서 샘플 82건/410행).

| 요인 | 참값 | 현행 SVM<br>(80건 이진화) | **계층 로지스틱**<br>(390 판정) | +문서변량 |
|---|---|---|---|---|
why5 | 1.9 | 1.07 | **2.78** | 2.82 |
graph | 1.6 | 0.48 | **1.76** | 1.74 |
yokoten | 1.0 | 0.23 | **1.04** | 1.02 |
photo | 0.5 | **−0.09 ❌부호반대** | **0.89** (p=0.005) | 0.82 |
평가자E×서명란 | 3.0 | **표현 불가** | **+2.69** (p=0.0006) | +2.42 |
gantt (미끼) | 0 | — | 0.03 (p=0.93) ✅ | 0.06 |
appendix (미끼) | 0 | — | −0.06 (p=0.84) ✅ | −0.13 |
colorbox (미끼) | 0 | — | −0.96 (p=0.003) **❌오탐** | −0.99 |

읽는 법:
- 계수 **순서**를 정확히 복원. 크기는 참값보다 부풀지만(축소추정 없음) 비율은 맞다
- 정답지가 "40건으로는 검출 불가"라고 못 박은 **약한 요인 `photo` 가 검출**됨. 현행 SVM은 **부호가 반대**
- **평가자E의 서명란 의존은 계층 모형만 표현 가능** (풀링 SVM은 평가자 파라미터 자리가 없다)
- 힘이 세지면 **오탐도 는다** — 미끼 `colorbox` 가 잘못 유의. 축소추정/최소효과크기 문턱 필요(§6)

**적합 비용**

| 방법 | 적합 시간 | 모수 |
|---|---|---|
현행 SVM (80건) | 1.1 ms | 9 |
고정효과 계층 로지스틱 (390행) | **12.4 ms** | 18 |
+ 문서 변량절편 (VB) | 1,446 ms | 고정 18 + 문서절편 80 |
부트스트랩 200회 | 2.5 s | — |

**딥러닝식 학습이 아니라 최대우도추정**이다. 같은 입력이면 계수가 비트 단위로 동일하다(R9 재현성).
`alternatives_withoutLLM.md` 의 M1 과 같은 방안이고, **무상태 보증은 SVM과 동일하게 유지된다**.

---

## 3. 로지스틱이 바꾸는 산출물 — 목적함수 직접 최적화

확률이 나오면 등급표 대신 **"예산별 최적 조합"** 을 낼 수 있다. 이것이 질문에 가장 정확히 답하는 형태다.

```python
# src/svmtrial/objective.py (신규)
def p_all_pass(model, cfg, raters, features):
    """P(평가자 전원 Pass) = Π_r σ(η_r(x))"""
    rows = [{**{c: 0.0 for c in features}, **cfg, "rater": r} for r in raters]
    return float(np.prod(model.predict(pd.DataFrame(rows))))

# 요소가 보통 5~30개이므로 전수탐색 또는 beam search
best = max((p_all_pass(m, {c: 1.0 for c in combo}, R, F), combo)
           for k in range(len(ACT) + 1) for combo in itertools.combinations(ACT, k))
```

**실측 결과**

```
기준(요소 없음)            P(전원 통과) = 0.0000
요소 1개씩:  why5 0.0713 | graph 0.0058 | yokoten 0.0005 | photo 0.0003 | signature 0.0002

요소 수별 최적 조합 (분량 예산이 있을 때):
  1개 → 0.0713   why5
  2개 → 0.5343   why5 + graph
  3개 → 0.7891   why5 + graph + yokoten
  4개 → 0.9075   why5 + graph + yokoten + signature
  5개 → 0.9604   전부
```

> ### ⚠ 기존 설계의 오류 정정 (C1, 재게시)
> `Architecture.md` §4.4 는 "이진 특징에서 각 변경의 이득이 독립이므로 **탐욕이 최소해**"라고 적고 있다.
> **선형 `w·x` 에서는 맞다.** 그러나 목적이 `Π_r σ(·)` 로 바뀌면 이득이 **독립이 아니다** —
> why5 단독은 0.071 인데 why5+graph 는 0.534 다. 여러 요소가 함께 들어가야 각 평가자가 문턱을 넘는다(초모듈성).
> 따라서 새 목적함수에서는 **전수탐색 / beam search / ILP** 로 풀어야 한다. 위 32가지 탐색은 즉시 끝났다.
> **이 정정은 `Architecture.md` 본문에 아직 반영하지 않았다** — 목적함수를 실제로 바꿀 때 함께 수정해야 한다.

---

## 4. SVM·로지스틱 외의 선택지 (요약)

전부 **신규 의존성 0**이고 이미 설치된 패키지로 된다. 자세한 설명은 [`alternatives_withoutLLM.md`](alternatives_withoutLLM.md) M1~M14.

| 방안 | SVM과의 차이 | 산출물 | 난이도 |
|---|---|---|---|
**순서형 회귀** (`statsmodels OrderedModel`) | `n_pass`(0~5)를 이진화하지 않고 순서형으로 | 계수 + 임계값 | **S ← 가장 싸게 큰 개선** |
**SLIM 정수 점수표** (`imodels`) | 실수 가중치 → **정수 배점** | 손 채점 체크리스트 | S |
**규칙 학습** (`imodels` FIGS/BayesianRuleList/FastFrugalTree) | 선형식 → **조건문** | "A이고 B면 Pass" | S~M |
**단조 GBM** (`sklearn monotonic_cst`) | 선형 → 상호작용 허용, 단조성 보장 | 부분의존도 | S |
**평가자별 규칙 교집합** | 풀링 → 평가자별 모델의 논리적 교집합 | 전원통과 필요조건 | M |
**연관규칙** (`mlxtend apriori`) | 모형 없음, 공출현 빈도 | 조합 후보 | S (단독 판정 금지 — 미끼를 못 걸러낸다) |
**Conformal prediction** | 점추정 → **보증된 구간** | "0.62 [0.41, 0.80]" | M |
**베이지안 계층** (`pymc`) | 축소추정으로 **오탐 억제** | 사후분포 `P(효과>0)` | L (신규 의존성) |

### 4.1 LLM이 판정까지 하는 방안 (제약 해제로 열린 선택지)

| 방안 | 내용 | 검증 가능성 |
|---|---|---|
**평가자 페르소나 시뮬레이션** | 평가자별 과거 판정을 few-shot으로 주고 **Claude가 그 평가자를 모사**. 5개 페르소나 전원 Pass → 전원통과 | ★★☆ — **홀드아웃 모사 정확도를 반드시 측정** |
**루브릭 생성 → 배점은 데이터** | Claude가 항목·수준(0/1/2) 정의 → 배점은 SLIM/로지스틱 | ★★★ |
**쌍대비교 → Bradley-Terry** | "A와 B 중 어느 쪽?" 이 절대판정보다 안정적 | ★★☆ |
**사례기반 검색** | 유사 과거 문서 k개의 실제 판정으로 예측 + **근거로 그 문서 제시** | ★★☆ — 현장 설득력 최고, 반나절 |

**페르소나가 목표 정의에 가장 부합**한다 — 보편 법칙을 만들지 않고 개인을 모사한다.
다만 페르소나 출력을 **특징으로 넣어 계층 로지스틱으로 결합**하는 하이브리드가 안전하다(검증 가능성 유지).

---

## 5. LLM을 Claude로 — 전환 방향

제약 해제에 따라 LLM 경로를 Gemini/Vertex → **Claude** 로 바꾼다.
현행 백엔드 추상화가 그대로 쓰이므로 **`claude_backend.py` 하나 추가**면 된다.

### 5.1 구조

```
                호출부 (ingest / ocr / sections / concepts / template_spec / diagnose)
                                      │  gemini_client.generate_json(model, prompt_id, parts, schema)
                        ┌─────────────┴─────────────┐
            SVMTRIAL_BACKEND                         │
     ┌──────────┬───────────┴──────┬────────────────┐
     ▼          ▼                  ▼                ▼
  vertex     offline          **claude**        local (OCR)
```

호출부·스키마·캐시·프롬프트는 **손대지 않는다.** 세 백엔드가 같은 pydantic 스키마를 돌려준다.

### 5.2 구현 스케치

```python
# src/svmtrial/claude_backend.py (신규)
import base64

import anthropic

from svmtrial.parts import Part

# tier → 모델 ID. 대량 처리는 Sonnet, 추론은 Opus.
MODEL_MAP = {"fast": "claude-sonnet-5-5", "pro": "claude-opus-5-5"}


class ClaudeBackend:
    """Claude 백엔드. vertex_backend.VertexBackend 와 같은 generate() 인터페이스.

    우리 파이프라인은 이미 pydantic 스키마 기반이므로 `messages.parse()` 가 가장 잘 맞는다.
    스키마 검증까지 SDK 가 해주고 `response.parsed_output` 으로 인스턴스를 돌려준다.
    """

    def __init__(self, settings):
        self.s = settings
        # ANTHROPIC_API_KEY, 또는 `ant auth login` 프로필을 자동으로 찾는다
        self.client = anthropic.Anthropic()

    def generate(self, *, model, prompt_id, parts, schema, media_resolution=None):
        from svmtrial.gemini_client import Usage

        content = []
        for p in parts:
            if "text" in p:
                content.append({"type": "text", "text": p["text"]})
            else:
                content.append({"type": "image", "source": {
                    "type": "base64", "media_type": p["mime_type"],
                    "data": base64.standard_b64encode(p["bytes"]).decode()}})

        resp = self.client.messages.parse(
            model=model,
            max_tokens=16000,
            thinking={"type": "adaptive"},          # 복잡한 단계(가설 생성/템플릿)에 유리
            output_config={"effort": "high"},       # low ~ max 로 단계별 조절
            messages=[{"role": "user", "content": content}],
            output_format=schema,                   # pydantic 클래스를 그대로 넘긴다
        )
        if resp.stop_reason == "refusal":           # 안전 분류기 거부는 200 으로 온다
            raise RuntimeError(f"Claude refusal: {resp.stop_details}")

        obj = resp.parsed_output                    # 검증된 schema 인스턴스
        u = resp.usage
        return obj.model_dump(mode="json"), Usage(
            prompt_tokens=u.input_tokens,
            output_tokens=u.output_tokens,
        )
```

> **스키마 전달 방식 두 가지**
> - `client.messages.parse(..., output_format=PydanticModel)` → `resp.parsed_output` 로 검증된 인스턴스.
>   **우리 코드가 pydantic 기반이라 이쪽이 맞다.**
> - `client.messages.create(..., output_config={"format": {"type": "json_schema", "schema": {...}}})`
>   → 생 JSON 스키마를 넘기고 첫 text 블록을 `json.loads` 한다. (구 `output_format` 최상위 파라미터는 폐기)
>
> 모델 ID는 `claude-opus-5-5`(기본)·`claude-sonnet-5-5`(대량)을 쓰고 **날짜 접미사를 붙이지 않는다.**
> 프롬프트 캐시(`cache_control={"type": "ephemeral"}`)와 배치(`client.messages.batches`)는
> 비용 절감 수단으로 따로 적용한다(§5.3·§5.4).


### 5.3 Gemini 경로와 달라지는 점

| 항목 | Gemini(Vertex) | **Claude** |
|---|---|---|
인증 | GCP ADC / 서비스계정 | `ANTHROPIC_API_KEY` 또는 `ant auth login` — **훨씬 단순** |
구조화 출력 | `response_schema` | `output_config.format` (또는 `messages.parse()`) |
모델 ID | 사내 프로젝트에서 조회 필요 | `claude-opus-5-5` / `claude-sonnet-5-5` 고정 |
**PDF 직접 입력** | 페이지를 PNG로 변환해야 함 | **PDF를 그대로 보낼 수 있다** (32MB/600쪽) → S1 렌더링 단계 축소 가능 |
프롬프트 캐시 | 지원 | `cache_control` — 반복 호출 비용 대폭 절감 |
배치 | 지원 | `client.messages.batches` — **50% 할인**, OCR 568건에 적합 |
추론 깊이 | — | `output_config.effort` (low~max) 로 단계별 조절 |

**비용 추정** (대책서 샘플 568쪽 기준, 토큰 약 102만)

| 구성 | 모델 | 단가(입력/출력, $/MTok) | 비고 |
|---|---|---|---|
OCR·채점 (대량) | `claude-sonnet-5-5` | 2.00 / 10.00 | 배치 API로 50% 절감 가능 |
가설 생성·템플릿 (소량) | `claude-opus-5-5` | 4.00 / 20.00 | 호출 16회 |

> 실제 청구액은 토큰 수에 의존하므로 `client.messages.count_tokens` 로 사전 측정하고
> `--dry-run` 추정치를 갱신해야 한다. 지금 `config.gemini.cost_per_1m_tokens` 가 비어 있어 비용 추정이 생략된다.

### 5.4 두 가지 큰 기회

1. **PDF 직접 입력** — Claude는 PDF를 네이티브로 받는다. 현행은 `pymupdf` 로 PNG 변환 후 페이지당 1회 호출인데,
   문서 단위로 PDF를 보내면 호출 수가 **568 → 82** 로 줄 수 있다. 단, 페이지별 구조화 출력 품질을
   먼저 비교해야 한다(페이지 단위가 더 정확할 가능성).
2. **배치 API** — OCR은 지연에 민감하지 않다. `messages.batches` 로 **50% 할인**.

---

## 6. 주의사항

| # | 항목 | 내용 |
|---|---|---|
**1** | **벌점과 p값은 함께 오지 않는다** | `fit_regularized` 는 `pvalues` 를 주지 않는다. 벌점 계수와 비벌점 p값을 나눠 보고하거나 부트스트랩 신뢰구간을 쓴다 |
**2** | **완전분리** | 소표본 로지스틱은 계수가 발산할 수 있다. L2 벌점 기본값을 두고, Firth 벌점을 선택지로 (직접 구현 ~40줄) |
**3** | **오탐 증가** | 계층·순서형 모두 미끼 `colorbox` 를 잘못 유의하게 판정했다(−0.96, p=0.003). 축소추정(베이지안) 또는 최소효과크기 문턱이 필요 |
**4** | **실측은 seed 1개** | 샘플 정답지도 "seed 7은 평균보다 신호가 잘 보이는 편"이라고 밝힌다. 여러 seed로 반복해야 결론이 선다 |
**5** | **완벽 OCR 가정** | §2 단계 3의 비교는 정답지 특징을 그대로 썼다. 실제로는 OCR 오차로 계수가 감쇠한다 |
**6** | **평가자 변경** | 계층 모형은 평가자 파라미터를 명시한다 → 평가자가 바뀌면 재적합. 새 평가자는 과거 판정이 쌓이기 전엔 모집단 평균으로 시작 |
**7** | **무상태는 유지된다** | 로지스틱도 배치마다 적합·폐기다. `tests/test_stateless.py` 의 보증이 그대로 적용된다 (단 새 적합 함수에도 테스트 추가 필요) |
**8** | **Claude 전환 시** | 안전 분류기 거부(`stop_reason == "refusal"`) 처리, 캐시 키에 백엔드 포함(이미 구현됨), 모델 변경 시 재검증 |

---

## 7. 권장 진행 순서

| 단계 | 내용 | 신규 의존성 | 난이도 | 얻는 것 |
|---|---|---|---|---|
**1** | 순서형 회귀 + 단조 제약 + SLIM 점수표 | **0** | S | `photo` 검출, D6 음수 제거, 손 채점 점수표 |
**2** | 기본 로지스틱 → `analysis.method` 로 SVM과 병행 | **0** | S | 확률 출력, 오즈비 해석 |
**3** | 계층 로지스틱 + 목적함수 직접 최적화 | **0** | M | 평가자 개인차, 전원통과 확률, 예산별 최적 조합 |
**4** | `claude_backend.py` — LLM 경로를 Claude로 | `anthropic` 1개 | M | 인증 단순화, PDF 직접 입력, 배치 50% 할인 |
**5** | 여러 seed로 1~3 재측정 → 기본 방법 결정 | 0 | S | 결론의 신뢰도 |

1~3단계는 **신규 의존성이 전혀 없어 지금 바로** 할 수 있다. 4단계만 `anthropic` 추가 승인(R5)이 필요하다.

### 병행 운영 — SVM을 버리지 않는다

```yaml
# config/config.yaml
analysis:
  method: hierarchical      # svm | logistic | ordinal | hierarchical | slim | rules
  objective: all_pass_prob  # decision_value | all_pass_prob
  monotone: true            # 존재 특징 계수 ≥ 0
```

같은 데이터에 여러 방법을 돌려 `verify_invariance.py` 와 `_answer_key` 로 비교한다.
**어느 방법을 기본으로 올릴지는 측정으로 정한다** — 지금 결과는 seed 1개라 결론이 아니다.

---

## 8. 결론

| 질문 | 답 |
|---|---|
SVM 말고 다른 방식 | 많다. **로지스틱 계열이 1순위**이고, 순서형·SLIM·규칙·계층까지 전부 신규 의존성 0 |
SVM과의 차이 | 손실함수(hinge vs log)와 **출력(결정값 vs 확률)** 둘뿐. 특징 공학·무상태·문서종류 불변성은 동일 |
로지스틱 구현 방향 | §2 의 3단계. **단계 1은 함수 하나(약 20줄)** 로 시작한다 |
무상태 전제가 깨지는가 | **아니다.** 로지스틱도 배치마다 적합·폐기다. 12 ms, 모수 18개 |
LLM은 | **Claude로.** `claude_backend.py` 하나 추가. PDF 직접 입력과 배치 50% 할인이 큰 기회 |
