# 1014_inputdata.md — 확정 입력(InputData) 반영 파이프라인 재구성

> 2026-10-14 · 사용자 확정 입력 4가지를 파이프라인에 반영했다.
> 입력 안내: [`InputData/README.md`](../InputData/README.md) · 실행: [`Runbook.md` §4ter·§5](Runbook.md) ·
> 시각화 방안: [`visualization_options.md`](visualization_options.md)

---

## 0. 요약

| # | 확정 사항 | 반영 | 검증 |
|---|---|---|---|
| 1 | `InputData/Documents/` 아래 PDF | `input.layout: inputdata`(기본). 하위 폴더·`.PDF` 허용. PDF 아닌 파일·이름 중복·열리지 않는 PDF 는 이슈로 남기고 제외 | 테스트 + 더미 60건 완주 |
| 2 | `InputData/Judgement.xlsx` | 새 판독기. `FileName` + **평가자 열을 값으로 판별** | 예시 시트와 같은 모양으로 테스트 |
| 3 | 값 `p` / `f` / `f(사유)` | 사유 파싱(여러 개·전각 괄호·번호) → 사유 표·통계 → **기준(차원) 후보 K…** | 사유-채점 일치율 97% |
| 4 | 모든 PDF 를 먼저 이미지로 | 렌더 후 **이미지로만** 판단. 형식 판정도 이미지 비율 → 비전. 긴 변 2400px 상한 | A0 페이지·디지털 PDF 테스트 |

기존 구조(SETUP.md §6·§7)는 `input.layout: legacy` 로 그대로 쓸 수 있다. 회귀 없음(§4.2).

---

## 1. 입력 규격

```
InputData/
├─ Documents/          분석할 PDF 전부 (하위 폴더도 읽는다)
└─ Judgement.xlsx      FileName | Person1 | Person2 | Person3 | …
```

| 셀 값 | 해석 |
|---|---|
| `p` `P` `Pass` `합격` `OK` … | Pass |
| `f` `F` `Fail` `불합격` `NG` … | Fail, 사유 없음 |
| `f(향후 계획 미기입)` | Fail + 사유 1개 |
| `f(향후 일정 미기입, 사진 부적합)` | Fail + 사유 2개 (쉼표·세미콜론·줄바꿈·`1. 2.` 번호로 나눔. `/` 는 나누지 않음 — "전/후") |
| `f（사진 부적합）` | 전각 괄호도 된다 (NFKC 정규화) |
| `p(사진 보완 권고)` | Pass. 괄호 안은 **사유로 쓰지 않는다** |
| 빈칸 | 미평가 |

### 1.1 예시 시트의 모양을 그대로 받는다

사용자가 보여준 시트에는 판정 외의 칸이 섞여 있다. 모두 자동으로 처리하고 리포트 §2 에 남긴다.

| 시트 요소 | 처리 | 리포트 표기 예 |
|---|---|---|
| 비어 있는 `Person3` | 제외 | `F열(Person5) — 값이 없음` |
| 머리글 `..` 열 | 제외 | `G열(..) — 값이 없음` |
| "examples of cause" 메모 칸 | **판정값이 아닌 셀이 대부분이면 메모로 보고 제외** | `I열(examples of cause:) — 판정값이 아닌 셀이 대부분(4/4)` |
| 마지막 `..` 행 | 건너뜀 | `건너뛴 행 1행` |
| 같은 FileName 두 번 | 첫 행만 사용 | `중복 FileName: a` |

평가자 열 판정 규칙: 비어 있지 않은 셀의 **절반 이상**이 판정값이면 평가자 열이다.
평가자 열 **안의** 오타(`pp` 등)는 조용히 넘기지 않고 멈춘다 — `work/labels/label_value_errors.csv`.

### 1.2 파일명 맞추기

| 상황 | 처리 |
|---|---|
| 맥에서 만든 한글 파일명(NFD) vs 엑셀(NFC) | 양쪽 모두 NFC 로 맞춘다 |
| `a.pdf` 처럼 확장자를 적음 | 떼고 비교 |
| 대소문자·공백만 다름 (`Doc1 ` vs `doc1.pdf`) | **하나에만** 맞으면 연결하고 이슈로 기록. 둘 이상에 맞으면 연결하지 않는다 |
| `㈜` 같은 호환 문자 | 바꾸지 않는다 (NFKC 를 쓰면 폴더 이름이 원본과 달라진다) |

