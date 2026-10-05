"""Valeo_SVMtrial 검증용 샘플 데이터 생성기 (한글 8D 대책서, 이미지 전용 PDF + 5인 평가 시트 + 정답지).

- 고객사A(CUST_A): A4 세로 '문서형' 대책서를 흑백 스캔본처럼 만든다 (기울기·노이즈·JPEG).
- 고객사B(CUST_B): 16:9 '슬라이드형' 대책서를 컬러 이미지 PDF로 만든다.
- 모든 페이지는 이미지 한 장이며 텍스트 레이어가 없다 (OCR 필수 조건).

숨김 정답 규칙은 _answer_key/정답_README.md 참고. 파이프라인 입력 폴더에 _answer_key 를 넣지 말 것.

사용: python make_sample_data.py --out sample_data --n 40 --seed 7 [--font 경로.ttf|ttc --font-index 1]
필요: pymupdf, pillow, pandas, openpyxl, numpy, scipy, statsmodels (프로젝트 requirements 에 모두 포함)
"""
from __future__ import annotations

import argparse, io, json, math, random
from dataclasses import dataclass, field, asdict
from pathlib import Path

import numpy as np
import pandas as pd
import pymupdf
from PIL import Image, ImageDraw, ImageFilter, ImageFont

# ---------------------------------------------------------------- 폰트
FONT_CANDIDATES = [
    ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", 1),
    ("/usr/share/fonts/truetype/nanum/NanumGothic.ttf", 0),
    ("C:/Windows/Fonts/malgun.ttf", 0),
]
BOLD_CANDIDATES = [
    ("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc", 1),
    ("/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf", 0),
    ("C:/Windows/Fonts/malgunbd.ttf", 0),
]


class Fonts:
    def __init__(self, path=None, index=0):
        def pick(cands):
            for p, i in cands:
                if Path(p).exists():
                    return p, i
            raise SystemExit("한글 폰트를 찾지 못했습니다. --font 로 지정하세요 (Ubuntu: sudo apt install fonts-nanum).")
        self.reg = (path, index) if path else pick(FONT_CANDIDATES)
        self.bold = (path, index) if path else pick(BOLD_CANDIDATES)
        self._c = {}

    def get(self, size, bold=False):
        k = (size, bold)
        if k not in self._c:
            p, i = self.bold if bold else self.reg
            self._c[k] = ImageFont.truetype(p, size, index=i)
        return self._c[k]


# ---------------------------------------------------------------- 문서 내용
PARTS = [("와이퍼 모터", "WM-2210"), ("헤드램프 ASSY", "HL-7731"), ("냉각팬 모듈", "CF-4402"),
         ("클러스터 하우징", "CH-1093"), ("시트 히터 커넥터", "SH-5520"), ("파워윈도우 스위치", "PW-3318"),
         ("공조 액추에이터", "AC-6604"), ("리어 카메라 브래킷", "RC-8127")]
DEFECTS = [("커넥터 핀 휨", "조립 지그 가이드 마모로 핀 삽입 각도 편차 발생", "출하검사 시 핀 정렬 검사 항목 누락"),
           ("하우징 크랙", "사출 냉각시간 부족으로 잔류응력 증가", "외관검사 조도 기준 미설정으로 미세 크랙 미검출"),
           ("납땜 불량(냉납)", "리플로우 3존 온도 프로파일 이탈", "AOI 검사 판정 기준 완화 상태로 운영"),
           ("작동 소음(이음)", "기어 그리스 도포량 산포 과다", "EOL 소음 검사 임계값 고객 기준 대비 상향 설정"),
           ("누수", "실링 가스켓 압입 깊이 부족", "기밀 검사 압력 유지시간 단축 운영"),
           ("체결 토크 부족", "너트러너 캘리브레이션 주기 초과", "토크 이력 자동 판정 미연동")]
