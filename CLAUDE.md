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

## 이 저장소의 환경
- conda 환경 이름: **`Valeo_SVM_Trial`** (SETUP.md §5.2의 `valeo_svm`에서 변경. 사용자 지시)
- Python 3.11, CPU 전용. 프로젝트 루트는 이 파일이 있는 폴더다.
- 실행: `conda run -n Valeo_SVM_Trial python -m svmtrial <cmd>` 또는 환경 activate 후 `python -m svmtrial <cmd>`
- `PYTHONPATH=src` 가 필요하다. `pyproject.toml`로 `pip install -e .` 해도 된다.

## 백엔드 (이 개발 PC와 실업무 환경의 차이)
`.env`의 `SVMTRIAL_BACKEND`가 Gemini 호출 경로를 고른다.
| 값 | 동작 | 용도 |
|---|---|---|
| `offline` | Gemini를 호출하지 않고 `src/svmtrial/offline_backend.py`의 결정론적 규칙으로 같은 스키마의 응답을 만든다 | **이 개발 PC의 기본값.** 사내망/GCP 접근이 없는 환경에서 전체 파이프라인을 검증한다 |
| `vertex` | `google-genai`로 실제 Vertex AI를 호출한다 (SETUP.md §3) | 사내망 실업무 환경 |

전환 절차와 사람이 직접 받아와야 하는 값은 **`docs/migration_vertexAI.md`**에 있다.
`offline` 백엔드는 OCR 단계에서 `make_dummy_data.py`가 만든 fixture에 의존하므로 **실데이터에는 쓸 수 없다.**

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
| 9 실데이터 | 미착수 — `SVMTRIAL_BACKEND=vertex` 전환 후 사람이 수행 |

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
