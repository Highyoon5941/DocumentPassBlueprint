"""S5 model — 무엇이 Pass 를 가르는가 (SETUP.md §8.5).

1. 단변량 (주 근거, 소량 데이터에 강함): Pass율 차이, 위험차(RD), Fisher 정확검정, BH-FDR
2. 다변량: LinearSVC / L1 로지스틱 / 얕은 트리 / RuleFit + CV + 순열검정
3. 안정성: 부트스트랩 200회 가중치 부호 일관성
4. 검증셋 확인: 발견셋 효과 방향이 검증셋(40%)에서도 같은가
5. 개념 판정 등급: 확정 / 유력 / 참고 / 기각
6. 평가자별 모델 (라벨 30건 이상)
"""

from __future__ import annotations

import json
import warnings

import numpy as np
import pandas as pd
from scipy.stats import fisher_exact, mannwhitneyu
from sklearn.dummy import DummyClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import (
    LeaveOneOut,
    RepeatedStratifiedKFold,
    cross_val_score,
    permutation_test_score,
)
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.svm import LinearSVC
from sklearn.tree import DecisionTreeClassifier
from statsmodels.stats.multitest import multipletests

from svmtrial.config import Settings
from svmtrial.features import is_binary
from svmtrial.groups import Group, work_dir

warnings.filterwarnings("ignore", category=UserWarning)
warnings.filterwarnings("ignore", category=RuntimeWarning)


# ---------------------------------------------------------------- 데이터 준비


def prepare(X: pd.DataFrame, targets: pd.DataFrame, target_col: str, unstable: list[str] | None = None):
    """X 와 타깃을 doc_id 로 맞추고, 채점 불안정 개념과 상수열을 뺀다."""
    t = targets.dropna(subset=[target_col]).set_index(targets["doc_id"].astype(str))
    common = [d for d in X.index.astype(str) if d in t.index]
    Xi = X.loc[common].copy()
    y = t.loc[common, target_col].astype(float)

    drop = set(unstable or [])
    Xi = Xi.drop(columns=[c for c in Xi.columns if c in drop], errors="ignore")
    # 결측은 0(= '없음')으로 채운다. na 는 "해당 없음"이므로 요소 부재와 같게 다룬다.
    Xi = Xi.fillna(0.0)
    const = [c for c in Xi.columns if Xi[c].nunique(dropna=False) <= 1]
    Xi = Xi.drop(columns=const)
    return Xi, y, {"dropped_unstable": sorted(drop & set(X.columns)), "dropped_constant": const,
                   "n_docs": len(Xi), "n_features": Xi.shape[1]}


# ---------------------------------------------------------------- 1. 단변량


def univariate(X: pd.DataFrame, y: pd.Series, continuous_target: bool = False) -> pd.DataFrame:
    """이진 특징마다 있을 때/없을 때 Pass율, 위험차(RD), Fisher p, BH-FDR q."""
    rows = []
    for c in X.columns:
        col = X[c]
        if is_binary(col):
            with_ = y[col == 1]
            without = y[col == 0]
            n1, n0 = len(with_), len(without)
            if continuous_target:
                p = (mannwhitneyu(with_, without).pvalue if n1 >= 3 and n0 >= 3 else np.nan)
                r1 = float(with_.mean()) if n1 else np.nan
                r0 = float(without.mean()) if n0 else np.nan
            else:
                a, b = int(with_.sum()), int(n1 - with_.sum())
                cc, d = int(without.sum()), int(n0 - without.sum())
                p = fisher_exact([[a, b], [cc, d]]).pvalue if (n1 and n0) else np.nan
                r1 = a / n1 if n1 else np.nan
                r0 = cc / n0 if n0 else np.nan
            rows.append({"feature": c, "kind": "binary", "n_with": n1, "n_without": n0,
                         "rate_with": r1, "rate_without": r0, "rd": (r1 - r0) if n1 and n0 else np.nan,
                         "p_value": p})
        else:
            # 수치형: Pass/Fail 두 군의 분포 차이
            if continuous_target:
                from scipy.stats import spearmanr

                rr = spearmanr(col, y)
                rows.append({"feature": c, "kind": "numeric", "n_with": len(col), "n_without": 0,
                             "rate_with": float(rr.statistic), "rate_without": np.nan,
                             "rd": float(rr.statistic), "p_value": float(rr.pvalue)})
            else:
                g1, g0 = col[y == 1], col[y == 0]
                p = mannwhitneyu(g1, g0).pvalue if len(g1) >= 3 and len(g0) >= 3 else np.nan
                m1 = float(g1.mean()) if len(g1) else np.nan
                m0 = float(g0.mean()) if len(g0) else np.nan
                rows.append({"feature": c, "kind": "numeric", "n_with": len(g1), "n_without": len(g0),
                             "rate_with": m1, "rate_without": m0, "rd": m1 - m0, "p_value": p})
    df = pd.DataFrame(rows)
    ok = df["p_value"].notna()
    df["q_value"] = np.nan
    if ok.any():
        df.loc[ok, "q_value"] = multipletests(df.loc[ok, "p_value"], method="fdr_bh")[1]
    return df.sort_values("p_value", na_position="last").reset_index(drop=True)