LINES = ["1라인", "2라인", "3라인", "SMT-2", "사출-4호기", "조립 A동"]
MODELS = ["NX-5", "JK-2", "SV-7", "QM-3", "DL-9"]
NAMES = ["김○○", "이○○", "박○○", "최○○", "정○○", "강○○", "조○○", "윤○○"]
DEPTS = ["품질보증팀", "생산기술팀", "제조1팀", "개발팀", "구매팀"]


@dataclass
class DocTruth:
    doc_id: str
    customer: str
    layout: str               # docs | slides
    why5: bool                # D4 5Why 표(발생+유출)
    graph: bool               # D6 대책 전/후 불량률 그래프
    yokoten: bool             # D7 수평전개 표
    photo: bool               # D4 불량 현물 사진
    signature: bool           # 표지 작성/검토/승인 서명란
    gantt: bool               # (미끼) 일정표
    colorbox: bool            # (미끼) 강조 박스
    appendix: bool            # (미끼) 부록 페이지
    part: str = ""
    defect: str = ""
    n_pages: int = 0
    p_rater: dict = field(default_factory=dict)


def sample_truth(doc_id, customer, layout, rnd: random.Random) -> DocTruth:
    return DocTruth(doc_id, customer, layout,
                    why5=rnd.random() < 0.5, graph=rnd.random() < 0.5, yokoten=rnd.random() < 0.5,
                    photo=rnd.random() < 0.6, signature=rnd.random() < 0.7,
                    gantt=rnd.random() < 0.5, colorbox=rnd.random() < 0.5, appendix=rnd.random() < 0.35)


