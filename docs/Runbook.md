# Runbook.md

> Valeo_SVMtrial — 실행 방법
> 개념과 구조: [`Architecture.md`](Architecture.md) · 사내망 전환: [`migration_vertexAI.md`](migration_vertexAI.md) · 설계 기준: [`Valeo_SVMtrial_SETUP.md`](Valeo_SVMtrial_SETUP.md)
> 작성 2026-10-05

---

## 0. 지금 이 PC 의 상태

| 항목 | 값 |
|---|---|
| conda 환경 이름 | **`Valeo_SVM_Trial`** (Python 3.11, CPU 전용, 약 1.0 GB) |
| 프로젝트 루트 | `/home/kty2/Valeo_SVM_trial1` |
| 백엔드 | **`offline`** — Gemini 를 호출하지 않는 결정론 스텁 |
| 실데이터 분석 | ❌ 불가. [`migration_vertexAI.md`](migration_vertexAI.md) 로 전환해야 한다 |
| 더미 데이터 | `data/dummy/` (PDF 80건, 평가 400행) — 파이프라인 검증용 |

---

## 1. 환경 준비

### 1.1 이미 만들어져 있다

```bash
conda activate Valeo_SVM_Trial
python scripts/smoke_test.py          # ALL SMOKE TESTS PASSED
```

### 1.2 처음부터 다시 만들 때

```bash
cd /home/kty2/Valeo_SVM_trial1
conda env create -f environment.yml     # 환경 이름 Valeo_SVM_Trial
conda activate Valeo_SVM_Trial
pip install -e .                        # python -m svmtrial 를 쓰려면 필요
cp .env.example .env
python scripts/smoke_test.py
```

> `environment.yml` 의 환경 이름은 `Valeo_SVM_Trial` 이다.
> (`Valeo_SVMtrial_SETUP.md` §5.2 는 `valeo_svm` 이라고 적고 있지만, 사용자 지시에 따라 변경했다.
> Windows 배포 스크립트도 같은 이름을 쓴다.)

### 1.3 ⚠ 이 PC 에서 반드시 알아야 할 것 — `PYTHONPATH`

이 PC 에는 ROS Humble 이 설치돼 있고, **`PYTHONPATH` 에 Python 3.10 경로를 주입한다.**

```
/opt/ros/humble/lib/python3.10/site-packages
/opt/ros/humble/local/lib/python3.10/dist-packages
```

Python 3.11 환경에서 3.10 패키지가 import 되면 재현 불가능한 오류가 난다.
**특히 `pytest` 는 그 경로의 플러그인을 자동 로드하려다 수집 단계에서 깨진다.**

| 증상 | 조치 |
|---|---|
| 실행할 때마다 `⚠ 다른 파이썬 버전의 경로가 sys.path에 있습니다` 경고 | 아래 조치. 경고만 뜨고 동작은 한다 |
| `pytest` 가 `ModuleNotFoundError: No module named 'lark'` 로 죽는다 | **반드시** 아래 조치 |

```bash
unset PYTHONPATH            # 현재 셸에만 적용
# 또는 명령 하나에만
PYTHONPATH= python -m svmtrial doctor
```

테스트는 `scripts/run_tests.sh` 를 쓰면 이것이 자동으로 처리된다.
Windows 배포본의 `.bat` 파일에도 `set PYTHONPATH=` 가 들어 있다.

---

## 2. 명령 한눈에 보기

```
python -m svmtrial <명령> [옵션]
```

| 명령 | 단계 | 하는 일 | Gemini |
|---|---|---|---|
| `doctor` | — | 환경·설정 점검, 그룹 탐색 | 없음 |
| `ingest` | S1 | PDF → 페이지 PNG + 메타 + 형식(docs/slides) 판정 | 조건부 |
| `ocr` | S2 | 페이지 구조 추출 → `doc_outline.json` | **대량** |
| `labels` | S3 | 평가자 일치도(κ/α), Dawid-Skene, 타깃 결정 | 없음 |
| `sections` | S4a | 섹션 분류체계 후보 생성 **🔒** + 제목 매핑 | 있음 |
| `concepts discover` | S4b | Pass/Fail 대조로 개념 가설 생성 **🔒** | 있음 |
| `concepts score` | S4c | 문서 × 개념 0/1 행렬 + 구조 특징 | **대량** |
| `model` | S5 | 단변량 + 다변량 + 안정성 + 등급 + 평가자별 + 반사실 | 없음 |
| `template` | S6 | `template_spec.json/md` + `template.docx\|pptx` **🔒** | 있음 |
| `report` | — | `analysis_report.md` + 차트 | 없음 |
| `diagnose` | S7 | 새 문서 1건 진단 → 보완 목록 | 있음 |
| `all` | S1~S6 | 전체 실행. 🔒 관문에서 멈춘다 | — |
| `approve` | 🔒 | 후보 파일 → 승인 파일 (**사람이 직접 실행**) | 없음 |

