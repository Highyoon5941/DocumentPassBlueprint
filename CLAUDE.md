# CLAUDE.md — Valeo_SVMtrial

## 최우선 규칙
**모든 작업은 `docs/Valeo_SVMtrial_SETUP.md`를 따른다.** 그 파일의 §1 작업 규칙(R1~R10)은 항상 유효하다.
설계와 충돌하는 변경이 필요하면 먼저 사용자에게 보고하고 승인을 받는다.

## 문서
| 문서 | 내용 |
|---|---|
| `docs/Valeo_SVMtrial_SETUP.md` | **설계 기준.** 모든 결정의 출처 |
| `docs/Architecture.md` | 전체 아키텍처와 기본 개념 (Concept Bottleneck, 통계 설계, 모듈 지도, 한계) |
| `docs/Runbook.md` | 실행 방법 (명령 목록, 더미/실데이터 절차, 🔒 관문 검토 기준, 산출물 읽는 법, 문제 해결) |
| `docs/migration_vertexAI.md` | 사내망 Vertex AI 전환. **§1 에 사람이 직접 받아와야 하는 항목(M1~M16)** 이 정리돼 있다 |
| `docs/alternatives_withoutLLM.md` | **제안서.** LLM 없이 — 로컬 OCR(P1~P8) + SVM 대체 모델(M1~M14). 샘플 데이터 실측 비교 포함 |
| `docs/alternatives_withLLM.md` | **제안서.** LLM 사용 + SVM 제거 — 계층 로지스틱/평가자 페르소나/루브릭/쌍대비교 등(L1~L12) |
| `docs/1007.md` | 2026-10-07 검토일지 — 대안 문서 2종 작성, L1 학습 비용/역할 분리 Q&A, **정정 3건(C1~C3)**, 미결 사항 |
| `docs/1007_night_SVM.md` | **구현보고서.** 학습 없는 SVM 활용 — 무상태 보증, 문서종류 불변성 실증(요구사양서), 발견·수정한 버그 3건 |
| `docs/1007_night_alternatives.md` | SVM 외 대안과 **로지스틱 구현 방향 3단계**, Claude 백엔드 전환 스케치 |
| `docs/autonomous_pipeline.md` | **자율 기준 선정 + 로지스틱 + 형식 자유 출력.** 사람 의견 배제, LayoutSpec, 목적함수 최적화 |
| `docs/claude_backend_guide.md` | **Claude 백엔드 적용 방법.** 설치→인증→데이터 배치→비용 산정→소규모 검증→전체 실행→문제 해결 |
| `docs/1014_inputdata.md` | **확정 입력 반영 (2026-10-14).** InputData 구조, Judgement `f(사유)` → 기준(차원), 이미지 기반 통일, 검증·미결 사항 |
| `docs/visualization_options.md` | **시각화 방안 목록.** 정답 경계·문서 간 관계 8가지 + 미리보기(`scripts/viz_gallery.py`). 리포트 통합은 사용자 선택 대기 |

## 입력 (2026-10-14 사용자 확정)
```
InputData/
├─ Documents/*.pdf      파일명(확장자 제외) = FileName. 하위 폴더도 읽는다
└─ Judgement.xlsx       FileName | Person1 | Person2 | …   값: p / f / f(사유)
```
- `config.input.layout: inputdata` 가 기본. SETUP.md 원래 구조는 `layout: legacy` (더미·샘플·검증 스크립트가 사용)
- **문서 종류는 한 번에 하나로 통일한다** (대책서면 대책서만 — 사용자 확인). 그래서 Documents 전체가 단일 그룹이다.
  종류를 `input.doc_type` 에 적으면 그룹 이름이 되고, 대책서면 8D seed 를 쓴다(자율 모드 제외)
- **모든 PDF 를 먼저 페이지 이미지로 바꾸고 이후는 이미지로만 판단한다** (형식 판정 포함). 텍스트 레이어·PDF 메타는 참고값
- Judgement 의 평가자 열은 **값으로 판별**한다 — 메모 칸·빈 열·`..` 열/행은 자동 제외하고 리포트에 기록
- `f(사유)` 의 사유 → S3 사유 표(`causes_long.csv`) → S4b **기준 후보 K…** (발견셋 사유만, 🔒 유지) → S4c 사유-채점 일치율.
  끄려면 `analysis.use_causes: false`. **사유 문구의 LLM 전송은 사용자 승인됨** — 평가자 이름·문서명은 보내지 않는다
- 시각화: **plotly·matplotlib 모두 사용 승인됨** (plotly 추가 시 R5 의 Windows wheel 확인은 여전히 한다).
  후보 8종은 `scripts/viz_gallery.py` / `docs/visualization_options.md`. 리포트 통합은 방안 선택 대기
- `InputData/` 는 README.md 만 git 추적 (R3). 배포 zip 에도 README 만 들어간다

