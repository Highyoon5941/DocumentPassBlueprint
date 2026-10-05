"""S4b/S4c concepts — 개념 가설 생성(discover)과 문서×개념 채점(score). (SETUP.md §8.4)

누수 방지: 그룹 문서를 타깃 기준으로 층화해 발견셋 60% / 검증셋 40% 로 나눈다.
가설은 **발견셋으로만** 만든다. seed 고정, work/split.csv 에 기록.
"""

from __future__ import annotations

import json
import random

import pandas as pd
import yaml

from svmtrial import parts as P
from svmtrial import textutil as tu
from svmtrial.config import Settings
from svmtrial.features import build_matrix, save_matrix
from svmtrial.gemini_client import DryRunHit, GeminiClient, iter_chunks, load_prompt
from svmtrial.groups import Group, work_dir
from svmtrial.ocr import load_outline
from svmtrial.payload import wrap
from svmtrial.schemas import Concept, ConceptAnswers, ConceptCandidates
from svmtrial.sections import GateNotPassed

CANDIDATES = "concepts_candidates.yaml"
APPROVED = "concepts_approved.yaml"


# ---------------------------------------------------------------- split


def make_split(s: Settings, g: Group, targets: pd.DataFrame, target_col: str) -> pd.DataFrame:
    """타깃 기준 층화 분할. 이미 있으면 그대로 쓴다(재현성)."""
    f = work_dir(s, "concepts", g) / "split.csv"
    if f.exists():
        return pd.read_csv(f)
    frac = s.analysis.split.discovery
    rng = random.Random(s.analysis.split.seed)
    rows = []
    df = targets.dropna(subset=[target_col]) if target_col in targets else targets
    for _, grp in df.groupby(df[target_col].astype(str) if target_col in df else "0"):
        ids = sorted(grp["doc_id"].astype(str))
        rng.shuffle(ids)
        n_disc = max(1, round(len(ids) * frac)) if len(ids) > 1 else len(ids)
        for i, d in enumerate(ids):
            rows.append({"doc_id": d, "split": "discovery" if i < n_disc else "validation"})
    out = pd.DataFrame(rows).sort_values("doc_id").reset_index(drop=True)
    out.to_csv(f, index=False, encoding="utf-8")
    return out


def load_split(s: Settings, g: Group) -> pd.DataFrame:
    f = work_dir(s, "concepts", g) / "split.csv"
    if not f.exists():
        raise FileNotFoundError(f"{f} 가 없습니다 — 먼저 `svmtrial concepts discover` 를 실행하세요.")
    return pd.read_csv(f)


# ---------------------------------------------------------------- S4b discover


def _doc_summary(s: Settings, doc_id: str, max_chars: int = 2500) -> dict:
    """가설 생성에 넘길 문서 요약 (섹션 구조, 표 열 이름, 그림 종류, 핵심 줄)."""
    o = load_outline(s, doc_id)
    return {
        "doc_id": doc_id,
        "n_pages": o.get("n_pages"),
        "headings": [h["text"] for h in o["headings"]][:40],
        "tables": [{"caption": t.get("caption"), "columns": t.get("columns")} for t in o["tables"]][:20],
        "figures": [{"type": f.get("type"), "text": f.get("text")} for f in o["figures"]][:20],
        "n_signatures": o.get("n_signatures", 0),
        "outline_text": o.get("outline_text", "")[:max_chars],
    }