공통 옵션

| 옵션 | 의미 |
|---|---|
| `--group`, `-g` | 분석 그룹 (예: `대책서__CUST_A`). 생략하면 발견된 모든 그룹 |
| `--dry-run` | Gemini 호출 없이 **호출 수와 예상 토큰만** 출력 |
| `--force` | 캐시를 무시하고 다시 호출 |
| `--config` | `config.yaml` 경로 (기본 `config/config.yaml`) |
| `--limit` | (`ocr` 전용) 앞 N개 문서만 — 시험용 |

---

## 3. 가장 먼저: `doctor`

```bash
PYTHONPATH= python -m svmtrial doctor
```

```
┌───────────────────────┬──────────────────────────────────────────────┐
│ python                │ 3.11.17                                      │
│ backend               │ offline                                      │
│ GOOGLE_CLOUD_PROJECT  │ your-gcp-project-id                          │
│ GEMINI_MODEL_FAST     │ (없음)                                        │
│ PDF 경로              │ .../data/dummy/pdfs (있음)                    │
│ 평가 시트 경로        │ .../data/dummy/labels (있음)                  │
│ PYTHONPATH 위생       │ 다른 파이썬 버전의 경로가 sys.path에 있습니다  │
│ 발견된 그룹           │ 대책서__CUST_A, 대책서__CUST_B                │
└───────────────────────┴──────────────────────────────────────────────┘
```

`발견된 그룹` 이 비어 있으면 입력 경로 구조(§5.1)를 확인한다.

---

## 4. 더미 데이터로 전체 돌려보기 (처음 한 번은 이것부터)

### 4.1 더미 데이터 생성

```bash
PYTHONPATH= python scripts/make_dummy_data.py --out data/dummy --n 40
```

```
PDF 80개, 평가 400행 → data/dummy
숨김 정답 → data/dummy/truth.csv  (숨김 규칙: why5, graph, yokoten / 잡음: photo, org)
offline fixture 232페이지 → work/offline_fixtures/pages.json
```

더미 데이터에는 **숨겨진 정답 규칙 3개**가 심어져 있다.
5Why 근본원인 표 / 효과검증 그래프 / 수평전개 표 가 있을수록 Pass 확률이 올라간다.
여기에 영향 없는 **잡음 요소 2개**(불량품 사진, 팀 명단 표)도 섞여 있다.
파이프라인이 앞의 3개를 찾아내고 뒤의 2개를 걸러내면 정상 동작이다.

### 4.2 더미용 config 만들기

기본 `config.yaml` 은 `data/raw/` 를 가리킨다. 더미는 `data/dummy/` 이므로 사본을 만든다.

```bash
sed -e 's|data/raw/pdfs|data/dummy/pdfs|' -e 's|data/raw/labels|data/dummy/labels|' \
    config/config.yaml > /tmp/config_dummy.yaml
export CFG=/tmp/config_dummy.yaml
```

### 4.3 실행 — 🔒 관문에서 두 번 멈춘다

```bash
export PYTHONPATH=
export G=대책서__CUST_A

# ① 1차 실행 → 섹션 분류체계 승인 대기에서 멈춘다
python -m svmtrial all --group $G --config $CFG

# ② 후보 파일을 열어 검토한다 (사람이 하는 일)
cat work/sections/$G/taxonomy_candidates.yaml

# ③ 승인
python -m svmtrial approve --group $G --gate sections --config $CFG

# ④ 2차 실행 → 개념 승인 대기에서 멈춘다
python -m svmtrial all --group $G --config $CFG

# ⑤ 개념 후보를 열어 검토한다 (사람이 하는 일 — 가장 중요)
cat work/concepts/$G/concepts_candidates.yaml

# ⑥ 승인
python -m svmtrial approve --group $G --gate concepts --config $CFG

# ⑦ 3차 실행 → 완주
python -m svmtrial all --group $G --config $CFG
```

완주 출력:

