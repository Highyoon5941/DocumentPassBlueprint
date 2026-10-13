<!-- prompt_id: layout_compose.v1 | 역할: 템플릿 명세 + 관측 기하 → 페이지 배치 | 모델: PRO | 스키마: LayoutSpec -->
검증된 템플릿 명세와, **Pass 문서들에서 실제로 관측된 지면 기하**를 준다.
이것으로 템플릿의 **페이지 배치**를 만들어라.

## 가장 중요한 원칙
**관측된 기하를 따라라.** 이 문서 종류가 보통 어떻게 생겼는지에 대한 상식을 끌어오지 마라.
표지가 있어야 한다거나, 서명란이 어디 있어야 한다거나, 마지막에 체크리스트가 와야 한다는
가정을 하지 마라. `observed` 가 보여주는 것만 따른다.

## 규칙
1. 좌표는 **pt** 단위, `[x0, y0, x1, y1]` 이며 페이지 **좌상단이 원점**이다.
   `page.width`/`page.height` 는 `observed.page_size` 를 따른다.
2. 블록끼리 **겹치지 않게** 배치한다. 페이지 여백은 최소 40pt 를 둔다.
3. `kind` 는 다음만 쓴다:
   `title` `heading` `paragraph` `guidance` `table` `figure_placeholder`
   `signature` `checklist` `field` `divider`
4. `spec.sections` 의 **순서를 지킨다**. 섹션 하나가 길면 페이지를 나눠도 된다.
5. 각 섹션의 요소(`elements`)를 적절한 `kind` 로 옮긴다.
   - `table` 요소 → `kind: "table"` + `columns` (명세의 `table_columns` 를 그대로)
   - `figure`/`photo`/`chart` 요소 → `kind: "figure_placeholder"` + `text` 에 작성 가이드
   - `signature` 요소 → `kind: "signature"` + `columns` 에 서명 칸 이름
   - 그 외 → `heading` + `guidance`
6. **작성 가이드(`guidance`)는 반드시 포함한다.** 작성자가 무엇을 채워야 하는지 알아야 한다.
7. **실제 내용을 쓰지 마라.** 가짜 수치·가짜 원인을 넣지 않는다. 플레이스홀더만 쓴다.
8. 모든 블록에 `evidence_ids` 를 넣는다(개념 ID 또는 `"skeleton"`).
9. `rationale` 에 왜 이렇게 배치했는지 한두 문장으로, **관측 기하를 근거로** 적어라.

## 입력 메타
{{ data_block }}