# 블록: ("h1", text) ("h2", text) ("p", text) ("bul", [..]) ("table", cols, rows, widths) ("img", PIL, caption)
#       ("sig",) ("box", text)
def build_blocks(t: DocTruth, rnd: random.Random):
    part, pno = rnd.choice(PARTS)
    defect, occ_cause, out_cause = rnd.choice(DEFECTS)
    t.part, t.defect = part, defect
    line, _model = rnd.choice(LINES), rnd.choice(MODELS)  # _model: 난수 순서 유지용
    qty, found = rnd.randint(3, 60), rnd.choice(["고객 입고검사", "고객 조립라인", "필드 클레임"])
    ppm_before = rnd.randint(180, 900)
    docno = f"CAR-2025-{rnd.randint(100, 999)}"
    team = rnd.sample(NAMES, 4)

    cover = [("title", "품질 문제 대책서 (8D Report)"),
             ("table", ["항목", "내용"], [["고객사", t.customer], ["품명 / 품번", f"{part} / {pno}"],
                                          ["문서번호", docno], ["불량 현상", defect],
                                          ["발생 일자", f"2025-{rnd.randint(1,12):02d}-{rnd.randint(1,28):02d}"],
                                          ["작성 부서", "품질보증팀"]], [0.3, 0.7])]
    if t.signature:
        cover.append(("sig",))

    secs = []
    secs.append(("D1. 팀 구성", [("table", ["역할", "성명", "부서"],
                                  [["팀장", team[0], "품질보증팀"], ["팀원", team[1], rnd.choice(DEPTS)],
                                   ["팀원", team[2], rnd.choice(DEPTS)], ["팀원", team[3], rnd.choice(DEPTS)]],
                                  [0.25, 0.3, 0.45])]))
    secs.append(("D2. 문제 정의 (5W2H)", [("table", ["구분", "내용"],
                  [["What (무엇이)", f"{part} {defect}"], ["Where (어디서)", found],
                   ["When (언제)", "2025년 생산분 (LOT 추적 중)"], ["Who (누가)", f"고객사 {t.customer} 검사원"],
                   ["How (어떻게)", f"{defect} 현상 육안 확인"], ["How many (얼마나)", f"{qty} EA"]], [0.3, 0.7])]))
    d3 = [("bul", ["고객사 재고 및 사내 완성품 전수 선별 실시",
                   f"선별 결과: 총 {rnd.randint(800, 4000)} EA 중 불량 {rnd.randint(0, 8)} EA 추가 발견",
                   "선별 완료품 식별 라벨(청색) 부착 후 출하"])]
    secs.append(("D3. 임시 조치 (봉쇄)", d3))

    d4 = []
    if t.why5:
        whys = ["불량 발생", occ_cause.split("로")[0] if "로" in occ_cause else occ_cause,
                "설비 점검 기준서에 해당 항목 없음", "점검 주기 설정 근거 부재", "신규 설비 이관 시 기준 검토 누락"]
        d4.append(("p", "■ 발생 원인 – 5Why 분석"))
        d4.append(("table", ["단계", "Why (왜?)"], [[f"Why {i+1}", w] for i, w in enumerate(whys)], [0.2, 0.8]))
        d4.append(("p", "■ 유출 원인 – 5Why 분석"))
        d4.append(("table", ["단계", "Why (왜?)"],
                   [["Why 1", "출하검사에서 미검출"], ["Why 2", out_cause],
                    ["Why 3", "검사 기준서 개정 이력 관리 미흡"]], [0.2, 0.8]))
        d4.append(("p", f"▶ 근본 원인: {occ_cause} / {out_cause}"))
    else:
        d4.append(("p", f"원인: {line} 작업자 부주의 및 설비 상태 불량으로 추정됨."))
        d4.append(("p", "추가 조사 진행 예정."))
    if t.photo:
        d4.append(("img", make_photo(defect, rnd), f"[사진] {defect} 불량 현물 (발생품)"))
    secs.append(("D4. 근본 원인 분석", d4))

    d5 = [("table", ["No", "대책 내용", "담당", "완료 일정"],
           [["1", f"{occ_cause.split(' ')[0]} 관련 공정 조건 재설정", team[1], "2025-W32"],
            ["2", "검사 기준서 개정 및 검사 항목 추가", team[2], "2025-W33"],
            ["3", "작업자 재교육 실시", team[3], "2025-W33"]], [0.08, 0.52, 0.18, 0.22])]
    secs.append(("D5. 영구 대책 수립", d5))

    d6 = []
    if t.graph:
        d6.append(("img", make_chart(ppm_before, rnd), "[그래프] 대책 전/후 불량률 추이 (ppm)"))
        d6.append(("p", f"대책 전 평균 {ppm_before} ppm → 대책 후 {rnd.randint(0, 40)} ppm (3개월 모니터링)"))
    else:
        d6.append(("p", "대책 적용 후 현재까지 동일 불량 미발생."))
    secs.append(("D6. 대책 실행 및 효과 검증", d6))

    d7 = [("bul", ["관리계획서(CP) 및 작업표준서 개정 완료", "PFMEA 발생도/검출도 재평가 반영"])]
    if t.yokoten:
        rows = [[ln, rnd.choice(MODELS), f"2025-W{rnd.randint(33, 40)}", rnd.choice(NAMES)]
                for ln in rnd.sample(LINES, 3)]
        d7.append(("p", "■ 수평 전개"))
        d7.append(("table", ["적용 라인", "차종", "적용 일자", "담당"], rows, [0.3, 0.2, 0.25, 0.25]))
    secs.append(("D7. 재발 방지 (표준화)", d7))

    d8 = [("p", "상기 대책 완료 및 효과 확인으로 본 건 종결을 요청함."), ("p", f"팀 활동 공유: 품질 회의 (2025-W{rnd.randint(41, 45)})")]
    secs.append(("D8. 종결 및 팀 인정", d8))

    if t.gantt:  # 미끼
        secs.insert(6, ("대책 추진 일정", [("table", ["항목", "W31", "W32", "W33", "W34", "W35"],
                                        [["원인 분석", "■", "■", "", "", ""], ["대책 적용", "", "■", "■", "", ""],
                                         ["효과 검증", "", "", "■", "■", "■"]], [0.3] + [0.14] * 5)]))
    if t.colorbox:  # 미끼
        secs[1][1].append(("box", "※ 고객 요청 회신 기한: 접수 후 14일 이내"))
    if t.appendix:  # 미끼
        secs.append(("부록. 참고 자료", [("bul", ["검사 성적서 사본", "설비 점검 일지 사본", "교육 참석자 명단"])]))
    return cover, secs