```
S1 ingest: 문서 40건 / 페이지 80장 / 형식 docs
S2 ocr: 페이지 80장 / 평균 판독성 0.95
S3 labels: α=0.183 / 타깃 majority (y_majority)
⚠ 평가자 간 기준이 거의 일치하지 않는다 (Krippendorff α = 0.183 < 0.2) ...
⚠ 타깃 자동 전환: 전원 합격 비율 7.5% 이 가드(15%~85%) 밖이라 `unanimous` → `majority` 로 바꿨다.
S4a sections: 분류체계 9개 / OTHER 제목 0개
S4b concepts discover: 개념 후보 5개
S4c concepts score: 개념 5개 / 구조특징 18개 / 불안정 0개
S5 model: 등급 {'유력': 6, '기각': 5, '참고': 3, '확정': 1} / 순열검정 p=0.002
┌────────────────┬──────────────────────────────────────────────────┐
│ 출력 폴더      │ outputs/20261005_214718/대책서__CUST_A           │
│ 생성 파일      │ template_spec.json, template_spec.md,            │
│                │ template.docx, analysis_report.md                │
│ 근거 없는 요소 │ 0                                                │
└────────────────┴──────────────────────────────────────────────────┘
🔒 .../template.docx 를 사람이 최종 검토한 뒤 배포하세요
```

### 4.4 숨김 정답을 다시 찾아냈는지 검증

```bash
PYTHONPATH= python scripts/verify_dummy.py --group $G --config $CFG
```

```
[1] 숨김 규칙 3개가 확정/유력 인가
    ✓ why5     → C001 = 유력  | 부호일관성=100%, 검증셋 동일방향 — 안정성만(통계적 유의성 없음)
    ✓ graph    → C003 = 유력  | 부호일관성=88%, 검증셋 동일방향 — 안정성만(통계적 유의성 없음)
    ✓ yokoten  → C004 = 유력  | 부호일관성=90%, 검증셋 동일방향 — 안정성만(통계적 유의성 없음)
[2] 순열검정 p < 0.05
    ✓ p = 0.002
[3] 잡음 요소가 '확정' 이 아닌가
    ✓ photo  → C005 = 기각
    ✓ org    → C002 = 유력
========================================================
Phase 6 검증: 합격
```

슬라이드형 그룹(`대책서__CUST_B`)도 같은 방식으로 돌리면 `.pptx` 가 나온다.

---

## 4bis. 한글 샘플 데이터로 검증하기 (권장)

`Valeo_SVMtrial_sample_data/` 에 **가상의 한글 8D 대책서 82건 + 5인 평가 시트**가 들어 있다.
§4 의 영문 더미보다 실데이터에 가깝고(이미지 전용 스캔본, 표기 혼재, 의도된 오류 포함) 채점용 정답지가 함께 있다.

```bash
# 데이터 배치 (_answer_key 는 넣지 않는다)
cp -r Valeo_SVMtrial_sample_data/data/raw/. data/raw/

python -m svmtrial ingest          # S1 — 기본 config 가 data/raw 를 가리킨다
python -m svmtrial labels          # S3
```

바이너리는 git 에서 제외돼 있다. 없으면 **같은 내용으로 재생성**된다(결정론, 검증됨).

```bash
python Valeo_SVMtrial_sample_data/tools/make_sample_data.py     --out Valeo_SVMtrial_sample_data --n 40 --seed 7
# 한글 폰트가 없으면: sudo apt install fonts-nanum  (또는 --font <ttf 경로>)
```

### 채점 기준과 실제 결과

정답지는 `Valeo_SVMtrial_sample_data/_answer_key/정답_README.md` 다.
**SETUP.md §12 의 완료 기준(영문 더미 생성기 기준)과 다르므로 이 샘플에서는 정답지를 우선한다.**

| 단계 | 정답지 기준 | 실제 결과 |
|---|---|---|
| S1 | PDF **82개**, 매칭 오류 **4건** | ✅ 82개 / 4건 (`CUST_A_041`, `CUST_A_999`, `CUST_B_041`, `CUST_B_999`) |
| S1 형식 | CUST_A → docs, CUST_B → slides | ✅ CUST_A 41/41 docs(비율 판정, producer=`MFP Scan Utility 4.2`) / CUST_B 41/41 slides(creator 15 + 비율 26) |
| S1 텍스트 레이어 | 0 | ✅ 0 (전 82건) |
| S3 정규화 | 표기 8종 + 빈칸 10개 | ✅ Pass 274 / Fail 126 / 결측 10, 오류 0건 |
| S3 wide 형식 | long 과 같은 결과 | ✅ 410행 전부 판정 일치 |
| S3 엄격도 | B 0.75 > C 0.70 > E 0.68 ≈ A 0.67 > **D 0.63(가장 엄격)** | ✅ B 0.747 > C 0.704 > E 0.675 > A 0.671 > **D 0.630** |
| S3 전원합격률 | 0.35 | ✅ 0.350 (28/80) |

