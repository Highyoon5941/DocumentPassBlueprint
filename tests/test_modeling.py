"""S5 modeling — 단변량, 등급, 안정성, 검증셋, 반사실 최소성."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from svmtrial import modeling as M
from svmtrial.counterfactual import decision, explain_doc, flip_candidates
from svmtrial.schemas import Concept


def _xy(n=80, seed=0):
    """c0, c3 가 진짜 신호. c1, c2 는 잡음."""
    rng = np.random.default_rng(seed)
    X = pd.DataFrame(rng.integers(0, 2, (n, 5)).astype(float), columns=[f"c{i}" for i in range(5)])
    X.index = [f"D{i:03d}" for i in range(n)]
    X.index.name = "doc_id"
    y = pd.Series(((X.c0 + X.c3) >= 1).astype(float).to_numpy(), index=X.index)
    return X, y


def test_univariate_finds_real_signal():
    X, y = _xy()
    u = M.univariate(X, y)
    top = u.head(2)["feature"].tolist()
    assert set(top) == {"c0", "c3"}
    assert (u[u.feature == "c0"]["rd"] > 0).all()
    assert u[u.feature == "c0"]["q_value"].iloc[0] < 0.05
    # 잡음은 유의하지 않아야 한다
    assert u[u.feature == "c1"]["q_value"].iloc[0] > 0.1


def test_univariate_handles_numeric_feature():
    X, y = _xy()
    X["num"] = np.where(y == 1, 10.0, 2.0)   # 완전 분리되는 수치형
    u = M.univariate(X, y)
    row = u[u.feature == "num"].iloc[0]
    assert row["kind"] == "numeric"
    assert row["rd"] > 0 and row["p_value"] < 0.001


def test_prepare_drops_constant_and_unstable():
    X, y = _xy()
    X["const"] = 1.0
    targets = pd.DataFrame({"doc_id": X.index, "y_majority": y.to_numpy()})
    Xi, yy, info = M.prepare(X, targets, "y_majority", unstable=["c1"])
    assert "const" in info["dropped_constant"]
    assert "c1" in info["dropped_unstable"]
    assert "const" not in Xi.columns and "c1" not in Xi.columns
    assert len(Xi) == len(X)


def test_prepare_fills_na_as_absent():
    X, y = _xy(40)
    X.iloc[0, 0] = np.nan
    targets = pd.DataFrame({"doc_id": X.index, "y_majority": y.to_numpy()})
    Xi, _, _ = M.prepare(X, targets, "y_majority")
    assert Xi.iloc[0, 0] == 0.0 and not Xi.isna().to_numpy().any()


def test_svm_weights_sign_matches_effect():
    X, y = _xy()
    from svmtrial.config import load_settings

    s = load_settings()
    w = M.fit_svm_weights(s, X, y, "svm_C0.1")
    assert w["weights"]["c0"] > 0 and w["weights"]["c3"] > 0
    assert abs(w["weights"]["c0"]) > abs(w["weights"]["c1"])


def test_validation_check_detects_same_direction():
    X, y = _xy(60)
    split = pd.DataFrame({"doc_id": list(X.index),
                          "split": ["discovery"] * 36 + ["validation"] * 24})
    v = M.validation_check(X, y, split, continuous=False)
    assert bool(v[v.feature == "c0"]["same_direction"].iloc[0]) is True
    assert v["n_validation"].iloc[0] == 24


def test_validation_check_handles_numeric():
    X, y = _xy(60)
    X["num"] = np.where(y == 1, 5.0, 1.0)
    split = pd.DataFrame({"doc_id": list(X.index), "split": ["discovery"] * 36 + ["validation"] * 24})
    v = M.validation_check(X, y, split, continuous=False)
    row = v[v.feature == "num"].iloc[0]
    assert bool(row["same_direction"]) is True, "수치형도 방향 확인이 되어야 한다"
    assert row["rd_discovery"] > 0 and row["rd_validation"] > 0


def test_verdicts_grading_bands(settings):
    uni = pd.DataFrame([
        {"feature": "strong", "rd": 0.5, "q_value": 0.01, "n_with": 20, "n_without": 20},
        {"feature": "likely_q", "rd": 0.3, "q_value": 0.2, "n_with": 20, "n_without": 20},
        {"feature": "likely_stab", "rd": 0.1, "q_value": 0.9, "n_with": 20, "n_without": 20},
        {"feature": "ref", "rd": 0.4, "q_value": 0.9, "n_with": 20, "n_without": 20},
        {"feature": "rejected", "rd": 0.01, "q_value": 0.9, "n_with": 20, "n_without": 20},
    ])
    stab = pd.DataFrame([
        {"feature": "strong", "sign_stability": 0.95},
        {"feature": "likely_q", "sign_stability": 0.5},
        {"feature": "likely_stab", "sign_stability": 0.85},
        {"feature": "ref", "sign_stability": 0.5},
        {"feature": "rejected", "sign_stability": 0.5},
    ])
    val = pd.DataFrame([{"feature": f, "same_direction": (f != "ref"), "rd_discovery": 1, "rd_validation": 1}
                        for f in uni.feature])
    v = M.verdicts(settings, uni, stab, val).set_index("feature")
    assert v.loc["strong", "verdict"] == "확정"
    assert v.loc["likely_q", "verdict"] == "유력"
    assert v.loc["likely_stab", "verdict"] == "유력"
    assert "안정성만" in v.loc["likely_stab", "verdict_basis"]
    assert "안정성만" not in v.loc["likely_q", "verdict_basis"]
    assert v.loc["ref", "verdict"] == "참고"
    assert v.loc["rejected", "verdict"] == "기각"


def test_exploratory_guard_triggers(settings):
    X, y = _xy(20)
    targets = pd.DataFrame({"doc_id": X.index, "y_majority": y.to_numpy()})
    split = pd.DataFrame({"doc_id": list(X.index), "split": ["discovery"] * 12 + ["validation"] * 8})
    long = pd.DataFrame([{"doc_id": d, "rater": "r1", "y": float(y.loc[d])} for d in X.index])
    settings.analysis.n_bootstrap = 20
    settings.analysis.n_permutations = 30
    res = M.run(settings, _G(), X, targets, "y_majority", split, long)
    # 소수 클래스 n < 10 * 특징수 → 탐색적
    assert res["exploratory_only"] is True
    assert "탐색적" in res["exploratory_reason"]


def _G():
    from svmtrial.groups import Group

    return Group("테스트", "G1")


# ---------------------------------------------------------------- 반사실


def test_counterfactual_greedy_is_minimal():
    """이진 특징에서 |w| 내림차순 탐욕이 변경 개수 최소해여야 한다."""
    w = {"a": 0.5, "b": 0.3, "c": 0.1, "d": -0.4}
    b = 0.0
    x = pd.Series({"a": 0.0, "b": 0.0, "c": 0.0, "d": 1.0})
    allowed = set(w)
    assert decision(x, w, b) == pytest.approx(-0.4)
    r = explain_doc("D", x, w, b, allowed)
    # 필요한 상승폭 0.4 → 'a' 추가(0.5) 하나면 충분
    assert r["n_changes"] == 1 and r["changes"][0]["feature"] == "a"
    assert r["reached_pass_side"] is True

    # 완전탐색으로 최소성 검증
    import itertools

    cands = flip_candidates(x, w, allowed)
    need = -decision(x, w, b)
    best = min((k for k in range(1, len(cands) + 1)
                if any(sum(g[1] for g in combo) > need for combo in itertools.combinations(cands, k))),
               default=None)
    assert r["n_changes"] == best


def test_counterfactual_already_pass_side():
    w = {"a": 1.0}
    x = pd.Series({"a": 1.0})
    r = explain_doc("D", x, w, 0.0, {"a"})
    assert r["already_pass_side"] is True and r["changes"] == []


def test_counterfactual_respects_actionable():
    from svmtrial import counterfactual as cf
    from svmtrial.groups import Group

    X = pd.DataFrame({"C001": [0.0, 0.0], "C002": [0.0, 0.0]}, index=["D1", "D2"])
    X.index.name = "doc_id"
    y = pd.Series([0.0, 0.0], index=X.index)
    concepts = [Concept(id="C001", question="q1", type="structure", actionable=True, rationale=""),
                Concept(id="C002", question="q2", type="structure", actionable=False, rationale="")]
    import tempfile
    from pathlib import Path

    from svmtrial.config import load_settings

    s = load_settings()
    object.__setattr__(s, "root", Path(tempfile.mkdtemp()))
    out = cf.run(s, Group("T", "G"), X, y, {"C001": 1.0, "C002": 9.0}, -0.5, concepts)
    feats = {r["feature"] for r in out["ranking"]}
    assert feats == {"C001"}, "actionable=false 인 개념은 보완 목록에 들어가면 안 된다"
