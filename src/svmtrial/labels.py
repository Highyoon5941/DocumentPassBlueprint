"""S3 labels — 평가자 일치도와 타깃 (SETUP.md §8.3).

산출물: work/labels/labels_long.{parquet|csv}, doc_targets.csv, agreement.json
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from svmtrial.config import Settings
from svmtrial.groups import Group
from svmtrial.io_utils import load_table, save_table

CANON = ["doc_id", "customer", "doc_type", "rater", "result"]


class LabelError(RuntimeError):
    pass


# ---------------------------------------------------------------- 읽기/정규화


def _norm_value(v: object) -> str:
    import unicodedata

    s = unicodedata.normalize("NFKC", str(v)).strip()
    return s


def _value_map(s: Settings) -> dict[str, int]:
    m: dict[str, int] = {}
    for v in s.labels.pass_values:
        m[_norm_value(v).lower()] = 1
    for v in s.labels.fail_values:
        m[_norm_value(v).lower()] = 0
    return m


def _is_blank(v: object) -> bool:
    return v is None or (isinstance(v, float) and np.isnan(v)) or _norm_value(v) in {"", "nan", "none", "-", "n/a", "na"}


def read_label_files(s: Settings) -> list[Path]:
    d = s.p("labels")
    return sorted([*d.glob("*.xlsx"), *d.glob("*.xls"), *d.glob("*.csv")]) if d.exists() else []


def _read_one(f: Path) -> pd.DataFrame:
    if f.suffix.lower() == ".csv":
        return pd.read_csv(f, dtype=object)
    return pd.read_excel(f, dtype=object)


def to_long(df: pd.DataFrame, s: Settings, source: str) -> pd.DataFrame:
    """long / wide 자동 판별 후 long 으로 통일 (§7.2)."""
    cmap = {k: v for k, v in (s.labels.columns or {}).items()}
    cols = {str(c).strip(): c for c in df.columns}

    def col(name: str) -> object | None:
        want = str(cmap.get(name, name)).strip()
        for c in cols:
            if c.lower() == want.lower():
                return cols[c]
        return None

    c_doc, c_rater, c_res = col("doc_id"), col("rater"), col("result")
    if c_doc is None:
        raise LabelError(f"{source}: doc_id 열을 찾을 수 없습니다 (config.labels.columns 확인).")

    if c_rater is not None and c_res is not None:          # long 형식
        out = pd.DataFrame({
            "doc_id": df[c_doc].map(_norm_value),
            "rater": df[c_rater].map(_norm_value),
            "result": df[c_res],
        })
    else:                                                   # wide 형식 → melt
        meta_cols = [c for c in (col("customer"), col("doc_type")) if c is not None]
        rater_cols = [c for c in df.columns if c not in [c_doc, *meta_cols]]
        if not rater_cols:
            raise LabelError(f"{source}: 평가자 열을 찾을 수 없습니다.")
        out = df.melt(id_vars=[c_doc, *meta_cols], value_vars=rater_cols,
                      var_name="rater", value_name="result")
        out = out.rename(columns={c_doc: "doc_id"})
        out["doc_id"] = out["doc_id"].map(_norm_value)
        out["rater"] = out["rater"].map(_norm_value)

    for name in ("customer", "doc_type"):
        c = col(name)
        if c is not None and name not in out.columns:
            out[name] = df[c].map(_norm_value) if len(df) == len(out) else None
        elif name not in out.columns:
            out[name] = None
    if out["customer"].isna().all() or out["doc_type"].isna().all():
        src = df
        for name in ("customer", "doc_type"):
            c = col(name)
            if c is not None:
                mapping = dict(zip(src[c_doc].map(_norm_value), src[c].map(_norm_value), strict=False))
                out[name] = out["doc_id"].map(mapping)
    out["source_file"] = source
    return out


def normalize(long: pd.DataFrame, s: Settings) -> tuple[pd.DataFrame, list[dict]]:
    vm = _value_map(s)
    errors: list[dict] = []
    ys: list[float] = []
    for _, row in long.iterrows():
        v = row["result"]
        if _is_blank(v):
            ys.append(np.nan)
            continue
        key = _norm_value(v).lower()
        if key in vm:
            ys.append(float(vm[key]))
        else:
            ys.append(np.nan)
            errors.append({"doc_id": row["doc_id"], "rater": row["rater"],
                           "value": _norm_value(v), "source_file": row.get("source_file", "")})
    out = long.copy()
    out["y"] = ys
    return out, errors


def read_labels(s: Settings) -> pd.DataFrame:
    files = read_label_files(s)
    if not files:
        raise LabelError(f"평가 시트가 없습니다: {s.p('labels')} (xlsx/csv)")
    frames = [to_long(_read_one(f), s, f.name) for f in files]
    long = pd.concat(frames, ignore_index=True)
    long, errors = normalize(long, s)
    if errors:
        out = s.work / "labels"
        out.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(errors).to_csv(out / "label_value_errors.csv", index=False, encoding="utf-8")
        vals = sorted({e["value"] for e in errors})[:10]
        raise LabelError(
            f"인식할 수 없는 평가 결과값 {len(errors)}건: {vals} → "
            f"{out / 'label_value_errors.csv'} 확인 후 config.labels.pass_values/fail_values 에 추가하세요."
        )
    dup = long.duplicated(subset=["doc_id", "rater"], keep="first")
    if dup.any():
        long = long[~dup].reset_index(drop=True)
    return long


def filter_group(long: pd.DataFrame, g: Group, s: Settings, doc_ids: set[str] | None = None) -> pd.DataFrame:
    out = long
    if "doc_type" in out.columns and out["doc_type"].notna().any():
        out = out[out["doc_type"].astype(str) == g.doc_type]
    if g.customer and "customer" in out.columns and out["customer"].notna().any():
        out = out[out["customer"].astype(str) == g.customer]
    if doc_ids is not None:
        out = out[out["doc_id"].isin(doc_ids)]
    return out.reset_index(drop=True)


# ---------------------------------------------------------------- Dawid-Skene


def label_matrix(long: pd.DataFrame) -> tuple[np.ndarray, list[str], list[str]]:
    """(문서 × 평가자) 행렬. 미평가는 np.nan."""
    docs = sorted(long["doc_id"].astype(str).unique())
    raters = sorted(long["rater"].astype(str).unique())
    di = {d: i for i, d in enumerate(docs)}
    ri = {r: j for j, r in enumerate(raters)}
    M = np.full((len(docs), len(raters)), np.nan)
    for _, row in long.iterrows():
        if not np.isnan(row["y"]):
            M[di[str(row["doc_id"])], ri[str(row["rater"])]] = row["y"]
    return M, docs, raters


def dawid_skene(M: np.ndarray, n_iter: int = 50, tol: float = 1e-6, eps: float = 1e-9):
    """이진 Dawid-Skene EM (§8.3). 다수결로 초기화.

    반환: (posterior P(y=1) 길이 n_docs, prior, 평가자별 2x2 혼동행렬 pi[r][true][said])
    """
    n_docs, n_raters = M.shape
    obs = ~np.isnan(M)
    # 초기화: 다수결 (전부 결측이면 0.5)
    with np.errstate(invalid="ignore"):
        mv = np.nanmean(np.where(obs, M, np.nan), axis=1)
    T = np.vstack([1 - np.nan_to_num(mv, nan=0.5), np.nan_to_num(mv, nan=0.5)]).T  # (n_docs, 2)
    T = np.clip(T, eps, 1 - eps)

    prior = np.array([0.5, 0.5])
    pi = np.tile(np.array([[0.75, 0.25], [0.25, 0.75]]), (n_raters, 1, 1))
    prev = None
    for _ in range(n_iter):
        # M-step
        prior = T.mean(axis=0)
        prior = np.clip(prior, eps, 1)
        prior /= prior.sum()
        for r in range(n_raters):
            o = obs[:, r]
            if not o.any():
                continue
            said1 = (M[o, r] == 1).astype(float)
            said0 = 1 - said1
            for j in (0, 1):
                w = T[o, j]
                denom = w.sum() + 2 * eps
                pi[r, j, 1] = (w @ said1 + eps) / denom
                pi[r, j, 0] = (w @ said0 + eps) / denom
                ssum = pi[r, j, 0] + pi[r, j, 1]
                pi[r, j, :] /= ssum
        # E-step (로그공간)
        logT = np.log(prior)[None, :].repeat(n_docs, axis=0)
        for r in range(n_raters):
            o = obs[:, r]
            if not o.any():
                continue
            said = M[o, r].astype(int)
            for j in (0, 1):
                logT[o, j] += np.log(np.clip(pi[r, j, said], eps, 1))
        logT -= logT.max(axis=1, keepdims=True)
        T = np.exp(logT)
        T /= T.sum(axis=1, keepdims=True)
        cur = T[:, 1].copy()
        if prev is not None and np.max(np.abs(cur - prev)) < tol:
            break
        prev = cur
    return T[:, 1], prior, pi


# ---------------------------------------------------------------- 일치도


def agreement_stats(long: pd.DataFrame) -> dict:
    import krippendorff
    from sklearn.metrics import cohen_kappa_score
    from statsmodels.stats.inter_rater import aggregate_raters, fleiss_kappa

    M, docs, raters = label_matrix(long)
    obs = ~np.isnan(M)
    n_per_doc = obs.sum(axis=1)

    out: dict = {
        "n_docs": len(docs),
        "n_raters": len(raters),
        "raters": raters,
        "n_ratings": int(obs.sum()),
        "n_raters_per_doc": {int(k): int(v) for k, v in zip(*np.unique(n_per_doc, return_counts=True), strict=False)},
    }

    # Fleiss κ: 평가자 수가 같은 문서만 (최빈값 기준)
    if len(n_per_doc) and n_per_doc.max() >= 2:
        modal = int(np.bincount(n_per_doc.astype(int)).argmax())
        sel = M[n_per_doc == modal]
        out["fleiss_subset_n_raters"] = modal
        out["fleiss_subset_n_docs"] = int(sel.shape[0])
        if sel.shape[0] >= 2 and modal >= 2:
            rows = [[int(np.nansum(r == 0)), int(np.nansum(r == 1))] for r in sel]
            try:
                counts, _ = aggregate_raters(np.array([[0] * int(a) + [1] * int(b) for a, b in rows]))
                out["fleiss_kappa"] = float(fleiss_kappa(counts))
            except (ValueError, ZeroDivisionError) as e:
                out["fleiss_kappa"] = None
                out["fleiss_error"] = str(e)[:120]

    # Krippendorff α (nominal, 결측 허용). reliability_data = 평가자 × 문서
    try:
        out["krippendorff_alpha"] = float(
            krippendorff.alpha(reliability_data=M.T, level_of_measurement="nominal")
        )
    except (ValueError, ZeroDivisionError, AssertionError) as e:
        out["krippendorff_alpha"] = None
        out["krippendorff_error"] = str(e)[:120]

    # 평가자 쌍별 Cohen κ
    pair: dict[str, dict[str, float | None]] = {}
    for i, a in enumerate(raters):
        pair[a] = {}
        for j, b in enumerate(raters):
            if i == j:
                pair[a][b] = 1.0
                continue
            both = obs[:, i] & obs[:, j]
            if both.sum() < 5 or len(set(M[both, i]) | set(M[both, j])) < 2:
                pair[a][b] = None
            else:
                pair[a][b] = round(float(cohen_kappa_score(M[both, i], M[both, j])), 4)
    out["cohen_kappa_pairwise"] = pair

    # 평가자별 엄격도
    strict = {}
    for j, r in enumerate(raters):
        o = obs[:, j]
        strict[r] = {
            "n": int(o.sum()),
            "pass_rate": round(float(np.nanmean(M[o, j])), 4) if o.any() else None,
        }
    out["rater_strictness"] = dict(sorted(strict.items(), key=lambda kv: (kv[1]["pass_rate"] is None, kv[1]["pass_rate"])))
    if strict:
        valid = {k: v["pass_rate"] for k, v in strict.items() if v["pass_rate"] is not None}
        if valid:
            out["strictest_rater"] = min(valid, key=valid.get)
            out["most_lenient_rater"] = max(valid, key=valid.get)

    # Dawid-Skene
    post, prior, pi = dawid_skene(M)
    out["dawid_skene"] = {
        "prior_pass": round(float(prior[1]), 4),
        "confusion": {
            r: {"P(say Pass | true Fail)": round(float(pi[j, 0, 1]), 4),
                "P(say Pass | true Pass)": round(float(pi[j, 1, 1]), 4)}
            for j, r in enumerate(raters)
        },
    }
    out["_posterior"] = {d: float(p) for d, p in zip(docs, post, strict=False)}
    return out


# ---------------------------------------------------------------- 타깃


def doc_targets(long: pd.DataFrame, agree: dict) -> pd.DataFrame:
    post = agree.get("_posterior", {})
    rows = []
    for doc_id, grp in long.groupby(long["doc_id"].astype(str)):
        ys = grp["y"].dropna()
        n = len(ys)
        n_pass = int(ys.sum())
        ratio = n_pass / n if n else np.nan
        rows.append({
            "doc_id": doc_id,
            "customer": next(iter(grp["customer"].dropna()), None) if "customer" in grp else None,
            "doc_type": next(iter(grp["doc_type"].dropna()), None) if "doc_type" in grp else None,
            "n_raters": n,
            "n_pass": n_pass,
            "pass_ratio": round(ratio, 4) if n else np.nan,
            "y_majority": int(ratio >= 0.5) if n else np.nan,
            "y_unanimous": (int(ratio == 1.0) if n >= 3 else np.nan),
            "y_ds": int(post.get(doc_id, np.nan) >= 0.5) if doc_id in post else np.nan,
            "ds_posterior": round(post.get(doc_id, np.nan), 4) if doc_id in post else np.nan,
        })
    return pd.DataFrame(rows).sort_values("doc_id").reset_index(drop=True)


def choose_target(targets: pd.DataFrame, s: Settings) -> dict:
    """config.analysis.target 을 적용하고, unanimous 가 치우치면 majority 로 자동 전환 (§8.3)."""
    want = s.analysis.target
    notes: list[str] = []
    col = {"unanimous": "y_unanimous", "majority": "y_majority", "ds": "y_ds", "ratio": "pass_ratio"}[want]
    used = want
    if want == "unanimous":
        v = targets["y_unanimous"].dropna()
        rate = float(v.mean()) if len(v) else 0.0
        lo, hi = s.analysis.unanimous_guard.low, s.analysis.unanimous_guard.high
        if len(v) == 0 or rate < lo or rate > hi:
            used, col = "majority", "y_majority"
            notes.append(
                f"타깃 자동 전환: 전원 합격 비율 {rate:.1%} 이 가드({lo:.0%}~{hi:.0%}) 밖이라 "
                f"`unanimous` → `majority` 로 바꿨다."
            )
    defs = {
        "unanimous": "평가자 전원이 Pass (평가자 3명 이상인 문서만)",
        "majority": "Pass 비율 ≥ 50%",
        "ds": "Dawid-Skene 사후확률 ≥ 0.5",
        "ratio": "Pass 비율 (연속값)",
    }
    y = targets[col].dropna()
    return {
        "requested": want,
        "used": used,
        "column": col,
        "definition": defs[used],
        "n_labeled": int(len(y)),
        "n_positive": int(y.sum()) if used != "ratio" else None,
        "positive_rate": round(float(y.mean()), 4) if used != "ratio" else round(float(y.mean()), 4),
        "notes": notes,
    }


# ---------------------------------------------------------------- 실행


def run(s: Settings, g: Group, doc_ids: set[str] | None = None) -> dict:
    out_dir = s.work / "labels" / g.safe
    out_dir.mkdir(parents=True, exist_ok=True)

    long_all = read_labels(s)
    long = filter_group(long_all, g, s, doc_ids)
    if long.empty:
        raise LabelError(f"그룹 {g.key} 에 해당하는 평가 행이 없습니다.")

    saved = save_table(long[["doc_id", "customer", "doc_type", "rater", "result", "y", "source_file"]],
                       out_dir / "labels_long")
    agree = agreement_stats(long)
    targets = doc_targets(long, agree)
    targets.to_csv(out_dir / "doc_targets.csv", index=False, encoding="utf-8")
    tsel = choose_target(targets, s)

    alpha = agree.get("krippendorff_alpha")
    warnings_: list[str] = list(tsel["notes"])
    if alpha is not None and alpha < 0.2:
        warnings_.insert(0, (
            f"⚠ 평가자 간 기준이 거의 일치하지 않는다 (Krippendorff α = {alpha:.3f} < 0.2). "
            "공통 템플릿의 근거가 약하다. 평가자별 분석이 주가 되어야 한다."
        ))

    agree_out = {k: v for k, v in agree.items() if not k.startswith("_")}
    agree_out["target"] = tsel
    agree_out["warnings"] = warnings_
    (out_dir / "agreement.json").write_text(json.dumps(agree_out, ensure_ascii=False, indent=1), encoding="utf-8")

    return {
        "group": g.key,
        "labels_table": str(saved.relative_to(s.root)),
        "n_docs": int(len(targets)),
        "n_ratings": agree["n_ratings"],
        "krippendorff_alpha": alpha,
        "fleiss_kappa": agree.get("fleiss_kappa"),
        "strictest_rater": agree.get("strictest_rater"),
        "target": tsel,
        "warnings": warnings_,
    }


def load_targets(s: Settings, g: Group) -> pd.DataFrame:
    f = s.work / "labels" / g.safe / "doc_targets.csv"
    if not f.exists():
        raise FileNotFoundError(f"{f} 가 없습니다 — 먼저 `svmtrial labels` 를 실행하세요.")
    return pd.read_csv(f)


def load_agreement(s: Settings, g: Group) -> dict:
    f = s.work / "labels" / g.safe / "agreement.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}


def load_long(s: Settings, g: Group) -> pd.DataFrame:
    return load_table(s.work / "labels" / g.safe / "labels_long")
