"""S1 ingest — PDF → 페이지 PNG + 메타 (SETUP.md §8.1).

- pymupdf 로 페이지마다 PNG 렌더 (Poppler 불필요, R4)
- 메타: 페이지 수, 페이지별 비율, creator/producer, 텍스트 레이어 유무, 파일 sha256
- 원본 형식(docs/slides) 판정 4단계
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

from svmtrial import parts as P
from svmtrial.config import Settings
from svmtrial.gemini_client import DryRunHit, GeminiClient, load_prompt
from svmtrial.groups import Group, pdfs_of
from svmtrial.payload import wrap
from svmtrial.schemas import DocFormatGuess

SLIDE_HINTS = ("powerpoint", "keynote", "google slides", "impress", "pptx", "슬라이드")
DOCS_HINTS = ("word", "hangul", "hwp", "google docs", "writer", "docx", "한글", "polaris")


@dataclass
class DocMeta:
    doc_id: str
    pdf_path: str
    sha256: str
    n_pages: int
    page_ratios: list[float]
    creator: str = ""
    producer: str = ""
    has_text_layer: bool = False
    doc_format: str = "docs"
    doc_format_source: str = "ratio"       # creator | ratio | gemini
    doc_format_confidence: float = 0.0
    pages: list[str] = field(default_factory=list)

    def to_json(self) -> dict:
        d = dict(self.__dict__)
        return d


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _format_from_creator(creator: str, producer: str) -> str | None:
    blob = f"{creator} {producer}".lower()
    if any(h in blob for h in SLIDE_HINTS):
        return "slides"
    if any(h in blob for h in DOCS_HINTS):
        return "docs"
    return None


def _format_from_ratio(ratios: list[float], thr: float) -> tuple[str | None, float]:
    """페이지 과반이 가로형이면 slides, 세로형이면 docs. 혼재면 (None, 신뢰도)."""
    if not ratios:
        return None, 0.0
    land = sum(1 for r in ratios if r >= thr)
    frac = land / len(ratios)
    if frac >= 0.75:
        return "slides", frac
    if frac <= 0.25:
        return "docs", 1 - frac
    return None, abs(frac - 0.5) * 2


def render_doc(s: Settings, pdf: Path, out_dir: Path, dpi: int | None = None, force: bool = False) -> DocMeta:
    """PDF 1건 → work/pages/<doc_id>/pNNN.png + meta.json"""
    doc_id = pdf.stem
    dpi = dpi or s.ingest.dpi
    pdir = out_dir / doc_id
    pdir.mkdir(parents=True, exist_ok=True)

    with pymupdf.open(pdf) as d:
        md = d.metadata or {}
        ratios, names, has_text = [], [], False
        for i, page in enumerate(d, start=1):
            r = page.rect
            ratios.append(round(r.width / r.height, 4) if r.height else 1.0)
            if not has_text and page.get_text().strip():
                has_text = True
            png = pdir / f"p{i:03d}.png"
            if force or not png.exists():
                page.get_pixmap(dpi=dpi).save(png)
            names.append(png.name)

        meta = DocMeta(
            doc_id=doc_id,
            pdf_path=str(pdf),
            sha256=_sha256(pdf),
            n_pages=len(ratios),
            page_ratios=ratios,
            creator=str(md.get("creator") or ""),
            producer=str(md.get("producer") or ""),
            has_text_layer=has_text,
            pages=names,
        )
    return meta


def rerender_page(s: Settings, meta_dir: Path, doc_id: str, page_no: int, dpi: int) -> Path:
    """판독성이 낮은 페이지를 더 높은 dpi 로 다시 렌더링한다 (§8.2)."""
    meta = json.loads((meta_dir / doc_id / "meta.json").read_text(encoding="utf-8"))
    out = meta_dir / doc_id / f"p{page_no:03d}@{dpi}.png"
    with pymupdf.open(meta["pdf_path"]) as d:
        d[page_no - 1].get_pixmap(dpi=dpi).save(out)
    return out


def decide_doc_format(s: Settings, meta: DocMeta, gc: GeminiClient | None, pages_dir: Path) -> DocMeta:
    """1) creator/producer → 2) 페이지 비율 → 3) 애매하면 Gemini 로 첫 2페이지 판정."""
    f = _format_from_creator(meta.creator, meta.producer)
    if f:
        meta.doc_format, meta.doc_format_source, meta.doc_format_confidence = f, "creator", 1.0
        return meta
    f, conf = _format_from_ratio(meta.page_ratios, s.ingest.landscape_ratio)
    if f:
        meta.doc_format, meta.doc_format_source, meta.doc_format_confidence = f, "ratio", round(conf, 3)
        return meta
    meta.doc_format_source, meta.doc_format_confidence = "ratio", round(conf, 3)
    meta.doc_format = "slides" if conf and meta.page_ratios and meta.page_ratios[0] >= s.ingest.landscape_ratio else "docs"
    if gc is None:
        return meta
    imgs = [pages_dir / meta.doc_id / n for n in meta.pages[:2]]
    prompt = load_prompt(
        s, "doc_format.v1",
        data_block=wrap({"doc_id": meta.doc_id, "ratios": meta.page_ratios[:4],
                         "creator": meta.creator, "producer": meta.producer}),
    )
    try:
        guess = gc.generate_json(
            model=s.model_for("fast"), prompt_id="doc_format.v1",
            parts=[P.text(prompt), *[P.image(p.read_bytes()) for p in imgs if p.exists()]],
            schema=DocFormatGuess, cache_key_extra=meta.doc_id,
        )
    except DryRunHit:
        return meta
    meta.doc_format = guess.doc_format
    meta.doc_format_source = "gemini"
    meta.doc_format_confidence = guess.confidence
    return meta


def run(
    s: Settings,
    g: Group,
    gc: GeminiClient | None = None,
    force: bool = False,
    label_doc_ids: set[str] | None = None,
) -> dict:
    """그룹 1개를 ingest 한다. 반환값은 리포트용 요약."""
    pages_dir = s.work / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)
    pdfs = pdfs_of(s, g)
    issues: list[dict] = []
    metas: list[DocMeta] = []

    for pdf in pdfs:
        meta = render_doc(s, pdf, pages_dir, force=force)
        if meta.n_pages == 0:
            issues.append({"doc_id": meta.doc_id, "issue": "페이지 0개(손상 가능)", "path": str(pdf)})
            continue
        meta = decide_doc_format(s, meta, gc, pages_dir)
        (pages_dir / meta.doc_id / "meta.json").write_text(
            json.dumps(meta.to_json(), ensure_ascii=False, indent=1), encoding="utf-8"
        )
        metas.append(meta)

    # 평가 시트와 PDF 교차 확인 (§7.1)
    pdf_ids = {m.doc_id for m in metas}
    if label_doc_ids is not None:
        for d in sorted(label_doc_ids - pdf_ids):
            issues.append({"doc_id": d, "issue": "평가 시트에는 있으나 PDF 없음", "path": ""})
        for d in sorted(pdf_ids - label_doc_ids):
            issues.append({"doc_id": d, "issue": "PDF는 있으나 평가 시트에 없음", "path": ""})

    if issues:
        import pandas as pd

        pd.DataFrame(issues).to_csv(s.work / "ingest_issues.csv", index=False, encoding="utf-8")

    fmt_votes = [m.doc_format for m in metas]
    group_format = max(set(fmt_votes), key=fmt_votes.count) if fmt_votes else "docs"
    summary = {
        "group": g.key,
        "n_pdfs": len(pdfs),
        "n_docs": len(metas),
        "n_pages": sum(m.n_pages for m in metas),
        "group_doc_format": group_format,
        "doc_format_votes": {f: fmt_votes.count(f) for f in set(fmt_votes)},
        "format_sources": {src: sum(1 for m in metas if m.doc_format_source == src)
                           for src in {m.doc_format_source for m in metas}},
        "n_with_text_layer": sum(1 for m in metas if m.has_text_layer),
        "n_issues": len(issues),
        "doc_ids": sorted(pdf_ids),
    }
    out = s.work / "ingest"
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{g.safe}.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    return summary
