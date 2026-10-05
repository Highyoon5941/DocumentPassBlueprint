"""반사실 분석 — Fail 문서가 Pass 쪽으로 가려면 무엇을 바꿔야 하는가 (SETUP.md §8.5-7).

선형 SVM 의 결정함수 f(x) = w·x + b 가 0 을 넘을 때까지, `actionable=true` 인 이진 개념을
|w| 큰 순서로 하나씩 바꾼다. 이진 특징에서 각 변경의 이득(|w_j|)이 서로 독립이므로
이 탐욕 방식이 **변경 개수 기준 최소해**다.
"""

from __future__ import annotations

import json
from collections import Counter

import pandas as pd

from svmtrial.config import Settings
from svmtrial.features import is_binary
from svmtrial.groups import Group, work_dir
from svmtrial.schemas import Concept


def decision(x: pd.Series, w: dict[str, float], b: float) -> float:
    return float(sum(w.get(k, 0.0) * float(v) for k, v in x.items()) + b)


def flip_candidates(x: pd.Series, w: dict[str, float], allowed: set[str]) -> list[tuple[str, float, str]]:
    """바꿀 수 있는 특징과 그 이득. (특징, 이득, 'add'|'remove')

    - 없는데(0) w > 0 → 추가하면 +w 만큼 올라간다
    - 있는데(1) w < 0 → 제거하면 +|w| 만큼 올라간다
    """
    out = []
    for k, wk in w.items():
        if k not in allowed or k not in x.index:
            continue
        v = float(x[k])
        if v == 0.0 and wk > 0:
            out.append((k, wk, "add"))
        elif v == 1.0 and wk < 0:
            out.append((k, -wk, "remove"))
    out.sort(key=lambda t: -t[1])
    return out


def explain_doc(
    doc_id: str, x: pd.Series, w: dict[str, float], b: float, allowed: set[str], max_changes: int = 10
) -> dict:
    """문서 1건의 최소 보완 목록."""
    f0 = decision(x, w, b)
    if f0 > 0:
        return {"doc_id": doc_id, "f0": round(f0, 4), "already_pass_side": True, "changes": [], "f1": round(f0, 4)}
    need = -f0
    gained, changes = 0.0, []
    for k, gain, action in flip_candidates(x, w, allowed):
        if gained >= need or len(changes) >= max_changes:
            break
        changes.append({"feature": k, "action": action, "gain": round(float(gain), 5)})
        gained += gain
    return {
        "doc_id": doc_id,
        "f0": round(f0, 4),
        "already_pass_side": False,
        "changes": changes,
        "f1": round(f0 + gained, 4),
        "reached_pass_side": bool(f0 + gained > 0),
        "n_changes": len(changes),
    }


def run(
    s: Settings, g: Group, X: pd.DataFrame, y: pd.Series, w: dict[str, float], b: float,
    concepts: list[Concept], labels_map: dict[str, str] | None = None,
) -> dict:
    """전체 Fail 문서의 보완 목록 + 자주 등장하는 보완 항목 순위."""
    actionable = {c.id for c in concepts if c.actionable}
    allowed = {k for k in X.columns if k in actionable and is_binary(X[k])}
    label = labels_map or {c.id: c.question for c in concepts}

    per_doc = []
    for doc_id in X.index.astype(str):
        if doc_id in y.index.astype(str) and float(y.loc[doc_id]) == 1.0:
            continue
        per_doc.append(explain_doc(doc_id, X.loc[doc_id], w, b, allowed))

    freq: Counter[str] = Counter()
    for r in per_doc:
        for ch in r["changes"]:
            freq[f"{ch['action']}:{ch['feature']}"] += 1

    n_fail = max(1, len(per_doc))
    ranking = [
        {
            "feature": k.split(":", 1)[1],
            "action": k.split(":", 1)[0],
            "n_docs": v,
            "share_of_fail": round(v / n_fail, 3),
            "label": label.get(k.split(":", 1)[1], k.split(":", 1)[1]),
        }
        for k, v in freq.most_common()
    ]
    out = {
        "group": g.key,
        "n_fail_docs": len(per_doc),
        "n_actionable_features": len(allowed),
        "n_reached_pass_side": sum(1 for r in per_doc if r.get("reached_pass_side")),
        "mean_changes_needed": round(
            sum(r["n_changes"] for r in per_doc if not r["already_pass_side"]) / max(1, len(per_doc)), 2
        ),
        "ranking": ranking,
        "per_doc": per_doc,
    }
    wd = work_dir(s, "models", g)
    (wd / "counterfactual.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    pd.DataFrame(ranking).to_csv(wd / "counterfactual_ranking.csv", index=False, encoding="utf-8")
    return out
