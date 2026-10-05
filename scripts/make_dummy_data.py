"""가짜 '이미지 전용' PDF + 평가 시트를 만든다 (실데이터 없이 파이프라인 시험용).

정답 규칙(숨김): 5Why 근본원인 / 효과검증 그래프 / 수평전개 표 가 있을수록 Pass 확률↑.
파이프라인이 이 3가지를 다시 찾아내면 정상 동작으로 본다.

사용: python scripts/make_dummy_data.py --out data/dummy --n 40

부가 산출물:
- `<out>/truth.csv`               : 숨김 정답 (Phase 6 검증용)
- `work/offline_fixtures/pages.json` : offline 백엔드 OCR 스텁이 쓰는 fixture
                                       (이미지 sha256 → 그 페이지에 그려진 줄 목록)
                                       실데이터에는 존재하지 않는다 → SVMTRIAL_BACKEND=vertex 필요.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import random
from pathlib import Path

import pandas as pd
import pymupdf
from PIL import Image, ImageDraw, ImageFont

SECTIONS = [
    "D1 Team", "D2 Problem description (5W2H)", "D3 Containment action",
    "D4 Root cause", "D5 Corrective action", "D6 Effectiveness check",
    "D7 Prevent recurrence", "D8 Closure",
]
HIDDEN = {
    "why5": "5-Why analysis table: Why1 | Why2 | Why3 | Why4 | Why5",
    "graph": "[Chart] Defect rate before/after (ppm) - trend graph",
    "yokoten": "Horizontal deployment table: Line | Model | Applied date | Owner",
}
# 숨김 규칙과 무관한 잡음 요소 (파이프라인이 이것들은 기각해야 한다)
NOISE = {
    "photo": "[Photo] Defective part close-up",
    "org": "Team member list table: Name | Dept | Role",
}
RATERS = ["rater_A", "rater_B", "rater_C", "rater_D", "rater_E"]


def page_image(lines, landscape):
    w, h = (1600, 900) if landscape else (1240, 1754)
    img = Image.new("RGB", (w, h), "white")
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.load_default(size=28)
    except TypeError:  # 구버전 Pillow
        font = ImageFont.load_default()
    y = 60
    for ln in lines:
        if ln.startswith("[Chart]"):
            d.rectangle([80, y, w - 80, y + 220], outline="black", width=3)
            d.line([100, y + 200, w // 2, y + 60, w - 100, y + 180], fill="black", width=3)
        d.text((80, y), ln, fill="black", font=font)
        y += 260 if ln.startswith("[Chart]") else 50
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def make_pdf(path, landscape, feats, rnd, fixtures: dict, doc_id: str):
    doc = pymupdf.open()
    secs = [s for s in SECTIONS if rnd.random() > 0.15 or s.startswith(("D2", "D4", "D5"))]
    per_page = 2 if landscape else 4
    pno = 0
    for i in range(0, len(secs), per_page):
        lines = []
        for s in secs[i : i + per_page]:
            lines += [s, "  lorem ipsum dolor sit amet " * 2]
            if s.startswith("D1") and feats["org"]:
                lines.append(NOISE["org"])
            if s.startswith("D3") and feats["photo"]:
                lines.append(NOISE["photo"])
            if s.startswith("D4") and feats["why5"]:
                lines.append(HIDDEN["why5"])
            if s.startswith("D6") and feats["graph"]:
                lines.append(HIDDEN["graph"])
            if s.startswith("D7") and feats["yokoten"]:
                lines.append(HIDDEN["yokoten"])
        png = page_image(lines, landscape)
        pw, ph = (842, 474) if landscape else (595, 842)
        page = doc.new_page(width=pw, height=ph)
        page.insert_image(page.rect, stream=png)  # 텍스트 레이어 없음 = 스캔본과 동일 조건
        pno += 1
        # offline 백엔드 OCR 스텁용 fixture.
        # 키는 (doc_id, 페이지번호) 다. ingest 가 PDF를 다시 렌더링하면 PNG 바이트가 달라지므로
        # 이미지 sha256 을 키로 쓸 수 없다. 이미지 해시는 보조 확인용으로만 남긴다.
        fixtures[f"{doc_id}/p{pno:03d}"] = {
            "doc_id": doc_id,
            "page": pno,
            "page_role": "cover" if pno == 1 and not landscape else "body",
            "legibility": 0.95,
            "source_png_sha256": hashlib.sha256(png).hexdigest(),
            "lines": lines,
        }
    doc.save(path)
    doc.close()
    return pno


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/dummy")
    ap.add_argument("--n", type=int, default=40)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--work", default="work", help="offline fixture 를 쓸 work 디렉토리")
    a = ap.parse_args()

    rnd = random.Random(a.seed)
    out = Path(a.out)
    rows, truth, fixtures = [], [], {}
    all_feats = list(HIDDEN) + list(NOISE)
    for customer, doc_type, landscape in [("CUST_A", "대책서", False), ("CUST_B", "대책서", True)]:
        pdir = out / "pdfs" / doc_type / customer
        pdir.mkdir(parents=True, exist_ok=True)
        for k in range(a.n):
            feats = {f: rnd.random() < 0.5 for f in all_feats}
            doc_id = f"{customer}_{k:03d}"
            npages = make_pdf(pdir / f"{doc_id}.pdf", landscape, feats, rnd, fixtures, doc_id)
            p = 0.15 + 0.25 * sum(feats[f] for f in HIDDEN)  # 숨김 규칙 (잡음 요소는 영향 없음)
            truth.append({"doc_id": doc_id, "customer": customer, "doc_type": doc_type,
                          "n_pages": npages, "p_pass": round(p, 3),
                          **{f"truth_{f}": int(feats[f]) for f in all_feats}})
            for r in RATERS:
                strict = 0.1 if r == "rater_E" else 0.0  # 한 명은 더 깐깐함
                rows.append({"doc_id": doc_id, "customer": customer, "doc_type": doc_type, "rater": r,
                             "result": "Pass" if rnd.random() < p - strict else "Fail"})

    lab = out / "labels"
    lab.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_excel(lab / "labels_long.xlsx", index=False)
    pd.DataFrame(truth).to_csv(out / "truth.csv", index=False, encoding="utf-8")

    fx = Path(a.work) / "offline_fixtures" / "pages.json"
    fx.parent.mkdir(parents=True, exist_ok=True)
    fx.write_text(json.dumps(fixtures, ensure_ascii=False), encoding="utf-8")

    print(f"PDF {2 * a.n}개, 평가 {len(rows)}행 → {out}")
    print(f"숨김 정답 → {out / 'truth.csv'}  (숨김 규칙: {', '.join(HIDDEN)} / 잡음: {', '.join(NOISE)})")
    print(f"offline fixture {len(fixtures)}페이지 → {fx}")


if __name__ == "__main__":
    main()