def make_photo(defect, rnd):
    w, h = 520, 340
    img = Image.new("RGB", (w, h))
    px = img.load()
    base = rnd.randint(110, 160)
    for y in range(h):
        for x in range(0, w, 2):
            v = base + int(40 * math.sin(x / 37.0) * math.cos(y / 53.0)) + rnd.randint(-12, 12)
            px[x, y] = (v, v, v + 8)
            if x + 1 < w:
                px[x + 1, y] = (v, v, v + 8)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([90, 70, 430, 270], 18, outline=(60, 60, 60), width=6, fill=(170, 170, 175))
    x0, y0 = 200, 110
    pts = [(x0 + i * 22, y0 + int(18 * math.sin(i)) + i * 12) for i in range(8)]
    d.line(pts, fill=(25, 25, 25), width=4)
    d.ellipse([pts[3][0] - 50, pts[3][1] - 50, pts[3][0] + 50, pts[3][1] + 50], outline=(220, 30, 30), width=5)
    return img.filter(ImageFilter.GaussianBlur(1.1))


def make_chart(ppm_before, rnd, fonts=None):
    w, h = 760, 380
    img = Image.new("RGB", (w, h), "white")
    d = ImageDraw.Draw(img)
    f = FONTS.get(16)
    L, R, T, B = 70, 30, 30, 60
    d.line([L, T, L, h - B], fill="black", width=2); d.line([L, h - B, w - R, h - B], fill="black", width=2)
    months = ["3월", "4월", "5월", "6월", "7월", "8월", "9월", "10월"]
    vals = [ppm_before + rnd.randint(-80, 80) for _ in range(4)] + [rnd.randint(0, 60) for _ in range(4)]
    ymax = max(vals) * 1.2
    step = (w - L - R) / len(months)
    pts = []
    for i, (m, v) in enumerate(zip(months, vals)):
        x = L + step * (i + 0.5); y = h - B - (h - T - B) * v / ymax
        pts.append((x, y))
        d.text((x - 14, h - B + 8), m, fill="black", font=f)
        d.rectangle([x - 16, y, x + 16, h - B], fill=(170, 190, 225) if i < 4 else (120, 200, 140))
    d.line(pts, fill=(200, 40, 40), width=3)
    for p in pts:
        d.ellipse([p[0] - 5, p[1] - 5, p[0] + 5, p[1] + 5], fill=(200, 40, 40))
    xm = L + step * 4
    for yy in range(T, h - B, 10):
        d.line([xm, yy, xm, yy + 5], fill="gray", width=2)
    d.text((xm + 6, T), "대책 적용", fill="black", font=f)
    d.text((8, T - 6), "ppm", fill="black", font=f)
    return img


# ---------------------------------------------------------------- 렌더러
def wrap(text, font, maxw):
    lines, cur = [], ""
    for ch in text:
        if font.getlength(cur + ch) > maxw and cur:
            lines.append(cur); cur = ch
        else:
            cur += ch
    if cur:
        lines.append(cur)
    return lines


def block_height(b, width, fs):
    f = FONTS.get(fs)
    lh = int(fs * 1.55)
    k = b[0]
    if k in ("h1",):
        return int(fs * 2.4)
    if k == "title":
        return int(fs * 3.2)
    if k in ("p", "box"):
        return len(wrap(b[1], f, width - 20)) * lh + (24 if k == "box" else 8)
    if k == "bul":
        return sum(len(wrap(x, f, width - 40)) for x in b[1]) * lh + 8
    if k == "table":
        cols, rows, ws = b[1], b[2], b[3]
        tot = 0
        for r in [cols] + rows:
            n = max(len(wrap(str(c), f, ws[i] * width - 16)) for i, c in enumerate(r))
            tot += n * lh + 10
        return tot + 12
    if k == "img":
        im = b[1]; s = min(1.0, (width * 0.8) / im.width)
        return int(im.height * s) + lh + 16
    if k == "sig":
        return 150
    return lh


