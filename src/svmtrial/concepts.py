"""S4b/S4c concepts — 개념(기준) 후보 생성(discover)과 문서×개념 채점(score). (SETUP.md §8.4)

개념 = 분석의 차원. 후보는 두 곳에서 온다.
  cause      : 평가자가 Judgement 에 남긴 `f(사유)` 를 예/아니오 기준으로 바꾼 것 (id K001…)
  hypothesis : Pass/Fail 문서를 대조해 LLM 이 세운 가설 (id C001…, HypoGeniC 방식)
둘 다 🔒 사람 검토를 거친다.

누수 방지: 그룹 문서를 타깃 기준으로 층화해 발견셋 60% / 검증셋 40% 로 나눈다.
가설과 사유 기준 모두 **발견셋으로만** 만든다. seed 고정, work/split.csv 에 기록.
검증셋에만 나온 사유는 후보 파일에 따로 적어 사람이 볼 수 있게 한다.
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
from svmtrial.sections import GateNotPassed, prompt_version

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


def _cause_items(causes: pd.DataFrame) -> list[dict]:
    """사유 표 → 같은 사유(cause_key)끼리 묶은 목록. 건수 많은 순.

    LLM 에 보내는 값이므로 평가자 이름과 문서명은 넣지 않는다 (평가 시트의 신원 정보는 로컬에만).
    """
    items = []
    for key, grp in causes.groupby("cause_key", sort=False):
        items.append({
            "cause": grp["cause"].value_counts().index[0],
            "variants": sorted(set(grp["cause"])),
            "n_ratings": int(len(grp)),
            "n_docs": int(grp["doc_id"].nunique()),
            "n_raters": int(grp["rater"].nunique()),
            "_key": key,
        })
    items.sort(key=lambda d: (-d["n_ratings"], d["cause"]))
    return items


def cause_criteria(s: Settings, g: Group, gc: GeminiClient, disc_ids: set[str], taxonomy: list[dict]) -> dict:
    """평가자 Fail 사유(발견셋 문서의 것만) → 예/아니오 기준 후보 (id K001…).

    검증셋 문서의 사유는 쓰지 않는다(가설 생성과 같은 누수 방지 규칙). 대신 `validation_only` 로
    목록만 남겨 🔒 검토에서 사람이 볼 수 있게 한다.
    """
    from svmtrial.labels import cause_key, load_causes
    from svmtrial.sections import collect_titles

    causes = load_causes(s, g)
    out: dict = {"used": True, "concepts": [], "n_cause_ratings_discovery": 0, "n_distinct_discovery": 0,
                 "unmapped": [], "validation_only": []}
    if causes.empty:
        out["note"] = "Judgement 에 Fail 사유가 없다 — 가설만 쓴다."
        return out
    in_disc = causes["doc_id"].astype(str).isin(disc_ids)
    disc, val = causes[in_disc], causes[~in_disc]
    items = _cause_items(disc)
    disc_keys = {it["_key"] for it in items}
    out["validation_only"] = sorted({c for c, k in zip(val["cause"], val["cause_key"], strict=False)
                                     if k not in disc_keys})
    out["n_cause_ratings_discovery"] = int(len(disc))
    out["n_distinct_discovery"] = len(items)
    if not items:
        return out

    vocab = [t for t, _ in collect_titles(s, sorted(disc_ids))][:40]
    prompt = load_prompt(
        s, "cause_criteria.v1", id_prefix="K",
        data_block=wrap({
            "group": g.key,
            "id_prefix": "K",
            "taxonomy": taxonomy,
            "document_vocabulary": vocab,
            "causes": [{k: v for k, v in it.items() if not k.startswith("_")} for it in items],
        }),
    )
    try:
        res = gc.generate_json(
            model=s.model_for("pro"), prompt_id="cause_criteria.v1",
            parts=[P.text(prompt)], schema=ConceptCandidates, cache_key_extra=f"{g.safe}:causes",
        )
    except DryRunHit:
        return out

    # LLM 이 사유 원문을 바꿔 적었어도 cause_key 로 원문(표기 변형 전부)에 되돌린다
    by_key: dict[str, list[str]] = {}
    for it in items:
        for v in it["variants"]:
            by_key.setdefault(cause_key(v), []).append(v)
    covered: set[str] = set()
    concepts: list[dict] = []
    for i, c in enumerate(res.concepts, start=1):
        refs: list[str] = []
        for r in c.cause_refs:
            k = cause_key(r)
            if k in by_key and k not in covered:
                refs.extend(by_key[k])
                covered.add(k)
        concepts.append({**c.model_dump(), "id": f"K{i:03d}", "source": "cause", "cause_refs": refs})
    out["concepts"] = concepts
    out["unmapped"] = sorted({v for it in items for v in it["variants"] if cause_key(v) not in covered})
    return out


def _phrase_tokens(c: dict) -> set[str]:
    return tu.token_set(tu.quoted_phrase(c.get("question", "")) or c.get("question", ""))


def drop_duplicates_of_causes(hyps: list[dict], cause_concepts: list[dict],
                              min_overlap: float = 0.8) -> tuple[list[dict], list[dict]]:
    """사유 기준과 같은 것을 묻는 가설을 뺀다. 사유 기준은 평가자의 직접 진술이므로 그쪽을 남긴다.

    같은 것 = 인용 문구 토큰의 포함도(교집합 / 작은 쪽) ≥ min_overlap, 작은 쪽 토큰이 2개 이상.
    뺀 가설은 후보 파일의 `dropped_as_duplicate_of_cause` 에 남아 사람이 되살릴 수 있다.
    사유 기준에 섹션이 없고 겹친 가설에는 있으면 그 섹션을 물려받는다(가설은 문서에서 위치를 봤다).
    """
    if not cause_concepts:
        return hyps, []
    ctoks = [(k, _phrase_tokens(k)) for k in cause_concepts]
    kept, dropped = [], []
    for h in hyps:
        ht = _phrase_tokens(h)
        dup_of = None
        for k, kt in ctoks:
            small = min(len(ht), len(kt))
            if small >= 2 and len(ht & kt) / small >= min_overlap:
                dup_of = k
                break
        if dup_of is not None:
            if not dup_of.get("section_id") and h.get("section_id"):
                dup_of["section_id"] = h["section_id"]
            dropped.append({"id": h["id"], "question": h["question"], "duplicate_of": dup_of["id"]})
        else:
            kept.append(h)
    return kept, dropped


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

    causes_part = (cause_criteria(s, g, gc, disc_ids, taxonomy) if s.analysis.use_causes
                   else {"concepts": [], "used": False})

    rounds = s.analysis.discovery.rounds
    k = s.analysis.discovery.k_per_class
    rng = random.Random(s.analysis.split.seed)
    all_concepts: list[dict] = []
    per_round: list[dict] = []

    for r in range(1, rounds + 1):
        ps = rng.sample(pass_ids, min(k, len(pass_ids)))
        fs = rng.sample(fail_ids, min(k, len(fail_ids)))
        prompt = load_prompt(
            s, prompt_version(s, "concept_discovery"),
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
                model=s.model_for("pro"), prompt_id=prompt_version(s, "concept_discovery"),
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

    hyps = [{**c.model_dump(), "source": "hypothesis", "cause_refs": []} for c in merged.concepts]
    kept, dropped = drop_duplicates_of_causes(hyps, causes_part["concepts"])
    payload = {
        "_안내": [
            "이 파일은 LLM 이 만든 **개념(기준) 후보**다. 사람이 검토(삭제·수정·추가)한 뒤",
            f"같은 폴더에 `{APPROVED}` 로 저장해야 채점(S4c)이 실행된다 (SETUP.md R7).",
            "source: cause = 평가자 Fail 사유에서 온 기준(K…), hypothesis = Pass/Fail 대조 가설(C…)",
            "검토 기준: ① 문서만 보고 예/아니오로 답할 수 있는가 ② 내용 타당성 판단이 섞이지 않았는가",
            "           ③ actionable(템플릿으로 유도 가능)이 맞는가 ④ 중복이 없는가",
            "           ⑤ cause_criteria.validation_only / unmapped 의 사유 중 빠진 기준이 없는가",
        ],
        "group": g.key,
        "target_column": target_col,
        "discovery_only": True,
        "n_discovery_docs": len(disc_ids),
        "n_pass_in_discovery": len(pass_ids),
        "n_fail_in_discovery": len(fail_ids),
        "rounds": per_round,
        "cause_criteria": {k_: v for k_, v in causes_part.items() if k_ != "concepts"},
        "dropped_as_duplicate_of_cause": dropped,
        "concepts": causes_part["concepts"] + kept,
    }
    cand.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")
    if s.analysis.autonomous:
        payload["_autonomous_auto_approved"] = True
        (wd / APPROVED).write_text(
            yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")
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
            s, prompt_version(s, "concept_scoring"),
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
                model=s.model_for("fast"), prompt_id=prompt_version(s, "concept_scoring"),
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

    from svmtrial.labels import load_causes

    meta = {
        "group": g.key,
        "n_docs": len(X),
        "n_concepts": len(concepts),
        "cause_consistency": cause_consistency(concepts, cached, load_causes(s, g)),
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


def cause_consistency(concepts: list[Concept], answers: dict[str, dict[str, str]], causes: pd.DataFrame) -> dict:
    """사유에서 온 기준의 채점이 평가자 진술과 맞는가 (측정 타당성 점검, 문서 단위).

    평가자가 그 사유로 Fail 을 준 문서라면 LLM 채점도 'no'(기준 미충족)여야 한다.
    일치율이 낮으면 질문 문구가 사유를 제대로 옮기지 못했거나 채점이 틀린 것이다.
    반대 방향(Pass 문서 = 충족)은 확인할 수 없다 — Pass 를 준 평가자가 모든 기준을 본 것은 아니다.
    검증셋 문서의 사유도 쓴다. 기준을 **정의**하는 데 쓰지 않고 **측정을 점검**하는 데만 쓰기 때문이다.
    """
    from svmtrial.labels import cause_key

    per: dict[str, dict] = {}
    hit_all = n_all = 0
    for c in concepts:
        if c.source != "cause" or not c.cause_refs or causes.empty:
            continue
        keys = {cause_key(r) for r in c.cause_refs}
        docs = sorted(set(causes.loc[causes["cause_key"].isin(keys), "doc_id"].astype(str)))
        hit = sum(1 for d in docs if answers.get(d, {}).get(c.id) == "no")
        per[c.id] = {"n_docs_cited": len(docs), "n_scored_no": hit,
                     "agreement": round(hit / len(docs), 3) if docs else None}
        hit_all += hit
        n_all += len(docs)
    low = [cid for cid, v in per.items()
           if v["agreement"] is not None and v["n_docs_cited"] >= 2 and v["agreement"] < 0.5]
    return {"overall": round(hit_all / n_all, 3) if n_all else None, "n_doc_cause_pairs": n_all,
            "per_concept": per, "low_agreement": low}


def load_X(s: Settings, g: Group) -> pd.DataFrame:
    """features.load_X 로 위임 (호출부 편의)."""
    from svmtrial.features import load_X as _load

    return _load(s, g)


def load_scoring_meta(s: Settings, g: Group) -> dict:
    f = work_dir(s, "features", g) / "scoring_meta.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
