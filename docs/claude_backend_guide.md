# claude_backend_guide.md — Claude 백엔드 적용 방법

> 실데이터를 받았을 때 **처음부터 끝까지** 따라가는 절차.
> 구현: `src/svmtrial/claude_backend.py` · 점검: `scripts/check_claude.py`
> 관련: [`Runbook.md`](Runbook.md) · [`1007_night_alternatives.md`](1007_night_alternatives.md)
> 작성 2026-10-07

---

## 0. 한 장 요약 — 내일 할 일

```bash
# ① 설치 (환경이 이미 있으면 anthropic 만 추가)
conda activate Valeo_SVM_Trial && unset PYTHONPATH
pip install -r requirements.txt

# ② 키 설정 — .env 에 한 줄
echo 'SVMTRIAL_BACKEND=claude'       >> .env
echo 'ANTHROPIC_API_KEY=sk-ant-...'  >> .env

# ③ 연결 확인
python scripts/check_claude.py                 # → "CLAUDE 연결 정상"

# ④ 데이터 배치
#    data/raw/pdfs/<문서종류>/<고객사>/<doc_id>.pdf
#    data/raw/labels/*.xlsx        ※ PDF 파일명(확장자 제외) == 시트의 doc_id

# ⑤ 돈 쓰기 전에 규모 확인
python -m svmtrial ingest
python scripts/check_claude.py --estimate <문서종류>__<고객사>
python scripts/check_claude.py --count-tokens work/pages/<doc_id>/p001.png

# ⑥ 작게 먼저 — 3건만 실제 호출해 OCR 품질 확인 (가장 중요)
python -m svmtrial ocr -g <그룹> --limit 3
cat work/ocr/<doc_id>/doc_outline.json

# ⑦ 괜찮으면 전체 (🔒 관문에서 두 번 멈춘다)
python -m svmtrial all -g <그룹>
```

**⑥을 건너뛰지 마세요.** 실제 스캔본에서 OCR이 제대로 읽히는지가 전체의 성패를 가릅니다.

---

## 1. 왜 Claude 경로인가

| | Vertex AI(Gemini) | **Claude** |
|---|---|---|
인증 | GCP 프로젝트 + ADC/서비스계정 + IAM 역할 | **API 키 한 줄** |
사람이 받아와야 할 것 | 프로젝트 ID, 리전, 모델 ID, IAM, API 설정 (M1~M6) | **API 키 하나** |
모델 ID | 사내 프로젝트에서 조회해야 함 | `claude-opus-5-5` / `claude-sonnet-5-5` 고정 |
구조화 출력 | `response_schema` | `messages.parse(output_format=<pydantic>)` — **SDK가 검증까지** |
프롬프트 캐시 | 지원 | **지원, 이미 적용돼 있음** (§4) |
배치 | 지원 | 지원 — **50% 할인** (§7.2) |

`migration_vertexAI.md` 의 M1~M6(프로젝트 ID·리전·IAM·API 설정)이 **전부 불필요**해집니다.

---

## 2. 설치

### 2.1 기존 환경에 추가

```bash
conda activate Valeo_SVM_Trial
unset PYTHONPATH                      # ⚠ 이 PC 필수 (ROS python3.10 오염)
pip install -r requirements.txt       # anthropic==1.11.0 이 추가됨
python scripts/smoke_test.py          # ALL SMOKE TESTS PASSED
```

### 2.2 Windows 배포본

`anthropic==1.11.0` 은 전이 의존성까지 **wheel 15개 / sdist 0개, 4.8MB** 로 확인했습니다(R4 준수 — 컴파일러 불필요).

```bash
python scripts/build_release.py --with-wheels    # 오프라인 설치용 zip
```

---

## 3. 인증 — 두 가지 방법

### 방법 A: API 키 (권장, 가장 단순)

```dotenv
# .env
SVMTRIAL_BACKEND=claude
ANTHROPIC_API_KEY=sk-ant-...
```