def draw_block(d, page, b, x, y, width, fs, color):
    f, fb = FONTS.get(fs), FONTS.get(fs, True)
    lh = int(fs * 1.55)
    k = b[0]
    if k == "title":
        d.text((x, y), b[1], fill="black", font=FONTS.get(int(fs * 1.9), True)); return
    if k == "h1":
        d.rectangle([x, y + 4, x + 8, y + int(fs * 1.8)], fill=color)
        d.text((x + 18, y + 2), b[1], fill="black", font=FONTS.get(int(fs * 1.3), True)); return
    if k == "p":
        for i, ln in enumerate(wrap(b[1], f, width - 20)):
            d.text((x + 6, y + i * lh), ln, fill="black", font=fb if b[1].startswith(("■", "▶")) else f)
        return
    if k == "box":
        n = len(wrap(b[1], f, width - 20))
        d.rectangle([x, y, x + width, y + n * lh + 16], fill=(255, 236, 200), outline=(230, 140, 30), width=2)
        for i, ln in enumerate(wrap(b[1], f, width - 20)):
            d.text((x + 10, y + 8 + i * lh), ln, fill=(140, 60, 0), font=fb)
        return
    if k == "bul":
        yy = y
        for it in b[1]:
            for j, ln in enumerate(wrap(it, f, width - 40)):
                d.text((x + 10, yy), ("• " if j == 0 else "   ") + ln, fill="black", font=f); yy += lh
        return
    if k == "table":
        cols, rows, ws = b[1], b[2], b[3]
        yy = y
        for ri, r in enumerate([cols] + rows):
            n = max(len(wrap(str(c), f, ws[i] * width - 16)) for i, c in enumerate(r))
            rh = n * lh + 10
            xx = x
            for ci, c in enumerate(r):
                cw = ws[ci] * width
                d.rectangle([xx, yy, xx + cw, yy + rh], outline="black", width=1,
                            fill=(225, 230, 240) if ri == 0 else None)
                for li, ln in enumerate(wrap(str(c), f, cw - 16)):
                    d.text((xx + 8, yy + 5 + li * lh), ln, fill="black", font=fb if ri == 0 else f)
                xx += cw
            yy += rh
        return
    if k == "img":
        im = b[1]; s = min(1.0, (width * 0.8) / im.width)
        im2 = im.resize((int(im.width * s), int(im.height * s)))
        page.paste(im2, (int(x + (width - im2.width) / 2), int(y)))
        d.text((x + (width - f.getlength(b[2])) / 2, y + im2.height + 6), b[2], fill="black", font=f)
        return
    if k == "sig":
        bw = 120
        x0 = x + width - 3 * bw
        for i, lab in enumerate(["작성", "검토", "승인"]):
            d.rectangle([x0 + i * bw, y, x0 + (i + 1) * bw, y + 34], outline="black", width=2, fill=(235, 235, 235))
            d.text((x0 + i * bw + 40, y + 6), lab, fill="black", font=fb)
            d.rectangle([x0 + i * bw, y + 34, x0 + (i + 1) * bw, y + 130], outline="black", width=2)
            d.text((x0 + i * bw + 22, y + 70), random.choice(NAMES) + " (인)", fill=(30, 30, 160), font=f)


