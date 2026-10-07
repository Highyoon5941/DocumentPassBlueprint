# 1007_night_SVM.md — SVM 구현 보고서

> **질문**: 학습 없이, 어떤 형식의 문서든 Pass/Fail 평가와 함께 주면 그 문서들의 성격에 맞는
> 가이드라인을 내는 파이프라인 — 이것이 SVM으로 가능한가?
> **답: 가능하다. 그리고 이미 그렇게 구현돼 있었다.** 다만 SVM의 용도가 통상적 이해와 다르다.
> 브랜치: `SVM` · 짝 문서: [`1007_night_alternatives.md`](1007_night_alternatives.md)
> 작성 2026-10-07 · 모든 수치는 실측

---

## 0. 세 줄 요약

| 질문 | 답 |
|---|---|
SVM으로 가능한가 | **가능하다.** 지속되는 학습 모델을 만들지 않고, **매 실행마다 주어진 배치에서만 적합**해 폐기한다 |
통상적 SVM 이해와 어떻게 다른가 | 목적이 **판별(1/0 예측)이 아니라 경계면의 법선벡터 `w` 를 읽는 것**이다. 예측은 부산물이고 `w` 가 산출물이다 |
문서 성격에 국한되는가 | **아니다.** 완전히 다른 문서종류(요구사양서)로 **코드 변경 0줄** 실행해 그 문서종류의 숨김 규칙 3개를 모두 복원했다 (§4) |

---

## 1. 용어 정리 — 무엇이 "학습"인가

질문의 전제를 정확히 옮기면 두 가지가 섞여 있다.

| 우려 | 실제 |
|---|---|
**(A)** 학습된 모델이 생겨서 그 문서 성격에 묶인다 | **정당한 우려.** 그리고 이 파이프라인은 그런 모델을 만들지 않는다 |
**(B)** 적합(parameter estimation) 자체를 하지 않는다 | **불가능하다.** Pass/Fail 라벨에서 가이드라인을 뽑으려면 어떤 규칙성이든 추정해야 한다 |

(B)를 하지 않으면 라벨을 쓰지 않는다는 뜻이고, 그러면 "Pass 확률이 높은 가이드라인"이라는 목표가 성립하지 않는다.
그래서 정확한 목표는 **"지속되는 범용 모델을 만들지 않는다"** 이고, 그것이 이 설계가 하는 일이다.

```
❌ 하지 않는 것:  문서 1만 건으로 범용 모델을 학습 → 저장 → 새 문서종류에 적용
✅ 하는 것:       주어진 배치(이 문서종류 × 이 고객사 × 이 평가자들)에서만 적합
                 → 가이드라인 산출 → 모델 폐기. 다음 배치는 처음부터 다시
```

통계학 용어로는 **전이적(transductive)** 접근이다. 평균이나 상관계수를 계산하는 것을 "모델을 학습했다"고
부르지 않는 것과 같은 범주다 — SVM 적합은 **1.1 밀리초**, 모수 9개다(§3).

---

## 2. 통상적 SVM 이해와 무엇이 다른가

| 항목 | 일반적인 SVM 사용 | **이 파이프라인** |
|---|---|---|
**목적** | 판별 — 새 입력이 1인가 0인가 | **역추적 — 경계면의 방정식을 읽는다** |
**산출물** | 예측 라벨 / 정확도 | **법선벡터 `w` = "어떤 요소가 Pass 쪽으로 미는가"** |
**모델 수명** | 학습 → 저장 → 재사용 | **적합 → 사용 → 폐기.** 모델 파일이 없다 |
**새 데이터** | 저장된 모델로 추론 | 새 배치는 **처음부터 다시 적합** |
**평가 기준** | 정확도 / F1 | **해석성.** 정확도는 "경계면이 의미가 있나"의 건전성 검사일 뿐 |
**특징** | 무엇이든 (임베딩 포함) | **사람이 읽을 수 있는 예/아니오만** (Concept Bottleneck) |

### 2.1 왜 `w` 가 산출물인가

상사의 비유가 그대로 구현이다.

