"""S7 diagnose — 새 문서 1건 진단 (SETUP.md §8.8).

처리: S1 → S2 → S4c(해당 그룹의 승인 개념) → SVM 점수 → 반사실 보완 목록 → diagnose_<doc_id>.md

평가자들이 이 문서를 실제로 평가하면 그 결과를 평가 시트에 추가해 재학습한다(피드백 루프).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pandas as pd

from svmtrial import labels as L
from svmtrial import modeling
from svmtrial.concepts import load_approved_concepts, load_X, score_doc
from svmtrial.config import Settings
from svmtrial.counterfactual import explain_doc
from svmtrial.features import is_binary, structural_features
from svmtrial.gemini_client import GeminiClient
from svmtrial.groups import Group, work_dir
from svmtrial.ingest import decide_doc_format, render_doc
from svmtrial.ocr import build_outline, ocr_page
from svmtrial.sections import doc_sections, load_approved_taxonomy


def run(s: Settings, g: Group, gc: GeminiClient, pdf: Path, out_dir: Path, force: bool = False) -> dict:
    pdf = Path(pdf)
    if not pdf.exists():
        raise FileNotFoundError(f"PDF 를 찾을 수 없습니다: {pdf}")

    # --- S1 ingest
    pages_dir = s.work / "pages"
    meta = render_doc(s, pdf, pages_dir, force=force)
    meta = decide_doc_format(s, meta, gc, pages_dir)
    (pages_dir / meta.doc_id / "meta.json").write_text(
        json.dumps(meta.to_json(), ensure_ascii=False, indent=1), encoding="utf-8"
    )
    doc_id = meta.doc_id

    # --- S2 ocr
    md = meta.to_json()
    for i, name in enumerate(meta.pages, start=1):
        ocr_page(s, gc, doc_id, i, pages_dir / doc_id / name, md, force=force)
    outline = build_outline(s, doc_id, md)

    # --- 섹션 매핑 (그룹의 승인 분류체계 사용)
    taxonomy = load_approved_taxonomy(s, g)
    ds = doc_sections(s, doc_id, {}, taxonomy)
    (work_dir(s, "sections", g) / f"{doc_id}.sections.json").write_text(
        json.dumps(ds, ensure_ascii=False, indent=1), encoding="utf-8"
    )

    # --- S4c 채점 (그룹의 승인 개념)
    concepts = load_approved_concepts(s, g)
    answers = score_doc(s, g, gc, doc_id, concepts)

    row: dict[str, float] = {}
    for c in concepts:
        v = answers.get(c.id)
        row[c.id] = 1.0 if v == "yes" else (0.0 if v == "no" else float("nan"))
    row.update(structural_features(s, g, doc_id))

    # --- 학습 데이터로 SVM 재적합 후 점수
    results = modeling.load_results(s, g)
    X_train = load_X(s, g)
    targets = L.load_targets(s, g)
    target_col = results["target_column"]
    scoring_meta = json.loads((work_dir(s, "features", g) / "scoring_meta.json").read_text(encoding="utf-8"))
    Xi, y, _ = modeling.prepare(X_train, targets, target_col, scoring_meta.get("unstable_concepts"))
    primary = results.get("multivariate", {}).get("primary_model", "svm_C0.1")
    fit = modeling.fit_svm_weights(s, Xi, y, primary)
    w, b = fit["weights"], fit["intercept"]

    x = pd.Series({k: float(row.get(k, 0.0) if row.get(k, 0.0) == row.get(k, 0.0) else 0.0) for k in Xi.columns})
    actionable = {c.id for c in concepts if c.actionable}
    allowed = {k for k in Xi.columns if k in actionable and is_binary(Xi[k])}
    cf = explain_doc(doc_id, x, w, b, allowed)

    labels_map = {c.id: c.question for c in concepts}
    f = float(cf["f0"])

    # 판독성 가드: OCR 이 아무것도 못 읽었으면 모든 개념이 '없음' 이 되어
    # 결정값이 무의미해진다. 조용히 'Pass 쪽' 이라고 말하지 않는다.
    legib = float(outline["mean_legibility"])
    reliable = legib >= s.ocr.min_legibility
    unreliable_reasons: list[str] = []
    if not reliable:
        unreliable_reasons.append(
            f"평균 판독성 {legib:.2f} < 기준 {s.ocr.min_legibility} — OCR 이 페이지를 읽지 못했다. "
            "개념 채점이 모두 '없음' 으로 나오므로 판정을 신뢰할 수 없다."
        )
        if s.backend == "offline":
            unreliable_reasons.append(
                "현재 백엔드가 `offline` 이다. offline 스텁은 make_dummy_data.py 가 심은 "
                "fixture(키: <doc_id>/p<page>)만 읽을 수 있어 새 문서나 이름이 바뀐 문서를 읽지 못한다. "
                "실제 진단은 `SVMTRIAL_BACKEND=vertex` 로 바꿔 실행해야 한다 (docs/migration_vertexAI.md)."
            )
    if all(v == "no" for v in answers.values()) and answers:
        unreliable_reasons.append("승인된 개념 전부가 '없음' 으로 채점됐다 — 입력 문서나 OCR 을 먼저 확인할 것.")

    out = {
        "doc_id": doc_id,
        "group": g.key,
        "pdf": str(pdf),
        "n_pages": meta.n_pages,
        "doc_format": meta.doc_format,
        "mean_legibility": outline["mean_legibility"],
        "target_column": target_col,
        "decision_value": round(f, 4),
        "side": ("Pass 쪽" if f > 0 else "Fail 쪽") if reliable else "판정 불가",
        "reliable": reliable,
        "unreliable_reasons": unreliable_reasons,
        "answers": answers,
        "missing_sections": ds.get("missing", []),
        "present_sections": ds.get("present", []),
        "counterfactual": cf,
        "primary_model": primary,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"diagnose_{doc_id}.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    md_path = _markdown(s, g, out, concepts, labels_map, w, out_dir)
    out["report"] = str(md_path.relative_to(s.root))
    return out


def _markdown(s, g, out: dict, concepts, labels_map: dict[str, str], w: dict[str, float], out_dir: Path) -> Path:
    from svmtrial.report import md

    cf = out["counterfactual"]
    L_: list[str] = []
    A = L_.append
    A(f"# 문서 진단 — {out['doc_id']}")
    A("")
    if not out.get("reliable", True):
        A("> ## ⚠ 판정 불가 — 결과를 신뢰할 수 없다")
        A(">")
        for r in out.get("unreliable_reasons", []):
            A(f"> - {r}")
        A("")
    A(f"- 그룹: `{out['group']}`  |  원본: `{out['pdf']}`")
    A(f"- 페이지 {out['n_pages']}장 / 형식 {out['doc_format']} / 평균 판독성 {out['mean_legibility']}")
    A(f"- 타깃 기준: `{out['target_column']}`  |  모델: `{out['primary_model']}`")
    A("")
    A(f"## 판정: **{out['side']}** (결정값 f = {out['decision_value']:+.4f})")
    A("")
    A("> f > 0 이면 Pass 쪽, f < 0 이면 Fail 쪽이다. 이 값은 합격을 보장하지 않으며, "
      "과거 평가 데이터에서 학습한 경계선과의 거리다.")
    A("")
    if out["missing_sections"]:
        A("### 빠진 섹션")
        A("")
        for sid in out["missing_sections"]:
            A(f"- `{sid}`")
        A("")
    A("## 보완이 필요한 항목")
    A("")
    if not out.get("reliable", True):
        A("판독에 실패해 보완 목록을 만들 수 없다. 위의 경고를 먼저 해결할 것.")
    elif cf.get("already_pass_side"):
        A("이미 Pass 쪽이다. 추가 보완 없이도 경계선을 넘는다.")
    elif not cf.get("changes"):
        A("바꿀 수 있는(actionable) 항목이 없어 보완 목록을 만들 수 없다.")
    else:
        A(f"아래 **{len(cf['changes'])}개**를 보완하면 Pass 쪽으로 넘어간다 "
          f"(f: {cf['f0']:+.4f} → {cf['f1']:+.4f}, "
          f"{'도달' if cf.get('reached_pass_side') else '⚠ 미도달 — 더 많은 보완 필요'}).")
        A("")
        A("| 순위 | 조치 | 항목 | 기대 이득 | 내용 |")
        A("|---|---|---|---|---|")
        for i, ch in enumerate(cf["changes"], start=1):
            act = "추가" if ch["action"] == "add" else "제거"
            A(f"| {i} | **{act}** | `{ch['feature']}` | {ch['gain']:+.4f} | "
              f"{md(labels_map.get(ch['feature'], ch['feature'])[:70])} |")
    A("")
    A("## 개념 채점 결과")
    A("")
    A("| 개념 | 질문 | 판정 | SVM w |")
    A("|---|---|---|---|")
    for c in concepts:
        v = out["answers"].get(c.id, "—")
        mark = {"yes": "✅ 있음", "no": "❌ 없음", "na": "— 해당없음"}.get(v, v)
        A(f"| `{c.id}` | {md(c.question[:68])} | {mark} | {w.get(c.id, 0):+.4f} |")
    A("")
    A("## 피드백 루프")
    A("")
    A("평가자들이 이 문서를 실제로 평가하면, 그 결과를 평가 시트에 다음 형식으로 추가하고 "
      "`svmtrial labels` 부터 다시 실행해 재학습한다.")
    A("")
    A("| doc_id | customer | doc_type | rater | result |")
    A("|---|---|---|---|---|")
    A(f"| {out['doc_id']} | {g.customer or ''} | {g.doc_type} | (평가자명) | Pass 또는 Fail |")
    A("")
    f = out_dir / f"diagnose_{out['doc_id']}.md"
    f.write_text("\n".join(L_), encoding="utf-8")
    return f


def stage_pdf(src: Path, s: Settings, g: Group) -> Path:
    """진단 대상 PDF 를 data/raw/pdfs/<doc_type>/<customer>/ 로 복사한다(선택)."""
    dst_dir = s.p("raw_pdfs", g.doc_type, g.customer or "_")
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / Path(src).name
    if Path(src).resolve() != dst.resolve():
        shutil.copy2(src, dst)
    return dst
