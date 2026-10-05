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
