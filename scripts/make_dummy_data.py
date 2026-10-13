"""가짜 '이미지 전용' PDF + 평가 시트를 만든다 (실데이터 없이 파이프라인 시험용).

정답 규칙(숨김): 5Why 근본원인 / 효과검증 그래프 / 수평전개 표 가 있을수록 Pass 확률↑.
파이프라인이 이 3가지를 다시 찾아내면 정상 동작으로 본다.

사용: python scripts/make_dummy_data.py --out data/dummy --n 40
      python scripts/make_dummy_data.py --layout inputdata --out data/dummy_input --n 60

--layout legacy    (기본) <out>/pdfs/대책서/CUST_A|CUST_B/*.pdf + <out>/labels/labels_long.xlsx
--layout inputdata (2026-10-14 확정 입력) <out>/InputData/Documents/*.pdf + <out>/InputData/Judgement.xlsx
                   Judgement 는 FileName | Person1~4 (값 p / f / f(사유)) + 빈 평가자 열 + `..` 열·행
                   + "examples of cause" 메모 칸 — 실제 시트 예시와 같은 모양이다.
                   사유는 그 문서에 **없는** 숨김 요소를 평가자가 적은 것이다 (가끔 틀린 사유·사유 없는 f).

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
# inputdata 레이아웃: 평가자가 Fail 사유로 적는 문구 (그 요소가 없을 때)
CAUSES = {
    "why5": "5-Why analysis table 미기입",
    "graph": "Defect rate trend graph 누락",
    "yokoten": "Horizontal deployment table 없음",
}
WEIGHTS = {"why5": 3, "graph": 2, "yokoten": 1}       # 빠진 요소가 여럿이면 무엇을 적을지 고르는 가중치
NOTE = ["1. 5-Why analysis table 미기입", "2. Defect rate trend graph 누락",
        "3. Horizontal deployment table 없음", "4. etc.."]


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


def fail_value(feats: dict, rnd: random.Random) -> str:
    """Fail 셀 값. 빠진 숨김 요소를 사유로 적는다 — 실제 평가자처럼 가끔 사유를 안 적거나 틀리게 적는다."""
    missing = [f for f in HIDDEN if not feats[f]]
    r = rnd.random()
    if missing and r < 0.75:
        k = 2 if (len(missing) >= 2 and rnd.random() < 0.25) else 1
        pool, picked = list(missing), []
        while pool and len(picked) < k:
            f = rnd.choices(pool, weights=[WEIGHTS[x] for x in pool])[0]
            picked.append(f)
            pool.remove(f)
        return "f(" + ", ".join(CAUSES[f] for f in picked) + ")"
    if r < 0.80:                                   # 있는 요소를 사유로 적은 오기 (채점 일치도 점검용)
        present = [f for f in HIDDEN if feats[f]]
        if present:
            return f"f({CAUSES[rnd.choice(present)]})"
    if r < 0.88:
        return "f(기타 형식 미흡)"                   # 기준으로 쓰기 어려운 사유
    return "f" if rnd.random() < 0.8 else "F"


def write_inputdata(out: Path, n: int, rnd: random.Random, fixtures: dict, n_raters: int = 4) -> tuple[int, int]:
    """확정 입력 레이아웃: InputData/Documents/*.pdf + InputData/Judgement.xlsx (wide, p/f/f(사유))."""
    root = out / "InputData"
    docs = root / "Documents"
    docs.mkdir(parents=True, exist_ok=True)
    all_feats = list(HIDDEN) + list(NOISE)
    rows, truth = [], []
    for k in range(n):
        feats = {f: rnd.random() < 0.5 for f in all_feats}
        doc_id = f"DOC_{k:03d}"
        npages = make_pdf(docs / f"{doc_id}.pdf", False, feats, rnd, fixtures, doc_id)
        p = 0.15 + 0.25 * sum(feats[f] for f in HIDDEN)
        row: dict[str, object] = {"FileName": doc_id}
        for j in range(1, n_raters + 1):
            strict = 0.1 if j == n_raters else 0.0        # 마지막 평가자가 더 깐깐하다
            row[f"Person{j}"] = ("p" if rnd.random() < 0.9 else "P") if rnd.random() < p - strict \
                else fail_value(feats, rnd)
        rows.append(row)
        truth.append({"doc_id": doc_id, "n_pages": npages, "p_pass": round(p, 3),
                      **{f"truth_{f}": int(feats[f]) for f in all_feats}})

    df = pd.DataFrame(rows)
    df[f"Person{n_raters + 1}"] = None                    # 비어 있는 평가자 열 (예시 시트의 Person3 처럼)
    df[".."] = None
    df[""] = None                                         # 빈 열 하나 띄우고
    note = [None] * len(df)
    for i, t in enumerate(NOTE[: len(df)]):
        note[i] = t
    df["examples of cause:"] = note                       # 메모 칸 — 파이프라인이 무시해야 한다
    tail = {c: None for c in df.columns}
    tail["FileName"] = ".."                               # 예시 시트의 마지막 '..' 행
    df = pd.concat([df, pd.DataFrame([tail])], ignore_index=True)
    df.to_excel(root / "Judgement.xlsx", index=False)
    pd.DataFrame(truth).to_csv(out / "truth.csv", index=False, encoding="utf-8")
    return n, n * n_raters


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/dummy")
    ap.add_argument("--n", type=int, default=40)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--layout", choices=["legacy", "inputdata"], default="legacy")
    ap.add_argument("--work", default="work", help="offline fixture 를 쓸 work 디렉토리")
    a = ap.parse_args()

    rnd = random.Random(a.seed)
    out = Path(a.out)
    fx = Path(a.work) / "offline_fixtures" / "pages.json"
    if a.layout == "inputdata":
        fixtures = _load_fixtures(fx)
        n_docs, n_rows = write_inputdata(out, a.n, rnd, fixtures)
        _save_fixtures(fx, fixtures)
        print(f"PDF {n_docs}개 → {out / 'InputData' / 'Documents'}")
        print(f"Judgement {n_docs}행 × 평가자 4명 (p / f / f(사유)) → {out / 'InputData' / 'Judgement.xlsx'}")
        print(f"숨김 정답 → {out / 'truth.csv'}  (숨김 규칙: {', '.join(HIDDEN)} / 잡음: {', '.join(NOISE)})")
        print(f"offline fixture 누적 {len(fixtures)}페이지 → {fx}")
        return

    rows, truth, fixtures = [], [], _load_fixtures(fx)
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

    _save_fixtures(fx, fixtures)

    print(f"PDF {2 * a.n}개, 평가 {len(rows)}행 → {out}")
    print(f"숨김 정답 → {out / 'truth.csv'}  (숨김 규칙: {', '.join(HIDDEN)} / 잡음: {', '.join(NOISE)})")
    print(f"offline fixture {len(fixtures)}페이지 → {fx}")


def _load_fixtures(fx: Path) -> dict:
    """기존 fixture 에 **누적**한다. 레이아웃별 생성기를 번갈아 돌려도 서로 지우지 않는다(doc_id 가 다르다)."""
    if fx.exists():
        try:
            return json.loads(fx.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


def _save_fixtures(fx: Path, fixtures: dict) -> None:
    fx.parent.mkdir(parents=True, exist_ok=True)
    fx.write_text(json.dumps(fixtures, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