> **엄격도는 82건 전체(풀링) 기준이다.** 고객사별 40건으로 쪼개면 표본 노이즈 때문에
> CUST_B 에서는 평가자C 가 가장 엄격하게 나온다. 정답지 수치와 맞추려면 전체를 합쳐 본다
> (`config.analysis.pool_customers: true`).
>
> **평가자E 의 특징은 엄격도가 아니라 서명란 의존이다.** 데이터 수준에서 확인된다 —
> 표지 서명란이 있을 때 Pass율 0.857 vs 없을 때 0.345 (격차 +0.512로 5명 중 가장 크다.
> 2위 평가자D 는 +0.248). 이것이 **S5 평가자별 모델**에서 드러나려면 OCR 이 서명란을
> 읽어야 하므로 `vertex` 백엔드가 필요하다.

### ⚠ offline 백엔드로는 S2 부터 검증할 수 없다

샘플 PDF 는 실제 스캔 이미지이고 offline 스텁에는 대응 fixture 가 없다. 확인된 동작:

```
mean_legibility        0.0
docs_low_legibility    ["CUST_A_001", "CUST_A_002", "CUST_A_003"]
total_headings/tables/figures   0 / 0 / 0
```

**조용히 틀린 답을 주지 않고 판독 불량으로 보고한다.** 따라서 S2~S6 검증은
[`migration_vertexAI.md`](migration_vertexAI.md) 로 전환한 뒤에 한다. 전체 규모는 미리 확인할 수 있다.

```bash
python -m svmtrial ocr --dry-run     # 568회 호출 / 약 1,022,400 토큰 (페이지 568장)
```

정답지는 전환 후 다음을 기대한다 — 5Why 표 `확정`, 효과검증 그래프 전체·CUST_B `확정`,
수평전개 표 `유력`/`참고`, 현물 사진 `기각`(40건으로는 검출 불가), 미끼 3종(일정표·강조박스·부록) `기각`.
고객사별 40건에서는 5Why 만 유의하게 나오며, 리포트가 **"탐색적"** 경고를 띄우는지도 확인 대상이다.

---

## 5. 실데이터로 돌리기

> ⚠ **먼저 [`migration_vertexAI.md`](migration_vertexAI.md) 를 따라 `SVMTRIAL_BACKEND=vertex` 로 전환해야 한다.**
> offline 백엔드로 실데이터를 돌리면 OCR 이 아무것도 읽지 못하고, "요소가 하나도 없는 문서들"로 분석된다.

### 5.1 입력 데이터 넣기

```
data/raw/pdfs/<문서종류>/<고객사>/<doc_id>.pdf
data/raw/labels/*.xlsx
```

예:

```
data/raw/pdfs/대책서/현대/HD_2025_001.pdf
data/raw/pdfs/대책서/현대/HD_2025_002.pdf
data/raw/labels/2025_평가결과.xlsx
```

**규칙**

| 항목 | 내용 |
|---|---|
| `doc_id` | **PDF 파일명(확장자 제외)** 이다. 평가 시트의 `doc_id` 와 **반드시 일치**해야 한다 |
| 분석 단위 | `(문서종류, 고객사)` = 그룹. 그룹 이름은 `문서종류__고객사` |
| PDF 전제 | **이미지 기반(스캔본)** 을 기본으로 본다. 텍스트 레이어는 무시한다 |
| 불일치 | 시트에만 있거나 PDF 에만 있는 문서는 `work/ingest_issues.csv` 에 기록하고 제외한다 |

### 5.2 평가 시트 형식

**권장: long 형식** (평가 1건 = 1행)

| doc_id | customer | doc_type | rater | result |
|---|---|---|---|---|
| HD_2025_001 | 현대 | 대책서 | 윤정호 | Pass |
| HD_2025_001 | 현대 | 대책서 | 김철수 | Fail |

**허용: wide 형식** (문서 1건 = 1행, 평가자별 열) → 내부에서 long 으로 변환한다

| doc_id | customer | doc_type | 평가자1 | 평가자2 | … |
|---|---|---|---|---|---|

**결과값 정규화**

| 종류 | 인식하는 값 |
|---|---|
| Pass | `Pass, P, OK, O, ○, 합격, 1, Y` |
| Fail | `Fail, F, NG, X, ×, 불합격, 0, N` |
| 미평가 | 빈칸 (일치도 계산에서 결측으로 처리) |

> **인식할 수 없는 값이 있으면 즉시 중단**하고 `work/labels/label_value_errors.csv` 를 남긴다.
> 그 파일을 보고 `config.yaml` 의 `labels.pass_values` / `fail_values` 에 추가한다.
> 열 이름이 다르면 `labels.columns` 로 매핑한다 ([`migration_vertexAI.md` §4.2](migration_vertexAI.md)).

### 5.3 비용·시간 추정부터

```bash
python -m svmtrial ingest --group 대책서__현대
python -m svmtrial ocr --group 대책서__현대 --dry-run
```

`ocr` 이 호출 수의 대부분을 차지한다 (페이지 1장 = 1회).
`config.yaml` 의 `gemini.cost_per_1m_tokens` 에 단가를 넣으면 `est_cost` 도 나온다.

