"""문서종류 불변성 증명용 더미 데이터 — **요구사양서** (대책서와 완전히 다른 성격).

`make_dummy_data.py`(대책서/8D)와 의도적으로 모든 것을 다르게 했다.
파이프라인 코드를 **한 줄도 바꾸지 않고** 이 문서종류의 숨김 규칙을 다시 찾아내면,
"학습된 문서의 성격에만 국한된다"는 우려가 해소된다.

| 축 | make_dummy_data.py | 이 파일 |
|---|---|---|
| 문서종류 | 대책서 | **요구사양서** |
| 섹션 체계 | 8D (D1~D8) — seeds.py 에 seed 있음 | R1~R8 — **seed 없음(데이터에서 유도)** |
| 숨김 규칙 | 5Why / 효과검증 그래프 / 수평전개 표 | **합격판정기준 표 / 추적성 표 / 규격 상·하한 표** |
| 평가자 | 5명 (rater_A~E) | **4명 (reviewer_1~4)** |
| 레이아웃 | 세로 docs / 가로 slides | 세로 docs 만 |

사용: python scripts/make_dummy_reqspec.py --out data/reqspec --n 60
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

# 8D 와 겹치지 않는 섹션 체계
SECTIONS = [
    "R1 Scope and applicability",
    "R2 Terms and definitions",
    "R3 Functional requirements",
    "R4 Performance requirements",
    "R5 Environmental and reliability",
    "R6 Interface requirements",
    "R7 Verification and test",
    "R8 Approval",
]

# 숨김 정답 규칙 (대책서와 전혀 다른 요소들)
HIDDEN = {
    "accept":    ("R7", "Acceptance criteria table: Item | Spec | Method | Judgement"),
    "trace":     ("R3", "Traceability matrix table: ReqID | Test case | Status"),
    "tolerance": ("R4", "Tolerance table: Parameter | Min | Nom | Max | Unit"),
}
# 미끼 (효과 0)
NOISE = {
    "glossary": ("R2", "Glossary table: Term | Definition"),
    "revision": ("R1", "Revision history table: Rev | Date | Author"),
}
RATERS = ["reviewer_1", "reviewer_2", "reviewer_3", "reviewer_4"]

# 숨김 규칙의 가중치 (로짓). accept 가 가장 강하다.
W = {"accept": 2.0, "trace": 1.3, "tolerance": 0.9}
BIAS = -1.5
RATER_BIAS = {"reviewer_1": 0.0, "reviewer_2": 0.45, "reviewer_3": -0.1, "reviewer_4": -0.35}


def page_image(lines: list[str]) -> bytes:
    w, h = 1240, 1754
    img = Image.new("RGB", (w, h), "white")
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.load_default(size=26)
    except TypeError:
        font = ImageFont.load_default()
    y = 60
    for ln in lines:
        d.text((80, y), ln, fill="black", font=font)
        y += 48
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def make_pdf(path: Path, feats: dict, rnd: random.Random, fixtures: dict, doc_id: str) -> int:
    doc = pymupdf.open()
    secs = [s for s in SECTIONS if rnd.random() > 0.12 or s.startswith(("R3", "R4", "R7"))]
    per_page = 3
    pno = 0
    for i in range(0, len(secs), per_page):
        lines: list[str] = []
        for s in secs[i : i + per_page]:
            lines += [s, "  requirement text placeholder " * 2]
            sec_id = s.split()[0]
            for key, (owner, text) in {**HIDDEN, **NOISE}.items():
                if owner == sec_id and feats[key]:
                    lines.append(text)
        png = page_image(lines)
        page = doc.new_page(width=595, height=842)
        page.insert_image(page.rect, stream=png)
        pno += 1
        fixtures[f"{doc_id}/p{pno:03d}"] = {
            "doc_id": doc_id, "page": pno,
            "page_role": "cover" if pno == 1 else "body",
            "legibility": 0.95,
            "source_png_sha256": hashlib.sha256(png).hexdigest(),
            "lines": lines,
        }
    doc.save(path)
    doc.close()
    return pno


def main() -> None:
    import math

    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/reqspec")
    ap.add_argument("--n", type=int, default=60)
    ap.add_argument("--seed", type=int, default=5)
    ap.add_argument("--work", default="work")
    a = ap.parse_args()

    rnd = random.Random(a.seed)
    out = Path(a.out)
    rows, truth, fixtures = [], [], {}
    all_feats = list(HIDDEN) + list(NOISE)
    customer, doc_type = "CUST_R", "요구사양서"
    pdir = out / "pdfs" / doc_type / customer
    pdir.mkdir(parents=True, exist_ok=True)

    for k in range(a.n):
        feats = {f: rnd.random() < 0.5 for f in all_feats}
        doc_id = f"{customer}_{k:03d}"
        npages = make_pdf(pdir / f"{doc_id}.pdf", feats, rnd, fixtures, doc_id)
        logit = BIAS + sum(W[f] * feats[f] for f in HIDDEN) + rnd.gauss(0, 0.3)
        truth.append({"doc_id": doc_id, "customer": customer, "doc_type": doc_type,
                      "n_pages": npages, **{f"truth_{f}": int(feats[f]) for f in all_feats}})
        for r in RATERS:
            p = 1 / (1 + math.exp(-(logit + RATER_BIAS[r])))
            rows.append({"doc_id": doc_id, "customer": customer, "doc_type": doc_type,
                         "rater": r, "result": "Pass" if rnd.random() < p else "Fail"})

    lab = out / "labels"
    lab.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_excel(lab / "labels_long.xlsx", index=False)
    pd.DataFrame(truth).to_csv(out / "truth.csv", index=False, encoding="utf-8")

    fx = Path(a.work) / "offline_fixtures" / "pages.json"
    fx.parent.mkdir(parents=True, exist_ok=True)
    prev = json.loads(fx.read_text(encoding="utf-8")) if fx.exists() else {}
    prev.update(fixtures)          # 기존(대책서) fixture 를 지우지 않고 합친다
    fx.write_text(json.dumps(prev, ensure_ascii=False), encoding="utf-8")

    print(f"요구사양서 PDF {a.n}개, 평가 {len(rows)}행 ({len(RATERS)}명) → {out}")
    print(f"숨김 규칙: {', '.join(f'{k}(w={W[k]})' for k in HIDDEN)} / 미끼: {', '.join(NOISE)}")
    print("섹션 체계: R1~R8 (seeds.py 에 seed 없음 → 데이터에서 유도되어야 함)")
    print(f"offline fixture {len(fixtures)}페이지 추가 → {fx} (총 {len(prev)})")


if __name__ == "__main__":
    main()
