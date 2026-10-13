"""목적함수 직접 최적화 — "Pass 확률이 가장 높은 요소 조합" (S5 후처리).

등급표(확정/유력)를 만드는 대신, 질문에 직접 답한다:
  "요소를 N개까지만 넣을 수 있다면 무엇을 넣어야 Pass 확률이 가장 높은가?"

두 목적함수를 지원한다.
  decision_value : f(x) = w·x + b          (SVM 호환. 확률이 아니다)
  all_pass_prob  : P = Π_r σ(w·x + b + β_r) (평가자 전원 통과 확률)

⚠ 탐욕이 최적이 아니다
  선형 w·x 에서는 각 변경의 이득이 독립이라 탐욕이 변경 개수 기준 최소해였다.
  목적이 Π_r σ(·) 로 바뀌면 이득이 독립이 아니다 — 여러 요소가 함께 들어가야
  각 평가자가 문턱을 넘기 때문이다(초모듈성). 그래서 전수탐색 또는 beam search 를 쓴다.
"""

from __future__ import annotations

import itertools
import json
import math
from typing import Any

import numpy as np
import pandas as pd

from svmtrial.config import Settings
from svmtrial.features import is_binary
from svmtrial.groups import Group, work_dir
from svmtrial.schemas import Concept

MAX_EXHAUSTIVE = 20          # 이보다 많으면 beam search 로 전환
DEFAULT_BEAM = 64


def _sigmoid(z: float) -> float:
    return 1.0 / (1.0 + math.exp(-max(-500.0, min(500.0, z))))


class Objective:
    """요소 조합 → 점수. 모델 종류에 무관한 공통 인터페이스."""

    def __init__(self, fit: dict, feature_order: list[str], baseline: dict[str, float],
                 kind: str = "all_pass_prob"):
        self.w = fit.get("weights", {})
        self.b = float(fit.get("intercept", 0.0))
        self.rater_effect: dict[str, float] = fit.get("rater_effect", {}) or {}
        self.interactions: dict[str, float] = fit.get("rater_interactions", {}) or {}
        self.features = feature_order
        self.baseline = dict(baseline)
        self.kind = kind if (kind != "all_pass_prob" or self.rater_effect) else "prob"

    def _eta(self, x: dict[str, float], rater: str | None = None) -> float:
        z = self.b + sum(self.w.get(k, 0.0) * float(v) for k, v in x.items())
        if rater is not None:
            z += self.rater_effect.get(rater, 0.0)
            for term, coef in self.interactions.items():
                # 항 이름 형태: is_<rater>:Q('<feature>')
                if f"is_{rater}" not in term and rater not in term:
                    continue
                feat = term.split("Q('")[-1].rstrip("')") if "Q('" in term else term.split(":")[-1]
                z += coef * float(x.get(feat, 0.0))
        return z

    def score(self, add: tuple[str, ...] = ()) -> float:
        """baseline 에 add 의 요소를 1 로 켠 구성의 점수."""
        x = dict(self.baseline)
        for c in add:
            x[c] = 1.0
        if self.kind == "decision_value":
            return self._eta(x)
        if self.kind == "prob":
            return _sigmoid(self._eta(x))
        return float(np.prod([_sigmoid(self._eta(x, r)) for r in self.rater_effect])) \
            if self.rater_effect else _sigmoid(self._eta(x))

    def label(self) -> str:
        return {"decision_value": "결정값 f(x)", "prob": "P(Pass)",
                "all_pass_prob": "P(평가자 전원 Pass)"}[self.kind]


def optimize(
    obj: Objective, candidates: list[str], max_k: int | None = None, beam: int = DEFAULT_BEAM
) -> dict[str, Any]:
    """요소 수별 최적 조합을 찾는다. 후보가 적으면 전수탐색, 많으면 beam search."""
    cands = [c for c in candidates if c in obj.w]
    max_k = len(cands) if max_k is None else min(max_k, len(cands))
    base = obj.score(())
    exhaustive = len(cands) <= MAX_EXHAUSTIVE

    best_by_k: dict[int, tuple[float, tuple[str, ...]]] = {0: (base, ())}
    if exhaustive:
        for k in range(1, max_k + 1):
            best = max(((obj.score(c), c) for c in itertools.combinations(cands, k)),
                       default=(base, ()))
            best_by_k[k] = best
    else:
        frontier: list[tuple[float, tuple[str, ...]]] = [(base, ())]
        for k in range(1, max_k + 1):
            nxt: dict[tuple[str, ...], float] = {}
            for _, combo in frontier:
                for c in cands:
                    if c in combo:
                        continue
                    nc = tuple(sorted((*combo, c)))
                    if nc not in nxt:
                        nxt[nc] = obj.score(nc)
            if not nxt:
                break
            ranked = sorted(((v, k_) for k_, v in nxt.items()), reverse=True)
            best_by_k[k] = ranked[0]
            frontier = ranked[:beam]

    marginal = {c: obj.score((c,)) - base for c in cands}
    curve = [{"k": k, "score": round(v, 6), "elements": list(combo)}
             for k, (v, combo) in sorted(best_by_k.items())]
    top = max(best_by_k.items(), key=lambda kv: kv[1][0])
    return {
        "objective": obj.kind,
        "objective_label": obj.label(),
        "search": "exhaustive" if exhaustive else f"beam({beam})",
        "n_candidates": len(cands),
        "baseline_score": round(base, 6),
        "budget_curve": curve,
        "best": {"k": top[0], "score": round(top[1][0], 6), "elements": list(top[1][1])},
        "marginal_gain": {k: round(v, 6) for k, v in sorted(marginal.items(), key=lambda kv: -kv[1])},
        "greedy_is_optimal": _greedy_matches(obj, cands, curve),
    }