### 5.4 작은 그룹 또는 일부 문서로 먼저

```bash
python -m svmtrial ocr --group 대책서__현대 --limit 5
cat work/ocr/HD_2025_001/doc_outline.json | head -40
```

제목 계층과 표 열 이름이 제대로 들어왔는지 확인한다.

### 5.5 전체 실행

```bash
python -m svmtrial all --group 대책서__현대
#   → 🔒 에서 멈춤 → 후보 검토 → approve → 다시 all  (§4.3 과 같은 흐름)
```

### 5.6 단계별로 돌리기

`all` 대신 하나씩 돌릴 수도 있다. 중간 산출물이 남으므로 이어서 실행된다.

```bash
python -m svmtrial ingest           --group $G
python -m svmtrial ocr              --group $G
python -m svmtrial labels           --group $G
python -m svmtrial sections         --group $G
python -m svmtrial approve          --group $G --gate sections      # 🔒 검토 후
python -m svmtrial sections         --group $G                      # 매핑 진행
python -m svmtrial concepts discover --group $G
python -m svmtrial approve          --group $G --gate concepts      # 🔒 검토 후
python -m svmtrial concepts score   --group $G
python -m svmtrial model            --group $G
python -m svmtrial template         --group $G
python -m svmtrial report           --group $G
```

---

## 6. 🔒 사람 검토 관문 — 무엇을 봐야 하는가

`approve` 는 후보 파일을 승인 파일로 **복사만** 한다. 검토는 사람이 한다.
`all` 은 이 명령을 **절대 자동으로 호출하지 않는다.**

### 6.1 관문 1 — 섹션 분류체계

파일: `work/sections/<그룹>/taxonomy_candidates.yaml`

```yaml
observed_titles:              # 데이터에서 실제로 관측된 제목과 등장 문서 수
- {title: D4 Root cause, n_docs: 40}
items:                        # 이것이 표준 섹션 분류체계다
- id: D4
  name: 근본원인 (발생/유출)
  synonyms: [D4 Root cause, root cause, 근본원인, 원인분석]
  description: ''
```

| 볼 것 | 왜 |
|---|---|
| 같은 뜻의 제목이 하나로 묶였는가 | "근본원인" 과 "Root cause" 가 다른 항목이면 특징이 쪼개진다 |
| `observed_titles` 에 있는데 어느 항목에도 안 들어간 제목이 있는가 | `n_titles_other` 가 크면 매핑이 실패한 것이다 |
| `id` 가 `X1`, `X2` 인 항목 | seed(8D)에 없던 제목이다. 진짜 섹션인지 확인한다 |
| 쓰지 않는 섹션 | 지워도 된다 (예: D0 긴급대응이 한 번도 안 나오면) |

승인: `python -m svmtrial approve --group $G --gate sections`
또는 직접 `taxonomy_approved.yaml` 로 저장 (수정하면서 저장하는 쪽을 권한다)

### 6.2 관문 2 — 개념 후보 (가장 중요)

파일: `work/concepts/<그룹>/concepts_candidates.yaml`

```yaml
discovery_only: true          # 발견셋으로만 만들었다는 표시
n_discovery_docs: 24
n_pass_in_discovery: 10
n_fail_in_discovery: 14
rounds: [...]                 # 라운드별로 어떤 문서를 봤는지
concepts:
- id: C001
  question: D4 섹션에 "5-Why analysis table: Why1 | Why2 | ..." 에 해당하는 항목이 있는가?
  section_id: D4
  type: structure
  actionable: true
  rationale: 발견셋에서 Pass 문서 출현율이 Fail 보다 50% 높았다.
```

| 검토 기준 | 버려야 하는 예 |
|---|---|
| **① 문서만 보고 예/아니오로 답할 수 있는가** | "작성자가 성실한가?" — 문서에서 확인 불가 |
| **② 내용 타당성 판단이 섞이지 않았는가** | "근본원인 분석이 타당한가?" — 주관적 판단. 이 시스템의 비목표 |
| **③ `actionable` 이 맞는가** | "페이지가 50장 이상인가?" → 템플릿으로 유도할 수 없으면 `false` |
| **④ 중복이 없는가** | 같은 것을 묻는 두 개념은 특징을 쪼개 통계력을 떨어뜨린다 |
| **⑤ `section_id` 가 맞는가** | 틀리면 템플릿의 엉뚱한 섹션에 요소가 들어간다 |

**직접 추가해도 된다.** 현장 지식으로 아는 요소(예: "고객 승인란이 있는가")를 같은 형식으로 넣으면 같이 검증된다.
핵심 문구는 **쌍따옴표로 인용**해야 한다 — 채점이 그 문구를 쓴다.

