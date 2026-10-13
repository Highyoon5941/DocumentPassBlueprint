"""S1 ingest — PDF → 페이지 이미지 + 메타 (SETUP.md §8.1, 2026-10-14 입력 확정 반영).

**모든 PDF 를 먼저 페이지마다 이미지로 바꾸고, 이후 단계는 이미지로만 처리한다.**
스캔본이든 Word/PowerPoint 에서 내보낸 PDF 든 같은 경로를 탄다.

- pymupdf 로 페이지마다 PNG 렌더 (Poppler 불필요, R4). 긴 변을 `ingest.max_long_edge_px` 안으로 맞춘다
- 메타: 페이지 수, 페이지별 이미지 크기·비율, 파일 sha256
  (PDF creator/producer, 텍스트 레이어 유무는 **참고값**으로만 남기고 판단에 쓰지 않는다)
- 원본 형식(docs/slides) 판정도 이미지로만 한다: 페이지 이미지 비율 → 애매하면 첫 2페이지 비전 판정
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf
from PIL import Image

from svmtrial import parts as P
from svmtrial import textutil as tu
from svmtrial.config import Settings
from svmtrial.gemini_client import DryRunHit, GeminiClient, load_prompt
from svmtrial.groups import Group, list_documents, pdfs_of
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
    page_ratios: list[float]                 # 페이지 **이미지**의 가로/세로
    page_px: list[list[int]] = field(default_factory=list)
    render_dpi: int = 150
    creator: str = ""                        # 참고값
    producer: str = ""                       # 참고값
    creator_hint: str = ""                   # creator/producer 로 본 형식 (참고값 — 판정에 쓰지 않는다)
    has_text_layer: bool = False             # 참고값 — 텍스트 레이어는 쓰지 않는다
    doc_format: str = "docs"
    doc_format_source: str = "ratio"         # ratio | vision
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


def _render_page(page: pymupdf.Page, dpi: float, max_long_edge_px: int) -> pymupdf.Pixmap:
    """페이지 1장 → 이미지. 긴 변이 max_long_edge_px 를 넘지 않도록 배율을 낮춘다.

    페이지 크기가 제각각인 PDF(A3 도면, 포스터 등)도 비슷한 크기의 이미지가 되게 한다.
    """
    r = page.rect
    long_pt = max(r.width, r.height) or 1.0
    eff = float(dpi)
    if max_long_edge_px and long_pt * eff / 72.0 > max_long_edge_px:
        eff = max_long_edge_px * 72.0 / long_pt
    z = eff / 72.0
    return page.get_pixmap(matrix=pymupdf.Matrix(z, z), alpha=False)


def render_doc(s: Settings, pdf: Path, out_dir: Path, dpi: int | None = None, force: bool = False) -> DocMeta:
    """PDF 1건 → work/pages/<doc_id>/pNNN.png + 메타. 이후 단계는 이 이미지만 쓴다."""
    doc_id = tu.doc_id(pdf.name)
    dpi = dpi or s.ingest.dpi
    pdir = out_dir / doc_id
    pdir.mkdir(parents=True, exist_ok=True)

    with pymupdf.open(pdf) as d:
        if d.needs_pass:
            raise ValueError("암호가 걸린 PDF")
        md = d.metadata or {}
        ratios, sizes, names, has_text = [], [], [], False
        for i, page in enumerate(d, start=1):
            if not has_text and page.get_text().strip():
                has_text = True                      # 참고값만 남긴다
            png = pdir / f"p{i:03d}.png"
            if force or not png.exists():
                pix = _render_page(page, dpi, s.ingest.max_long_edge_px)
                pix.save(png)
                w, h = pix.width, pix.height
            else:
                with Image.open(png) as im:
                    w, h = im.size
            sizes.append([int(w), int(h)])
            ratios.append(round(w / h, 4) if h else 1.0)
            names.append(png.name)

        creator, producer = str(md.get("creator") or ""), str(md.get("producer") or "")
        meta = DocMeta(
            doc_id=doc_id,
            pdf_path=str(pdf),
            sha256=_sha256(pdf),
            n_pages=len(ratios),
            page_ratios=ratios,
            page_px=sizes,
            render_dpi=int(dpi),
            creator=creator,
            producer=producer,
            creator_hint=_format_from_creator(creator, producer) or "",
            has_text_layer=has_text,
            pages=names,
        )
    return meta


def rerender_page(s: Settings, meta_dir: Path, doc_id: str, page_no: int, dpi: int) -> Path:
    """판독성이 낮은 페이지를 더 높은 dpi 의 **이미지로** 다시 만든다 (§8.2)."""
    meta = json.loads((meta_dir / doc_id / "meta.json").read_text(encoding="utf-8"))
    out = meta_dir / doc_id / f"p{page_no:03d}@{dpi}.png"
    cap = int(s.ingest.max_long_edge_px * dpi / max(1, s.ingest.dpi)) if s.ingest.max_long_edge_px else 0
    with pymupdf.open(meta["pdf_path"]) as d:
        _render_page(d[page_no - 1], dpi, cap).save(out)
    return out


def decide_doc_format(s: Settings, meta: DocMeta, gc: GeminiClient | None, pages_dir: Path) -> DocMeta:
    """이미지로만 판정한다: 1) 페이지 이미지 비율 → 2) 애매하면 첫 2페이지 이미지를 비전 모델로.

    SETUP.md §8.1 의 1단계(creator/producer)는 쓰지 않는다 — 모든 PDF 를 이미지로 통일하기로 했다
    (2026-10-14). creator/producer 로 본 값은 `creator_hint` 에 참고로만 남는다.
    """
    f, conf = _format_from_ratio(meta.page_ratios, s.ingest.landscape_ratio)
    if f:
        meta.doc_format, meta.doc_format_source, meta.doc_format_confidence = f, "ratio", round(conf, 3)
        return meta
    meta.doc_format_source, meta.doc_format_confidence = "ratio", round(conf, 3)
    meta.doc_format = "slides" if meta.page_ratios and meta.page_ratios[0] >= s.ingest.landscape_ratio else "docs"
    if gc is None:
        return meta
    imgs = [pages_dir / meta.doc_id / n for n in meta.pages[:2]]
    prompt = load_prompt(
        s, "doc_format.v1",
        data_block=wrap({"doc_id": meta.doc_id, "ratios": meta.page_ratios[:4]}),
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
    meta.doc_format_source = "vision"
    meta.doc_format_confidence = guess.confidence
    return meta


def _write_issues(s: Settings, g: Group, issues: list[dict]) -> Path | None:
    """그룹별 이슈를 쓰고, 전역 `work/ingest_issues.csv` 를 모든 그룹의 합집합으로 다시 만든다.

    전역 파일을 그룹마다 덮어쓰면 마지막 그룹의 이슈만 남는다(SETUP.md §7.1 은 단일 파일을
    요구한다). 그룹별 파일을 원본으로 두고 매번 합쳐 쓰므로, 한 그룹만 다시 ingest 해도
    다른 그룹의 이슈가 사라지지 않고 그 그룹의 이슈는 갱신된다(멱등).
    """
    import pandas as pd

    out = s.work / "ingest"
    out.mkdir(parents=True, exist_ok=True)
    per_group = out / f"{g.safe}.issues.csv"
    rows = [{"group": g.key, **i} for i in issues]
    if rows:
        pd.DataFrame(rows).to_csv(per_group, index=False, encoding="utf-8")
    elif per_group.exists():
        per_group.unlink()          # 이슈가 해소되면 그룹 파일도 지운다

    merged: list[pd.DataFrame] = []
    for f in sorted(out.glob("*.issues.csv")):
        try:
            merged.append(pd.read_csv(f))
        except (OSError, ValueError):
            continue
    global_f = s.work / "ingest_issues.csv"
    if merged:
        df = pd.concat(merged, ignore_index=True).drop_duplicates(subset=["group", "doc_id", "issue"])
        df = df.sort_values(["group", "doc_id"]).reset_index(drop=True)
        df.to_csv(global_f, index=False, encoding="utf-8")
        return global_f
    if global_f.exists():
        global_f.unlink()
    return None


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
    if s.inputdata:
        pdfs, issues = list_documents(s)
    else:
        pdfs, issues = pdfs_of(s, g), []
    metas: list[DocMeta] = []

    for pdf in pdfs:
        try:
            meta = render_doc(s, pdf, pages_dir, force=force)
        except (RuntimeError, ValueError, OSError) as e:   # 손상·암호화 PDF 가 전체를 멈추지 않게 한다
            issues.append({"doc_id": tu.doc_id(pdf.name),
                           "issue": f"PDF 를 이미지로 바꾸지 못함 ({type(e).__name__}: {str(e)[:80]})",
                           "path": str(pdf)})
            continue
        if meta.n_pages == 0:
            issues.append({"doc_id": meta.doc_id, "issue": "페이지 0개(손상 가능)", "path": str(pdf)})
            continue
        meta = decide_doc_format(s, meta, gc, pages_dir)
        (pages_dir / meta.doc_id / "meta.json").write_text(
            json.dumps(meta.to_json(), ensure_ascii=False, indent=1), encoding="utf-8"
        )
        metas.append(meta)

    # 평가 시트와 PDF 교차 확인 (§7.1). 대소문자·공백만 다른 이름은 연결하고 이슈로 남긴다.
    pdf_ids = {m.doc_id for m in metas}
    if label_doc_ids is not None:
        mapping, notes = tu.match_doc_ids(set(label_doc_ids), pdf_ids)
        issues.extend(notes)
        for d in sorted(set(label_doc_ids) - set(mapping)):
            issues.append({"doc_id": d, "issue": "평가 시트에는 있으나 PDF 없음", "path": ""})
        for d in sorted(pdf_ids - set(mapping.values())):
            issues.append({"doc_id": d, "issue": "PDF는 있으나 평가 시트에 없음", "path": ""})

    _write_issues(s, g, issues)

    fmt_votes = [m.doc_format for m in metas]
    group_format = max(set(fmt_votes), key=fmt_votes.count) if fmt_votes else "docs"
    summary = {
        "group": g.key,
        "input_layout": "inputdata" if s.inputdata else "legacy",
        "input_dir": str(s.documents_dir if s.inputdata else s.p("raw_pdfs")),
        "image_based": True,
        "render": {"dpi": s.ingest.dpi, "max_long_edge_px": s.ingest.max_long_edge_px},
        "n_pdfs": len(pdfs),
        "n_docs": len(metas),
        "n_pages": sum(m.n_pages for m in metas),
        "group_doc_format": group_format,
        "doc_format_votes": {f: fmt_votes.count(f) for f in set(fmt_votes)},
        "format_sources": {src: sum(1 for m in metas if m.doc_format_source == src)
                           for src in {m.doc_format_source for m in metas}},
        "n_with_text_layer": sum(1 for m in metas if m.has_text_layer),
        "n_issues": len(issues),
        "issues_file": (str((s.work / "ingest_issues.csv").relative_to(s.root))
                        if (s.work / "ingest_issues.csv").exists() else None),
        "doc_ids": sorted(pdf_ids),
    }
    out = s.work / "ingest"
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{g.safe}.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    return summary