---

## 2. 이미지 기반 통일

```
PDF (스캔본이든 Word·PPT 에서 내보낸 것이든)
  └─ 페이지마다 PNG  (dpi 150, 긴 변 ≤ 2400px — A3·A0 도면이 섞여도 비슷한 크기)
       ├─ 원본 형식 판정: 이미지 가로/세로 비율 → 애매하면 첫 2페이지를 비전 LLM 으로
       ├─ S2 OCR: 페이지 이미지 → 구조 JSON
       └─ S4c 채점: 개념이 표·그림·형식이면 페이지 이미지도 함께
```

| 이전 | 지금 |
|---|---|
| 형식 판정 1단계가 PDF `creator/producer` (PowerPoint → slides) | **쓰지 않는다.** `creator_hint` 로 참고만 남긴다 |
| `page.rect` 비율 | 렌더한 **이미지 픽셀** 비율 |
| 손상·암호 PDF 하나가 ingest 전체를 멈춤 | 이슈로 남기고 다음 문서로 |

텍스트 레이어는 원래도 쓰지 않았다(`has_text_layer` 는 리포트 숫자). 이번에 형식 판정까지 이미지로 옮겨
**PDF 의 출처와 관계없이 같은 경로**를 탄다.

---

## 3. 사유(cause) — 기준이 곧 차원이다

사용자 이해("각 기준이 차원")가 정확하다. `f(사유)` 는 그 기준을 평가자가 직접 말해 준 것이다.

```
S3 labels      Fail 사유를 편다 → causes_long.csv  (doc_id | rater | cause | cause_key)
               사유별 건수·문서·평가자 → cause_summary.json          ← LLM 없음
S4b concepts   ① 발견셋 문서의 사유 → cause_criteria.v1 → 기준 K001…  (예: "향후 계획" 이 기입되어 있는가?)
               ② Pass/Fail 대조 가설 → C001…            (평가자가 안 적은 기준을 찾는다)
               ②가 ①과 같은 것을 물으면 ②를 뺀다 (뺀 목록은 후보 파일에 남는다)
               → 🔒 사람 검토 (R7 그대로)
S4c score      문서 × 기준 채점 + 사유-채점 일치율
```

### 3.1 누수 방지 규칙 — 그대로 지켰다

| 용도 | 쓰는 사유 | 이유 |
|---|---|---|
| 기준을 **정의** (S4b) | **발견셋 60% 문서의 사유만** | 가설 생성과 같은 규칙. 검증셋 40% 의 방향 확인이 깨끗하게 남는다 |
| 채점을 **점검** (S4c 일치율) | 전체 사유 | 기준을 만드는 데 쓰지 않고 측정이 맞는지만 본다 |
| 검증셋에만 나온 사유 | 기준으로 만들지 않고 후보 파일 `cause_criteria.validation_only` 에 목록 | 🔒 에서 사람이 판단 |

### 3.2 사유-채점 일치율

평가자가 "향후 계획 미기입" 으로 Fail 을 준 문서라면, 비전 채점도 `"향후 계획" 이 기입되어 있는가? → no` 여야 한다.
이 비율이 낮으면 질문 문구가 사유를 잘못 옮겼거나 채점이 틀린 것이다 — **LLM 채점의 정확도를 사람 진술로 재는 유일한 지표**다.
반대 방향(Pass 문서 = 기준 충족)은 확인하지 않는다. Pass 를 준 평가자가 모든 기준을 본 것은 아니기 때문이다.

### 3.3 설정

```yaml
analysis:
  use_causes: true     # false 면 사유를 기준 후보로 쓰지 않는다 (통계 표는 그대로 만든다)
```

---

## 4. 검증 결과

### 4.1 확정 입력 형식 더미 — `data/dummy_input` (offline 백엔드)