> 승인 파일이 없으면 S4c(채점)는 **실행되지 않는다.**

### 6.3 관문 3 — 최종 템플릿

`outputs/<run_id>/<그룹>/template.docx` 또는 `.pptx` 를 Word/PowerPoint 에서 **직접 열어본다.**

| 볼 것 |
|---|
| 한글이 깨지지 않는가 (글꼴 맑은 고딕, `w:eastAsia` 지정됨) |
| 섹션 순서가 실무 순서와 맞는가 |
| **실제 내용(가짜 수치·가짜 원인)이 들어가 있지 않은가** — 플레이스홀더여야 한다 |
| 필수(☑) / 권장(☐) 구분이 납득되는가 |
| `analysis_report.md` 의 한계와 주의사항을 읽었는가 |

---

## 7. 산출물 읽는 법

### 7.1 중간 산출물 (`work/`)

```
work/
├─ pages/<doc_id>/           p001.png, p002.png, meta.json       S1
├─ ingest/<그룹>.json         문서 수, 형식 판정 결과             S1
├─ ingest_issues.csv         시트↔PDF 불일치 목록                S1
├─ ocr/<doc_id>/             p001.json, doc_outline.json         S2
├─ labels/<그룹>/             labels_long.csv  doc_targets.csv
│                            agreement.json  label_value_errors.csv   S3
├─ sections/<그룹>/           taxonomy_candidates.yaml  🔒
│                            taxonomy_approved.yaml
│                            <doc_id>.sections.json  summary.json      S4a
├─ concepts/<그룹>/           split.csv  concepts_candidates.yaml  🔒
│                            concepts_approved.yaml  scores_raw.json   S4b/c
├─ features/<그룹>/           X.csv  scoring_meta.json                 S4c
├─ models/<그룹>/             univariate.csv  concept_verdicts.csv
│                            stability.csv  results.json
│                            counterfactual.json  counterfactual_ranking.csv
│                            template_spec.json                        S5/S6
└─ cache/                    Gemini 응답 캐시 (백엔드별로 분리)
```

가장 자주 보는 두 파일:

| 파일 | 내용 |
|---|---|
| `work/features/<그룹>/X.csv` | 분석의 입력 행렬. 행=문서, 열=개념(0/1)+구조 특징. **엑셀로 열어 눈으로 확인할 수 있다** |
| `work/models/<그룹>/concept_verdicts.csv` | 각 특징의 등급과 통계. 리포트 §4 의 원본 |

### 7.2 최종 산출물 (`outputs/<run_id>/<그룹>/`)

| 파일 | 누가 읽는가 | 내용 |
|---|---|---|
| `template.docx` 또는 `.pptx` | **문서 작성자** | 바로 쓰는 템플릿. 섹션 + 빈 표 + 그림 자리 + 작성 가이드 + 제출 전 체크리스트 |
| `template_spec.md` | 검토자 | 사람이 읽는 명세. 섹션 구조표, 요소별 근거 ID, 근거 통계 |
| `template_spec.json` | 프로그램 | 기계판독 명세. 다음 단계 자동화에 쓴다 |
| `analysis_report.md` | **상사·검토 회의** | 8개 절. 아래 §7.3 |
| `charts/forest.png` | 같음 | 효과크기(위험차 RD) 막대 |
| `charts/weights.png` | 같음 | 선형 SVM 가중치 |
| `diagnose_<doc_id>.md` | 작성자 | (S7) 문서 1건의 보완 목록 |

### 7.3 `analysis_report.md` 읽는 순서

| 절 | 무엇을 보는가 | 주의 |
|---|---|---|
| 1. 요약 | 핵심 결론 3줄, 문서 수, 타깃 정의 | **`⚠ 탐색적`** 표기가 있으면 다변량 결과를 약하게 본다 |
| 2. 데이터 품질 | 평균 판독성, 판독 불량 페이지, 매칭 실패 | 판독성이 낮으면 OCR 부터 다시 본다 |
| 3. 평가자 일치도 | **Krippendorff α**, 평가자별 Pass율 | **α < 0.2 면 공통 템플릿의 근거가 약하다** |
| 4. Pass를 가르는 요인 | 등급별 표 + forest plot | `verdict_basis` 의 **"안정성만"** 은 통계적 유의성이 없다는 뜻 |
| 5. 모델 성능과 순열검정 | dummy 와 비교, **p 값** | **p ≥ 0.05 면 "모델 신호 없음"** — 다변량 결과를 쓰지 않는다 |
| 6. 평가자별 차이와 충돌 | 합집합 요구사항, 충돌 목록 | 충돌이 있으면 템플릿으로 해결되지 않는다. 사람이 조정해야 한다 |
| 7. 자주 필요한 보완 항목 | 반사실 순위 | "Fail 문서 중 몇 %가 이것을 추가해야 하는가" |
| 8. 한계와 주의사항 | — | **상사에게 보고할 때 반드시 같이 전달한다** |