# ---------------------------------------------------------------- 2. 다변량


def _make_models(s: Settings) -> dict[str, object]:
    def svc(C: float):
        return Pipeline([("sc", StandardScaler(with_mean=False)),
                         ("m", LinearSVC(C=C, class_weight="balanced", max_iter=20000))])

    models: dict[str, object] = {
        "svm_C0.01": svc(0.01), "svm_C0.1": svc(0.1), "svm_C1": svc(1.0),
        # scikit-learn 1.8 부터 penalty= 는 폐기 예고(1.10 제거)이므로 l1_ratio=1 로 L1 을 지정한다.
        "l1_logistic": Pipeline([("sc", StandardScaler(with_mean=False)),
                                 ("m", LogisticRegression(l1_ratio=1, solver="liblinear",
                                                          class_weight="balanced", max_iter=5000))]),
        "tree_d3": DecisionTreeClassifier(max_depth=3, class_weight="balanced", random_state=0),
        "dummy": DummyClassifier(strategy="stratified", random_state=0),
    }
    try:
        from imodels import RuleFitClassifier

        models["rulefit"] = RuleFitClassifier(max_rules=15, random_state=0)
    except ImportError:
        pass
    return models


def _cv(s: Settings, n: int, y: pd.Series):
    minority = int(min((y == 0).sum(), (y == 1).sum()))
    if n < 40 or minority < s.analysis.cv.n_splits:
        return LeaveOneOut(), "LeaveOneOut"
    return (RepeatedStratifiedKFold(n_splits=s.analysis.cv.n_splits,
                                    n_repeats=s.analysis.cv.n_repeats, random_state=0),
            f"RepeatedStratifiedKFold({s.analysis.cv.n_splits}x{s.analysis.cv.n_repeats})")