예시 시트와 같은 모양(빈 Person 열, `..` 열·행, 메모 칸)에 PDF 60건 × 평가자 4명.
Fail 셀은 대부분 그 문서에 **없는** 숨김 요소를 사유로 적는다. 일부는 사유 없이 `f`,
일부는 기준으로 쓰기 어려운 "기타 형식 미흡", 일부는 있는 요소를 적은 오기다.

| 항목 | 결과 |
|---|---|
| Judgement 판독 | 평가자 열 4개. 제외: `Person5`(빈 열), `..`, 머리글 없는 빈 열, `examples of cause:`(메모). `..` 행 1개 건너뜀 |
| 사유 | Fail 108건 중 사유 기재 97건(90%), 서로 다른 사유 4종 |
| 기준 후보 | 사유 기준 K001~K004 + 가설 C003·C005. 사유 기준과 같은 가설 3개(C001·C002·C004) 자동 제거 |
| 사유-채점 일치율 | 97% (K001 100%, K002 92% — 의도적 오기, K003 100%) |

| 숨김 요소 | 기준 | 등급 | RD | q | 판정 |
|---|---|---|---|---|---|
| 5-Why 표 | K001 | **확정** | +0.340 | 0.023 | ✓ |
| 효과검증 그래프 | K002 | **유력** | +0.196 | 0.287 | ✓ |
| 수평전개 표 | K003 | **유력** | +0.125 | 0.635 | ✓ (안정성 100%) |
| 불량 사진 (잡음) | C005 | 기각 | +0.046 | 1.0 | ✓ |
| 팀 명단 표 (잡음) | C003 | 기각 | +0.016 | 1.0 | ✓ |
| 순열검정 | — | p = **0.002** | | | ✓ |

### 4.2 legacy 회귀

`input.layout: legacy` + `data/dummy` 로 `all` → `verify_dummy.py -g 대책서__CUST_A` **합격** (숨김 3개 `유력` 이상, p=0.002).

### 4.3 테스트

```
./scripts/run_tests.sh       → 221 passed   (183 + 신규 38: tests/test_inputdata.py 외)
ruff check src/ scripts/ tests/ → All checks passed
```

신규 테스트가 고정한 것: 셀 값 해석 16종, 예시 시트 모양 판독, 메모 칸 판별 경계, 오타 시 중단,
파일명 정규화(NFD·확장자·경로·㈜), 느슨한 매칭의 모호성 거부, 입력 폴더 이슈, A0 페이지 크기 상한,
creator 무시, 사유 기준의 **발견셋 전용**(누수), `use_causes: false`, 사유-채점 일치율, 아래 버그 회귀.

---

## 5. 이번에 발견해 고친 기존 버그

| 버그 | 증상 | 원인 | 수정 |
|---|---|---|---|
| 기본 설정(`method: svm`)에서 `all` 이 S5+ 에서 중단 | `TypeError: Object of type bool is not JSON serializable` | SVM 가중치가 numpy 스칼라로 남아 `objective.json` 의 비교 결과가 `numpy.bool` 이 됨. 직전 커밋(5b4fa10, 목적함수 추가)에서 유입 — 그때 검증은 logistic/hierarchical 로 돌려 드러나지 않았다 | `fit_primary` 가 float 로 돌려주고, `shortfall` 의 비교도 `bool()` 로 감쌈. 회귀 테스트 추가 |

---

## 6. 바뀐 파일

```
신규  InputData/README.md                입력 안내 (git 은 이것만 추적)
      prompts/cause_criteria.v1.md       사유 → 기준. 도메인 용어 없음(자율 모드 공용, 테스트로 고정)
      tests/test_inputdata.py            38건
      scripts/viz_gallery.py             시각화 후보 미리보기 (파이프라인 미통합)
      docs/1014_inputdata.md, docs/visualization_options.md, docs/img/viz/*.png
수정  config.py / config.yaml            input: 절, ingest.max_long_edge_px, analysis.use_causes
      groups.py                          InputData 단일 그룹, 문서 목록·입력 이슈
      ingest.py                          이미지 우선 렌더, 이미지로만 형식 판정, 손상 PDF 격리, 느슨한 매칭
      labels.py                          Judgement 판독, f(사유) 파싱, 사유 표·통계
      concepts.py / schemas.py           사유 기준(source·cause_refs), 중복 가설 제거, 사유-채점 일치율
      offline_backend.py                 cause_criteria 스텁 (STUB_VERSION 8)
      report.py / cli.py / diagnose.py   사유 절, 입력 이슈 표기, --group 생략 가능
      textutil.py                        doc_id 정규화, 시트↔PDF 매칭
      modeling.py / objective.py         §5 버그 수정
      make_dummy_data.py                 --layout inputdata
      .gitignore / build_release.py      InputData 보호 (README 만 허용)
```