def discover(
    s: Settings, g: Group, gc: GeminiClient, targets: pd.DataFrame, target_col: str, force: bool = False
) -> dict:
    """라운드마다 Pass k개 / Fail k개를 뽑아 가설을 만들고, 합쳐서 후보 파일로 쓴다 (🔒 전 단계)."""
    wd = work_dir(s, "concepts", g)
    cand = wd / CANDIDATES
    split = make_split(s, g, targets, target_col)
    disc_ids = set(split.loc[split["split"] == "discovery", "doc_id"].astype(str))

    t = targets.dropna(subset=[target_col]).copy()
    t["doc_id"] = t["doc_id"].astype(str)
    t = t[t["doc_id"].isin(disc_ids)]
    pass_ids = sorted(t.loc[t[target_col] >= (0.5 if target_col == "pass_ratio" else 1), "doc_id"])
    fail_ids = sorted(t.loc[t[target_col] < (0.5 if target_col == "pass_ratio" else 1), "doc_id"])

    if cand.exists() and not force:
        return yaml.safe_load(cand.read_text(encoding="utf-8"))
    if not pass_ids or not fail_ids:
        raise RuntimeError(
            f"발견셋에 Pass({len(pass_ids)}) 또는 Fail({len(fail_ids)}) 문서가 없어 가설을 만들 수 없습니다."
        )

    taxonomy = []
    tax_f = work_dir(s, "sections", g) / "taxonomy_approved.yaml"
    if tax_f.exists():
        taxonomy = (yaml.safe_load(tax_f.read_text(encoding="utf-8")) or {}).get("items", [])

    rounds = s.analysis.discovery.rounds
    k = s.analysis.discovery.k_per_class
    rng = random.Random(s.analysis.split.seed)
    all_concepts: list[dict] = []
    per_round: list[dict] = []

    for r in range(1, rounds + 1):
        ps = rng.sample(pass_ids, min(k, len(pass_ids)))
        fs = rng.sample(fail_ids, min(k, len(fail_ids)))
        prompt = load_prompt(
            s, "concept_discovery.v1",
            max_hypotheses=s.analysis.discovery.max_hypotheses,
            id_prefix="C",
            data_block=wrap({
                "group": g.key,
                "round": r,
                "target_definition": target_col,
                "taxonomy": taxonomy,
                "max_hypotheses": s.analysis.discovery.max_hypotheses,
                "id_start": len(all_concepts) + 1,
                "pass_docs": [_doc_summary(s, d) for d in ps],
                "fail_docs": [_doc_summary(s, d) for d in fs],
            }),
        )
        try:
            res = gc.generate_json(
                model=s.model_for("pro"), prompt_id="concept_discovery.v1",
                parts=[P.text(prompt)], schema=ConceptCandidates,
                cache_key_extra=f"{g.safe}:r{r}",
            )
        except DryRunHit:
            continue
        got = [c.model_dump() for c in res.concepts]
        all_concepts.extend(got)
        per_round.append({"round": r, "pass_docs": ps, "fail_docs": fs, "n_concepts": len(got)})

    if gc.dry_run:
        return {}

    # 라운드 결과 합치고 중복 제거
    merge_prompt = load_prompt(
        s, "concept_merge.v1", min_keep=30, max_keep=60,
        data_block=wrap({"group": g.key, "min_keep": 30, "max_keep": 60, "concepts": all_concepts}),
    )
    merged = gc.generate_json(
        model=s.model_for("pro"), prompt_id="concept_merge.v1",
        parts=[P.text(merge_prompt)], schema=ConceptCandidates, cache_key_extra=g.safe,
    )

    payload = {
        "_안내": [
            "이 파일은 Gemini 가 만든 **개념 후보**다. 사람이 검토(삭제·수정·추가)한 뒤",
            f"같은 폴더에 `{APPROVED}` 로 저장해야 채점(S4c)이 실행된다 (SETUP.md R7).",
            "검토 기준: ① 문서만 보고 예/아니오로 답할 수 있는가 ② 내용 타당성 판단이 섞이지 않았는가",
            "           ③ actionable(템플릿으로 유도 가능)이 맞는가 ④ 중복이 없는가",
        ],
        "group": g.key,
        "target_column": target_col,
        "discovery_only": True,
        "n_discovery_docs": len(disc_ids),
        "n_pass_in_discovery": len(pass_ids),
        "n_fail_in_discovery": len(fail_ids),
        "rounds": per_round,
        "concepts": [c.model_dump() for c in merged.concepts],
    }
    cand.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return payload


def load_approved_concepts(s: Settings, g: Group) -> list[Concept]:
    wd = work_dir(s, "concepts", g)
    f = wd / APPROVED
    if not f.exists():
        raise GateNotPassed(
            f"🔒 승인된 개념 파일이 없습니다: {f}\n"
            f"  1) {wd / CANDIDATES} 를 열어 검토·수정하세요.\n"
            f"  2) `{APPROVED}` 로 저장하거나 `python -m svmtrial approve --group {g.key} --gate concepts` 를 실행하세요.\n"
            "  승인 파일이 없으면 S4c(채점)는 실행되지 않습니다 (SETUP.md §8.4)."
        )
    data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
    items = data.get("concepts", [])
    if not items:
        raise GateNotPassed(f"{f} 의 `concepts` 가 비어 있습니다.")
    return [Concept.model_validate(c) for c in items]


# ---------------------------------------------------------------- S4c score

_IMG_TYPES = {"format", "structure"}


def _needs_pages(c: Concept) -> bool:
    """개념 type 이 format 이거나 표/그림 관련이면 페이지 PNG 도 함께 보낸다 (§8.4c)."""
    if c.type in _IMG_TYPES:
        return True
    label = tu.quoted_phrase(c.question) or c.question
    return bool(tu.token_set(label) & {"표", "그림", "사진", "그래프", "table", "chart", "figure", "photo"})