def multivariate(s: Settings, X: pd.DataFrame, y: pd.Series) -> dict:
    Xv, yv = X.to_numpy(float), y.to_numpy(int)
    cv, cv_name = _cv(s, len(yv), y)
    out: dict = {"cv": cv_name, "models": {}}
    scoring = "balanced_accuracy"
    for name, model in _make_models(s).items():
        try:
            sc = cross_val_score(model, Xv, yv, cv=cv, scoring=scoring, error_score=np.nan)
            entry = {"balanced_accuracy": round(float(np.nanmean(sc)), 4),
                     "std": round(float(np.nanstd(sc)), 4)}
            if cv_name != "LeaveOneOut":
                auc = cross_val_score(model, Xv, yv, cv=cv, scoring="roc_auc", error_score=np.nan)
                entry["roc_auc"] = round(float(np.nanmean(auc)), 4)
            out["models"][name] = entry
        except (ValueError, RuntimeError) as e:
            out["models"][name] = {"error": str(e)[:150]}

    # 대표 모델 = 선형 SVM 중 CV 성능이 가장 좋은 것
    best = max(
        (k for k in out["models"] if k.startswith("svm_") and "balanced_accuracy" in out["models"][k]),
        key=lambda k: out["models"][k]["balanced_accuracy"], default="svm_C0.1",
    )
    out["primary_model"] = best

    # 순열검정 (§8.5-2)
    try:
        model = _make_models(s)[best]
        score, perm, pval = permutation_test_score(
            model, Xv, yv, cv=cv, scoring=scoring,
            n_permutations=s.analysis.n_permutations, random_state=0, n_jobs=-1,
        )
        out["permutation_test"] = {"score": round(float(score), 4),
                                   "permutation_mean": round(float(np.mean(perm)), 4),
                                   "p_value": round(float(pval), 4),
                                   "n_permutations": s.analysis.n_permutations}
        out["signal"] = bool(pval < 0.05)
    except (ValueError, RuntimeError) as e:
        out["permutation_test"] = {"error": str(e)[:150]}
        out["signal"] = None

    # 대표 모델 가중치
    w = fit_svm_weights(s, X, y, out["primary_model"])
    out["weights"] = {k: round(float(v), 5) for k, v in w["weights"].items()}
    out["intercept"] = round(float(w["intercept"]), 5)
    return out


def fit_svm_weights(s: Settings, X: pd.DataFrame, y: pd.Series, model_name: str = "svm_C0.1") -> dict:
    """전체 데이터로 선형 SVM 을 적합해 가중치 w 와 절편 b 를 돌려준다 (반사실에 쓴다)."""
    C = float(model_name.split("C")[-1]) if "C" in model_name else 0.1
    scaler = StandardScaler(with_mean=False).fit(X.to_numpy(float))
    Xs = scaler.transform(X.to_numpy(float))
    clf = LinearSVC(C=C, class_weight="balanced", max_iter=20000).fit(Xs, y.to_numpy(int))
    # 표준화를 되돌려 원래 특징 단위의 가중치로 만든다 (with_mean=False 이므로 scale 로만 나눈다)
    scale = np.where(scaler.scale_ == 0, 1.0, scaler.scale_)
    w = clf.coef_[0] / scale
    return {"weights": dict(zip(X.columns, w, strict=False)), "intercept": float(clf.intercept_[0]),
            "C": C, "model": model_name}


# ---------------------------------------------------------------- 로지스틱 계열 (SVM 대체)


def fit_logistic(s: Settings, X: pd.DataFrame, y: pd.Series, l2: float = 0.0) -> dict:
    """기본 로지스틱. SVM 과 같은 입력을 받고 **확률**을 돌려준다.

    목표가 "Pass 확률이 높은 가이드라인"이므로 결정값만 주는 SVM 보다 맞는다.

    기본은 벌점 없는 최대우도(MLE)다. statsmodels 의 `fit_regularized` 는 로그우도를
    표본 수로 나눈 뒤 alpha 를 곱하므로, alpha=1 같은 값은 소표본에서 계수를 0 으로
    뭉개버린다. 그래서 **완전분리/비수렴일 때만** 약한 벌점으로 되돌아간다.
    """
    import statsmodels.api as sm
    from statsmodels.tools.sm_exceptions import PerfectSeparationError

    names = ["_intercept", *X.columns]
    Xd = sm.add_constant(X.to_numpy(float), has_constant="add")
    yv = y.to_numpy(float)

    def _mle():
        r = sm.GLM(yv, Xd, family=sm.families.Binomial()).fit()
        return np.asarray(r.params, float), np.asarray(r.pvalues, float), "mle", 0.0

    def _ridge(alpha: float):
        r = sm.GLM(yv, Xd, family=sm.families.Binomial()).fit_regularized(alpha=alpha, L1_wt=0.0)
        return np.asarray(r.params, float), np.full(len(names), np.nan), "ridge", alpha

    if l2 and l2 > 0:
        params, pvals, how, used = _ridge(l2)
    else:
        try:
            params, pvals, how, used = _mle()
            # 완전분리 징후: 계수가 비정상적으로 큼 → 약한 벌점으로 되돌린다
            if not np.all(np.isfinite(params)) or np.max(np.abs(params[1:])) > 25:
                params, pvals, how, used = _ridge(0.01)
        except (PerfectSeparationError, ValueError, np.linalg.LinAlgError):
            params, pvals, how, used = _ridge(0.01)

    coef = dict(zip(names, params, strict=False))
    pv = dict(zip(names, pvals, strict=False))
    return {
        "kind": "logistic",
        "fit": how,
        "l2": used,
        "weights": {k: float(v) for k, v in coef.items() if k != "_intercept"},
        "intercept": float(coef["_intercept"]),
        "odds_ratio": {k: float(np.exp(np.clip(v, -30, 30))) for k, v in coef.items() if k != "_intercept"},
        "p_values": {k: (None if v != v else float(v)) for k, v in pv.items() if k != "_intercept"},
    }