## 이 저장소의 환경
- conda 환경 이름: **`Valeo_SVM_Trial`** (SETUP.md §5.2의 `valeo_svm`에서 변경. 사용자 지시)
- Python 3.11, CPU 전용. 프로젝트 루트는 이 파일이 있는 폴더다.
- 실행: `conda run -n Valeo_SVM_Trial python -m svmtrial <cmd>` 또는 환경 activate 후 `python -m svmtrial <cmd>`
- `PYTHONPATH=src` 가 필요하다. `pyproject.toml`로 `pip install -e .` 해도 된다.

## 백엔드 (이 개발 PC와 실업무 환경의 차이)
`.env`의 `SVMTRIAL_BACKEND`가 Gemini 호출 경로를 고른다.
| 값 | 동작 | 용도 |
|---|---|---|
| `claude` | `anthropic` SDK로 Claude를 호출한다 (`claude_backend.py`). 인증은 `ANTHROPIC_API_KEY` 한 줄 | **실업무 권장 경로.** 적용 방법은 `docs/claude_backend_guide.md` |
| `vertex` | `google-genai`로 Vertex AI(Gemini)를 호출한다 (SETUP.md §3) | 사내 GCP 프로젝트를 쓸 때 |
| `offline` | LLM을 호출하지 않고 `offline_backend.py`의 결정론적 규칙으로 같은 스키마의 응답을 만든다 | 기본값. **실데이터 분석 불가** — 더미/샘플로 파이프라인 검증 |

전환 절차와 사람이 직접 받아와야 하는 값은 **`docs/migration_vertexAI.md`**에 있다.
`offline` 백엔드는 OCR 단계에서 `make_dummy_data.py`가 만든 fixture에 의존하므로 **실데이터에는 쓸 수 없다.**
실데이터는 `claude`(권장) 또는 `vertex` 로 전환한다.

## 자율 모드 (R7 off — 사용자 승인됨)
`analysis.autonomous: true` 는 **사람의 도메인 의견을 일절 넣지 않는 모드**다.
- `seeds.py` 의 섹션 seed 를 쓰지 않는다 (분류체계를 데이터에서만 유도)
- 🔒 섹션·개념 관문을 자동 통과한다 — **SETUP.md R7 을 명시적으로 끈 것**. 기본값은 `false`
- 도메인 용어가 없는 `*.v2` 프롬프트를 쓴다 (`prompt_version()`)
- 승인 파일에 `_autonomous_auto_approved: true` 가 남아 사후 구분이 된다
자세한 내용과 검증 결과: `docs/autonomous_pipeline.md`

## 현재 Phase
| Phase | 상태 |
|---|---|
| 0 환경 | 완료 |
| 1 Gemini(config/client/cache/check) | 완료 |
| 2 더미 + S1 ingest | 완료 |
| 3 S2 ocr | 완료 |
| 4 S3 labels | 완료 |
| 5 S4 sections/concepts | 완료 |
| 6 S5 model/counterfactual | 완료 |
| 7 S6 template + S7 diagnose | 완료 |
| 8 Windows 배포 | 완료 (zip 빌드까지. Windows PC 실행 검증은 사람이 수행) |
| 9 실데이터 | 미착수 — `SVMTRIAL_BACKEND=claude`(권장) 또는 `vertex` 전환 후 사람이 수행. **입력 구조는 확정(InputData)** |

검증 상태 (2026-10-14, 확정 입력 형식 — offline + `data/dummy_input` PDF 60건 × 평가자 4명):
- `run_tests.sh` → **221 passed** (+38: Judgement 판독·사유·이미지 기반·사유 기준·회귀)
- 숨김 규칙 3개 → K001 `확정`, K002·K003 `유력` / 잡음 2개 `기각` / 순열검정 p=0.002 / 사유-채점 일치율 97%
- legacy 회귀 없음: `verify_dummy.py -g 대책서__CUST_A` 합격
- 기존 버그 수정: `method: svm`(기본)에서 `all` 이 S5+ objective.json 저장 중 죽던 문제 (numpy.bool, 5b4fa10 에서 유입)
- 주의: 이 PC 의 비대화형 셸에는 `conda` 가 PATH 에 없을 수 있다 → `~/anaconda3/envs/Valeo_SVM_Trial/bin/python -m pytest` (PYTHONPATH 비우고)

검증 상태 (2026-10-05, offline 백엔드 + 더미 데이터 PDF 80건/평가 400행):
- `python scripts/smoke_test.py` → ALL SMOKE TESTS PASSED
- `./scripts/run_tests.sh` → **139 passed** (Gemini 호출 없음, R6)
- `ruff check src/ scripts/ tests/` → All checks passed
- `python scripts/verify_dummy.py -g <그룹>` → 두 그룹 모두 **Phase 6 합격**
  (숨김 규칙 3개 전부 `유력` 이상, 순열검정 p=0.002, 잡음 요소는 `확정` 아님)
- `대책서__CUST_A` → `template.docx`, `대책서__CUST_B` → `template.pptx` (16:9) 생성 확인
- 모든 Section/Element 에 `evidence_ids` 존재 (근거 없는 요소 0개)

