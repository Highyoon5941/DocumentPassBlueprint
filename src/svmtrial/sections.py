"""S4a sections — 표준 섹션 분류체계와 문서별 섹션 매핑 (SETUP.md §8.4).

1. 그룹 내 모든 문서의 level 1~2 제목을 모은다.
2. Gemini PRO 가 표준 섹션 목록(분류체계)을 만든다. 대책서는 8D 를 seed 로 준다.
3. 🔒 taxonomy_candidates.yaml → 사람 검토 → taxonomy_approved.yaml
4. 매핑: 동의어 사전으로 먼저, 실패한 제목만 Gemini FAST 가 분류.
5. 문서별 sections.json: 섹션 존재 여부, 순서, 차지 페이지 수
"""

from __future__ import annotations

import json
from collections import Counter

import yaml

from svmtrial import parts as P
from svmtrial import textutil as tu
from svmtrial.config import Settings
from svmtrial.gemini_client import DryRunHit, GeminiClient, load_prompt
from svmtrial.groups import Group, work_dir
from svmtrial.ocr import load_outline
from svmtrial.payload import wrap
from svmtrial.schemas import SectionTaxonomy, TitleMappings
from svmtrial.seeds import seed_for

CANDIDATES = "taxonomy_candidates.yaml"
APPROVED = "taxonomy_approved.yaml"


class GateNotPassed(RuntimeError):
    """🔒 사람 검토 관문이 통과되지 않았다 (R7)."""


def collect_titles(s: Settings, doc_ids: list[str], max_level: int = 2) -> list[tuple[str, int]]:
    """그룹 내 level 1~max_level 제목과 등장 문서 수."""
    cnt: Counter[str] = Counter()
    raw: dict[str, str] = {}
    for d in doc_ids:
        o = load_outline(s, d)
        seen = set()
        for h in o["headings"]:
            if (h.get("level") or 1) > max_level:
                continue
            t = tu.strip_numbering(str(h.get("text", "")))
            if len(t) < 2:
                continue
            k = tu.norm(t)
            if k and k not in seen:
                seen.add(k)
                raw.setdefault(k, t)
        for k in seen:
            cnt[k] += 1
    return [(raw[k], c) for k, c in cnt.most_common()]


def propose_taxonomy(s: Settings, g: Group, gc: GeminiClient, doc_ids: list[str], force: bool = False) -> dict:
    """분류체계 후보를 만들어 taxonomy_candidates.yaml 로 쓴다 (🔒 전 단계)."""
    wd = work_dir(s, "sections", g)
    cand = wd / CANDIDATES
    titles = collect_titles(s, doc_ids)
    if cand.exists() and not force:
        return yaml.safe_load(cand.read_text(encoding="utf-8"))

    seed = seed_for(g.doc_type)
    prompt = load_prompt(
        s, "section_taxonomy.v1",
        data_block=wrap({
            "group": g.key,
            "doc_type": g.doc_type,
            "n_docs": len(doc_ids),
            "titles": [t for t, _ in titles],
            "title_doc_counts": {t: c for t, c in titles},
            "seed": seed,
        }),
    )
    try:
        tax = gc.generate_json(
            model=s.model_for("pro"), prompt_id="section_taxonomy.v1",
            parts=[P.text(prompt)], schema=SectionTaxonomy, cache_key_extra=g.safe,
        )
    except DryRunHit:
        return {}

    payload = {
        "_안내": [
            "이 파일은 Gemini 가 만든 **후보**다. 사람이 검토해 수정한 뒤",
            f"같은 폴더에 `{APPROVED}` 로 저장해야 다음 단계가 실행된다 (SETUP.md R7).",
            "항목 삭제·추가·id 변경·동의어 추가 모두 가능하다.",
        ],
        "group": g.key,
        "doc_type": g.doc_type,
        "n_docs": len(doc_ids),
        "observed_titles": [{"title": t, "n_docs": c} for t, c in titles],
        "items": [i.model_dump() for i in tax.items],
    }
    cand.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return payload


def load_approved_taxonomy(s: Settings, g: Group) -> list[dict]:
    wd = work_dir(s, "sections", g)
    f = wd / APPROVED
    if not f.exists():
        raise GateNotPassed(
            f"🔒 승인된 분류체계가 없습니다: {f}\n"
            f"  1) {wd / CANDIDATES} 를 열어 검토·수정하세요.\n"
            f"  2) `{APPROVED}` 로 저장하거나 `python -m svmtrial approve --group {g.key} --gate sections` 를 실행하세요."
        )
    data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
    items = data.get("items", [])
    if not items:
        raise GateNotPassed(f"{f} 의 `items` 가 비어 있습니다.")
    return items


