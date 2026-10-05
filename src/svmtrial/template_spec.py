"""S6 template — 섹션 골격 + 검증된 개념 → TemplateSpec (SETUP.md §8.6).

1. 섹션 골격은 **결정론**으로 만든다 (Pass 문서의 섹션 존재율·중앙 순서·분량·대표 표 열).
2. 명세 문구만 Gemini PRO 가 쓴다. 모든 Section/Element 에 `evidence_ids` 가 있어야 하고,
   없으면 오류를 붙여 재요청한다.
3. 통계값·meta·evidence 는 코드가 채운다 (LLM 이 수치를 만들지 못하게).
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime
from statistics import median

import pandas as pd
import yaml

from svmtrial import parts as P
from svmtrial.config import Settings
from svmtrial.gemini_client import DryRunHit, GeminiClient, load_prompt, prompt_versions
from svmtrial.groups import Group, work_dir
from svmtrial.ocr import load_outline
from svmtrial.payload import wrap
from svmtrial.schemas import Concept, Element, Evidence, Section, TemplateSpec, TemplateSpecLLM
from svmtrial.sections import load_doc_sections

GOOD_VERDICTS = ("확정", "유력")


class EvidenceMissing(RuntimeError):
    pass


# ---------------------------------------------------------------- 1. 섹션 골격


def build_skeleton(
    s: Settings, g: Group, pass_ids: list[str], taxonomy: list[dict], concept_sections: dict[str, list[str]]
) -> list[dict]:
    """Pass 문서에서 섹션 골격을 만든다."""
    names = {it["id"]: it.get("name", it["id"]) for it in taxonomy}
    order_std = [it["id"] for it in taxonomy]

    presence: Counter[str] = Counter()
    positions: dict[str, list[int]] = {}
    spans: dict[str, list[float]] = {}
    cols_by_sec: dict[str, Counter] = {}

    for d in pass_ids:
        ds = load_doc_sections(s, g, d)
        if not ds:
            continue
        present = ds.get("present", [])
        for pos, sid in enumerate(present, start=1):
            presence[sid] += 1
            positions.setdefault(sid, []).append(pos)
        for sec in ds.get("sections", []):
            spans.setdefault(sec["section_id"], []).append(float(sec.get("pages_span", 1)))
        o = load_outline(s, d)
        for t in o.get("tables", []):
            page = t.get("page")
            sid = next((sec["section_id"] for sec in ds.get("sections", [])
                        if sec.get("first_page") and sec["first_page"] <= (page or 0) <= (sec.get("last_page") or 0)),
                       None)
            if sid and t.get("columns"):
                cols_by_sec.setdefault(sid, Counter())[tuple(t["columns"])] += 1

    n_pass = max(1, len(pass_ids))
    min_presence = s.template.section_min_presence
    keep: list[dict] = []
    for sid in order_std:
        rate = presence[sid] / n_pass
        has_concept = bool(concept_sections.get(sid))
        if rate < min_presence and not has_concept:
            continue
        span = median(spans[sid]) if spans.get(sid) else 1.0
        top_cols = cols_by_sec.get(sid, Counter()).most_common(1)
        keep.append({
            "section_id": sid,
            "title": names.get(sid, sid),
            "presence_in_pass": round(rate, 3),
            "required": rate >= min_presence,
            "median_position": median(positions[sid]) if positions.get(sid) else 99,
            "pages_hint": round(float(span), 2),
            "slides_hint": max(1, int(round(float(span)))),
            "table_columns": list(top_cols[0][0]) if top_cols else None,
            "concept_ids": concept_sections.get(sid, []),
            "include_reason": ("존재율" if rate >= min_presence else "검증된 개념 보유"),
        })
    keep.sort(key=lambda k: (k["median_position"], order_std.index(k["section_id"])))
    for i, k in enumerate(keep, start=1):
        k["order"] = i
    return keep


def global_hints(s: Settings, g: Group, pass_ids: list[str], verdict_rows: list[dict]) -> list[dict]:
    """문서 전체 규칙 후보 (분량, 서명란 등)."""
    pages = []
    sigs = 0
    for d in pass_ids:
        o = load_outline(s, d)
        pages.append(float(o.get("n_pages") or 0))
        sigs += int(bool(o.get("n_signatures", 0)))
    hints: list[dict] = []
    if pages:
        hints.append({
            "kind": "text", "title": "전체 분량",
            "required": False,
            "guidance": f"[작성 가이드] Pass 문서의 분량 중앙값은 {median(pages):.0f}페이지다. "
                        f"크게 벗어나지 않게 한다.",
            "evidence_ids": ["skeleton"],
        })
    if pass_ids and sigs / len(pass_ids) >= 0.5:
        hints.append({
            "kind": "signature", "title": "작성·검토·승인 서명란",
            "required": sigs / len(pass_ids) >= 0.7,
            "guidance": f"[작성 가이드] Pass 문서 {sigs / len(pass_ids):.0%}에 서명·승인란이 있었다. "
                        "표지 또는 마지막 장에 작성/검토/승인 란을 둔다.",
            "evidence_ids": ["skeleton"],
        })
    for r in verdict_rows:
        if r["verdict"] in GOOD_VERDICTS and str(r["feature"]).startswith("STR_"):
            hints.append({
                "kind": "checklist", "title": f"구조 지표: {r['feature']}",
                "required": r["verdict"] == "확정",
                "guidance": f"[작성 가이드] {_struct_label(r['feature'])} "
                            f"(근거: {r.get('verdict_basis', '')})",
                "evidence_ids": [r["feature"]],
            })
    return hints


_STRUCT_LABELS = {
    "STR_n_pages": "전체 페이지 수가 Pass 문서 수준인지 확인한다.",
    "STR_n_tables": "표의 개수가 Pass 문서 수준인지 확인한다(핵심 분석은 표로 정리한다).",
    "STR_n_figures": "그림·도식의 개수가 Pass 문서 수준인지 확인한다.",
    "STR_n_photos": "현품 사진을 포함한다.",
    "STR_n_charts": "정량 근거를 그래프로 제시한다.",
    "STR_n_headings": "섹션 제목을 충분히 나눠 쓴다.",
    "STR_has_signature": "서명·승인란을 둔다.",
    "STR_order_tau": "섹션 순서를 표준 순서대로 배치한다.",
    "STR_mean_legibility": "스캔 품질(판독성)을 확보한다.",
}


def _struct_label(f: str) -> str:
    if f.startswith("SEC_"):
        return f"{f[4:]} 섹션을 포함한다."
    return _STRUCT_LABELS.get(f, f"{f} 지표를 확인한다.")


# ---------------------------------------------------------------- 2. 명세 작성


def _validate_evidence(spec: TemplateSpecLLM) -> list[str]:
    errs: list[str] = []
    for sec in spec.sections:
        if not sec.evidence_ids:
            errs.append(f"Section {sec.section_id}: evidence_ids 가 비었다")
        for el in sec.elements:
            if not el.evidence_ids:
                errs.append(f"Section {sec.section_id} / Element '{el.title}': evidence_ids 가 비었다")
    for el in spec.global_rules:
        if not el.evidence_ids:
            errs.append(f"global_rules / '{el.title}': evidence_ids 가 비었다")
    return errs


def compose(
    s: Settings, g: Group, gc: GeminiClient, skeleton: list[dict], concept_payload: list[dict],
    hints: list[dict], doc_format: str, rater_conflicts: list[str], max_tries: int = 2,
) -> TemplateSpecLLM:
    """Gemini PRO 가 명세 문구를 쓴다. evidence_ids 가 비면 오류를 붙여 재요청한다 (§8.6-2)."""
    base = wrap({
        "group": g.key,
        "doc_format": doc_format,
        "skeleton": skeleton,
        "concepts": concept_payload,
        "global_hints": hints,
        "rater_conflicts": rater_conflicts,
    })
    extra = ""
    last: list[str] = []
    for attempt in range(1, max_tries + 1):
        prompt = load_prompt(s, "template_compose.v1", data_block=base) + extra
        spec = gc.generate_json(
            model=s.model_for("pro"), prompt_id="template_compose.v1",
            parts=[P.text(prompt)], schema=TemplateSpecLLM,
            cache_key_extra=f"{g.safe}:try{attempt}",
        )
        last = _validate_evidence(spec)
        if not last:
            return spec
        extra = (
            "\n\n## 재요청\n앞선 응답에서 아래 항목의 `evidence_ids` 가 비어 있었다. "
            "모든 Section 과 Element 에 개념 ID 또는 \"skeleton\" 을 넣어 다시 출력하라.\n- "
            + "\n- ".join(last[:20])
        )
    raise EvidenceMissing(f"evidence_ids 누락이 {max_tries}회 재요청 후에도 남았다: {last[:5]}")


# ---------------------------------------------------------------- 3. 통계 채우기


def _fill_stats(
    spec: TemplateSpecLLM, skeleton: list[dict], concept_payload: list[dict]
) -> tuple[list[Section], list[Element]]:
    """LLM 이 쓴 문구에 코드가 통계값과 required 를 덮어쓴다 (LLM 이 수치를 만들지 못하게)."""
    sk = {k["section_id"]: k for k in skeleton}
    cmap = {c["id"]: c for c in concept_payload}
    confirmed = {c["id"] for c in concept_payload if c.get("verdict") == "확정"}

    out: list[Section] = []
    for sec in spec.sections:
        k = sk.get(sec.section_id)
        d = sec.model_dump()
        if k:
            d["presence_in_pass"] = k["presence_in_pass"]
            d["required"] = bool(k["required"])
            d["title"] = k["title"] or d["title"]
            d["pages_hint"] = k.get("pages_hint")
            d["slides_hint"] = k.get("slides_hint")
            d["order"] = k["order"]
        for el in d["elements"]:
            ids = [i for i in el.get("evidence_ids", []) if i]
            el["required"] = bool(set(ids) & confirmed)
            if el.get("kind") == "table" and not el.get("table_columns") and k:
                el["table_columns"] = k.get("table_columns")
            # 통계 구절이 비어 있으면 코드가 붙인다
            for cid in ids:
                c = cmap.get(cid)
                if c and "근거:" not in (el.get("guidance") or "") and c.get("rate_with") is not None:
                    el["guidance"] = (el.get("guidance") or "") + (
                        f" (근거: 있을 때 Pass율 {float(c['rate_with']):.0%} vs 없을 때 "
                        f"{float(c['rate_without']):.0%}, 등급 {c.get('verdict')})"
                    )
                    break
        out.append(Section.model_validate(d))
    out.sort(key=lambda x: x.order)
    for i, sec in enumerate(out, start=1):
        sec.order = i
    return out, [Element.model_validate(e.model_dump()) for e in spec.global_rules]


# ---------------------------------------------------------------- 실행


def run(
    s: Settings, g: Group, gc: GeminiClient, run_id: str, results: dict, targets: pd.DataFrame,
    agreement: dict, concepts: list[Concept], counterfactual: dict | None = None,
) -> TemplateSpec:
    wd = work_dir(s, "models", g)
    target_col = results["target_column"]
    taxonomy = (yaml.safe_load((work_dir(s, "sections", g) / "taxonomy_approved.yaml").read_text(encoding="utf-8"))
                or {}).get("items", [])

    t = targets.dropna(subset=[target_col])
    pos = 0.5 if target_col == "pass_ratio" else 1
    pass_ids = [str(d) for d in t.loc[t[target_col] >= pos, "doc_id"].astype(str)]

    cmeta = {c.id: c for c in concepts}
    verdict_rows = [r for r in results["verdicts"]]
    good = [r for r in verdict_rows if r["verdict"] in GOOD_VERDICTS]

    concept_payload: list[dict] = []
    concept_sections: dict[str, list[str]] = {}
    for r in good:
        f = str(r["feature"])
        c = cmeta.get(f)
        if c is None:
            continue    # 구조 특징은 global_hints 로 간다
        concept_payload.append({
            "id": c.id, "question": c.question, "type": c.type, "section_id": c.section_id,
            "actionable": c.actionable, "verdict": r["verdict"], "verdict_basis": r.get("verdict_basis"),
            "rate_with": r.get("rate_with"), "rate_without": r.get("rate_without"),
            "rd": r.get("rd"), "q_value": r.get("q_value"),
            "svm_weight": r.get("svm_weight"), "sign_stability": r.get("sign_stability"),
            "pass_rate_with": r.get("rate_with"), "pass_rate_without": r.get("rate_without"),
        })
        if c.section_id:
            concept_sections.setdefault(c.section_id, []).append(c.id)

    skeleton = build_skeleton(s, g, pass_ids, taxonomy, concept_sections)
    hints = global_hints(s, g, pass_ids, verdict_rows)
    doc_format = _group_format(s, g)
    conflicts = results.get("per_rater", {}).get("conflicts", [])

    try:
        llm = compose(s, g, gc, skeleton, concept_payload, hints, doc_format, conflicts)
    except DryRunHit:
        raise RuntimeError("--dry-run 에서는 template 단계를 실행할 수 없습니다.") from None

    sections_, globals_ = _fill_stats(llm, skeleton, concept_payload)

    evidence = [
        Evidence(
            concept_id=c["id"], verdict=c["verdict"],
            pass_rate_with=float(c.get("rate_with") or 0.0),
            pass_rate_without=float(c.get("rate_without") or 0.0),
            n_with=int(next((r.get("n_with") or 0 for r in good if r["feature"] == c["id"]), 0)),
            n_without=int(next((r.get("n_without") or 0 for r in good if r["feature"] == c["id"]), 0)),
            q_value=c.get("q_value"), svm_weight=c.get("svm_weight"), sign_stability=c.get("sign_stability"),
        )
        for c in concept_payload
    ]

    caveats = _caveats(s, results, agreement, counterfactual)
    spec = TemplateSpec(
        group=g.key,
        doc_format=doc_format,  # type: ignore[arg-type]
        target_definition=agreement.get("target", {}).get("definition", target_col),
        n_docs=int(results["prepare"]["n_docs"]),
        n_target_pass=len(pass_ids),
        agreement_alpha=agreement.get("krippendorff_alpha"),
        sections=sections_,
        global_rules=globals_,
        rater_conflicts=conflicts,
        caveats=caveats,
        evidence=evidence,
        meta={
            "run_id": run_id,
            "backend": gc.backend_name,
            "model_fast": s.model_for("fast"),
            "model_pro": s.model_for("pro"),
            "prompt_versions": prompt_versions(s),
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "target_column": target_col,
            "exploratory_only": results.get("exploratory_only"),
            "primary_model": results.get("multivariate", {}).get("primary_model"),
            "permutation_p": results.get("multivariate", {}).get("permutation_test", {}).get("p_value"),
        },
    )
    (wd / "template_spec.json").write_text(spec.model_dump_json(indent=1), encoding="utf-8")
    return spec


def _group_format(s: Settings, g: Group) -> str:
    f = s.work / "ingest" / f"{g.safe}.json"
    if f.exists():
        return json.loads(f.read_text(encoding="utf-8")).get("group_doc_format", "docs")
    return "docs"


def _caveats(s: Settings, results: dict, agreement: dict, cf: dict | None) -> list[str]:
    out = [
        "상관관계는 인과관계가 아니다. 여기의 요소들은 Pass 문서에 더 자주 있었다는 뜻이며, "
        "넣으면 반드시 합격한다는 보장이 아니다.",
        "내용의 기술적 타당성(근본원인이 실제로 맞는지)은 판정하지 않았다. 구조와 요소의 유무만 분석했다.",
    ]
    n = results.get("prepare", {}).get("n_docs", 0)
    out.append(f"표본 수 {n}건. 문서 수가 적으면 통계의 폭이 넓어진다.")
    if results.get("exploratory_only"):
        out.append("⚠ " + str(results.get("exploratory_reason")))
    a = agreement.get("krippendorff_alpha")
    if a is not None and a < 0.2:
        out.append(f"⚠ 평가자 일치도가 낮다 (Krippendorff α = {a:.3f}). 공통 템플릿의 근거가 약하다.")
    mv = results.get("multivariate", {})
    if mv.get("signal") is False:
        out.append("⚠ 순열검정에서 모델 신호가 유의하지 않았다 (p ≥ 0.05). 다변량 결과를 신뢰하지 말 것.")
    for w in agreement.get("warnings", []):
        if w not in out:
            out.append(w)
    weak = [r["feature"] for r in results.get("verdicts", []) if "안정성만" in str(r.get("verdict_basis", ""))]
    if weak:
        out.append(
            "다음 항목은 통계적 유의성 없이 부호 안정성만으로 `유력` 이 됐다 — 데이터가 늘면 재확인이 필요하다: "
            + ", ".join(weak[:10])
        )
    return out


def load_spec(s: Settings, g: Group) -> TemplateSpec:
    f = work_dir(s, "models", g) / "template_spec.json"
    if not f.exists():
        raise FileNotFoundError(f"{f} 가 없습니다 — 먼저 `svmtrial template` 를 실행하세요.")
    return TemplateSpec.model_validate_json(f.read_text(encoding="utf-8"))
