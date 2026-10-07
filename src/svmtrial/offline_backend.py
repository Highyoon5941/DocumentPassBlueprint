"""offline 백엔드 — Gemini를 호출하지 않는 결정론적 스텁 (이 개발 PC 전용).

**실데이터에 쓰지 말 것.** 사내망/실업무에서는 `SVMTRIAL_BACKEND=vertex` 로 바꾼다.
(전환 절차: docs/migration_vertexAI.md)

목적: GCP 접근이 없는 개발 PC에서 S1~S7 전체 파이프라인과 통계·문서 생성을 검증한다.
방법: Gemini 호출마다 같은 입력(프롬프트의 데이터 블록)을 읽어 **같은 스키마**의 응답을 규칙으로 만든다.
      호출부와 스키마는 vertex 백엔드와 완전히 동일하므로, 백엔드만 바꾸면 코드 변경이 없다.

각 prompt_id 별 대체 규칙:
| prompt_id             | offline 규칙 | 실데이터에서 쓸 수 있나 |
|---|---|---|
| doc_format.v1         | 이미지 가로/세로 비율 다수결 | ✅ 픽셀에서 직접 계산 |
| ocr_page.v1           | make_dummy_data.py 가 심은 fixture(이미지 sha256 → 페이지 줄) 조회 | ❌ fixture 없으면 빈 페이지 |
| section_taxonomy.v1   | 8D seed + 관측 제목 토큰 매칭 | ⚠️ 한국어 동의어 일반화 약함 |
| section_map.v1        | 토큰 Jaccard 최대 매칭 | ⚠️ 동일 |
| concept_discovery.v1  | Pass/Fail 문서빈도 차이가 큰 줄을 가설로 | ⚠️ 표현이 다양하면 약함 |
| concept_merge.v1      | 정규화 문구 기준 중복 제거 | ✅ |
| concept_scoring.v1    | 질문의 인용 문구를 개요 텍스트에서 부분일치 | ⚠️ 표현 변형에 약함 |
| template_compose.v1   | 골격 + 등급 개념으로 문구 조립 | ✅ 문구가 기계적일 뿐 |
"""

from __future__ import annotations

import hashlib
import io
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from svmtrial import textutil as tu
from svmtrial.config import Settings
from svmtrial.parts import Part, blobs, joined_text
from svmtrial.payload import extract
from svmtrial.seeds import SEED_8D
from svmtrial.textutil import best_section as _best_section

FIXTURE_REL = "offline_fixtures/pages.json"

# 스텁 규칙을 고칠 때 올린다. 캐시 키에 들어가므로, 규칙이 바뀌면 캐시가 자동 무효화된다.
STUB_VERSION = "6"


_TABLE_HINT = re.compile(r"(table|표|목록|list|matrix)", re.IGNORECASE)
_CHART_HINT = re.compile(r"(\[chart\]|graph|chart|그래프|차트|추이|trend)", re.IGNORECASE)
_PHOTO_HINT = re.compile(r"(\[photo\]|photo|사진|이미지)", re.IGNORECASE)
_SIGN_HINT = re.compile(r"(signature|sign|승인|결재|결제|서명|approval|날인|stamp)", re.IGNORECASE)
# 제목 번호 패턴. 특정 문서종류에 묶이지 않게 일반화했다.
#   D4 / R1 / A2 (영문 1~3자 + 숫자) | 1. / 1) | 1.2 / 1.2.3 | 제 3 장 | III.
# 과거에는 `d[0-9]` 만 받아서 대책서(8D) 외의 문서종류는 제목이 하나도 안 잡혔다.
_HEAD_HINT = re.compile(
    r"^\s*(?:[A-Za-z]{1,3}\s?\d+(?:\.\d+)*\b"      # D4, R1, APP2, R1.2
    r"|\d+(?:\.\d+)*[.)]?\s"                          # 1. / 1) / 1.2 / 1.2.3
    r"|제?\s*\d+\s*장"                                  # 제3장
    r"|[IVXivx]{1,5}[.)]\s)",                            # III.
    re.IGNORECASE,
)
_COLS_SPLIT = re.compile(r"\s*[|/]\s*|\s{2,}|\s*>\s*")
_BOILER = re.compile(r"lorem ipsum", re.IGNORECASE)
# OCR 개요 텍스트가 붙이는 표기를 개념 문구에서 걷어낸다
_OCR_PREFIX = re.compile(r"^\s*\[(표|chart|photo|image|diagram|signature|stamp|form_field)\]\s*", re.IGNORECASE)
_OCR_COLS = re.compile(r"\s*\|\s*열\s*:.*$")