> "평면에 Pass와 Fail 점이 찍혀 있고 경계선의 방정식은 모른다.
> 결과로 경계선을 찾고, Fail 문서가 Pass 쪽으로 가려면 무엇을 바꿔야 하는지 가이드한다."

```
f(x) = w·x + b          ← 경계면
w[C001] = +0.233        ← C001 = 'D4에 "5Why 단계적 원인분석 표"가 있는가?'
                          ↓ 번역
        "D4에 5Why 표를 넣어라 (근거: 있을 때 Pass율 61% vs 없을 때 5%)"
```

**이것이 SVM을 "판별기"가 아니라 "역문제 풀이 도구"로 쓰는 것이다.**
판별 정확도는 `DummyClassifier` 와 비교해 "경계면이 우연이 아닌가"만 확인한다(순열검정 p<0.05).

### 2.2 그래서 무엇이 필요하고 무엇이 안 필요한가

| | 필요 | 이유 |
|---|---|---|
학습 데이터셋 (대규모) | ❌ | 배치 하나가 전부다 |
사전학습 / 파인튜닝 | ❌ | SVM은 사전학습 개념이 없다 |
GPU | ❌ | 1.1 ms, CPU |
모델 파일 / 체크포인트 | ❌ | 만들지 않는다 (§3.1에서 코드로 검증) |
**주어진 배치의 Pass/Fail 라벨** | ✅ | 이것이 유일한 입력 |
**배치당 문서 수** | ✅ | 40~80건이 현실적 하한 (§6) |

---

## 3. 구현 — 무상태 보증

### 3.1 모델이 디스크에 저장되지 않는다

```bash
grep -rnE "pickle|joblib|torch\.save" src/svmtrial/
# → 결과 없음 (docx/pptx/png 의 .save() 와 pydantic 의 model_dump 만 존재)
```

`work/models/<group>/results.json` 에 **계수 값은 기록**하지만 이것은 리포트용 숫자이고,
다음 실행이 이 파일을 읽어 모델을 복원하는 경로는 없다. `diagnose`(새 문서 진단)조차
매번 다시 적합한다:

```python
# diagnose.py:74
fit = modeling.fit_svm_weights(s, Xi, y, primary)   # 저장된 모델을 로드하지 않는다
```

### 3.2 역할 분리 — 계수 층은 LLM과 무관

```
LLM 호출 있음:  ingest  ocr  sections  concepts  template_spec  diagnose
LLM 호출 없음:  labels  features  modeling  counterfactual  report
```

`modeling.py` 는 `gemini_client` 를 **import 조차 하지 않는다.** sklearn·statsmodels·scipy만 쓴다.

```
LLM이 결정:    무엇을 볼지 (개념 후보) + 그 문서에 있는지 (yes/no)
데이터가 결정:  얼마나 중요한지 (계수 w)
```

### 3.3 테스트로 고정했다 — `tests/test_stateless.py` (8건)

| 테스트 | 막는 회귀 |
|---|---|
`test_no_model_serialization_in_source` | pickle/joblib/torch.save 도입 |
`test_modeling_does_not_import_llm` | 계수 층에 LLM 유입 |
`test_fit_is_deterministic` | 숨은 상태 (같은 입력 → 같은 계수) |
`test_fit_depends_only_on_given_batch` | 이전 배치가 다음 배치에 영향 |
`test_seed_absent_for_unknown_doc_type` | 새 문서종류에 대책서 seed 유출 |
`test_empty_seed_does_not_fall_back_to_8d` | 빈 seed → 8D 폴백 (§4.2 버그) |
`test_title_doc_count_threshold_uses_doc_counts` | 문서 수 임계값 오판 (§4.2 버그) |
`test_generalized_heading_pattern_matches_non_8d` | 제목 패턴의 문서종류 종속 (§4.2 버그) |

---

## 4. 문서종류 불변성 — 실증

"학습된 문서의 성격에만 국한될 수 있다"는 우려를 **다른 문서종류로 직접 실행해** 확인했다.

### 4.1 실험 설계