def fit_monotone_logistic(X: pd.DataFrame, y: pd.Series, nonneg_cols: set[str], l2: float = 0.01) -> dict:
    """존재 특징의 계수를 >= 0 으로 제약한다.

    "요소를 추가하는 것이 해를 끼치지 않는다"는 도메인 지식을 제약으로 넣으면,
    템플릿이 '이 섹션을 빼라' 같은 말을 구조적으로 못 하게 된다.
    """
    from scipy.optimize import minimize

    cols = list(X.columns)
    Xm = np.c_[np.ones(len(X)), X.to_numpy(float)]
    yv = y.to_numpy(float)
    lo = [-np.inf] + [0.0 if c in nonneg_cols else -np.inf for c in cols]
    hi = [np.inf] * Xm.shape[1]

    def nll(w: np.ndarray) -> float:
        z = Xm @ w
        return float(np.logaddexp(0, z).sum() - (yv @ z) + l2 * (w[1:] ** 2).sum())

    r = minimize(nll, np.zeros(Xm.shape[1]), method="L-BFGS-B",
                 bounds=list(zip(lo, hi, strict=False)))
    coef = dict(zip(["_intercept", *cols], r.x, strict=False))
    return {
        "kind": "monotone_logistic",
        "weights": {k: float(v) for k, v in coef.items() if k != "_intercept"},
        "intercept": float(coef["_intercept"]),
        "odds_ratio": {k: float(np.exp(v)) for k, v in coef.items() if k != "_intercept"},
        "p_values": dict.fromkeys(cols, None),
        "converged": bool(r.success),
        "n_constrained": len(nonneg_cols & set(cols)),
    }