---

## 8. Windows 배포

### 8.1 zip 만들기 (이 PC 에서)

```bash
PYTHONPATH= python scripts/build_release.py
#   → dist/valeo_svmtrial_0.1.0.zip

# 사내망에서 pip 가 막혀 있으면 (약 160MB)
PYTHONPATH= python scripts/build_release.py --with-wheels
```

포함: `src/ prompts/ config/ scripts/ windows/ docs/ requirements.txt environment.yml pyproject.toml .env.example CLAUDE.md`
제외: `.env`, 인증키(`*-sa.json`, `*.json.key`, `credentials.json`), `data/ work/ outputs/ logs/ dist/`, `__pycache__`

> 빌드는 비밀정보가 포함될 수 있는 파일을 감지하면 **실패한다** (zip 을 만들지 않는다).
> 검사는 수집 단계와 zip 내부에서 두 번 돈다.

### 8.2 Windows PC 에서

```
1. zip 을 한글·공백 없는 경로에 푼다.      예: C:\valeo_svmtrial
   (260자 경로 제한 때문에 깊은 경로는 피한다)
2. Anaconda Prompt 를 연다.
3. cd C:\valeo_svmtrial
4. windows\setup_windows.bat        ← 환경 생성 + smoke test
5. .env 편집 (migration_vertexAI.md §3.4)
6. gcloud auth application-default login
   gcloud auth application-default set-quota-project <PROJECT_ID>
7. windows\run_windows.bat models   ← 모델 ID 목록 → .env 에 기입
8. windows\run_windows.bat check    ← 실제 호출 1회 테스트
9. windows\run_windows.bat all --group 대책서__CUST_A
```

`windows\run_windows.bat` 의 단축 명령

| 명령 | 하는 일 |
|---|---|
| `run_windows.bat check` | `check_gemini.py` (모델 목록 + 테스트 호출) |
| `run_windows.bat models` | `check_gemini.py --list-only` |
| `run_windows.bat smoke` | `smoke_test.py` |
| `run_windows.bat <그 외>` | `python -m svmtrial <그 외>` 로 전달 |

자세한 안내는 zip 안의 `windows\README_windows.txt` 에도 들어 있다.

---

## 9. 테스트

```bash
./scripts/run_tests.sh              # 136개, 약 55초
./scripts/run_tests.sh -k labels    # 일부만
./scripts/run_tests.sh -v           # 자세히
```

**테스트는 Gemini 를 호출하지 않는다 (R6).** 네트워크를 쓰려 하면 즉시 실패한다.
실제 호출 점검은 `scripts/check_gemini.py` 로만 한다.

| 모듈 | 개수 | 무엇을 고정하는가 |
|---|---|---|
| `test_labels.py` | 26 | 결과값 정규화, long/wide 판별, κ/α, **Dawid-Skene 이 다수결보다 정확** |
| `test_gemini_client.py` | 17 | **캐시가 재호출을 막는다**, 검증 실패 시 1회 재요청, dry-run 이 호출하지 않음, 모든 프롬프트 렌더 |
| `test_ingest_release.py` | 15 | 형식 판정(creator/비율), **배포 zip 의 비밀정보 차단** |
| `test_textutil_features.py` | 15 | 제목 정규화·섹션 매칭, Kendall τ |
| `test_render.py` | 15 | **모든 요소에 근거 ID 필수**, docx `w:eastAsia`, pptx 16:9 + 노트, 마크다운 표, **수치형 특징을 Pass율로 표기하지 않음** |
| `test_modeling.py` | 12 | 단변량이 신호를 찾고 잡음을 거른다, 등급 밴드, **반사실 탐욕이 최소해** |
| `test_offline_backend.py` | 12 | 스텁 규약. **fixture 없으면 판독성 0 으로 정직하게 보고** |
| `test_config_cache.py` | 11 | 환경변수 치환, **캐시가 백엔드별로 분리됨** |
| `test_gates.py` | 9 | **🔒 승인 없이는 다음 단계가 돌지 않는다** |
| `test_pipeline_e2e.py` | 4 | S1~S5 통합. **숨김 규칙 복원**, **검증셋이 가설 생성에 안 쓰임** |

린트:

```bash
PYTHONPATH= ruff check src/ scripts/ tests/
```

---

## 10. 문제 해결

### 10.1 이 개발 환경