한글 샘플 데이터 검증 (2026-10-06, `Valeo_SVMtrial_sample_data/`, PDF 82건 / 평가 410행):
- **S1 합격** — PDF 82개, 매칭 오류 4건, CUST_A→docs / CUST_B→slides, 텍스트 레이어 0
- **S3 합격** — 표기 8종+빈칸 10개 정규화, wide 형식 동일 결과, 엄격도 순서가 정답지와 일치
  (B 0.747 > C 0.704 > E 0.675 > A 0.671 > **D 0.630 가장 엄격**), 전원합격률 0.350
- 평가자E 의 특징은 엄격도가 아니라 **서명란 의존** (있음 0.857 vs 없음 0.345, 격차 1위)
- **S2~S6 은 offline 로 검증 불가** (실 스캔 이미지, fixture 없음 → 판독성 0 으로 정직하게 보고).
  `vertex` 전환 후 수행. `ocr --dry-run` = 568회 호출
- 채점 기준은 `_answer_key/정답_README.md` 가 SETUP.md §12 보다 **우선**한다

## 설계 기준(SETUP.md)과 달라진 점 — 모두 의도적
| SETUP.md | 이 저장소 | 이유 |
|---|---|---|
| §5.2 conda 환경 `valeo_svm` | **`Valeo_SVM_Trial`** | 사용자 지시 |
| §8.3 `labels_long.parquet` | `labels_long.csv` (엔진 있으면 parquet) | `pyarrow`/`fastparquet` 가 검증된 requirements 99개에 없다 (R5). `io_utils.save_table` 이 자동 선택 |
| §13.1 `LogisticRegression(penalty="l1")` | `l1_ratio=1` | scikit-learn 1.8+ 에서 `penalty=` 폐기 예고(1.10 제거). 설치된 버전은 1.9.1 |
| — | `SVMTRIAL_BACKEND` 백엔드 전환 추가 | 이 개발 PC 에 GCP 접근이 없다. `docs/migration_vertexAI.md` |
| — | `doctor`, `approve` CLI 명령 추가 | 환경 점검과 🔒 관문의 **명시적** 통과 (R7) |
| — | `scripts/verify_dummy.py`, `scripts/run_tests.sh` 추가 | Phase 6 자동 검증 / `PYTHONPATH` 오염 회피 |
| — | 프롬프트에 기계판독 데이터 블록(`payload.py`) | 두 백엔드가 같은 입력을 쓰게 해 호출부가 갈라지지 않게 한다 |
| §6·§7.1 `data/raw/pdfs/<doc_type>/<customer>/` + `data/raw/labels/` | **`InputData/Documents/*.pdf` + `InputData/Judgement.xlsx`** (legacy 는 `input.layout: legacy`) | 사용자 확정 입력 (2026-10-14) |
| §7.2 long/wide (`doc_id`·`rater`·`result`) | Judgement wide: `FileName` + 평가자 열(값으로 판별), 값 `p`/`f`/`f(사유)` | 사용자 확정 입력 |
| §7.3 그룹 = (doc_type, customer) | InputData 는 **단일 그룹** (`input.doc_type` 으로 이름·seed 지정 가능) | 입력에 고객사·문서종류 정보가 없다 |
| §8.1 형식 판정 1단계 creator/producer | 쓰지 않음(참고값 `creator_hint`). 페이지 **이미지** 비율 → 애매하면 비전 판정 | 사용자 지시: 모든 PDF 를 이미지 기반으로 통일 |
| §2.2 "사유는 없다" / §8.4 S4b 가설만 | 평가자 Fail 사유 → 기준 후보(K…) 추가. 발견셋 사유만 쓰고 🔒 관문 유지 | 입력에 사유가 생겼다. 누수 방지 규칙은 그대로 |

## 이 PC 의 함정
`PYTHONPATH` 에 ROS Humble 의 **python3.10** 경로가 들어 있다. python3.11 환경에서 import 되면 깨진다.
- 일반 실행: 경고가 뜨지만 동작한다 (`config.check_interpreter_hygiene`)
- **`pytest` 는 깨진다** (ROS 플러그인 자동 로드) → 반드시 `./scripts/run_tests.sh` 를 쓸 것
- Windows 배포본의 `.bat` 에는 `set PYTHONPATH=` 가 들어 있다

## 사람 검토 관문 (🔒, R7)
자동 통과 금지. 각 관문은 승인 파일이 있어야 다음 단계가 돌아간다.
1. `work/sections/<group>/taxonomy_candidates.yaml` → 사람이 검토 → `taxonomy_approved.yaml`
2. `work/concepts/<group>/concepts_candidates.yaml` → 사람이 검토 → `concepts_approved.yaml`
3. `outputs/<run_id>/.../template.docx|pptx` → 사람이 최종 검토 후 배포

`python -m svmtrial approve --group G --gate sections|concepts` 는 후보 파일을 승인 파일로 복사하는 **명시적** 명령이다.
사람이 의도적으로 실행해야 하며, `all` 파이프라인은 절대 자동으로 호출하지 않는다.
