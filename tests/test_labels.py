"""S3 labels — 정규화, long/wide 판별, κ/α, Dawid-Skene."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from svmtrial import labels as L
from svmtrial.groups import Group


def _settings(settings):
    settings.labels.columns = {"doc_id": "doc_id", "customer": "customer", "doc_type": "doc_type",
                               "rater": "rater", "result": "result"}
    settings.labels.pass_values = ["Pass", "P", "OK", "O", "○", "합격", "1", "Y"]
    settings.labels.fail_values = ["Fail", "F", "NG", "X", "×", "불합격", "0", "N"]
    return settings


# ---------------------------------------------------------------- 정규화


@pytest.mark.parametrize("raw,expected", [
    ("Pass", 1.0), ("pass", 1.0), ("P", 1.0), ("OK", 1.0), ("○", 1.0), ("합격", 1.0), ("1", 1.0), ("Y", 1.0),
    ("Fail", 0.0), ("NG", 0.0), ("×", 0.0), ("불합격", 0.0), ("0", 0.0), ("N", 0.0),
])
def test_value_normalization(settings, raw, expected):
    s = _settings(settings)
    df = pd.DataFrame([{"doc_id": "D1", "rater": "r1", "result": raw}])
    long = L.to_long(df, s, "t")
    out, errs = L.normalize(long, s)
    assert errs == []
    assert out["y"].iloc[0] == expected


def test_blank_is_missing_not_error(settings):
    s = _settings(settings)
    df = pd.DataFrame([{"doc_id": "D1", "rater": "r1", "result": ""},
                       {"doc_id": "D1", "rater": "r2", "result": None},
                       {"doc_id": "D1", "rater": "r3", "result": np.nan}])
    out, errs = L.normalize(L.to_long(df, s, "t"), s)
    assert errs == []
    assert out["y"].isna().all()


def test_unknown_value_is_error(settings):
    s = _settings(settings)
    df = pd.DataFrame([{"doc_id": "D1", "rater": "r1", "result": "보류"}])
    out, errs = L.normalize(L.to_long(df, s, "t"), s)
    assert len(errs) == 1 and errs[0]["value"] == "보류"


def test_wide_format_is_melted(settings):
    s = _settings(settings)
    wide = pd.DataFrame([{"doc_id": "D1", "customer": "C", "doc_type": "대책서",
                          "평가자1": "Pass", "평가자2": "Fail", "평가자3": ""}])
    long = L.to_long(wide, s, "wide.xlsx")
    assert len(long) == 3
    assert set(long["rater"]) == {"평가자1", "평가자2", "평가자3"}
    out, errs = L.normalize(long, s)
    assert errs == []
    assert sorted(out["y"].dropna().tolist()) == [0.0, 1.0]


def test_long_format_detected(settings):
    s = _settings(settings)
    df = pd.DataFrame([{"doc_id": "D1", "customer": "C", "doc_type": "대책서", "rater": "윤정호", "result": "Pass"}])
    long = L.to_long(df, s, "long.xlsx")
    assert long["rater"].iloc[0] == "윤정호" and len(long) == 1


# ---------------------------------------------------------------- 타깃


def test_doc_targets_and_unanimous(settings):
    long = pd.DataFrame([{"doc_id": "D1", "rater": f"r{i}", "y": 1.0, "customer": "C", "doc_type": "T"}
                         for i in range(5)]
                        + [{"doc_id": "D2", "rater": f"r{i}", "y": float(i < 3), "customer": "C", "doc_type": "T"}
                           for i in range(5)])
    t = L.doc_targets(long, {"_posterior": {}})
    d1 = t[t.doc_id == "D1"].iloc[0]
    d2 = t[t.doc_id == "D2"].iloc[0]
    assert d1.n_raters == 5 and d1.n_pass == 5 and d1.pass_ratio == 1.0
    assert d1.y_unanimous == 1 and d1.y_majority == 1
    assert d2.pass_ratio == 0.6 and d2.y_majority == 1 and d2.y_unanimous == 0


def test_unanimous_too_rare_switches_to_majority(settings):
    s = settings
    s.analysis.target = "unanimous"
    t = pd.DataFrame({"doc_id": [f"D{i}" for i in range(20)],
                      "y_unanimous": [1.0] + [0.0] * 19,          # 5% < 가드 15%
                      "y_majority": [1.0] * 10 + [0.0] * 10,
                      "y_ds": [1.0] * 10 + [0.0] * 10,
                      "pass_ratio": [0.5] * 20})
    sel = L.choose_target(t, s)
    assert sel["used"] == "majority" and sel["column"] == "y_majority"
    assert sel["notes"] and "자동 전환" in sel["notes"][0]


def test_unanimous_kept_when_in_band(settings):
    s = settings
    s.analysis.target = "unanimous"
    t = pd.DataFrame({"doc_id": [f"D{i}" for i in range(20)],
                      "y_unanimous": [1.0] * 8 + [0.0] * 12,      # 40% → 가드 안
                      "y_majority": [1.0] * 10 + [0.0] * 10,
                      "y_ds": [1.0] * 10 + [0.0] * 10,
                      "pass_ratio": [0.5] * 20})
    sel = L.choose_target(t, s)
    assert sel["used"] == "unanimous" and not sel["notes"]


# ---------------------------------------------------------------- Dawid-Skene


def test_dawid_skene_recovers_truth_and_rater_quality():
    """합성 데이터: 정확도 다른 평가자 5명 + 10% 결측 → DS 가 다수결보다 정확해야 한다."""
    rng = np.random.default_rng(7)
    n, R = 300, 5
    y = (rng.random(n) < 0.4).astype(int)
    acc = [0.95, 0.90, 0.85, 0.80, 0.55]
    M = np.empty((n, R))
    for r, a in enumerate(acc):
        flip = rng.random(n) > a
        M[:, r] = np.where(flip, 1 - y, y)
    M[rng.random((n, R)) < 0.1] = np.nan

    post, prior, pi = L.dawid_skene(M)
    pred = (post >= 0.5).astype(int)
    mv = (np.nanmean(M, axis=1) >= 0.5).astype(int)

    assert abs(prior[1] - y.mean()) < 0.08
    assert (pred == y).mean() > 0.95
    assert (pred == y).mean() >= (mv == y).mean()
    # 가장 부정확한 평가자가 가장 낮은 민감도를 가져야 한다
    sens = [pi[r, 1, 1] for r in range(R)]
    assert np.argmin(sens) == 4


def test_dawid_skene_all_missing_column():
    """한 평가자가 아무것도 평가하지 않아도 죽지 않아야 한다."""
    M = np.array([[1, 0, np.nan], [1, 1, np.nan], [0, 0, np.nan], [0, 1, np.nan]], dtype=float)
    post, prior, pi = L.dawid_skene(M)
    assert len(post) == 4 and np.all(np.isfinite(post))


def test_agreement_stats_perfect_and_zero():
    perfect = pd.DataFrame([{"doc_id": f"D{d}", "rater": f"r{r}", "y": float(d % 2)}
                            for d in range(10) for r in range(4)])
    a = L.agreement_stats(perfect)
    assert a["krippendorff_alpha"] > 0.99
    assert a["n_raters"] == 4 and a["n_docs"] == 10


def test_strictest_rater_identified():
    rows = []
    for d in range(20):
        rows.append({"doc_id": f"D{d}", "rater": "lenient", "y": 1.0})
        rows.append({"doc_id": f"D{d}", "rater": "strict", "y": float(d < 4)})
    a = L.agreement_stats(pd.DataFrame(rows))
    assert a["strictest_rater"] == "strict"
    assert a["most_lenient_rater"] == "lenient"


def test_filter_group():
    long = pd.DataFrame([
        {"doc_id": "A1", "customer": "CUST_A", "doc_type": "대책서", "rater": "r", "y": 1.0},
        {"doc_id": "B1", "customer": "CUST_B", "doc_type": "대책서", "rater": "r", "y": 0.0},
        {"doc_id": "C1", "customer": "CUST_A", "doc_type": "요구사양서", "rater": "r", "y": 1.0},
    ])
    out = L.filter_group(long, Group("대책서", "CUST_A"), None)
    assert out["doc_id"].tolist() == ["A1"]