`scripts/make_dummy_reqspec.py` 를 새로 만들어, 기존 대책서와 **모든 축을 다르게** 했다.

| 축 | 기존 (대책서) | 신규 (요구사양서) |
|---|---|---|
문서종류 | 대책서 | **요구사양서** |
섹션 체계 | 8D (D1~D8) — `seeds.py` 에 seed 있음 | **R1~R8 — seed 없음(데이터에서 유도해야 함)** |
숨김 규칙 | 5Why 표 / 효과검증 그래프 / 수평전개 표 | **합격판정기준 표(w=2.0) / 추적성 표(1.3) / 규격 상·하한 표(0.9)** |
미끼 | 현물 사진 / 팀 명단 표 | **용어정의 표 / 개정이력 표** |
평가자 | 5명 `rater_A~E` | **4명 `reviewer_1~4`** |
문서 수 | 40 | 60 |

### 4.2 이 과정에서 드러난 실제 버그 3건 — 모두 문서종류 종속

문서종류를 바꾸자마자 **대책서에 암묵적으로 묶여 있던 결함 3개**가 드러났다.
(세 개 모두 `offline_backend.py`, 즉 LLM을 대체하는 개발용 스텁에 있었다. 실제 LLM 경로는 영향 없다.)

| # | 버그 | 증상 | 수정 |
|---|---|---|---|
**B1** | `seed = data.get("seed") or SEED_8D` | 빈 목록이 falsy → **새 문서종류가 대책서의 8D 섹션을 물려받음** | 키 존재 여부로 구분 |
**B2** | `_HEAD_HINT = r"^\s*(d[0-9]\b\|...)"` | `D0~D9` 만 제목으로 인식 → **R1~R8 제목이 하나도 안 잡힘**(`observed_titles: []`) | 영문 1~3자+숫자, `1.`, `1.2`, `제3장`, `III.` 까지 일반화 |
**B3** | `unmatched[title] += 1` 후 `>= 2` 필터 | 제목 목록은 중복 제거돼 있어 전부 1 → **임계값이 모든 섹션을 탈락시킴** | 호출부가 넘기는 `title_doc_counts` 사용 |

> **이것이 이번 작업의 핵심 가치다.** "문서종류에 국한되지 않는다"는 주장은 다른 문서종류로
> 실제로 돌려보기 전까지는 검증되지 않은 가정이었다. 돌려보니 3건이 걸렸다.
> 세 버그는 모두 테스트로 고정했다(§3.3).

### 4.3 결과 — 파이프라인 로직 변경 0줄

```
$ python -m svmtrial all -g 요구사양서__CUST_R --config <cfg>

S1 ingest: 문서 60건 / 페이지 171장 / 형식 docs
S2 ocr: 페이지 171장 / 평균 판독성 0.95
S3 labels: α=0.292 / 타깃 unanimous (y_unanimous)
S4a sections: 분류체계 7개 / OTHER 제목 0개
S4b concepts discover: 개념 후보 5개
S4c concepts score: 개념 5개 / 구조특징 16개 / 불안정 0개
S5 model: 등급 {'확정': 2, '유력': 3, '참고': 3, '기각': 6} / 순열검정 p=0.002
```

**데이터에서 유도된 분류체계** (8D 가 아니다):

```
X1 | R3 Functional requirements        X5 | R1 Scope and applicability
X2 | R7 Verification and test          X6 | R2 Terms and definitions
X3 | R4 Performance requirements       X7 | R6 Interface requirements
X4 | R5 Environmental and reliability
```

**데이터에서 유도된 개념 후보** (5Why·수평전개와 무관하다):

```
C001 [structure] X6  "Glossary table: Term | Definition"                      ← 미끼
C002 [structure] X2  "Acceptance criteria table: Item | Spec | Method | Ju…"   ← 숨김규칙 1
C003 [structure] X1  "Traceability matrix table: ReqID | Test case | Status"   ← 숨김규칙 2
C004 [format]    X2  "R8 Approval"
C005 [structure] X3  "Tolerance table: Parameter | Min | Nom | Max | Unit"     ← 숨김규칙 3
```

