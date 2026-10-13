"""LayoutSpec → .docx (편집 가능한 흐름 문서).

`render_image.py` 와 **같은 LayoutSpec** 을 원천으로 쓴다. 그래서 파이썬에
"표지는 이렇게 생겼다", "서명란은 작성/검토/승인이다" 같은 문서 종류 가정이 없다.

이미지(render_image)는 좌표를 그대로 그리고, 이쪽은 좌표를 **위→아래, 좌→우 흐름**으로
근사 변환한다. Word 는 흐름 문서라 절대 좌표를 그대로 쓸 수 없기 때문이다.
"""

from __future__ import annotations

from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt, RGBColor

from svmtrial.schemas import LayoutBlock, LayoutSpec

GRAY = RGBColor(0x66, 0x66, 0x66)


def _set_font(doc: Document, font: str) -> None:
    for name in ("Normal", "Heading 1", "Heading 2", "Title"):
        try:
            st = doc.styles[name]
        except KeyError:
            continue
        st.font.name = font
        rpr = st.element.get_or_add_rPr()
        rf = rpr.find(qn("w:rFonts"))
        if rf is None:
            rf = OxmlElement("w:rFonts")
            rpr.append(rf)
        for attr in ("w:eastAsia", "w:ascii", "w:hAnsi"):
            rf.set(qn(attr), font)


def _reading_order(blocks: list[LayoutBlock]) -> list[LayoutBlock]:
    """좌표를 흐름 순서로: 위에서 아래, 같은 줄이면 왼쪽에서 오른쪽."""
    return sorted(blocks, key=lambda b: (round(b.rect[1] / 12.0), b.rect[0]))


def _dotted_box(doc: Document, caption: str) -> None:
    t = doc.add_table(rows=1, cols=1)
    t.style = "Table Grid"
    cell = t.cell(0, 0)
    p = cell.paragraphs[0]
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = p.add_run(caption or "[그림 자리]")
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


def _block(doc: Document, b: LayoutBlock) -> None:
    if b.kind == "title":
        h = doc.add_heading(b.text, level=0)
        del h
    elif b.kind == "heading":
        doc.add_heading(b.text, level=1)
    elif b.kind == "guidance":
        p = doc.add_paragraph()
        r = p.add_run(b.text)
        r.italic = True
        r.font.color.rgb = GRAY
        r.font.size = Pt(9)
    elif b.kind == "divider":
        doc.add_paragraph("―" * 40).runs[0].font.color.rgb = GRAY
    elif b.kind == "table":
        cols = b.columns or ["항목", "내용", "비고"]
        t = doc.add_table(rows=max(2, b.n_rows + 1), cols=len(cols))
        t.style = "Table Grid"
        for j, c in enumerate(cols):
            cell = t.cell(0, j)
            cell.text = str(c)
            for pp in cell.paragraphs:
                for rr in pp.runs:
                    rr.bold = True
        doc.add_paragraph()
    elif b.kind == "figure_placeholder":
        _dotted_box(doc, b.text)
        doc.add_paragraph()
    elif b.kind == "signature":
        cols = b.columns or ["작성", "검토", "승인"]
        t = doc.add_table(rows=2, cols=len(cols))
        t.style = "Table Grid"
        for j, c in enumerate(cols):
            t.cell(0, j).text = str(c)
        doc.add_paragraph()
    elif b.kind == "checklist":
        for it in (b.items or ([b.text] if b.text else [])):
            doc.add_paragraph(f"☐  {it}")
    elif b.kind == "field":
        t = doc.add_table(rows=1, cols=1)
        t.style = "Table Grid"
        t.cell(0, 0).text = b.text
        doc.add_paragraph()
    else:
        doc.add_paragraph(b.text)


def render(spec: LayoutSpec, out: Path, font: str = "맑은 고딕") -> Path:
    doc = Document()
    _set_font(doc, font)
    for i, page in enumerate(spec.pages):
        if i:
            doc.add_page_break()
        for b in _reading_order(page.blocks):
            _block(doc, b)
    out.parent.mkdir(parents=True, exist_ok=True)
    doc.save(out)
    return out
