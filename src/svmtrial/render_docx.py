"""TemplateSpec → template.docx (SETUP.md §8.6-3).

- 기본 글꼴은 config.template.font(맑은 고딕). `w:eastAsia` 도 지정한다 (한글 깨짐 방지).
- 표지(제목, 고객사, 문서번호, 작성/검토/승인란)
- 섹션마다 Heading 1 + 회색 이탤릭 `[작성 가이드]` 문단, 필수 요소는 ☐ 체크박스 줄
- 표 요소는 지정된 열 이름으로 빈 표(3행), 그림 요소는 점선 상자 + 캡션 플레이스홀더
- 마지막에 "제출 전 체크리스트"(확정/유력 개념)
"""

from __future__ import annotations

from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt, RGBColor

from svmtrial.schemas import Element, TemplateSpec

GRAY = RGBColor(0x66, 0x66, 0x66)


def _set_font(doc: Document, font: str) -> None:
    st = doc.styles["Normal"]
    st.font.name = font
    st.font.size = Pt(10.5)
    rpr = st.element.get_or_add_rPr()
    rf = rpr.find(qn("w:rFonts"))
    if rf is None:
        rf = OxmlElement("w:rFonts")
        rpr.append(rf)
    rf.set(qn("w:eastAsia"), font)
    rf.set(qn("w:ascii"), font)
    rf.set(qn("w:hAnsi"), font)
    for name in ("Heading 1", "Heading 2", "Title"):
        try:
            h = doc.styles[name]
        except KeyError:
            continue
        h.font.name = font
        hr = h.element.get_or_add_rPr()
        hf = hr.find(qn("w:rFonts"))
        if hf is None:
            hf = OxmlElement("w:rFonts")
            hr.append(hf)
        hf.set(qn("w:eastAsia"), font)


def _guide(doc: Document, text: str) -> None:
    p = doc.add_paragraph()
    r = p.add_run(text)
    r.italic = True
    r.font.color.rgb = GRAY
    r.font.size = Pt(9)


def _dotted_box(doc: Document, caption: str) -> None:
    """그림 자리를 점선 1x1 표로 표시한다 (python-docx 에 도형 API 가 없으므로)."""
    t = doc.add_table(rows=1, cols=1)
    t.style = "Table Grid"
    cell = t.cell(0, 0)
    cell.text = ""
    p = cell.paragraphs[0]
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = p.add_run(f"[그림 자리]\n{caption}")
    r.italic = True
    r.font.color.rgb = GRAY
    tcpr = cell._tc.get_or_add_tcPr()
    borders = OxmlElement("w:tcBorders")
    for side in ("top", "left", "bottom", "right"):
        e = OxmlElement(f"w:{side}")
        e.set(qn("w:val"), "dashed")
        e.set(qn("w:sz"), "8")
        e.set(qn("w:color"), "999999")
        borders.append(e)
    tcpr.append(borders)
    for _ in range(3):
        cell.add_paragraph()


def _element(doc: Document, el: Element) -> None:
    mark = "☑" if el.required else "☐"
    p = doc.add_paragraph()
    r = p.add_run(f"{mark} {el.title}" + ("  (필수)" if el.required else "  (권장)"))
    r.bold = el.required
    _guide(doc, f"    {el.guidance}")
    if el.kind == "table":
        cols = el.table_columns or ["항목", "내용", "비고"]
        t = doc.add_table(rows=4, cols=len(cols))
        t.style = "Table Grid"
        for j, c in enumerate(cols):
            cell = t.cell(0, j)
            cell.text = str(c)
            for pp in cell.paragraphs:
                for rr in pp.runs:
                    rr.bold = True
        doc.add_paragraph()
    elif el.kind in {"figure", "photo", "chart"}:
        _dotted_box(doc, el.guidance[:120])
        doc.add_paragraph()
    elif el.kind == "signature":
        t = doc.add_table(rows=2, cols=3)
        t.style = "Table Grid"
        for j, c in enumerate(["작성", "검토", "승인"]):
            t.cell(0, j).text = c
        doc.add_paragraph()