def render_docs(cover, secs, t, rnd):
    W, H, M, fs = 992, 1403, 70, 17          # A4 @120dpi
    color = (40, 40, 40)
    pages, page, d, y = [], None, None, 0

    def new_page():
        nonlocal page, d, y
        if page is not None:
            pages.append(page)
        page = Image.new("RGB", (W, H), "white"); d = ImageDraw.Draw(page); y = M
        d.text((W - M - 160, H - 45), f"- {len(pages) + 1} -", fill="black", font=FONTS.get(14))
        d.text((M, 30), f"{t.customer} 품질 대책서", fill=(90, 90, 90), font=FONTS.get(13))

    new_page()
    for b in cover:
        draw_block(d, page, b, M, y, W - 2 * M, fs, color); y += block_height(b, W - 2 * M, fs) + 18
    new_page()
    for title, blocks in secs:
        if title.startswith("부록"):
            new_page()
        h1 = ("h1", title)
        if y + block_height(h1, W - 2 * M, fs) + 80 > H - M:
            new_page()
        draw_block(d, page, h1, M, y, W - 2 * M, fs, color); y += block_height(h1, W - 2 * M, fs)
        for b in blocks:
            bh = block_height(b, W - 2 * M, fs)
            if y + bh > H - M:
                new_page()
            draw_block(d, page, b, M, y, W - 2 * M, fs, color); y += bh + 10
        y += 16
    pages.append(page)
    return pages


def render_slides(cover, secs, t, rnd):
    W, H, fs = 1280, 720, 18
    color = (0, 92, 170)
    pages = []

    def frame(title, idx):
        p = Image.new("RGB", (W, H), "white"); d = ImageDraw.Draw(p)
        d.rectangle([0, 0, W, 78], fill=color)
        d.text((40, 18), title, fill="white", font=FONTS.get(30, True))
        d.line([40, H - 40, W - 40, H - 40], fill=color, width=2)
        d.text((40, H - 34), f"{t.customer} | 품질 대책서", fill=(90, 90, 90), font=FONTS.get(13))
        d.text((W - 80, H - 34), str(idx), fill=(90, 90, 90), font=FONTS.get(13))
        return p, d

    p = Image.new("RGB", (W, H), "white"); d = ImageDraw.Draw(p)
    d.rectangle([0, 0, 24, H], fill=color)
    y = 90
    for b in cover:
        draw_block(d, p, b, 90, y, W - 180, fs, color); y += block_height(b, W - 180, fs) + 14
    pages.append(p)
    for title, blocks in secs:
        p, d = frame(title, len(pages) + 1); y = 100
        # 이미지가 있으면 2단 배치: 왼쪽 텍스트/표, 오른쪽 이미지
        imgs = [b for b in blocks if b[0] == "img"]; rest = [b for b in blocks if b[0] != "img"]
        colw = (W - 120) * (0.55 if imgs else 1.0)
        for b in rest:
            bh = block_height(b, colw, fs)
            if y + bh > H - 60:
                pages.append(p); p, d = frame(title + " (계속)", len(pages) + 1); y = 100
            draw_block(d, p, b, 50, y, colw, fs, color); y += bh + 10
        if imgs:
            yy = 100
            for b in imgs:
                draw_block(d, p, b, 50 + colw + 30, yy, (W - 120) * 0.45, fs, color)
                yy += block_height(b, (W - 120) * 0.45, fs) + 10
        pages.append(p)
    return pages


def scanify(img: Image.Image, rnd):
    g = img.convert("L")
    g = g.rotate(rnd.uniform(-0.8, 0.8), resample=Image.BICUBIC, expand=False, fillcolor=255)
    arr = np.asarray(g).astype(np.int16)
    arr = arr + np.random.default_rng(rnd.randint(0, 10**6)).normal(0, 9, arr.shape).astype(np.int16)
    arr = np.clip(arr * 0.93 + 12, 0, 255).astype(np.uint8)  # 약간 회색 바탕
    return Image.fromarray(arr).filter(ImageFilter.GaussianBlur(0.5))