---

## 7. 결정 사항과 남은 질문

### 7.1 결정 (2026-10-14 사용자 답변)

| # | 질문 | 결정 | 반영 |
|---|---|---|---|
| D1 | Documents 에 문서 종류가 섞이는가? | **섞이지 않는다.** 한 번에 한 종류만 넣는다 (대책서면 대책서만) | 단일 그룹 그대로. 종류를 알면 `input.doc_type` 에 적는다 — 그룹 이름이 되고, 대책서면 8D seed 를 쓴다(자율 모드 제외) |
| D2 | 사유 문구를 LLM 에 보내도 되는가? | **보내도 된다** | 그대로 유지. 평가자 이름·문서명은 빼고 사유 문구와 건수만 보낸다 (README 데이터 취급 절에 기록) |
| D3 | 3D·인터랙티브 시각화 도구 | **plotly 도 되고 matplotlib 도 된다** | plotly 를 실제로 추가할 때는 R5 절차대로 Windows wheel 을 확인한다. 아직 추가하지 않았다 |

### 7.2 남은 질문

| # | 질문 | 지금 동작 | 영향 |
|---|---|---|---|
| Q2 | 이번에 넣을 문서 종류를 `input.doc_type` 에 적을 것인가? | 비어 있음 → 그룹 이름 `InputData`, 섹션 seed 없음 | 적으면 그룹 이름이 그 종류가 되고(출력 폴더명), 대책서면 8D seed 를 쓴다 |
| Q3 | Person 열에 실명을 쓰는가? | 열 이름 그대로 리포트에 나온다. 템플릿 단계의 "평가자 간 충돌" 문장에도 들어가 LLM 에 간다(이번 변경 전부터) | 리포트를 공유할 때 주의. 필요하면 LLM 에 보낼 때만 익명화하도록 고칠 수 있다 |
| Q7 | 리포트에 넣을 시각화 | 미통합 | [`visualization_options.md`](visualization_options.md) §4 — 추천 V1·V5·V6 |
| Q4 | `p(코멘트)` 의 코멘트도 쓸 것인가? | 사유로 쓰지 않음 (labels_long 에는 남음) | 쓰면 "Pass 이지만 보완 권고" 같은 약한 신호를 얻는다 |
| Q5 | 사유 표기가 정해진 목록에서 고르는 방식인가, 자유 서술인가? | 자유 서술로 보고 띄어쓰기·기호만 묶음, 의미 묶음은 LLM | 목록이 있다면 그 목록을 기준 seed 로 넣을 수 있다 |
| Q6 | FileName 이 숫자인가? | 문자열로 비교 | 엑셀이 `001` 을 `1` 로 바꾸면 매칭 실패 → 셀 서식을 텍스트로 |

---

## 8. 재현

```bash
conda activate Valeo_SVM_Trial && unset PYTHONPATH
python scripts/make_dummy_data.py --layout inputdata --out data/dummy_input --n 60 --seed 3
sed -e 's|^  root: InputData |  root: data/dummy_input/InputData |' config/config.yaml > /tmp/config_input.yaml
python -m svmtrial all --config /tmp/config_input.yaml
python -m svmtrial approve --gate sections --config /tmp/config_input.yaml
python -m svmtrial all --config /tmp/config_input.yaml
python -m svmtrial approve --gate concepts --config /tmp/config_input.yaml
python -m svmtrial all --config /tmp/config_input.yaml
python scripts/viz_gallery.py --config /tmp/config_input.yaml --out outputs/viz
```