def fit_hierarchical(
    s: Settings, long: pd.DataFrame, X: pd.DataFrame, feature_cols: list[str],
    rater_interactions: tuple[str, ...] = (),
) -> dict:
    """평가자 수준 계층 로지스틱.

    문서 단위로 이진화하면 평가자 판정 N×R 건이 N 건으로 압축된다. 그 손실을 없애고,
    평가자 개인차(엄격도)와 평가자×특징 상호작용까지 모형에 넣는다.
    """
    import statsmodels.api as sm
    import statsmodels.formula.api as smf

    # long 에 특징 열이 이미 섞여 있어도 안전하도록 필요한 열만 뽑아 병합한다
    # (안 그러면 why5_x / why5_y 같은 접미사가 붙어 수식이 깨진다)
    need = ["doc_id", "rater", "y"]
    missing = [c for c in need if c not in long.columns]
    if missing:
        raise RuntimeError(f"계층 모형: long 테이블에 {missing} 열이 없습니다.")
    d = long[need].copy()
    d["doc_id"] = d["doc_id"].astype(str)
    Xr = X.reset_index()
    Xr["doc_id"] = Xr["doc_id"].astype(str)
    d = d.merge(Xr, on="doc_id", how="inner").dropna(subset=["y"])
    if d.empty or d["y"].nunique() < 2:
        raise RuntimeError("계층 모형: 평가자 수준 데이터가 부족하거나 라벨이 한쪽뿐입니다.")
    d["rater"] = d["rater"].astype(str)
    d["y"] = d["y"].astype(float)
    for c in feature_cols:
        d[c] = d[c].astype(float)

    raters = sorted(d["rater"].unique())
    terms = " + ".join(f"Q('{c}')" for c in feature_cols)
    f = f"y ~ {terms} + C(rater)"
    # 상호작용은 **기준 평가자를 빼고** 넣는다.
    # 전원을 넣으면 Σ_r is_r = 1 이라 주효과 Q('c') 와 완전공선이 되어
    # 설계행렬이 rank-deficient 가 되고 계수가 유일하게 정해지지 않는다.
    # C(rater) 가 첫 평가자를 절편에 흡수하므로 상호작용도 같은 기준을 쓴다.
    ref = raters[0] if raters else None
    for c in rater_interactions:
        if c not in feature_cols:
            continue
        for r in raters:
            if r == ref:
                continue                      # 기준 평가자 = 주효과가 그 역할을 한다
            col = f"is_{_safe(r)}"
            d[col] = (d["rater"] == r).astype(int)
            f += f" + {col}:Q('{c}')"

    res = smf.glm(f, data=d, family=sm.families.Binomial()).fit()
    raw = {str(k): float(v) for k, v in res.params.items()}
    pv = {str(k): (None if v != v else float(v)) for k, v in res.pvalues.items()}
    # Q('name') 표기를 원래 특징 이름으로 되돌린다
    weights = {c: raw.get(f"Q('{c}')", 0.0) for c in feature_cols}
    p_values = {c: pv.get(f"Q('{c}')") for c in feature_cols}
    rater_effect = {r: raw.get(f"C(rater)[T.{r}]", 0.0) for r in raters}
    interactions = {k: v for k, v in raw.items() if ":" in k}
    return {
        "kind": "hierarchical",
        "weights": weights,
        "intercept": raw.get("Intercept", 0.0),
        "odds_ratio": {c: float(np.exp(v)) for c, v in weights.items()},
        "p_values": p_values,
        "rater_effect": rater_effect,
        "rater_interactions": {k: float(v) for k, v in interactions.items()},
        "interaction_p": {k: pv.get(k) for k in interactions},
        "raters": raters,
        "reference_rater": ref,
        "n_obs": int(len(d)),
        "n_params": int(len(raw)),
        "_formula": f,
    }


def _safe(name: str) -> str:
    """평가자 이름을 수식에 쓸 수 있는 식별자로."""
    import re as _re

    return _re.sub(r"\W+", "_", str(name))


def fit_primary(
    s: Settings, X: pd.DataFrame, y: pd.Series, long: pd.DataFrame | None = None,
    concept_cols: list[str] | None = None,
) -> dict:
    """config.analysis.method 에 따라 주 모델을 적합한다. 반환 형태는 공통이다.

    어떤 방법이든 **매 실행마다 주어진 배치로 적합하고 저장하지 않는다**(무상태).
    """
    method = (s.analysis.method or "svm").lower()
    nonneg = {c for c in X.columns if is_binary(X[c])} if s.analysis.monotone else set()

    if method == "hierarchical" and long is not None:
        inter = tuple(concept_cols or [])
        try:
            return fit_hierarchical(s, long, X, list(X.columns), rater_interactions=inter)
        except (RuntimeError, ValueError, np.linalg.LinAlgError) as e:
            out = fit_logistic(s, X, y)
            out["fallback_from"] = f"hierarchical ({type(e).__name__}: {str(e)[:80]})"
            return out
    if method == "logistic":
        return (fit_monotone_logistic(X, y, nonneg) if s.analysis.monotone
                else fit_logistic(s, X, y))
    w = fit_svm_weights(s, X, y, "svm_C0.1")
    # numpy 스칼라를 그대로 두면 하류(objective.json)의 비교 결과가 numpy.bool 이 되어 JSON 저장이 깨진다
    return {"kind": "svm", "weights": {k: float(v) for k, v in w["weights"].items()},
            "intercept": float(w["intercept"]), "odds_ratio": {}, "p_values": dict.fromkeys(X.columns)}