def render(spec: TemplateSpec, out: Path, font: str = "맑은 고딕") -> Path:
    doc = Document()
    _set_font(doc, font)

    # ---- 표지
    title = doc.add_heading(f"{spec.group.split('__')[0]} 표준 템플릿", level=0)
    for r in title.runs:
        r.font.name = font
    sub = doc.add_paragraph()
    rr = sub.add_run(f"대상 그룹: {spec.group}  |  원본 형식: {spec.doc_format}")
    rr.font.color.rgb = GRAY
    cover = doc.add_table(rows=4, cols=2)
    cover.style = "Table Grid"
    for i, (k, v) in enumerate([("문서 제목", "[제목을 입력하세요]"), ("고객사", "[고객사]"),
                                ("문서번호", "[문서번호]"), ("작성일", "[YYYY-MM-DD]")]):
        cover.cell(i, 0).text = k
        cover.cell(i, 1).text = v
    doc.add_paragraph()
    sig = doc.add_table(rows=2, cols=3)
    sig.style = "Table Grid"
    for j, c in enumerate(["작성", "검토", "승인"]):
        sig.cell(0, j).text = c
    _guide(doc, f"[작성 가이드] 이 템플릿은 평가자 Pass 조건 분석({spec.target_definition})에서 "
                f"도출됐다. 문서 {spec.n_docs}건 중 Pass {spec.n_target_pass}건 기준.")
    doc.add_page_break()

    # ---- 문서 전체 규칙
    if spec.global_rules:
        doc.add_heading("0. 문서 전체 규칙", level=1)
        for el in spec.global_rules:
            _element(doc, el)
        doc.add_page_break()

    # ---- 섹션
    for sec in spec.sections:
        h = doc.add_heading(f"{sec.order}. {sec.title}" + ("" if sec.required else " (선택)"), level=1)
        for r in h.runs:
            r.font.name = font
        _guide(doc, f"{sec.guidance}  [Pass 문서 존재율 {sec.presence_in_pass:.0%}"
                    + (f", 권장 {sec.pages_hint:.1f}페이지" if sec.pages_hint else "") + "]")
        for el in sec.elements:
            _element(doc, el)
        doc.add_paragraph()

    # ---- 제출 전 체크리스트
    doc.add_page_break()
    doc.add_heading("제출 전 체크리스트", level=1)
    _guide(doc, "[작성 가이드] 아래는 통계로 검증된 항목이다. 제출 전에 모두 확인한다.")
    for ev in sorted(spec.evidence, key=lambda e: (e.verdict != "확정", -abs(e.pass_rate_with - e.pass_rate_without))):
        title = next((el.title for sec in spec.sections for el in sec.elements if ev.concept_id in el.evidence_ids),
                     ev.concept_id)
        p = doc.add_paragraph()
        r = p.add_run(f"{'☑' if ev.verdict == '확정' else '☐'} [{ev.verdict}] {title}")
        r.bold = ev.verdict == "확정"
        _guide(doc, f"    있을 때 Pass율 {ev.pass_rate_with:.0%} (n={ev.n_with}) vs "
                    f"없을 때 {ev.pass_rate_without:.0%} (n={ev.n_without})"
                    + (f", q={ev.q_value:.3g}" if ev.q_value is not None else ""))

    if spec.rater_conflicts:
        doc.add_heading("평가자 간 요구 충돌", level=1)
        for c in spec.rater_conflicts:
            doc.add_paragraph(c, style="List Bullet")

    doc.add_heading("한계와 주의사항", level=1)
    for c in spec.caveats:
        doc.add_paragraph(c, style="List Bullet")

    out.parent.mkdir(parents=True, exist_ok=True)
    doc.save(out)
    return out