키는 [Claude Console](https://console.anthropic.com/) 에서 발급합니다.
`.env` 는 `.gitignore` 에 있고 배포 zip 에서도 자동 제외됩니다(빌드가 실패 처리).

### 방법 B: 키를 파일에 두지 않기

```bash
ant auth login           # 프로필이 ~/.config/anthropic/ 에 저장된다
ant auth status          # 활성 프로필 확인
```

`.env` 에는 `SVMTRIAL_BACKEND=claude` 만 넣습니다. SDK가 프로필을 자동으로 찾습니다.

> `ANTHROPIC_API_KEY` 가 설정돼 있으면 프로필보다 **우선**합니다. 프로필을 쓰려면 환경변수를 비우세요.

### 3.1 확인

```bash
python scripts/check_claude.py
```

```
[i] SVMTRIAL_BACKEND = claude
[i] model_fast = claude-sonnet-5-5   (effort=low)
[i] model_pro  = claude-opus-5-5   (effort=high)
[OK] ANTHROPIC_API_KEY 설정됨 (…abcd)
[OK] 구조화 출력 응답: {'ok': True, 'seen': 'hello'}
[OK] 토큰: 입력 N / 출력 M

CLAUDE 연결 정상
```

실패하면 스크립트가 점검 항목 3개를 출력합니다(키 유효성 / 프록시 / 모델 ID).

---

## 4. 설정 — `config/config.yaml` 의 `claude` 절

```yaml
claude:
  model_fast: claude-sonnet-5-5    # 대량 호출 (OCR·채점) — 페이지 수 × 문서 수
  model_pro: claude-opus-5-5       # 추론 (가설 생성·템플릿 작성) — 십여 회
  effort_fast: low                 # low | medium | high | xhigh | max
  effort_pro: high
  max_tokens: 16000
  prompt_cache: true               # 고정 지시문을 system 에 캐시
  sdk_max_retries: 2
```

### 4.1 두 tier 로 나눈 이유

| tier | 쓰는 단계 | 호출 수 | 모델 | effort |
|---|---|---|---|---|
**FAST** | S2 OCR, S4c 채점, S4a 매핑, S1 형식판정 | **수백~수천** | `claude-sonnet-5-5` | `low` |
**PRO** | S4a 분류체계, S4b 가설 생성, S6 템플릿 작성 | **십여 회** | `claude-opus-5-5` | `high` |

비용의 거의 전부가 FAST에서 나오므로 거기만 싸게 하고, 품질이 중요한 PRO는 아끼지 않습니다.

### 4.2 프롬프트 캐시가 어떻게 걸려 있는가

프롬프트를 `payload.OPEN` 마커에서 **자동으로 둘로 쪼갭니다.**

```
프롬프트 파일 (prompts/ocr_page.v1.md)
├─ 고정 지시문 ("당신은 … 구조화하는 도구다. 출력 규칙: …")  → system  + cache_control
└─ <<<SVMTRIAL_DATA {doc_id, page, …} DATA>>>  + 페이지 이미지 → messages
```

OCR은 페이지마다 지시문이 **완전히 동일**하므로, 2회차부터 그 부분이 **1/10 단가**로 청구됩니다.
호출부 코드는 바뀌지 않았습니다 — 백엔드 안에서만 쪼갭니다.

**확인 방법**: `logs/<run_id>/gemini_calls.jsonl` 의 `cache_read_tokens` 가 0이 아니어야 합니다.

```bash
python -c "
import json,collections
c=collections.Counter(); t=collections.Counter()
for l in open('logs/<run_id>/gemini_calls.jsonl',encoding='utf-8'):
    d=json.loads(l); c[d['prompt_id']]+=1
    for k in ('prompt_tokens','output_tokens','cache_read_tokens','cache_write_tokens'): t[k]+=d.get(k,0)
print('호출 수:',dict(c)); print('토큰:',dict(t))"
```

`cache_read_tokens` 가 계속 0이면 고정부가 캐시 최소 길이(모델별 512~4096 토큰) 미달일 수 있습니다.
그 경우 캐시 이득이 없을 뿐 동작은 정상입니다.

### 4.3 effort 조정

처음에는 기본값(`low`/`high`)으로 돌리고, 결과를 보고 조정합니다.

| 증상 | 조치 |
|---|---|
OCR이 표 구조를 놓친다 | `effort_fast: medium` |
개념 후보가 피상적이다 | `effort_pro: xhigh` |
비용이 과하다 | `effort_fast` 유지, `effort_pro: medium` 으로 내려 비교 |

> **effort 를 바꾸면 캐시가 분리됩니다** — 캐시 네임스페이스에 effort 가 들어 있어
> 이전 응답을 재사용하지 않고 다시 호출합니다. 의도된 동작입니다(결과가 달라지므로).

---

## 5. 데이터 배치

```
data/raw/pdfs/<문서종류>/<고객사>/<doc_id>.pdf
data/raw/labels/*.xlsx
```

| 항목 | 규칙 |
|---|---|
`doc_id` | **PDF 파일명(확장자 제외)**. 평가 시트의 `doc_id` 와 **반드시 일치** |
그룹 | `<문서종류>__<고객사>` — 분석 단위 |
평가 시트 | long 형식 권장 (`doc_id, customer, doc_type, rater, result`). wide 도 자동 판별 |
결과값 | `Pass/P/OK/O/○/합격/1/Y` ↔ `Fail/F/NG/X/×/불합격/0/N`, 빈칸=미평가 |

열 이름이 다르면 `config.labels.columns` 로 매핑합니다. 인식 못 하는 값이 있으면
**즉시 중단**하고 `work/labels/label_value_errors.csv` 를 남깁니다 — 그 파일을 보고
`config.labels.pass_values` / `fail_values` 에 추가하세요.

```bash
python -m svmtrial doctor        # 발견된 그룹 확인
python -m svmtrial ingest        # PDF → PNG + 매칭 오류 확인
cat work/ingest_issues.csv       # 시트↔PDF 불일치 목록
```

---

## 6. 돈 쓰기 전에 — 규모와 비용 산정

### 6.1 호출 수

```bash
python scripts/check_claude.py --estimate <그룹>
```

```
[추정] 개념 10개 가정 — 대책서__CUST_A (문서 40건 / 페이지 80장)
   FAST(claude-sonnet-5-5)    93회
   PRO (claude-opus-5-5)       8회
```

### 6.2 실제 토큰 측정 ★

추정치 대신 **실제 페이지로 측정**하세요. 이미지 토큰은 해상도에 크게 좌우됩니다.

```bash
python scripts/check_claude.py --count-tokens work/pages/<doc_id>/p001.png
```

```
[측정] work/pages/CUST_A_001/p001.png  (1394 KB)
   OCR 1페이지 입력 토큰 = N
   그중 고정 지시문(system, 캐시 대상) ≈ M
   → 캐시 적중 시 2회차부터 약 M 토큰이 1/10 단가로 청구됩니다
   568페이지 기준 입력 토큰 ≈ N × 568
```

### 6.3 단가 (2026-09 기준, Anthropic 1st-party)

| 모델 | 입력 $/MTok | 출력 $/MTok |
|---|---|---|
`claude-opus-5-5` | 4.00 | 20.00 |
`claude-sonnet-5-5` | **2.00** | **10.00** |
`claude-haiku-4-5` | 1.00 | 5.00 |

```
비용 ≈ (입력 토큰 / 1e6 × 입력단가) + (출력 토큰 / 1e6 × 출력단가)
       − 캐시 적중분 (읽기는 약 1/10 단가)
```

> 단가는 바뀔 수 있습니다. 실제 청구액은 Console 의 Usage 에서 확인하세요.
> `config.gemini.cost_per_1m_tokens` 에 단가를 넣으면 `--dry-run` 이 비용 추정도 출력합니다.

### 6.4 `--dry-run` 의 한계

```bash
python -m svmtrial ocr -g <그룹> --dry-run
```

호출 수는 정확하지만 **토큰은 `config.gemini.est_tokens_per_page`(기본 1800)의 곱**이고,
이 값은 Gemini 기준 추정치입니다. §6.2 로 측정한 실측값으로 바꿔 두세요.

```yaml
gemini:
  est_tokens_per_page: <측정값>     # --dry-run 추정 정확도를 위해
```

---

## 7. 실행

### 7.1 ⚠ 반드시 작게 먼저

실데이터 스캔본은 더미와 품질이 다릅니다. **3건으로 OCR을 먼저 검증**하세요.

```bash
python -m svmtrial ocr -g <그룹> --limit 3
```

확인할 것:

| 항목 | 어디서 | 기준 |
|---|---|---|
평균 판독성 | 명령 출력 `mean_legibility` | **≥ 0.5** (낮으면 §8) |
제목 계층 | `work/ocr/<doc_id>/doc_outline.json` → `headings` | 실제 섹션 제목이 들어왔나 |
표 열 이름 | 같은 파일 → `tables[].columns` | 표의 헤더가 읽혔나 |
그림·차트 | 같은 파일 → `figures[]` | 그래프/사진이 구분됐나 |
서명란 | 같은 파일 → `n_signatures` | 표지 서명란이 잡혔나 |

```bash
python -c "
import json
o=json.load(open('work/ocr/<doc_id>/doc_outline.json',encoding='utf-8'))
print('판독성:',o['mean_legibility']); print('제목:',[h['text'] for h in o['headings']][:8])
print('표:',[t['columns'] for t in o['tables']][:3]); print('그림:',o['figures'][:3])
print('서명란:',o['n_signatures'])"
```

**여기서 품질이 나쁘면 전체를 돌리지 마세요.** §8의 조치를 먼저 적용합니다.

### 7.2 비용을 더 줄이려면 — 배치 API (선택)

OCR은 지연에 민감하지 않습니다. `client.messages.batches` 로 **50% 할인**을 받을 수 있지만
**현재 백엔드는 동기 호출만 구현했습니다.** 568 페이지 × 50% 는 의미 있는 금액이므로,
1회 실행 비용을 보고 필요하면 배치 경로를 추가하겠습니다(난이도 M).

### 7.3 전체 실행 — 🔒 관문에서 두 번 멈춘다

```bash
G=<문서종류>__<고객사>

python -m svmtrial all -g $G                      # ① → 🔒 섹션 분류체계 승인 대기
cat work/sections/$G/taxonomy_candidates.yaml     # ② 사람이 검토·수정
python -m svmtrial approve -g $G --gate sections  # ③ 승인

python -m svmtrial all -g $G                      # ④ → 🔒 개념 승인 대기
cat work/concepts/$G/concepts_candidates.yaml     # ⑤ 사람이 검토 (가장 중요)
python -m svmtrial approve -g $G --gate concepts  # ⑥ 승인

python -m svmtrial all -g $G                      # ⑦ 완주
```

**⑤에서 볼 것** (`Runbook.md` §6.2 상세):

| 기준 | 버려야 하는 예 |
|---|---|
문서만 보고 예/아니오로 답할 수 있는가 | "작성자가 성실한가?" |
내용 타당성 판단이 섞이지 않았는가 | "근본원인 분석이 타당한가?" |
`actionable` 이 맞는가 | "페이지가 50장 이상인가?" → false |
중복이 없는가 | 같은 것을 묻는 두 개념은 통계력을 쪼갠다 |

현장 지식으로 아는 요소는 **직접 추가해도 됩니다**(핵심 문구를 쌍따옴표로 인용).

### 7.4 산출물

```
outputs/<run_id>/<그룹>/
├─ template.docx 또는 .pptx    ← 🔒 사람이 열어보고 배포
├─ template_spec.md            섹션 구조 + 요소별 근거
├─ template_spec.json
├─ analysis_report.md          ← 상사 보고용. 8개 절
└─ charts/forest.png, weights.png
```

`analysis_report.md` 읽는 순서는 `Runbook.md` §7.3 에 있습니다.
**§8 한계와 주의사항을 반드시 함께 전달하세요.**

---

## 8. 문제 해결

### 8.1 Claude 고유

| 증상 | 원인 | 조치 |
|---|---|---|
`안전 분류기가 요청을 거부했습니다` | `stop_reason == "refusal"` | 해당 페이지를 직접 확인. 드물지만 내용에 따라 발생 가능. `logs/.../gemini_calls.jsonl` 로 어느 문서인지 추적 |
`출력이 max_tokens 에서 잘렸습니다` | 개념이 많아 응답이 길다 | `config.claude.max_tokens` 를 올리거나 `analysis.concepts_per_scoring_call` 을 25→15로 |
`AuthenticationError` | 키 무효 | `python scripts/check_claude.py` / `ant auth status` |
`RateLimitError` (429) | 분당 한도 | `gemini.max_workers` 를 4→2로. SDK가 `retry-after` 를 따라 자동 재시도하고, 캐시 덕분에 이어서 실행됩니다 |
`구조화 출력이 비었습니다` | 스키마 검증 실패 | `prompt_id` 를 보고 해당 프롬프트 확인. gemini_client 가 1회 재요청 후 예외를 던집니다 |
`cache_read_tokens` 가 계속 0 | 고정부가 캐시 최소 길이 미달 | 이득이 없을 뿐 정상. 무시해도 됨 |

### 8.2 OCR 품질

| 증상 | 조치 |
|---|---|
평균 판독성 < 0.5 | `ingest.dpi` 150 → 200. 파이프라인이 낮은 페이지를 `retry_dpi`(250)로 자동 재시도합니다 |
표 열 이름이 안 읽힌다 | `claude.effort_fast: medium`, `ingest.dpi: 200` |
제목 계층이 엉킨다 | 프롬프트 `prompts/ocr_page.v1.md` 를 손보고 **`.v2.md` 로 버전을 올립니다**(캐시 자동 분리) |
기울어진 스캔 | dpi를 올리거나, 심하면 전처리로 기울기 보정(미구현) |

### 8.3 공통

`Runbook.md` §10 참고. 요약: `🔒 승인 필요` = 정상 동작,
`인식할 수 없는 평가 결과값` = `config.labels` 보강, `모델 신호 없음(p≥0.05)` = 다변량 결과 사용 금지.

---

## 9. 백엔드 전환 시 주의

### 9.1 캐시는 백엔드별로 분리된다

```
offline/v6              ← 스텁 (실데이터 불가)
vertex/<project>/<loc>  ← Gemini
claude/<effort_f>/<effort_p>   ← Claude
```

offline 스텁 응답이 Claude 전환 후 재사용되는 일은 **없습니다**(테스트로 고정).

### 9.2 ⚠ 산출물 파일은 직접 지워야 한다

캐시는 분리되지만 **중간 산출물은 아닙니다.** offline/더미로 만든 것이 남아 있으면
재호출 없이 그대로 쓰입니다.

```bash
rm -rf work/ocr work/features work/concepts work/sections work/models
rm -rf work/offline_fixtures          # 더미 전용
```

🔒 승인 파일(`*_approved.yaml`)도 더미 기준으로 만든 것이므로 **실데이터에서는 다시 검토**하세요.

### 9.3 되돌리기

```dotenv
SVMTRIAL_BACKEND=offline     # 또는 vertex
```

캐시가 분리돼 있어 Claude로 받은 응답은 보존되고, 다시 전환하면 재사용됩니다.

---

## 10. 내일 검증 체크리스트

```
□ pip install -r requirements.txt  →  smoke_test 통과
□ .env 에 SVMTRIAL_BACKEND=claude + ANTHROPIC_API_KEY
□ python scripts/check_claude.py  →  "CLAUDE 연결 정상"
□ 데이터 배치 후 doctor 로 그룹 인식 확인
□ ingest  →  work/ingest_issues.csv 확인 (시트↔PDF 불일치)
□ labels  →  중단 없이 끝나는가 / Krippendorff α 확인 (< 0.2 면 경고)
□ --count-tokens 로 1페이지 입력 토큰 측정  →  비용 산정
□ ocr --limit 3  →  판독성 ≥ 0.5, 제목·표 열 이름 확인      ★ 여기서 판단
□ cache_read_tokens 가 0이 아닌지 확인 (캐시 동작)
□ 전체 실행  →  🔒 두 관문에서 사람이 검토
□ docx/pptx 를 Word/PowerPoint 에서 직접 열어 한글 확인
□ analysis_report.md §8 한계를 보고에 포함
```

### 10.1 결과를 믿을 수 있는지 판단

| 지표 | 어디서 | 기준 |
|---|---|---|
평균 판독성 | 리포트 §2 | ≥ 0.5 |
Krippendorff α | 리포트 §3 | **< 0.2 면 공통 템플릿의 근거가 약하다** |
순열검정 p | 리포트 §5 | **≥ 0.05 면 다변량 결과를 쓰지 않는다** |
`탐색적` 표기 | 리포트 §1 | 소수 클래스 < 10 × 특징 수 → 단변량 중심으로 해석 |
`verdict_basis` 의 "안정성만" | 리포트 §4 | 통계적 유의성 없음 — 데이터가 늘면 재확인 |
채점 불안정 개념 | 리포트 §2 | 일치율 < 85% 는 모델에서 제외됨 |

### 10.2 문서 수가 적을 때

정답지 시뮬레이션 기준 검출률:

```
그룹당 40건: 가장 강한 요인 44% / 중간 31% / 약한 요인 13%
그룹당 80건: 가장 강한 요인 92% / 중간 79% / 약한 요인 34%
```

**40건 미만이면 결과를 "탐색적"으로만 보세요.** `analysis.pool_customers: true` 로
고객사를 합치면 표본이 늘지만, "고객사 양식 = Pass" 같은 가짜 신호를 주의해야 합니다.

---

## 11. 아직 안 한 것

| # | 항목 | 비고 |
|---|---|---|
**1** | **실제 호출 검증** | API 키가 없어 `messages.parse` 실호출은 못 해봤습니다. SDK 표면(파라미터·응답 필드)은 설치된 1.11.0 에서 확인했고, 로직은 mock 테스트 14건으로 덮었습니다 |
**2** | 배치 API (50% 할인) | §7.2. 1회 실행 비용을 보고 판단 |
**3** | PDF 직접 입력 | Claude는 PDF를 네이티브로 받습니다(32MB/600쪽). 호출이 568→82회로 줄 수 있지만, 페이지 단위가 더 정확할 가능성이 있어 품질 비교가 먼저입니다 |
**4** | 스트리밍 | 긴 응답에서 타임아웃 방지. 현재 `max_tokens=16000` 이라 불필요 |
**5** | `est_tokens_per_page` 보정 | §6.2 측정값으로 바꿔야 `--dry-run` 이 정확해집니다 |