# ---------------------------------------------------------------- 3. 안정성


def bootstrap_stability(s: Settings, X: pd.DataFrame, y: pd.Series, model_name: str) -> pd.DataFrame:
    """부트스트랩으로 SVM 가중치 부호 일관성(%)과 상위 10위 진입 빈도."""
    n_boot = s.analysis.n_bootstrap
    rng = np.random.default_rng(0)
    cols = list(X.columns)
    signs = {c: [] for c in cols}
    top10 = dict.fromkeys(cols, 0)
    n_ok = 0
    for _ in range(n_boot):
        idx = rng.integers(0, len(X), len(X))
        yb = y.iloc[idx]
        if yb.nunique() < 2:
            continue
        try:
            w = fit_svm_weights(s, X.iloc[idx], yb, model_name)["weights"]
        except (ValueError, RuntimeError):
            continue
        n_ok += 1
        for c in cols:
            signs[c].append(np.sign(w[c]))
        for c in sorted(cols, key=lambda k: -abs(w[k]))[:10]:
            top10[c] += 1
    rows = []
    for c in cols:
        arr = np.array(signs[c])
        if len(arr) == 0:
            rows.append({"feature": c, "sign_stability": None, "top10_freq": None, "n_boot_ok": 0})
            continue
        pos, neg = float((arr > 0).mean()), float((arr < 0).mean())
        rows.append({"feature": c, "sign_stability": round(max(pos, neg), 4),
                     "dominant_sign": 1 if pos >= neg else -1,
                     "top10_freq": round(top10[c] / max(1, n_ok), 4), "n_boot_ok": n_ok})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- 4. 검증셋


def validation_check(X: pd.DataFrame, y: pd.Series, split: pd.DataFrame, continuous: bool) -> pd.DataFrame:
    """발견셋에서 찾은 효과 방향(RD 부호)이 검증셋에서도 같은지 (§8.5-4)."""
    sp = dict(zip(split["doc_id"].astype(str), split["split"].astype(str), strict=False))
    idx = X.index.astype(str)
    d_mask = np.array([sp.get(i) == "discovery" for i in idx])
    v_mask = np.array([sp.get(i) == "validation" for i in idx])
    def rd(col: str, mask) -> float:
        """효과 방향 지표. 이진 특징은 Pass율 차이(RD), 수치형은 Pass군-Fail군 평균차."""
        yy, cc = y[mask], X[col][mask]
        if len(yy) == 0:
            return np.nan
        if is_binary(cc):
            a, b = yy[cc == 1], yy[cc == 0]
            return (float(a.mean()) - float(b.mean())) if len(a) and len(b) else np.nan
        hi, lo = cc[yy >= 0.5], cc[yy < 0.5]
        return (float(hi.mean()) - float(lo.mean())) if len(hi) and len(lo) else np.nan

    rows = []
    for c in X.columns:
        rd_d, rd_v = rd(c, d_mask), rd(c, v_mask)
        same = (bool(np.sign(rd_d) == np.sign(rd_v)) if not (np.isnan(rd_d) or np.isnan(rd_v)) else None)
        rows.append({"feature": c, "rd_discovery": rd_d, "rd_validation": rd_v, "same_direction": same,
                     "n_validation": int(v_mask.sum())})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- 5. 등급