def score_doc(
    s: Settings, g: Group, gc: GeminiClient, doc_id: str, concepts: list[Concept], shuffle_seed: int | None = None
) -> dict[str, str]:
    """문서 1건 × 개념들 → {concept_id: yes|no|na}. 개념이 많으면 나눠서 호출한다."""
    o = load_outline(s, doc_id)
    order = list(concepts)
    if shuffle_seed is not None:
        random.Random(shuffle_seed).shuffle(order)

    want_imgs = any(_needs_pages(c) for c in order)
    img_parts: list = []
    if want_imgs:
        meta_f = s.work / "pages" / doc_id / "meta.json"
        if meta_f.exists():
            meta = json.loads(meta_f.read_text(encoding="utf-8"))
            for name in meta["pages"][:6]:          # 토큰 절약: 앞 6페이지
                p = s.work / "pages" / doc_id / name
                if p.exists():
                    img_parts.append(P.image(p.read_bytes()))

    answers: dict[str, str] = {}
    pages_payload = [{"page": p["page"], "text": p["text"]} for p in o.get("pages", [])]
    for chunk in iter_chunks(order, s.analysis.concepts_per_scoring_call):
        prompt = load_prompt(
            s, "concept_scoring.v1",
            data_block=wrap({
                "group": g.key,
                "doc_id": doc_id,
                "n_pages": o.get("n_pages"),
                "outline_text": o.get("outline_text", ""),
                "pages": pages_payload,
                "concepts": [{"id": c.id, "question": c.question, "type": c.type,
                              "section_id": c.section_id} for c in chunk],
            }),
        )
        try:
            res = gc.generate_json(
                model=s.model_for("fast"), prompt_id="concept_scoring.v1",
                parts=[P.text(prompt), *img_parts], schema=ConceptAnswers,
                cache_key_extra=f"{doc_id}:{shuffle_seed or 0}:{','.join(c.id for c in chunk)}",
            )
        except DryRunHit:
            continue
        for a in res.answers:
            answers[a.id] = a.value
    return answers


def score(s: Settings, g: Group, gc: GeminiClient, doc_ids: list[str], force: bool = False) -> dict:
    """전 문서 채점 + 신뢰도 점검(무작위 10% 재채점) → work/features/<group>/X.csv"""
    concepts = load_approved_concepts(s, g)
    wd = work_dir(s, "concepts", g)
    fdir = work_dir(s, "features", g)
    raw_f = wd / "scores_raw.json"

    cached: dict[str, dict[str, str]] = {}
    if raw_f.exists() and not force:
        cached = json.loads(raw_f.read_text(encoding="utf-8"))

    todo = [d for d in doc_ids if d not in cached]
    results = gc.map_parallel(lambda d: (d, score_doc(s, g, gc, d, concepts)), todo)
    if gc.dry_run:
        return {"group": g.key, "n_docs": len(doc_ids), "n_concepts": len(concepts), "dry_run": gc.summary()}
    for item in results:
        if item:
            cached[item[0]] = item[1]
    raw_f.write_text(json.dumps(cached, ensure_ascii=False, indent=1), encoding="utf-8")

    # 신뢰도 점검: 무작위 10% 를 다른 순서로 재채점 (§8.4c)
    rng = random.Random(s.analysis.split.seed)
    n_check = max(1, int(len(doc_ids) * s.analysis.scoring_recheck_ratio))
    check_ids = rng.sample(sorted(doc_ids), min(n_check, len(doc_ids)))
    agree_cnt: dict[str, list[int]] = {c.id: [0, 0] for c in concepts}
    for d in check_ids:
        again = score_doc(s, g, gc, d, concepts, shuffle_seed=s.analysis.split.seed + 1)
        for cid, v in again.items():
            if cid in agree_cnt:
                agree_cnt[cid][1] += 1
                agree_cnt[cid][0] += int(cached.get(d, {}).get(cid) == v)
    stability = {cid: (hit / n if n else None) for cid, (hit, n) in agree_cnt.items()}
    unstable = [cid for cid, v in stability.items() if v is not None and v < s.analysis.scoring_min_agreement]

    # 행렬 조립: 개념 0/1 + 구조 특징 (features.py)
    X = build_matrix(s, g, doc_ids, concepts, cached)
    save_matrix(s, g, X)

    meta = {
        "group": g.key,
        "n_docs": len(X),
        "n_concepts": len(concepts),
        "n_structural": len([c for c in X.columns if c.startswith(("STR_", "SEC_"))]),
        "scoring_stability": {k: (round(v, 3) if v is not None else None) for k, v in stability.items()},
        "n_recheck_docs": len(check_ids),
        "unstable_concepts": unstable,
        "na_rate": round(float(X[[c.id for c in concepts]].isna().to_numpy().mean()), 4),
        "yes_rate": {c.id: round(float(X[c.id].mean(skipna=True)), 4) for c in concepts},
        "gemini": gc.summary(),
    }
    (fdir / "scoring_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    return meta


def load_X(s: Settings, g: Group) -> pd.DataFrame:
    """features.load_X 로 위임 (호출부 편의)."""
    from svmtrial.features import load_X as _load

    return _load(s, g)


def load_scoring_meta(s: Settings, g: Group) -> dict:
    f = work_dir(s, "features", g) / "scoring_meta.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
