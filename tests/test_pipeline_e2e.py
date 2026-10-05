"""S1~S6 통합 — Gemini 는 전부 mock (R6). 숨김 규칙을 다시 찾아내는지 확인한다."""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from svmtrial import concepts as C
from svmtrial import features as F
from svmtrial import labels as L
from svmtrial import modeling as M
from svmtrial.groups import Group, work_dir

G = Group("대책서", "CUST_T")
HIDDEN = ["C001", "C002"]        # 진짜 신호
NOISE = ["C003"]                 # 잡음


def _write_outline(settings, doc_id: str, has: dict[str, bool]):
    """OCR 결과(doc_outline.json)를 직접 만들어 S2 를 건너뛴다."""
    d = settings.work / "ocr" / doc_id
    d.mkdir(parents=True, exist_ok=True)
    lines = ["# D2 문제 정의", "(본문)", "# D4 근본원인"]
    tables, figures = [], []
    if has["C001"]:
        lines.append("[표] 5Why 단계적 원인분석 표")
        tables.append({"page": 1, "columns": ["Why1", "Why2"], "n_rows": 5, "caption": "5Why"})
    lines.append("# D6 효과검증")
    if has["C002"]:
        lines.append("[chart] 대책 전후 불량률 추이 그래프")
        figures.append({"page": 1, "type": "chart", "text": "불량률 추이"})
    if has["C003"]:
        lines.append("[photo] 불량품 근접 사진")
        figures.append({"page": 1, "type": "photo", "text": "불량품"})
    outline = {
        "doc_id": doc_id, "n_pages": 2, "n_ocr_pages": 2, "doc_format": "docs",
        "headings": [{"page": 1, "level": 1, "text": t.lstrip("# ")} for t in lines if t.startswith("#")],
        "tables": tables, "figures": figures, "n_signatures": 1,
        "counts": {"headings": 3, "tables": len(tables), "figures": len(figures),
                   "photos": sum(1 for f in figures if f["type"] == "photo"),
                   "charts": sum(1 for f in figures if f["type"] == "chart")},
        "page_roles": ["body", "body"], "looks_like": ["document", "document"],
        "mean_legibility": 0.95, "low_legibility_pages": [],
        "pages": [{"page": 1, "role": "body", "text": "\n".join(lines)}],
        "outline_text": "\n".join(lines),
    }
    (d / "doc_outline.json").write_text(json.dumps(outline, ensure_ascii=False), encoding="utf-8")
    # 섹션 매핑도 직접 기록
    present = ["D2", "D4", "D6"]
    sd = {"doc_id": doc_id, "doc_format": "docs", "n_pages": 2,
          "taxonomy_order": ["D2", "D4", "D6"],
          "sections": [{"section_id": p, "title": p, "first_page": 1, "last_page": 1,
                        "n_headings": 1, "appear_order": i + 1, "pages_span": 1}
                       for i, p in enumerate(present)],
          "present": present, "missing": [], "order_tau": 1.0}
    (work_dir(settings, "sections", G) / f"{doc_id}.sections.json").write_text(
        json.dumps(sd, ensure_ascii=False), encoding="utf-8")


@pytest.fixture
def world(settings):
    """40개 문서. C001/C002 가 Pass 확률을 올리고, C003 은 영향 없음."""
    rng = np.random.default_rng(3)
    truth, rows, answers = [], [], {}
    for i in range(60):
        doc_id = f"T{i:03d}"
        has = {c: bool(rng.random() < 0.5) for c in HIDDEN + NOISE}
        _write_outline(settings, doc_id, has)
        answers[doc_id] = {c: ("yes" if has[c] else "no") for c in HIDDEN + NOISE}
        p = 0.1 + 0.4 * sum(has[c] for c in HIDDEN)
        for r in ("r1", "r2", "r3", "r4", "r5"):
            rows.append({"doc_id": doc_id, "customer": "CUST_T", "doc_type": "대책서",
                         "rater": r, "y": float(rng.random() < p)})
        truth.append({"doc_id": doc_id, **has})
    long = pd.DataFrame(rows)
    agree = L.agreement_stats(long)
    targets = L.doc_targets(long, agree)
    return {"long": long, "targets": targets, "answers": answers,
            "truth": pd.DataFrame(truth), "doc_ids": [f"T{i:03d}" for i in range(60)]}