def verdicts(s: Settings, uni: pd.DataFrame, stab: pd.DataFrame, val: pd.DataFrame) -> pd.DataFrame:
    v = s.analysis.verdict
    df = uni.merge(stab, on="feature", how="left").merge(val, on="feature", how="left")

    def grade(r) -> tuple[str, str]:
        """(등급, 근거). 등급 기준은 SETUP.md §8.5-5 를 그대로 따른다.

        `유력` 은 'q < 0.25 **또는** 부호 일관성 ≥ 80%' 이므로, 통계적 유의성 없이
        안정성만으로도 올라올 수 있다. 그 경우 근거에 `안정성만` 을 적어
        사람이 🔒 검토에서 구분할 수 있게 한다.
        """
        q = r.get("q_value")
        st = r.get("sign_stability")
        # pandas 는 numpy.bool_ 을 돌려줄 수 있고 `np.True_ is True` 는 False 다.
        # 정체성 비교에 기대지 않고 명시적으로 3상태(True/False/미정)로 환원한다.
        raw_same = r.get("same_direction")
        same = None if raw_same is None or raw_same != raw_same else bool(raw_same)
        q = np.nan if q is None else float(q)
        st = np.nan if st is None else float(st)
        q_ok_c = not np.isnan(q) and q < v.confirmed.q
        st_ok_c = not np.isnan(st) and st >= v.confirmed.stability
        q_ok_l = not np.isnan(q) and q < v.likely.q
        st_ok_l = not np.isnan(st) and st >= v.likely.stability
        if q_ok_c and st_ok_c and same is True:
            return "확정", f"q={q:.3g}<{v.confirmed.q}, 부호일관성={st:.0%}, 검증셋 동일방향"
        if (q_ok_l or st_ok_l) and same is True:
            why = []
            if q_ok_l:
                why.append(f"q={q:.3g}<{v.likely.q}")
            if st_ok_l:
                why.append(f"부호일관성={st:.0%}")
            basis = " + ".join(why) + ", 검증셋 동일방향"
            if not q_ok_l:
                basis += " — 안정성만(통계적 유의성 없음)"
            return "유력", basis
        rd = r.get("rd")
        if rd is not None and not np.isnan(float(rd)) and abs(float(rd)) > v.reference_rd:
            return "참고", f"|RD|={abs(float(rd)):.3f}>{v.reference_rd} (유의성·방향 확인 미달)"
        return "기각", "유의성·안정성·방향 기준 모두 미달"

    graded = df.apply(lambda r: grade(r), axis=1)
    df["verdict"] = [gg[0] for gg in graded]
    df["verdict_basis"] = [gg[1] for gg in graded]
    order = {"확정": 0, "유력": 1, "참고": 2, "기각": 3}
    df["_o"] = df["verdict"].map(order)
    return df.sort_values(["_o", "q_value"], na_position="last").drop(columns="_o").reset_index(drop=True)


# ---------------------------------------------------------------- 6. 평가자별


def per_rater(s: Settings, X: pd.DataFrame, long: pd.DataFrame) -> dict:
    """평가자별 라벨이 min_rater_labels 이상이면 같은 분석을 반복 (§8.5-6)."""
    out: dict = {"raters": {}, "conflicts": [], "union_requirements": []}
    bin_cols = [c for c in X.columns if is_binary(X[c])]
    for rater, grp in long.groupby(long["rater"].astype(str)):
        gg = grp.dropna(subset=["y"])
        ids = [str(d) for d in gg["doc_id"].astype(str) if str(d) in X.index.astype(str)]
        if len(ids) < s.analysis.min_rater_labels:
            out["raters"][rater] = {"n": len(ids), "skipped": "라벨 수 부족"}
            continue
        yv = gg.set_index(gg["doc_id"].astype(str)).loc[ids, "y"].astype(float)
        if yv.nunique() < 2:
            out["raters"][rater] = {"n": len(ids), "skipped": "라벨이 한쪽뿐"}
            continue
        Xi = X.loc[ids, bin_cols]
        u = univariate(Xi, yv)
        pos = u[(u["rd"] > 0) & (u["q_value"].fillna(1) < 0.25)]["feature"].tolist()
        neg = u[(u["rd"] < 0) & (u["q_value"].fillna(1) < 0.25)]["feature"].tolist()
        out["raters"][rater] = {
            "n": len(ids), "pass_rate": round(float(yv.mean()), 4),
            "requires": pos, "penalizes": neg,
            "top": u.head(5)[["feature", "rd", "q_value"]].to_dict("records"),
        }
    # 전원 통과 템플릿 = 각 평가자 요구사항의 합집합 (§8.5-6)
    req: set[str] = set()
    pen: set[str] = set()
    for d in out["raters"].values():
        req |= set(d.get("requires", []))
        pen |= set(d.get("penalizes", []))
    out["union_requirements"] = sorted(req)
    for f in sorted(req & pen):
        who_plus = [r for r, d in out["raters"].items() if f in d.get("requires", [])]
        who_minus = [r for r, d in out["raters"].items() if f in d.get("penalizes", [])]
        out["conflicts"].append(
            f"{f}: {', '.join(who_plus)} 는 있을 때 Pass 쪽, {', '.join(who_minus)} 는 없을 때 Pass 쪽 — 요구가 충돌한다."
        )
    return out