def _greedy_matches(obj: Objective, cands: list[str], curve: list[dict]) -> bool:
    """탐욕(한계이득 큰 순)이 전수탐색 결과와 같은지 — 다르면 탐욕을 쓰면 안 된다는 증거."""
    base = obj.score(())
    order = sorted(cands, key=lambda c: -(obj.score((c,)) - base))
    for row in curve:
        k = row["k"]
        if k == 0 or k > len(order):
            continue
        if abs(obj.score(tuple(sorted(order[:k]))) - row["score"]) > 1e-9:
            return False
    return True


def shortfall(obj: Objective, x: pd.Series, candidates: list[str], target: float = 0.5,
              max_k: int = 10) -> dict[str, Any]:
    """문서 1건에 대해: 목표 점수에 닿으려면 무엇을 더 넣어야 하는가 (반사실)."""
    cur = {k: float(v) for k, v in x.items()}
    o = Objective({"weights": obj.w, "intercept": obj.b,
                   "rater_effect": obj.rater_effect, "rater_interactions": obj.interactions},
                  obj.features, cur, obj.kind)
    missing = [c for c in candidates if c in obj.w and float(cur.get(c, 0.0)) == 0.0]
    now = o.score(())
    if now >= target:
        return {"already_meets": True, "score": round(float(now), 6), "changes": []}
    res = optimize(o, missing, max_k=min(max_k, len(missing)))
    pick = next((r for r in res["budget_curve"] if r["k"] > 0 and r["score"] >= target), None)
    chosen = pick or (res["budget_curve"][-1] if res["budget_curve"] else {"elements": [], "score": now})
    return {
        "already_meets": False,
        "score_before": round(now, 6),
        "score_after": round(chosen["score"], 6),
        "reached_target": bool(chosen["score"] >= target),
        "changes": chosen["elements"],
        "n_changes": len(chosen["elements"]),
        "budget_curve": res["budget_curve"],
    }


def run(
    s: Settings, g: Group, fit: dict, X: pd.DataFrame, y: pd.Series,
    concepts: list[Concept], labels_map: dict[str, str] | None = None,
) -> dict[str, Any]:
    """그룹 전체에 대해 최적 조합 + 예산 곡선 + Fail 문서별 보완 목록을 낸다."""
    actionable = {c.id for c in concepts if c.actionable}
    cands = [c for c in X.columns if c in actionable and is_binary(X[c])]
    # 기준점: 모든 actionable 요소가 없는 문서. 나머지 특징은 중앙값으로 고정한다.
    baseline = {c: (0.0 if c in cands else float(X[c].median())) for c in X.columns}
    obj = Objective(fit, list(X.columns), baseline, s.analysis.objective)

    res = optimize(obj, cands)
    label = labels_map or {c.id: c.question for c in concepts}
    res["element_labels"] = {c: label.get(c, c) for c in cands}

    # Fail 쪽 문서별 보완
    idx = X.index.astype(str)
    per_doc = []
    for doc_id in idx:
        if doc_id in y.index.astype(str) and float(y.loc[doc_id]) == 1.0:
            continue
        per_doc.append({"doc_id": doc_id, **shortfall(obj, X.loc[doc_id], cands)})
    from collections import Counter

    freq: Counter[str] = Counter()
    for r in per_doc:
        freq.update(r.get("changes", []))
    n_fail = max(1, len(per_doc))
    res["fail_docs"] = len(per_doc)
    res["ranking"] = [
        {"feature": k, "n_docs": v, "share_of_fail": round(v / n_fail, 3), "label": label.get(k, k)}
        for k, v in freq.most_common()
    ]
    res["per_doc"] = per_doc

    wd = work_dir(s, "models", g)
    (wd / "objective.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    pd.DataFrame(res["ranking"]).to_csv(wd / "objective_ranking.csv", index=False, encoding="utf-8")
    return res