class OfflineBackend:
    """vertex_backend.VertexBackend 와 같은 generate() 인터페이스."""

    def __init__(self, settings: Settings):
        self.s = settings
        self._fixtures: dict[str, Any] | None = None

    # ------------------------------------------------------------------ fixture

    @property
    def fixtures(self) -> dict[str, Any]:
        if self._fixtures is None:
            f = self.s.work / FIXTURE_REL
            self._fixtures = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
        return self._fixtures

    @staticmethod
    def fixture_path(settings: Settings) -> Path:
        return settings.work / FIXTURE_REL

    # ------------------------------------------------------------------ 진입점

    def generate(
        self,
        *,
        model: str,
        prompt_id: str,
        parts: list[Part],
        schema: type[BaseModel],
        media_resolution: str | None = None,
    ) -> tuple[dict, Any]:
        from svmtrial.gemini_client import Usage

        text = joined_text(parts)
        data = extract(text)
        imgs = blobs(parts)
        base = prompt_id.split(".")[0]
        fn = getattr(self, f"_h_{base}", None)
        if fn is None:
            raise RuntimeError(f"offline 백엔드에 prompt_id 처리기가 없습니다: {prompt_id}")
        payload = fn(data=data, imgs=imgs, schema=schema)
        # 토큰 수는 실제 호출이 아니므로 대략치만 기록한다(길이/4).
        usage = Usage(
            prompt_tokens=len(text) // 4 + 600 * len(imgs),
            output_tokens=len(json.dumps(payload, ensure_ascii=False)) // 4,
        )
        return payload, usage

    # ------------------------------------------------------------------ 핸들러

    def _h_doc_format(self, *, data: dict, imgs: list, schema: type[BaseModel]) -> dict:
        """이미지 비율로 docs/slides 판정 — 픽셀에서 직접 계산하므로 실데이터에도 유효."""
        ratios = [_aspect(b["bytes"]) for b in imgs] or [float(r) for r in data.get("ratios", [])]
        thr = self.s.ingest.landscape_ratio
        land = sum(1 for r in ratios if r >= thr)
        n = max(1, len(ratios))
        is_slides = land * 2 >= n
        conf = abs(land / n - 0.5) * 2
        return {
            "doc_format": "slides" if is_slides else "docs",
            "confidence": round(min(1.0, 0.5 + conf / 2), 3),
            "reason": f"가로형 페이지 {land}/{n} (임계 비율 {thr})",
        }

    def _h_ocr_page(self, *, data: dict, imgs: list, schema: type[BaseModel]) -> dict:
        """fixture 조회. 없으면 판독 불량 페이지로 돌려준다(파이프라인은 계속 돌아감).

        키는 `<doc_id>/p<page:03d>`. ingest 가 PDF를 다시 렌더링하면 PNG 바이트가 달라지므로
        이미지 해시는 키로 쓸 수 없다. 방향(orientation)만 실제 이미지에서 계산한다.
        """
        orientation = "portrait"
        if imgs:
            orientation = "landscape" if _aspect(imgs[0]["bytes"]) >= self.s.ingest.landscape_ratio else "portrait"
        doc_id, page = data.get("doc_id"), data.get("page")
        fx = self.fixtures.get(f"{doc_id}/p{int(page):03d}") if doc_id and page else None
        if fx is None:  # 보조: 이미지 해시로도 찾아본다
            for b in imgs:
                fx = self.fixtures.get(hashlib.sha256(b["bytes"]).hexdigest())
                if fx:
                    break
        if not fx:
            return {
                "page_role": "body",
                "orientation": orientation,
                "looks_like": "other",
                "blocks": [],
                "legibility": 0.0,
            }
        lines = [str(x) for x in fx.get("lines", [])]
        return {
            "page_role": fx.get("page_role", "body"),
            "orientation": orientation,
            "looks_like": "slide" if orientation == "landscape" else "document",
            "blocks": _lines_to_blocks(lines),
            "legibility": float(fx.get("legibility", 0.95)),
        }

    def _h_section_taxonomy(self, *, data: dict, imgs: list, schema: type[BaseModel]) -> dict:
        titles = [str(t) for t in data.get("titles", [])]
        # seed 가 **빈 목록**인 것과 **키가 아예 없는** 것을 구분해야 한다.
        # `or` 를 쓰면 빈 목록이 falsy 라서 대책서의 8D 로 되돌아가고,
        # 새 문서종류가 엉뚱한 섹션 체계를 물려받는다.
        seed = data["seed"] if "seed" in data else SEED_8D
        seed = list(seed or [])
        items = [
            {"id": s["id"], "name": s["name"], "synonyms": list(s.get("synonyms", [])), "description": s.get("description", "")}
            for s in seed
        ]
        by_id = {it["id"]: it for it in items}
        # `titles` 는 중복이 제거된 목록이므로 등장 횟수를 세면 전부 1이 된다.
        # 문서 수 기준 임계값을 쓰려면 호출부가 함께 넘기는 title_doc_counts 를 봐야 한다.
        doc_counts = {str(k): int(v) for k, v in (data.get("title_doc_counts") or {}).items()}
        unmatched: Counter[str] = Counter()
        for t in titles:
            sid = _best_section(t, items)
            if sid == "OTHER":
                unmatched[tu.strip_numbering(t)] += doc_counts.get(t, 1)
            else:
                syn = by_id[sid]["synonyms"]
                key = tu.norm(t)
                if key and key not in {tu.norm(x) for x in syn}:
                    syn.append(tu.strip_numbering(t))
        # 자주 나오지만 seed 에 없는 제목은 새 섹션 후보로 올린다(사람이 🔒에서 검토).
        # seed 가 없는 문서종류는 분류체계 전체를 데이터에서 만들어야 하므로 상한을 넉넉히 둔다.
        cap = 8 if seed else 24
        min_docs = 2 if seed else max(2, int(0.2 * int(data.get("n_docs") or 0)) or 2)
        for i, (t, c) in enumerate(x for x in unmatched.most_common(cap) if x[1] >= min_docs):
            items.append(
                {
                    "id": f"X{i + 1}",
                    "name": t[:60],
                    "synonyms": [t],
                    "description": f"seed 분류체계에 없던 제목 (문서 {c}건에서 관측). 사람이 검토 필요.",
                }
            )
        for it in items:
            it["synonyms"] = sorted({s for s in it["synonyms"] if s})[:20]
            it.setdefault("description", "")
        return {"items": items}

    def _h_section_map(self, *, data: dict, imgs: list, schema: type[BaseModel]) -> dict:
        tax = data.get("taxonomy", [])
        out = [{"title": str(t), "section_id": _best_section(str(t), tax)} for t in data.get("titles", [])]
        return {"mappings": out}

    def _h_concept_discovery(self, *, data: dict, imgs: list, schema: type[BaseModel]) -> dict:
        """Pass/Fail 문서빈도 차이가 큰 '줄'을 예/아니오 가설로 바꾼다 (HypoGeniC 의 규칙 근사)."""
        pass_docs = data.get("pass_docs", [])
        fail_docs = data.get("fail_docs", [])
        max_h = int(data.get("max_hypotheses", 15))
        start = int(data.get("id_start", 1))
        tax = data.get("taxonomy", [])

        df_pass = _line_df(pass_docs)
        df_fail = _line_df(fail_docs)
        np_, nf = max(1, len(pass_docs)), max(1, len(fail_docs))
        scored = []
        for key, (raw, cnt, head) in df_pass.items():
            diff = cnt / np_ - df_fail.get(key, ("", 0, ""))[1] / nf
            if diff > 0.05:
                scored.append((diff, key, raw, head))
        scored.sort(key=lambda x: (-x[0], x[1]))

        concepts = []
        for i, (diff, _key, raw, head) in enumerate(scored[:max_h]):
            # 섹션은 '그 줄이 들어 있던 상위 제목' 으로 먼저 찾고, 실패하면 줄 자체로 찾는다.
            sid = None
            if tax:
                sid = _best_section(head, tax) if head else "OTHER"
                if sid == "OTHER":
                    sid = _best_section(raw, tax)
            kind = (
                "quantitative" if _CHART_HINT.search(raw)
                else "structure" if _TABLE_HINT.search(raw)
                else "format" if _SIGN_HINT.search(raw)
                else "content_completeness"
            )
            label = _short(raw)
            where = f"{sid} 섹션에 " if sid and sid != "OTHER" else ""
            concepts.append(
                {
                    "id": f"C{start + i:03d}",
                    "question": f'{where}"{label}" 에 해당하는 항목이 있는가?',
                    "section_id": None if sid == "OTHER" else sid,
                    "type": kind,
                    "actionable": True,
                    "rationale": f"발견셋에서 Pass 문서 출현율이 Fail 보다 {diff:.0%} 높았다.",
                }
            )
        return {"concepts": concepts}

    def _h_concept_merge(self, *, data: dict, imgs: list, schema: type[BaseModel]) -> dict:
        seen: dict[str, dict] = {}
        for c in data.get("concepts", []):
            label = tu.quoted_phrase(c.get("question", "")) or c.get("question", "")
            k = tu.norm(label)[:80]
            if not k:
                continue
            if k not in seen:
                seen[k] = dict(c)
        out = list(seen.values())[: int(data.get("max_keep", 60))]
        for i, c in enumerate(out):  # id 재부여로 중복 id 제거
            c["id"] = f"C{i + 1:03d}"
        return {"concepts": out}

    def _h_concept_scoring(self, *, data: dict, imgs: list, schema: type[BaseModel]) -> dict:
        """개념 질문의 인용 문구를 문서 개요 텍스트에서 부분일치로 찾는다."""
        outline = str(data.get("outline_text", ""))
        page_index: list[tuple[int, str]] = [
            (int(p.get("page", 0)), str(p.get("text", ""))) for p in data.get("pages", [])
        ]
        answers = []
        for c in data.get("concepts", []):
            label = tu.quoted_phrase(c.get("question", "")) or c.get("question", "")
            hit = tu.contains_tokens(outline, label, ratio=0.7)
            page, quote = None, None
            if hit:
                for pno, ptxt in page_index:
                    if tu.contains_tokens(ptxt, label, ratio=0.7):
                        page = pno
                        quote = _short(ptxt, 120)
                        break
            answers.append(
                {
                    "id": c.get("id"),
                    "value": "yes" if hit else "no",
                    "page": page,
                    "evidence_quote": quote,
                }
            )
        return {"answers": answers}

    def _h_template_compose(self, *, data: dict, imgs: list, schema: type[BaseModel]) -> dict:
        """골격 + 등급 개념 → TemplateSpecLLM. 플레이스홀더만 쓴다 (R10)."""
        skeleton = data.get("skeleton", [])
        concepts = {c["id"]: c for c in data.get("concepts", [])}
        doc_format = data.get("doc_format", "docs")

        sections = []
        for i, sk in enumerate(skeleton, start=1):
            sid = sk.get("section_id", f"S{i}")
            cids = [c for c in sk.get("concept_ids", []) if c in concepts]
            elements: list[dict] = []
            for cid in cids:
                c = concepts[cid]
                label = tu.quoted_phrase(c.get("question", "")) or c.get("question", "")
                kind = {
                    "structure": "table",
                    "quantitative": "chart",
                    "format": "signature",
                    "content_completeness": "text",
                }.get(c.get("type", "content_completeness"), "text")
                elements.append(
                    {
                        "kind": kind,
                        "title": _short(label, 60),
                        "required": c.get("verdict") == "확정",
                        "guidance": _guidance(kind, label, c),
                        "table_columns": sk.get("table_columns") if kind == "table" else None,
                        "evidence_ids": [cid],
                    }
                )
            if not elements:
                elements.append(
                    {
                        "kind": "text",
                        "title": "본문",
                        "required": bool(sk.get("required")),
                        "guidance": "[작성 가이드] Pass 문서에서 이 섹션이 공통으로 존재했다. 해당 내용을 기술한다. (실제 내용은 작성자가 채운다)",
                        "table_columns": None,
                        "evidence_ids": ["skeleton"],
                    }
                )
            sections.append(
                {
                    "order": i,
                    "section_id": sid,
                    "title": sk.get("title", sid),
                    "required": bool(sk.get("required")),
                    "presence_in_pass": float(sk.get("presence_in_pass", 0.0)),
                    "guidance": f"[작성 가이드] Pass 문서 {float(sk.get('presence_in_pass', 0)):.0%}에 존재한 섹션이다. "
                    f"아래 필수 요소를 빠뜨리지 않는다.",
                    "elements": elements,
                    "slides_hint": sk.get("slides_hint") if doc_format == "slides" else None,
                    "pages_hint": sk.get("pages_hint") if doc_format == "docs" else None,
                    "evidence_ids": cids or ["skeleton"],
                }
            )

        global_rules = []
        for g in data.get("global_hints", []):
            global_rules.append(
                {
                    "kind": g.get("kind", "text"),
                    "title": g.get("title", ""),
                    "required": bool(g.get("required", False)),
                    "guidance": g.get("guidance", ""),
                    "table_columns": None,
                    "evidence_ids": g.get("evidence_ids", ["skeleton"]),
                }
            )
        return {"sections": sections, "global_rules": global_rules}


