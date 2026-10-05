"""S2 ocr — Gemini 비전으로 페이지 구조 추출 (SETUP.md §8.2).

- 페이지 PNG 1장당 1회 호출 (모델 FAST, media_resolution HIGH)
- 결과 → work/ocr/<doc_id>/pNNN.json
- 문서 단위로 doc_outline.json (제목 트리 + 블록 요약 + 전체 텍스트)
- 판독성 < config.ocr.min_legibility 인 페이지는 config.ingest.retry_dpi 로 재렌더 후 재시도
"""

from __future__ import annotations

import json
from pathlib import Path

from svmtrial import parts as P
from svmtrial.config import Settings
from svmtrial.gemini_client import DryRunHit, GeminiClient, load_prompt
from svmtrial.groups import Group
from svmtrial.ingest import rerender_page
from svmtrial.payload import wrap
from svmtrial.schemas import PageLayout

FIGURE_TYPES = {"image", "photo", "chart", "diagram"}


def _page_prompt(s: Settings, doc_id: str, page_no: int, meta: dict) -> str:
    return load_prompt(
        s, "ocr_page.v1",
        data_block=wrap({
            "doc_id": doc_id,
            "page": page_no,
            "n_pages": meta.get("n_pages"),
            "page_ratio": (meta.get("page_ratios") or [None])[page_no - 1] if meta.get("page_ratios") else None,
            "doc_format_hint": meta.get("doc_format"),
        }),
    )


def ocr_page(
    s: Settings, gc: GeminiClient, doc_id: str, page_no: int, png: Path, meta: dict, force: bool = False
) -> PageLayout | None:
    out = s.work / "ocr" / doc_id / f"p{page_no:03d}.json"
    if out.exists() and not force:
        return PageLayout.model_validate_json(out.read_text(encoding="utf-8"))

    def call(img: Path, extra: str = "") -> PageLayout:
        return gc.generate_json(
            model=s.model_for("fast"),
            prompt_id="ocr_page.v1",
            parts=[P.text(_page_prompt(s, doc_id, page_no, meta)), P.image(img.read_bytes())],
            schema=PageLayout,
            cache_key_extra=f"{doc_id}/p{page_no:03d}{extra}",
            media_resolution=s.gemini.media_resolution_ocr,
        )

    try:
        layout = call(png)
        if layout.legibility < s.ocr.min_legibility:
            # 더 높은 dpi 로 다시 렌더링해 1회 재시도 (§8.2)
            hi = rerender_page(s, s.work / "pages", doc_id, page_no, s.ingest.retry_dpi)
            retry = call(hi, extra=f"@{s.ingest.retry_dpi}")
            if retry.legibility >= layout.legibility:
                layout = retry
    except DryRunHit:
        return None

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(layout.model_dump_json(indent=1), encoding="utf-8")
    return layout


def _page_text(layout: PageLayout) -> str:
    lines: list[str] = []
    for b in layout.blocks:
        if b.type == "heading":
            lines.append(f"{'#' * (b.level or 1)} {b.text}".strip())
        elif b.type == "table":
            cols = ", ".join(b.table.columns) if b.table and b.table.columns else ""
            cap = b.table.caption if b.table and b.table.caption else b.text
            lines.append(f"[표] {cap}" + (f" | 열: {cols}" if cols else ""))
        elif b.type in FIGURE_TYPES:
            lines.append(f"[{b.type}] {b.text}")
        elif b.type in {"signature", "stamp", "form_field"}:
            lines.append(f"[{b.type}] {b.text}")
        elif b.text:
            lines.append(b.text)
    return "\n".join(x for x in lines if x.strip())