**정답지 대조** (`scripts/verify_invariance.py -g 요구사양서__CUST_R --preset reqspec`):

| 숨김 요인 | 참 가중치 | 개념 | 등급 | RD | 판정 |
|---|---|---|---|---|---|
합격판정기준 표 | 2.0 | C002 | **확정** | +0.519 | ✓ |
추적성 표 | 1.3 | C003 | **유력** | +0.215 | ✓ |
규격 상·하한 표 | 0.9 | C005 | **유력** | +0.177 | ✓ |
용어정의 표 (미끼) | 0.0 | C001 | **기각** | +0.038 | ✓ |
개정이력 표 (미끼) | 0.0 | — | 후보에도 없음 | — | ✓ |
순열검정 | — | — | **p = 0.002** | — | ✓ |

```
문서종류 불변성 검증 [요구사양서]: 합격
```

### 4.4 대책서 회귀 없음

같은 코드로 기존 대책서도 여전히 통과한다.

```
5Why 근본원인 표   1.0  C001  유력  +0.333 ✓
효과검증 그래프     1.0  C003  유력  +0.125 ✓
수평전개 표        1.0  C004  유력  +0.125 ✓
현물 사진(미끼)     0.0  C005  기각  -0.108 ✓
순열검정 p = 0.002                        ✓
문서종류 불변성 검증 [대책서]: 합격
```

---

## 5. 왜 문서종류에 묶이지 않는가 — 구조적 이유

특징 자체가 **배치에서 유도**되기 때문이다. 고정된 특징 집합이 없다.

```
배치 입력 (PDF들 + Pass/Fail)
   │
   ├─ S4a  섹션 분류체계를 이 배치의 제목들에서 만든다        ← 고정 목록 없음
   │        (seeds.py 의 seed 는 '초기값'이고, 없으면 빈 목록)
   │
   ├─ S4b  개념(특징)을 이 배치의 Pass/Fail 대조에서 만든다   ← 고정 특징 없음
   │        → 🔒 사람이 검토·수정·추가
   │
   ├─ S4c  이 배치의 문서들을 그 개념으로 채점 → X 행렬
   │
   └─ S5   X 와 이 배치의 라벨로 적합 → w → 가이드라인 → 폐기
```

대책서에 고유한 것은 `seeds.py` 의 8D **초기값 하나뿐**이고, 그것도:
- 모르는 문서종류에는 `seed_for()` 가 빈 목록을 돌려준다
- 있어도 "수정 가능한 출발점"이며 🔒에서 사람이 바꾼다

새 문서종류를 추가하는 작업은 **선택적으로** `seeds.py` 에 항목을 넣는 것뿐이고, 넣지 않아도 동작한다(§4.3이 그 증거).

---

## 6. 한계 — 정직하게

| # | 한계 | 내용 |
|---|---|---|
**1** | **배치당 문서 수** | 라벨 없이는 불가능하고, 적으면 검출력이 없다. 샘플 정답지 시뮬레이션: 그룹당 40건에서 가장 강한 요인조차 44%, 80건에서 92% |
**2** | **새 문서에 대한 일반화는 유지해야 한다** | "주어진 문서들만"을 문자 그대로 받으면 최적해는 '외우기'이고 템플릿의 가치가 0이다. 평가자 방향은 전이적, **문서 방향은 귀납적** |
**3** | **평가자가 바뀌면 다시 실행** | 이 평가자 패널 전용 결과다. 그것이 "보편성 없이"의 대가 |
**4** | **SVM은 확률을 주지 않는다** | 결정값 `f(x)` 는 확률이 아니다. "Pass 확률을 높이도록"이 목표라면 로지스틱이 더 맞다 → 짝 문서 |
**5** | **미끼 오탐** | 대책서에서 팀 명단 표(미끼)가 `유력` 으로 올라왔다. 축소추정이나 최소효과크기 문턱이 필요 |
**6** | **실측은 합성 데이터** | 두 문서종류 모두 생성기로 만든 것이다. 실데이터는 구조가 더 들쭉날쭉하다 |
**7** | **offline 백엔드 한계** | §4 실험은 offline 스텁으로 돌렸다. 실 스캔 이미지는 읽지 못한다 → 로컬 OCR 또는 Claude 전환 필요 |