# ---------------------------------------------------------------- 실행


def run(
    s: Settings, g: Group, X: pd.DataFrame, targets: pd.DataFrame, target_col: str,
    split: pd.DataFrame, long: pd.DataFrame, unstable: list[str] | None = None,
) -> dict:
    wd = work_dir(s, "models", g)
    continuous = target_col == "pass_ratio"
    Xi, y, prep = prepare(X, targets, target_col, unstable)
    if Xi.shape[1] == 0:
        raise RuntimeError("사용할 수 있는 특징이 없습니다 (모두 상수이거나 채점 불안정).")
    if not continuous and y.nunique() < 2:
        raise RuntimeError(f"타깃 {target_col} 이 한쪽 값뿐입니다 — 모델을 만들 수 없습니다.")

    # 데이터 양 가드 (§8.5)
    n_minor = int(min((y == 0).sum(), (y == 1).sum())) if not continuous else len(y)
    exploratory = n_minor < s.analysis.min_n_per_feature * Xi.shape[1]

    uni = univariate(Xi, y, continuous_target=continuous)
    multi = multivariate(s, Xi, y) if not continuous else {"skipped": "연속 타깃에는 분류 모델을 쓰지 않는다"}
    primary = multi.get("primary_model", "svm_C0.1")
    stab = (bootstrap_stability(s, Xi, y, primary) if not continuous
            else pd.DataFrame({"feature": Xi.columns, "sign_stability": None, "top10_freq": None}))
    val = validation_check(Xi, y, split, continuous)
    verd = verdicts(s, uni, stab, val)
    if not continuous:
        verd["svm_weight"] = verd["feature"].map(multi.get("weights", {}))
    raters = per_rater(s, Xi, long)

    uni.to_csv(wd / "univariate.csv", index=False, encoding="utf-8")
    verd.to_csv(wd / "concept_verdicts.csv", index=False, encoding="utf-8")
    stab.to_csv(wd / "stability.csv", index=False, encoding="utf-8")

    results = {
        "group": g.key,
        "target_column": target_col,
        "continuous_target": continuous,
        "prepare": prep,
        "n_minor": n_minor,
        "exploratory_only": bool(exploratory),
        "exploratory_reason": (
            f"소수 클래스 {n_minor}건 < {s.analysis.min_n_per_feature} × 특징 {Xi.shape[1]}개 "
            f"= {s.analysis.min_n_per_feature * Xi.shape[1]} → 다변량 결과는 탐색적으로만 본다."
            if exploratory else None
        ),
        "multivariate": multi,
        "verdict_counts": verd["verdict"].value_counts().to_dict(),
        "verdicts": verd.to_dict("records"),
        "per_rater": raters,
        "features": list(Xi.columns),
    }
    (wd / "results.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=1, default=_json_default), encoding="utf-8"
    )
    return results


def _json_default(o):
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.floating):
        return None if np.isnan(o) else float(o)
    if isinstance(o, np.bool_):
        return bool(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    if pd.isna(o):
        return None
    return str(o)


def load_results(s: Settings, g: Group) -> dict:
    f = work_dir(s, "models", g) / "results.json"
    if not f.exists():
        raise FileNotFoundError(f"{f} 가 없습니다 — 먼저 `svmtrial model` 를 실행하세요.")
    return json.loads(f.read_text(encoding="utf-8"))