# ---------------------------------------------------------------- 보조 함수


def _aspect(png: bytes) -> float:
    from PIL import Image

    with Image.open(io.BytesIO(png)) as im:
        w, h = im.size
    return (w / h) if h else 1.0


def _short(s: str, n: int = 80) -> str:
    s = _BOILER.sub("", tu.nfkc(s))
    s = _OCR_COLS.sub("", s)
    s = _OCR_PREFIX.sub("", s).strip(" :·-|")
    s = _OCR_PREFIX.sub("", s).strip(" :·-|")   # [chart] [Chart] 처럼 두 번 붙은 경우
    s = re.sub(r"\s+", " ", s)
    return s[:n].strip(" :·-|")




def _lines_to_blocks(lines: list[str]) -> list[dict]:
    blocks: list[dict] = []
    for ln in lines:
        s = tu.nfkc(ln).strip()
        if not s:
            continue
        if _CHART_HINT.search(s):
            blocks.append({"type": "chart", "level": None, "text": _short(s, 120), "table": None})
        elif _TABLE_HINT.search(s):
            cols = _guess_columns(s)
            blocks.append(
                {
                    "type": "table",
                    "level": None,
                    "text": _short(s, 120),
                    "table": {"columns": cols, "n_rows": max(2, len(cols)), "caption": _short(s, 60)},
                }
            )
        elif _PHOTO_HINT.search(s):
            blocks.append({"type": "photo", "level": None, "text": _short(s, 120), "table": None})
        elif _SIGN_HINT.search(s):
            blocks.append({"type": "signature", "level": None, "text": _short(s, 80), "table": None})
        elif _HEAD_HINT.match(s) and len(s) < 80:
            blocks.append({"type": "heading", "level": 1, "text": _short(s, 80), "table": None})
        elif _BOILER.search(s):
            blocks.append({"type": "paragraph", "level": None, "text": "(본문)", "table": None})
        else:
            blocks.append({"type": "paragraph", "level": None, "text": _short(s, 160), "table": None})
    return blocks