def _concepts():
    from svmtrial.schemas import Concept

    meta = {"C001": ("D4", "structure", '"5Why 단계적 원인분석 표" 가 있는가?'),
            "C002": ("D6", "quantitative", '"대책 전후 불량률 추이 그래프" 가 있는가?'),
            "C003": ("D4", "content_completeness", '"불량품 근접 사진" 이 있는가?')}
    return [Concept(id=k, question=q, section_id=sec, type=t, actionable=True, rationale="테스트")
            for k, (sec, t, q) in meta.items()]


def test_e2e_recovers_hidden_rules(settings, world):
    """핵심 검증: 파이프라인이 숨긴 규칙 2개를 찾아내고 잡음 1개는 기각해야 한다."""
    settings.analysis.n_bootstrap = 80
    settings.analysis.n_permutations = 200
    concepts = _concepts()

    X = F.build_matrix(settings, G, world["doc_ids"], concepts, world["answers"])
    assert list(X.index) == world["doc_ids"]
    assert all(c in X.columns for c in HIDDEN + NOISE)
    assert any(c.startswith("STR_") for c in X.columns)

    tsel = L.choose_target(world["targets"], settings)
    tcol = tsel["column"]
    split = C.make_split(settings, G, world["targets"], tcol)
    assert set(split["split"]) == {"discovery", "validation"}
    frac = (split["split"] == "discovery").mean()
    assert 0.5 < frac < 0.7, "발견셋 비율이 설정값(0.6) 근처여야 한다"

    res = M.run(settings, G, X, world["targets"], tcol, split, world["long"])
    verd = {r["feature"]: r["verdict"] for r in res["verdicts"]}

    for c in HIDDEN:
        assert verd[c] in ("확정", "유력"), f"{c} 는 확정/유력 이어야 하는데 {verd[c]}"
    assert verd["C003"] in ("참고", "기각"), f"잡음 C003 이 {verd['C003']} 로 올라왔다"
    assert res["multivariate"]["permutation_test"]["p_value"] < 0.05
    assert res["multivariate"]["signal"] is True
    # 더미보다 넉넉한 표본이므로 모델이 dummy 를 이겨야 한다
    mv = res["multivariate"]["models"]
    assert mv[res["multivariate"]["primary_model"]]["balanced_accuracy"] > mv["dummy"]["balanced_accuracy"]


def test_split_is_stable_across_runs(settings, world):
    tcol = "y_majority"
    a = C.make_split(settings, G, world["targets"], tcol)
    b = C.make_split(settings, G, world["targets"], tcol)
    pd.testing.assert_frame_equal(a, b)


def test_discovery_set_only_used_for_hypotheses(settings, world):
    """검증셋 문서가 가설 생성에 쓰이지 않아야 한다 (누수 방지)."""
    split = C.make_split(settings, G, world["targets"], "y_majority")
    val_ids = set(split.loc[split["split"] == "validation", "doc_id"])

    seen: list[str] = []

    class Spy:
        backend_name = "mock"
        dry_run = False

        def generate_json(self, *, model, prompt_id, parts, schema, cache_key_extra="", media_resolution=None):
            from svmtrial.payload import extract

            data = extract(parts[0]["text"])
            for d in data.get("pass_docs", []) + data.get("fail_docs", []):
                seen.append(d["doc_id"])
            if prompt_id.startswith("concept_discovery"):
                return schema.model_validate({"concepts": [
                    {"id": "C001", "question": 'q "5Why 단계적 원인분석 표"', "section_id": "D4",
                     "type": "structure", "actionable": True, "rationale": "r"}]})
            return schema.model_validate({"concepts": [
                {"id": "C001", "question": 'q "5Why 단계적 원인분석 표"', "section_id": "D4",
                 "type": "structure", "actionable": True, "rationale": "r"}]})

        def map_parallel(self, fn, jobs, desc=""):
            return [fn(j) for j in jobs]

        def summary(self):
            return {}

    C.discover(settings, G, Spy(), world["targets"], "y_majority")
    assert seen, "가설 생성에 문서가 전달되어야 한다"
    assert not (set(seen) & val_ids), f"검증셋 문서가 가설 생성에 들어갔다: {set(seen) & val_ids}"


def test_scoring_matrix_na_handling(settings, world):
    concepts = _concepts()
    answers = {d: dict(v) for d, v in world["answers"].items()}
    answers["T000"]["C001"] = "na"
    X = F.build_matrix(settings, G, world["doc_ids"], concepts, answers)
    assert pd.isna(X.loc["T000", "C001"])
    targets = world["targets"]
    Xi, _, _ = M.prepare(X, targets, "y_majority")
    assert Xi.loc["T000", "C001"] == 0.0, "na 는 '없음'(0)으로 다뤄야 한다"