def map_titles(s: Settings, g: Group, gc: GeminiClient, titles: list[str], taxonomy: list[dict]) -> dict[str, str]:
    """동의어 사전 우선 → 실패분만 Gemini FAST (§8.4-5)."""
    mapping: dict[str, str] = {}
    unresolved: list[str] = []
    for t in titles:
        sid = tu.best_section(t, taxonomy)
        if sid != "OTHER":
            mapping[t] = sid
        else:
            unresolved.append(t)

    if unresolved:
        prompt = load_prompt(
            s, "section_map.v1",
            data_block=wrap({"group": g.key, "titles": unresolved, "taxonomy": taxonomy}),
        )
        try:
            res = gc.generate_json(
                model=s.model_for("fast"), prompt_id="section_map.v1",
                parts=[P.text(prompt)], schema=TitleMappings,
                cache_key_extra=f"{g.safe}:{len(unresolved)}",
            )
            for m in res.mappings:
                mapping[m.title] = m.section_id
        except DryRunHit:
            pass
    for t in titles:
        mapping.setdefault(t, "OTHER")
    return mapping


def doc_sections(s: Settings, doc_id: str, mapping: dict[str, str], taxonomy: list[dict]) -> dict:
    """문서 1건의 섹션 존재/순서/분량."""
    o = load_outline(s, doc_id)
    order_ids = [it["id"] for it in taxonomy]
    found: list[dict] = []
    seen: dict[str, dict] = {}
    for h in o["headings"]:
        t = tu.strip_numbering(str(h.get("text", "")))
        sid = mapping.get(t) or tu.best_section(t, taxonomy)
        if sid == "OTHER":
            continue
        if sid not in seen:
            seen[sid] = {"section_id": sid, "title": t, "first_page": h.get("page"),
                         "last_page": h.get("page"), "n_headings": 1,
                         "appear_order": len(seen) + 1}
            found.append(seen[sid])
        else:
            seen[sid]["last_page"] = h.get("page")
            seen[sid]["n_headings"] += 1

    n_pages = max(1, int(o.get("n_pages") or 1))
    for f in found:
        fp, lp = f.get("first_page") or 1, f.get("last_page") or 1
        f["pages_span"] = max(1, int(lp) - int(fp) + 1)

    present = [f["section_id"] for f in found]
    return {
        "doc_id": doc_id,
        "doc_format": o.get("doc_format"),
        "n_pages": n_pages,
        "taxonomy_order": order_ids,
        "sections": found,
        "present": present,
        "missing": [i for i in order_ids if i not in present],
        "order_tau": order_kendall_tau(present, order_ids),
    }


def order_kendall_tau(present: list[str], standard: list[str]) -> float | None:
    """등장 순서가 표준 순서와 맞는 정도 (Kendall τ). 섹션 2개 미만이면 None."""
    idx = {sid: i for i, sid in enumerate(standard)}
    xs = [idx[s] for s in present if s in idx]
    if len(xs) < 2:
        return None
    from scipy.stats import kendalltau

    tau = kendalltau(list(range(len(xs))), xs).statistic
    return None if tau is None or tau != tau else round(float(tau), 4)  # NaN 체크


def run(s: Settings, g: Group, gc: GeminiClient, doc_ids: list[str], force: bool = False) -> dict:
    """후보 생성 → (승인되어 있으면) 매핑까지 진행."""
    wd = work_dir(s, "sections", g)
    cand = propose_taxonomy(s, g, gc, doc_ids, force=force)
    if gc.dry_run:
        return {"group": g.key, "stage": "dry-run", "dry_run": gc.summary()}

    try:
        taxonomy = load_approved_taxonomy(s, g)
    except GateNotPassed as e:
        return {
            "group": g.key,
            "stage": "awaiting_approval",
            "candidates": str((wd / CANDIDATES).relative_to(s.root)),
            "n_items": len(cand.get("items", [])),
            "message": str(e),
        }

    titles = [t for t, _ in collect_titles(s, doc_ids)]
    mapping = map_titles(s, g, gc, titles, taxonomy)
    (wd / "title_mapping.json").write_text(json.dumps(mapping, ensure_ascii=False, indent=1), encoding="utf-8")

    per_doc = {}
    for d in doc_ids:
        ds = doc_sections(s, d, mapping, taxonomy)
        per_doc[d] = ds
        (wd / f"{d}.sections.json").write_text(json.dumps(ds, ensure_ascii=False, indent=1), encoding="utf-8")

    presence = Counter()
    for ds in per_doc.values():
        presence.update(ds["present"])
    summary = {
        "group": g.key,
        "stage": "mapped",
        "n_docs": len(per_doc),
        "taxonomy": [{"id": i["id"], "name": i.get("name", "")} for i in taxonomy],
        "section_presence": {sid: round(presence[sid] / max(1, len(per_doc)), 3) for sid in [i["id"] for i in taxonomy]},
        "n_titles_mapped": sum(1 for v in mapping.values() if v != "OTHER"),
        "n_titles_other": sum(1 for v in mapping.values() if v == "OTHER"),
    }
    (wd / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    return summary


def load_doc_sections(s: Settings, g: Group, doc_id: str) -> dict:
    f = work_dir(s, "sections", g) / f"{doc_id}.sections.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