def _guess_columns(s: str) -> list[str]:
    tail = s.split(":", 1)[1] if ":" in s else s
    cols = [c.strip() for c in _COLS_SPLIT.split(tail) if c.strip()]
    return [c[:24] for c in cols][:8]


def _line_df(docs: list[dict]) -> dict[str, tuple[str, int, str]]:
    """문서 목록 → {정규화 줄: (원문 줄, 문서 수, 가장 흔한 상위 제목)}.

    개요 텍스트의 `# 제목` 줄을 따라가며 각 줄이 어느 섹션 아래 있었는지도 기록한다.
    채움 문장과 너무 짧은 줄은 뺀다.
    """
    out: dict[str, tuple[str, int, str]] = {}
    heads: dict[str, Counter] = {}
    for d in docs:
        seen: dict[str, str] = {}
        cur_head = ""
        for ln in str(d.get("outline_text", "")).splitlines():
            t = tu.nfkc(ln).strip()
            if t.startswith("#"):
                cur_head = t.lstrip("# ").strip()
                continue
            if t.startswith("---") or len(t) < 8 or _BOILER.search(t) or t == "(본문)":
                continue
            k = tu.norm(t)[:80]
            if len(k) < 6:
                continue
            seen.setdefault(k, t)
            heads.setdefault(k, Counter())[cur_head] += 1
        for k, raw in seen.items():
            prev = out.get(k)
            out[k] = (prev[0] if prev else raw, (prev[1] if prev else 0) + 1, "")
    for k in out:
        raw, cnt, _ = out[k]
        top = heads.get(k)
        out[k] = (raw, cnt, top.most_common(1)[0][0] if top else "")
    return out


def _guidance(kind: str, label: str, c: dict) -> str:
    stat = ""
    if c.get("pass_rate_with") is not None and c.get("pass_rate_without") is not None:
        stat = f" (근거: 있을 때 Pass율 {float(c['pass_rate_with']):.0%} vs 없을 때 {float(c['pass_rate_without']):.0%})"
    body = {
        "table": f"[작성 가이드] \"{label}\" 표를 넣는다. 아래 빈 표의 열을 채운다. 실제 수치는 작성자가 기입한다.",
        "chart": f"[작성 가이드] \"{label}\" 그래프를 넣는다. [예: 대책 전/후 불량률 추이 — ppm] 형태의 플레이스홀더만 두고, 실제 데이터는 작성자가 넣는다.",
        "signature": f"[작성 가이드] \"{label}\" 란을 둔다. 작성/검토/승인 서명이 보이게 한다.",
        "text": f"[작성 가이드] \"{label}\" 에 해당하는 내용을 기술한다. 실제 내용은 작성자가 채운다.",
    }.get(kind, "[작성 가이드] 해당 항목을 포함한다.")
    return body + stat
