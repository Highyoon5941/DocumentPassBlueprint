"""LayoutSpec → PNG / PDF (pymupdf).

**이 렌더러는 문서 종류를 모른다.** `kind` 와 `rect` 만 보고 그린다.
표지가 무엇인지, 서명란이 필요한지, 체크리스트가 끝에 와야 하는지 같은 지식은
전부 LayoutSpec 이 가진다. 그래서 완전히 다른 형식의 문서가 들어와도 배치가 따라간다.

한글: 시스템 한글 폰트를 찾아 쓰고, 없으면 pymupdf 내장 `korea` 폰트로 폴백한다
(신규 의존성 0). 내장 폰트는 자간이 넓지만 글자는 정확히 나온다.
"""

from __future__ import annotations

from pathlib import Path

import pymupdf

from svmtrial.schemas import LayoutBlock, LayoutSpec

GRAY = (0.4, 0.4, 0.4)
LIGHT = (0.95, 0.95, 0.95)
BLACK = (0, 0, 0)

# 시스템 한글 폰트 후보 (있으면 내장 폰트보다 보기 좋다)
_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
    "/usr/share/fonts/truetype/nanum/NanumBarunGothic.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "C:/Windows/Fonts/malgun.ttf",
    "/System/Library/Fonts/AppleSDGothicNeo.ttc",
]


def find_korean_font() -> tuple[str | None, str]:
    """(파일 경로, 설명). 없으면 (None, '내장 korea')."""
    for p in _FONT_CANDIDATES:
        if Path(p).exists():
            return p, Path(p).name
    return None, "pymupdf 내장 korea (자간 넓음)"


class _Pen:
    """폰트 등록을 한 번만 하고 텍스트를 그린다."""

    def __init__(self, page: pymupdf.Page, font_file: str | None):
        self.page = page
        self.font_file = font_file
        self.fontname = "kofont" if font_file else "korea"
        if font_file:
            page.insert_font(fontname=self.fontname, fontfile=font_file)

    def text(self, rect: pymupdf.Rect, s: str, size: float = 10.5,
             color=BLACK, bold: bool = False, align: int = 0) -> None:
        if not s:
            return
        # insert_textbox 는 자동 줄바꿈을 해준다. 넘치면 글자를 줄여 다시 시도한다.
        for sz in (size, size * 0.85, size * 0.7):
            rc = self.page.insert_textbox(rect, s, fontsize=sz, fontname=self.fontname,
                                          color=color, align=align)
            if rc >= 0:
                return
        self.page.insert_textbox(rect, s[:200] + "…", fontsize=size * 0.6,
                                 fontname=self.fontname, color=color, align=align)


def _draw_table(page: pymupdf.Page, pen: _Pen, b: LayoutBlock) -> None:
    x0, y0, x1, y1 = b.rect
    cols = b.columns or ["", "", ""]
    n_rows = max(2, int(b.n_rows) + 1)           # 헤더 1행 + 데이터
    cw = (x1 - x0) / max(1, len(cols))
    rh = (y1 - y0) / n_rows
    for i in range(n_rows + 1):                   # 가로선
        page.draw_line((x0, y0 + i * rh), (x1, y0 + i * rh), color=BLACK, width=0.7)
    for j in range(len(cols) + 1):                # 세로선
        page.draw_line((x0 + j * cw, y0), (x0 + j * cw, y1), color=BLACK, width=0.7)
    page.draw_rect(pymupdf.Rect(x0, y0, x1, y0 + rh), color=BLACK, fill=LIGHT, width=0.7)
    for j, c in enumerate(cols):                  # 헤더 글자
        pen.text(pymupdf.Rect(x0 + j * cw + 3, y0 + 3, x0 + (j + 1) * cw - 3, y0 + rh - 2),
                 str(c), size=min(9.0, b.font_size), bold=True)


def _draw_figure(page: pymupdf.Page, pen: _Pen, b: LayoutBlock) -> None:
    r = pymupdf.Rect(*b.rect)
    page.draw_rect(r, color=GRAY, dashes="[3 3] 0", width=1.0)
    pen.text(pymupdf.Rect(r.x0 + 6, r.y0 + 6, r.x1 - 6, r.y1 - 6),
             b.text or "[그림 자리]", size=min(9.0, b.font_size), color=GRAY, align=1)


