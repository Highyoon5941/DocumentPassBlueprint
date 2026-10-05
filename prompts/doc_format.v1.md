<!-- prompt_id: doc_format.v1 | 역할: 원본이 문서형(Word/HWP)인지 슬라이드형(PPT)인지 판정 | 모델: FAST | 스키마: DocFormatGuess -->
입력 이미지는 어떤 문서의 첫 페이지들이다. 이 문서의 **원본 형식**을 판정하라.

- `docs`: 세로 방향 본문 중심, 문단과 번호 매긴 제목, 페이지 하단 쪽번호 — Word/한글/Google Docs 형태
- `slides`: 가로 방향, 페이지당 제목 1개와 큰 글씨 박스·도식 중심 — PowerPoint/Keynote/Google Slides 형태

`confidence` 는 0.0~1.0 으로 하고, `reason` 에 판단 근거를 한 문장으로 쓴다.

## 입력 메타
{{ data_block }}