| 증상 | 원인 | 조치 |
|---|---|---|
| `⚠ 다른 파이썬 버전의 경로가 sys.path에 있습니다` | ROS 의 `PYTHONPATH` | `unset PYTHONPATH` (§1.3). 경고만 뜨고 동작은 한다 |
| `pytest` 가 `No module named 'lark'` 로 죽는다 | 같은 원인 | `./scripts/run_tests.sh` 를 쓴다 |
| `python -m svmtrial` 가 `No module named svmtrial` | 패키지 미설치 | `pip install -e .` 또는 `PYTHONPATH=src` |
| `그룹을 찾지 못했습니다` | 입력 경로 구조 | `<raw_pdfs>/<doc_type>/<customer>/*.pdf` 확인. `doctor` 로 경로 확인 |
| `offline fixture: ... 없음` | 더미 미생성 | `python scripts/make_dummy_data.py --out data/dummy --n 40` |
| 모든 페이지 판독성 0.0 | offline 백엔드 + fixture 없는 문서 | 더미로 돌리거나 vertex 로 전환 |
| 차트 라벨이 □□□ 로 나온다 | 한글 글꼴 없음 | Ubuntu: `sudo apt install fonts-nanum`. 리포트에 경고가 함께 나온다 |

### 10.2 파이프라인

| 증상 | 조치 |
|---|---|
| `🔒 승인된 ... 없습니다` | 의도된 동작이다. 후보 파일을 검토하고 `approve` (§6) |
| `인식할 수 없는 평가 결과값` | `work/labels/label_value_errors.csv` → `config.labels.pass_values`/`fail_values` 에 추가 |
| `평가자 간 기준이 거의 일치하지 않는다` | α < 0.2. 공통 템플릿의 근거가 약하다는 **경고**다. 평가자별 분석을 본다 |
| `타깃 자동 전환` | 전원 합격 비율이 15%~85% 밖이라 `majority` 로 바꿨다. 정상 동작 |
| `모델 신호 없음 (p ≥ 0.05)` | 다변량 결과를 쓰지 않는다. 단변량과 효과크기만 본다. 데이터를 늘리는 것이 근본 해결 |
| `사용할 수 있는 특징이 없습니다` | 모든 특징이 상수이거나 채점 불안정. `X.csv` 를 열어 확인 |
| `evidence_ids 누락이 2회 재요청 후에도 남았다` | LLM 이 근거 ID 를 못 채웠다. `--force` 로 재시도하거나 프롬프트를 손본다 |
| `탐색적` 표기 | 소수 클래스 < 10 × 특징 수. 개념 수를 줄이거나 문서를 늘린다 |
| 같은 결과만 나온다 | 캐시 적중이다. `--force` 또는 `work/<단계>` 디렉토리 삭제 |

### 10.3 Vertex AI (사내망)

[`migration_vertexAI.md` §7](migration_vertexAI.md) 에 정리돼 있다.
요약: `DefaultCredentialsError`→인증, `403`→IAM, `404`→모델 ID/리전, `429`→`max_workers` 낮추기.

---

## 11. 자주 하는 작업

| 하고 싶은 것 | 명령 |
|---|---|
| 환경·설정 확인 | `python -m svmtrial doctor` |
| 호출 수·토큰만 추정 | `python -m svmtrial ocr -g $G --dry-run` |
| 문서 5건으로만 시험 | `python -m svmtrial ocr -g $G --limit 5` |
| 캐시 무시하고 다시 | `python -m svmtrial <명령> -g $G --force` |
| 모델만 다시 돌리기 (LLM 호출 없음) | `python -m svmtrial model -g $G && python -m svmtrial report -g $G` |
| 등급 기준 바꾸기 | `config.yaml` → `analysis.verdict` → `model` 부터 재실행 |
| 새 문서 1건 진단 | `python -m svmtrial diagnose --pdf <경로> -g $G` |
| 새 문서 종류 추가 | `src/svmtrial/seeds.py` 에 seed 추가 |
| Gemini 사용량 확인 | `cat logs/<run_id>/gemini_calls.jsonl` |
| 모든 그룹 한 번에 | `--group` 을 생략한다 |

---

## 12. 피드백 루프 (S7 이후)

1. `diagnose` 로 새 문서를 진단하고 보완 목록을 작성자에게 준다.
2. 평가자들이 그 문서를 **실제로 평가**한다.
3. 그 결과를 평가 시트에 한 행 추가한다.

   | doc_id | customer | doc_type | rater | result |
   |---|---|---|---|---|
   | NEW_001 | 현대 | 대책서 | 윤정호 | Pass |

4. `labels` 부터 다시 실행해 재학습한다.

```bash
python -m svmtrial labels -g $G && python -m svmtrial concepts score -g $G \
  && python -m svmtrial model -g $G && python -m svmtrial template -g $G \
  && python -m svmtrial report -g $G
```

이미 채점된 문서는 캐시에서 오므로, 추가된 문서만 새로 호출된다.