def save_pdf(pages, path, scanned, rnd):
    doc = pymupdf.open()
    for im in pages:
        im2 = scanify(im, rnd) if scanned else im
        buf = io.BytesIO(); im2.save(buf, "JPEG", quality=62 if scanned else 80)
        wpt, hpt = (595, 842) if im.height > im.width else (842, 474)
        pg = doc.new_page(width=wpt, height=hpt)
        pg.insert_image(pg.rect, stream=buf.getvalue())
    if scanned:
        doc.set_metadata({"producer": "MFP Scan Utility 4.2", "creator": "Scanner"})
    else:
        doc.set_metadata({"producer": "Image to PDF Converter", "creator":
                          "Microsoft PowerPoint" if rnd.random() < 0.5 else ""})
    doc.save(path, garbage=4, deflate=True); doc.close()


# ---------------------------------------------------------------- 평가 시뮬레이션
RATERS = ["평가자A", "평가자B", "평가자C", "평가자D", "평가자E"]
RATER_BIAS = {"평가자A": 0.0, "평가자B": 0.4, "평가자C": 0.0, "평가자D": -0.2, "평가자E": -0.2}
W_TRUE = {"why5": 1.9, "graph": 1.6, "yokoten": 1.0, "photo": 0.5}
BASE = -1.6
SIG_E = (1.2, -1.8)   # 평가자E만: 서명란 있으면 +1.2, 없으면 -1.8
PASS_WORDS = ["Pass", "Pass", "Pass", "합격", "OK", "P"]
FAIL_WORDS = ["Fail", "Fail", "Fail", "불합격", "NG", "F"]


def rate(t: DocTruth, nrng: np.random.Generator):
    core = BASE + sum(w for k, w in W_TRUE.items() if getattr(t, k)) + nrng.normal(0, 0.35)
    out = {}
    for r in RATERS:
        lp = core + RATER_BIAS[r]
        if r == "평가자E":
            lp += SIG_E[0] if t.signature else SIG_E[1]
        p = 1 / (1 + math.exp(-lp)); t.p_rater[r] = round(p, 3)
        out[r] = nrng.random() < p
    return out


# ---------------------------------------------------------------- main
FONTS: Fonts


def main():
    global FONTS
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="sample_data"); ap.add_argument("--n", type=int, default=40)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--font"); ap.add_argument("--font-index", type=int, default=0)
    a = ap.parse_args()
    FONTS = Fonts(a.font, a.font_index)
    rnd = random.Random(a.seed); random.seed(a.seed); nrng = np.random.default_rng(a.seed)
    out = Path(a.out)
    truths, rows = [], []
    for cust, layout in [("CUST_A", "docs"), ("CUST_B", "slides")]:
        pdir = out / "data/raw/pdfs/대책서" / cust; pdir.mkdir(parents=True, exist_ok=True)
        for k in range(1, a.n + 2):                       # 마지막 1건 = 시트에 없는 PDF (매칭 오류 검증용)
            doc_id = f"{cust}_{k:03d}"
            t = sample_truth(doc_id, cust, layout, rnd)
            cover, secs = build_blocks(t, rnd)
            pages = render_docs(cover, secs, t, rnd) if layout == "docs" else render_slides(cover, secs, t, rnd)
            t.n_pages = len(pages)
            save_pdf(pages, pdir / f"{doc_id}.pdf", scanned=(layout == "docs"), rnd=rnd)
            res = rate(t, nrng)
            truths.append(t)
            if k <= a.n:
                for r, ok in res.items():
                    val = rnd.choice(PASS_WORDS) if ok else rnd.choice(FAIL_WORDS)
                    if rnd.random() < 0.03:
                        val = ""                           # 미평가(결측)
                    rows.append({"doc_id": doc_id, "customer": cust, "doc_type": "대책서", "rater": r, "result": val})
        # 시트에만 있고 PDF 없는 건 (매칭 오류 검증용)
        for r in RATERS:
            rows.append({"doc_id": f"{cust}_999", "customer": cust, "doc_type": "대책서", "rater": r, "result": "Pass"})

    lab = out / "data/raw/labels"; lab.mkdir(parents=True, exist_ok=True)
    long = pd.DataFrame(rows)
    long.to_excel(lab / "평가결과_long.xlsx", index=False, sheet_name="평가")
    alt = out / "alt_formats"; alt.mkdir(parents=True, exist_ok=True)
    wide = long.pivot_table(index=["doc_id", "customer", "doc_type"], columns="rater", values="result",
                            aggfunc="first").reset_index()
    wide.columns.name = None
    wide.to_excel(alt / "평가결과_wide.xlsx", index=False, sheet_name="평가")

    key = out / "_answer_key"; key.mkdir(parents=True, exist_ok=True)
    tdf = pd.DataFrame([{**{k: v for k, v in asdict(t).items() if k != "p_rater"},
                         **{f"p_{r}": t.p_rater.get(r) for r in RATERS}} for t in truths])
    tdf.to_csv(key / "truth.csv", index=False, encoding="utf-8-sig")
    stats = summarize(long, tdf)
    (key / "observed_stats.json").write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(stats["overview"], ensure_ascii=False, indent=2))