def build_outline(s: Settings, doc_id: str, meta: dict) -> dict:
    """페이지 JSON 들을 문서 단위 개요로 합친다."""
    pdir = s.work / "ocr" / doc_id
    pages, headings, tables, figures = [], [], [], []
    legib, low_pages, roles, looks = [], [], [], []
    signatures = 0
    for f in sorted(pdir.glob("p[0-9][0-9][0-9].json")):
        page_no = int(f.stem[1:])
        layout = PageLayout.model_validate_json(f.read_text(encoding="utf-8"))
        legib.append(layout.legibility)
        roles.append(layout.page_role)
        looks.append(layout.looks_like)
        if layout.legibility < s.ocr.min_legibility:
            low_pages.append(page_no)
        for b in layout.blocks:
            if b.type == "heading":
                headings.append({"page": page_no, "level": b.level or 1, "text": b.text})
            elif b.type == "table" and b.table:
                tables.append({"page": page_no, "columns": b.table.columns,
                               "n_rows": b.table.n_rows, "caption": b.table.caption or b.text})
            elif b.type in FIGURE_TYPES:
                figures.append({"page": page_no, "type": b.type, "text": b.text})
            elif b.type in {"signature", "stamp"}:
                signatures += 1
        pages.append({"page": page_no, "role": layout.page_role, "text": _page_text(layout)})

    outline = {
        "doc_id": doc_id,
        "n_pages": meta.get("n_pages", len(pages)),
        "n_ocr_pages": len(pages),
        "doc_format": meta.get("doc_format"),
        "headings": headings,
        "tables": tables,
        "figures": figures,
        "n_signatures": signatures,
        "counts": {
            "headings": len(headings), "tables": len(tables), "figures": len(figures),
            "photos": sum(1 for x in figures if x["type"] == "photo"),
            "charts": sum(1 for x in figures if x["type"] == "chart"),
        },
        "page_roles": roles,
        "looks_like": looks,
        "mean_legibility": round(sum(legib) / len(legib), 3) if legib else 0.0,
        "low_legibility_pages": low_pages,
        "pages": pages,
        "outline_text": "\n".join(f"--- p{p['page']} ---\n{p['text']}" for p in pages),
    }
    (pdir / "doc_outline.json").write_text(json.dumps(outline, ensure_ascii=False, indent=1), encoding="utf-8")
    return outline


def load_outline(s: Settings, doc_id: str) -> dict:
    f = s.work / "ocr" / doc_id / "doc_outline.json"
    if not f.exists():
        raise FileNotFoundError(f"doc_outline 이 없습니다: {f} — 먼저 `svmtrial ocr` 를 실행하세요.")
    return json.loads(f.read_text(encoding="utf-8"))


def run(s: Settings, g: Group, gc: GeminiClient, force: bool = False, limit: int | None = None) -> dict:
    ing = json.loads((s.work / "ingest" / f"{g.safe}.json").read_text(encoding="utf-8"))
    doc_ids = ing["doc_ids"][:limit] if limit else ing["doc_ids"]
    pages_dir = s.work / "pages"

    jobs = []
    for doc_id in doc_ids:
        meta = json.loads((pages_dir / doc_id / "meta.json").read_text(encoding="utf-8"))
        for i, name in enumerate(meta["pages"], start=1):
            jobs.append((doc_id, i, pages_dir / doc_id / name, meta))

    gc.map_parallel(lambda j: ocr_page(s, gc, j[0], j[1], j[2], j[3], force=force), jobs)

    if gc.dry_run:
        return {"group": g.key, "n_docs": len(doc_ids), "n_pages": len(jobs), "dry_run": gc.summary()}

    outlines = [build_outline(s, d, json.loads((pages_dir / d / "meta.json").read_text(encoding="utf-8")))
                for d in doc_ids]
    bad = [o["doc_id"] for o in outlines if o["mean_legibility"] < s.ocr.min_legibility]
    summary = {
        "group": g.key,
        "n_docs": len(outlines),
        "n_pages": sum(o["n_ocr_pages"] for o in outlines),
        "mean_legibility": round(sum(o["mean_legibility"] for o in outlines) / max(1, len(outlines)), 3),
        "docs_low_legibility": bad,
        "n_low_legibility_pages": sum(len(o["low_legibility_pages"]) for o in outlines),
        "total_headings": sum(o["counts"]["headings"] for o in outlines),
        "total_tables": sum(o["counts"]["tables"] for o in outlines),
        "total_figures": sum(o["counts"]["figures"] for o in outlines),
        "gemini": gc.summary(),
    }
    out = s.work / "ocr"
    (out / f"_summary_{g.safe}.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    return summary
