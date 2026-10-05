"""결정론적 구조 특징 + 특징 행렬 조립 (SETUP.md §6, §8.4b 마지막 항목).

LLM 없이 OCR 결과와 섹션 매핑에서 바로 계산하는 특징이다. 개념 채점(S4c)과 합쳐
`work/features/<group>/X.csv` 를 만든다.
"""

from __future__ import annotations

import pandas as pd

from svmtrial.config import Settings
from svmtrial.groups import Group, work_dir
from svmtrial.ocr import load_outline
from svmtrial.schemas import Concept

STRUCT_PREFIXES = ("STR_", "SEC_")


def structural_features(s: Settings, g: Group, doc_id: str) -> dict[str, float]:
    """페이지 수, 표·사진·차트 개수, 섹션 존재 여부, 섹션 순서 일치도(Kendall τ),
    서명·승인란 유무, 평균 판독성."""
    from svmtrial.sections import load_doc_sections

    o = load_outline(s, doc_id)
    ds = load_doc_sections(s, g, doc_id)
    c = o.get("counts", {})
    feats: dict[str, float] = {
        "STR_n_pages": float(o.get("n_pages") or 0),
        "STR_n_tables": float(c.get("tables", 0)),
        "STR_n_figures": float(c.get("figures", 0)),
        "STR_n_photos": float(c.get("photos", 0)),
        "STR_n_charts": float(c.get("charts", 0)),
        "STR_n_headings": float(c.get("headings", 0)),
        "STR_has_signature": float(bool(o.get("n_signatures", 0))),
        "STR_mean_legibility": float(o.get("mean_legibility") or 0.0),
    }
    tau = ds.get("order_tau")
    feats["STR_order_tau"] = float(tau) if tau is not None else 0.0
    for sid in ds.get("taxonomy_order", []):
        feats[f"SEC_{sid}"] = float(sid in set(ds.get("present", [])))
    return feats


def is_structural(col: str) -> bool:
    return col.startswith(STRUCT_PREFIXES)


def is_binary(series: pd.Series) -> bool:
    vals = set(series.dropna().unique().tolist())
    return vals.issubset({0.0, 1.0})


def build_matrix(
    s: Settings, g: Group, doc_ids: list[str], concepts: list[Concept], answers: dict[str, dict[str, str]]
) -> pd.DataFrame:
    """개념 응답(yes/no/na) + 구조 특징 → 문서 × 특징 행렬. 행 index = doc_id."""
    rows = []
    for d in doc_ids:
        ans = answers.get(d, {})
        row: dict[str, object] = {"doc_id": d}
        for c in concepts:
            v = ans.get(c.id)
            row[c.id] = 1.0 if v == "yes" else (0.0 if v == "no" else float("nan"))
        row.update(structural_features(s, g, d))
        rows.append(row)
    return pd.DataFrame(rows).set_index("doc_id")


def save_matrix(s: Settings, g: Group, X: pd.DataFrame) -> str:
    f = work_dir(s, "features", g) / "X.csv"
    X.to_csv(f, encoding="utf-8")
    return str(f.relative_to(s.root))


def load_X(s: Settings, g: Group) -> pd.DataFrame:
    f = work_dir(s, "features", g) / "X.csv"
    if not f.exists():
        raise FileNotFoundError(f"{f} 가 없습니다 — 먼저 `svmtrial concepts score` 를 실행하세요.")
    return pd.read_csv(f, index_col="doc_id")