def summarize(long, tdf):
    """생성된 라벨에서 실제로 관측되는 효과(파이프라인 결과와 비교할 기준값)."""
    from scipy.stats import fisher_exact
    from statsmodels.stats.multitest import multipletests
    norm = long.copy()
    norm["y"] = norm.result.map(lambda v: 1 if v in PASS_WORDS else (0 if v in FAIL_WORDS else np.nan))
    g = norm.dropna(subset=["y"]).groupby("doc_id").y.agg(["mean", "count", "min"]).reset_index()
    g["unanimous"] = (g["min"] == 1).astype(int); g["majority"] = (g["mean"] >= 0.5).astype(int)
    m = g.merge(tdf, on="doc_id")
    feats = ["why5", "graph", "yokoten", "photo", "signature", "gantt", "colorbox", "appendix"]
    res = {"overview": {}, "by_group": {}}
    for scope, sub in [("ALL", m)] + [(c, m[m.customer == c]) for c in ["CUST_A", "CUST_B"]]:
        out = {"n_docs": int(len(sub)), "unanimous_pass_rate": round(sub.unanimous.mean(), 3),
               "majority_pass_rate": round(sub.majority.mean(), 3), "features": {}}
        ps = []
        for f in feats:
            x = sub[f].astype(int)
            tab = pd.crosstab(x, sub.unanimous).reindex(index=[0, 1], columns=[0, 1], fill_value=0)
            p = fisher_exact(tab)[1]; ps.append(p)
            out["features"][f] = {"pass_rate_with": round(sub.unanimous[x == 1].mean(), 3),
                                  "pass_rate_without": round(sub.unanimous[x == 0].mean(), 3), "p": round(p, 4)}
        q = multipletests(ps, method="fdr_bh")[1]
        for f, qq in zip(feats, q):
            out["features"][f]["q"] = round(float(qq), 4)
        res["by_group"][scope] = out
    rr = norm.dropna(subset=["y"]).groupby("rater").y.mean().round(3).to_dict()
    res["overview"] = {"pdf_count": int(len(tdf)), "label_rows": int(len(long)),
                       "rater_pass_rate": rr,
                       "unanimous_pass_rate": res["by_group"]["ALL"]["unanimous_pass_rate"],
                       "q_values_ALL(unanimous)": {f: res["by_group"]["ALL"]["features"][f]["q"] for f in feats}}
    # 평가자E 서명란 효과
    e = norm[norm.rater == "평가자E"].dropna(subset=["y"]).merge(tdf[["doc_id", "signature"]], on="doc_id")
    res["overview"]["평가자E_pass_rate_by_signature"] = e.groupby("signature").y.mean().round(3).to_dict()
    return res


if __name__ == "__main__":
    main()