def _draw_signature(page: pymupdf.Page, pen: _Pen, b: LayoutBlock) -> None:
    cols = b.columns or ["작성", "검토", "승인"]
    x0, y0, x1, y1 = b.rect
    cw = (x1 - x0) / max(1, len(cols))
    page.draw_rect(pymupdf.Rect(x0, y0, x1, y1), color=BLACK, width=0.7)
    for j, c in enumerate(cols):
        if j:
            page.draw_line((x0 + j * cw, y0), (x0 + j * cw, y1), color=BLACK, width=0.7)
        page.draw_line((x0 + j * cw, y0 + 16), (x0 + (j + 1) * cw, y0 + 16), color=BLACK, width=0.5)
        pen.text(pymupdf.Rect(x0 + j * cw + 2, y0 + 2, x0 + (j + 1) * cw - 2, y0 + 15),
                 str(c), size=8.5, align=1)


def _draw_checklist(page: pymupdf.Page, pen: _Pen, b: LayoutBlock) -> None:
    r = pymupdf.Rect(*b.rect)
    items = b.items or ([b.text] if b.text else [])
    y = r.y0
    for it in items:
        if y > r.y1 - 10:
            break
        # 체크박스는 글리프(☐)에 의존하지 않고 사각형을 직접 그린다.
        # 한글 폰트에 그 글리프가 없으면 두부(�)로 나오기 때문이다.
        box = pymupdf.Rect(r.x0 + 1, y + 3, r.x0 + 10, y + 12)
        page.draw_rect(box, color=BLACK, width=0.8)
        pen.text(pymupdf.Rect(r.x0 + 16, y, r.x1, y + 16), str(it), size=b.font_size)
        y += 16


def render_page(page: pymupdf.Page, blocks: list[LayoutBlock], font_file: str | None) -> None:
    pen = _Pen(page, font_file)
    for b in blocks:
        r = pymupdf.Rect(*b.rect)
        if b.kind == "table":
            _draw_table(page, pen, b)
        elif b.kind == "figure_placeholder":
            _draw_figure(page, pen, b)
        elif b.kind == "signature":
            _draw_signature(page, pen, b)
        elif b.kind == "checklist":
            _draw_checklist(page, pen, b)
        elif b.kind == "divider":
            page.draw_line((r.x0, r.y0), (r.x1, r.y0), color=GRAY, width=0.8)
        elif b.kind == "field":
            page.draw_rect(r, color=GRAY, width=0.6)
            pen.text(pymupdf.Rect(r.x0 + 4, r.y0 + 2, r.x1 - 4, r.y1 - 2), b.text, size=b.font_size)
        elif b.kind == "title":
            pen.text(r, b.text, size=max(b.font_size, 18), bold=True)
        elif b.kind == "heading":
            pen.text(r, b.text, size=max(b.font_size, 13), bold=True)
        elif b.kind == "guidance":
            pen.text(r, b.text, size=min(b.font_size, 9), color=GRAY)
        else:                                      # paragraph 등
            pen.text(r, b.text, size=b.font_size)


def render(spec: LayoutSpec, out_dir: Path, basename: str = "template",
           dpi: int = 150) -> dict[str, list[str]]:
    """LayoutSpec → PDF 1개 + 페이지별 PNG. 생성된 경로를 돌려준다."""
    font_file, font_desc = find_korean_font()
    doc = pymupdf.open()
    for pg in spec.pages:
        page = doc.new_page(width=pg.width, height=pg.height)
        render_page(page, pg.blocks, font_file)

    out_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = out_dir / f"{basename}.pdf"
    try:
        doc.subset_fonts()          # 쓰인 글자만 남겨 용량을 줄인다 (없는 버전이면 건너뜀)
    except AttributeError:
        pass
    doc.save(pdf_path, garbage=4, deflate=True)

    png_dir = out_dir / "preview"
    png_dir.mkdir(parents=True, exist_ok=True)
    pngs = []
    for i, page in enumerate(doc, start=1):
        p = png_dir / f"p{i:03d}.png"
        page.get_pixmap(dpi=dpi).save(p)
        pngs.append(str(p.relative_to(out_dir)))
    doc.close()
    return {"pdf": [str(pdf_path.relative_to(out_dir))], "png": pngs, "font": [font_desc]}