### 6.1 §4 실험이 증명하지 못한 것

offline 스텁은 fixture를 조회하므로 **OCR 품질 문제를 우회한다.** 따라서 §4는
**"파이프라인 로직이 문서종류에 묶이지 않는다"** 는 것만 증명하고,
**"실제 스캔본에서 새 문서종류의 요소를 읽어낼 수 있다"** 는 증명하지 않는다.
후자는 LLM(Claude) 또는 로컬 OCR로 다시 확인해야 한다.

---

## 7. 추가·변경된 파일 (브랜치 `SVM`)

```
신규
  scripts/make_dummy_reqspec.py     요구사양서 생성기 (문서종류 불변성 실험용)
  scripts/verify_invariance.py      숨김 규칙 사양을 인자로 받는 범용 검증기
                                    (--preset taisakusho | reqspec | --spec <json>)
  tests/test_stateless.py           무상태·문서종류 불변 보증 8건
  docs/1007_night_SVM.md            이 문서
  docs/1007_night_alternatives.md   짝 문서

수정 (모두 offline_backend.py — LLM 대체 스텁의 문서종류 종속 버그)
  src/svmtrial/offline_backend.py   B1 seed 폴백 / B2 제목 패턴 / B3 문서 수 임계값
                                    STUB_VERSION 3 → 6 (캐시 자동 무효화)

변경 없음
  modeling.py  features.py  counterfactual.py  labels.py  sections.py  concepts.py
  template_spec.py  render_*.py  report.py  diagnose.py  cli.py  schemas.py
  → 파이프라인 로직은 한 줄도 바꾸지 않았다
```

### 7.1 검증 상태

```
./scripts/run_tests.sh                → 147 passed (139 + 신규 8)
ruff check src/ scripts/ tests/       → All checks passed
verify_invariance -g 요구사양서__CUST_R  → 합격
verify_invariance -g 대책서__CUST_A     → 합격 (회귀 없음)
```

### 7.2 재현 명령

```bash
conda activate Valeo_SVM_Trial && unset PYTHONPATH

# 요구사양서 (신규 문서종류)
python scripts/make_dummy_reqspec.py --out data/reqspec --n 60
sed -e 's|data/raw/pdfs|data/reqspec/pdfs|' -e 's|data/raw/labels|data/reqspec/labels|' \
    config/config.yaml > /tmp/cfg_req.yaml
G=요구사양서__CUST_R
python -m svmtrial all -g $G --config /tmp/cfg_req.yaml          # 🔒 섹션
python -m svmtrial approve -g $G --gate sections --config /tmp/cfg_req.yaml
python -m svmtrial all -g $G --config /tmp/cfg_req.yaml          # 🔒 개념
python -m svmtrial approve -g $G --gate concepts --config /tmp/cfg_req.yaml
python -m svmtrial all -g $G --config /tmp/cfg_req.yaml          # 완주
python scripts/verify_invariance.py -g $G --preset reqspec --config /tmp/cfg_req.yaml
```

---

## 8. 결론

**SVM으로 가능하다.** 단 "학습된 판별기"가 아니라 **"배치마다 경계면을 다시 구해 법선벡터를 읽는 역추적 도구"** 로 쓴다.
지속되는 모델이 없으므로 문서 성격에 묶이지 않고, 그 주장을 다른 문서종류로 실증했다(§4).

다만 §6.4의 한계가 남는다 — **SVM은 확률을 주지 않는다.** 목표가 "Pass **확률**이 가장 높은 가이드라인"이라면
로지스틱 계열이 구조적으로 더 맞다. 그 구현 방향은 짝 문서 [`1007_night_alternatives.md`](1007_night_alternatives.md) 에 있고,
**SVM을 버리지 않고 설정으로 병행**하는 방식을 제안한다.
