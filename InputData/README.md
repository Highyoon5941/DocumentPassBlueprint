# InputData — 분석 입력 폴더

이 폴더에 넣은 문서와 평가 시트가 분석 입력이다. **이 안내문 말고는 git 과 배포 zip 에 들어가지 않는다.**

```
InputData/
├─ Documents/          분석할 PDF 전부 (하위 폴더도 읽는다)
│   ├─ a.pdf
│   ├─ b.pdf
│   └─ …
└─ Judgement.xlsx      평가 시트
```

## Judgement.xlsx

| FileName | Person1 | Person2 | Person3 | … |
|---|---|---|---|---|
| a | p | f(향후 계획 미기입) | | |
| b | p | p | | |
| c | f(사진 부적합) | p | | |
| e | f | f(향후 일정 미기입, 사진 부적합) | | |

- **FileName** = `Documents` 안 PDF 의 파일명(확장자 없이). `a.pdf` 처럼 확장자를 붙여도 된다.
  대소문자·공백만 다르면 같은 문서로 연결하고 `work/ingest_issues.csv` 에 기록한다.
- **Person 열** 하나가 평가자 한 명이다. 열 이름은 실명이어도 된다.
- 값: `p` = Pass, `f` = Fail, `f(사유)` = 사유가 있는 Fail.
  사유가 여럿이면 쉼표·세미콜론·줄바꿈으로 나눈다. `Pass/Fail/합격/불합격/OK/NG` 도 읽는다.
- 빈칸 = 그 평가자가 평가하지 않음 (오류 아님).
- 판정값이 아닌 메모 칸(예: "examples of cause")과 비어 있는 열은 자동으로 제외하고 리포트에 적는다.
- 읽을 수 없는 값(오타)이 평가자 열에 있으면 멈추고 `work/labels/label_value_errors.csv` 에 목록을 남긴다.

## 처리 방식

1. 모든 PDF 를 **페이지마다 이미지로 바꾼다** (스캔본이든 Word·PowerPoint 에서 내보낸 PDF 든 같다).
   이후 단계는 이미지로만 판단한다. PDF 의 텍스트 레이어는 쓰지 않는다.
2. `f(사유)` 의 사유는 **기준(분석 차원) 후보**가 된다. 예: "향후 계획 미기입" → "향후 계획이 기입되어 있는가?"
3. 실행: `python -m svmtrial all` (🔒 관문에서 멈춘다 — `docs/Runbook.md`)

위치를 바꾸려면 `config/config.yaml` 의 `input:` 절을 고친다.
