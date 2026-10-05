"""analysis_report.md 와 차트 (SETUP.md §8.7).

섹션 순서:
1. 요약  2. 데이터 품질  3. 평가자 일치도와 엄격도  4. Pass를 가르는 요인
5. 모델 성능과 순열검정  6. 평가자별 차이와 충돌  7. 자주 필요한 보완 항목  8. 한계와 주의사항
"""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from svmtrial.config import Settings  # noqa: E402
from svmtrial.groups import Group  # noqa: E402
from svmtrial.schemas import TemplateSpec  # noqa: E402

VERDICT_ORDER = ["확정", "유력", "참고", "기각"]


def _font_setup() -> str | None:
    """한글 글꼴을 찾아 matplotlib 에 설정한다. 없으면 라벨을 ASCII 로 쓴다."""
    from matplotlib import font_manager

    for name in ("Malgun Gothic", "NanumGothic", "Noto Sans CJK KR", "Noto Sans KR",
                 "AppleGothic", "UnDotum", "DejaVu Sans"):
        try:
            path = font_manager.findfont(font_manager.FontProperties(family=name), fallback_to_default=False)
        except Exception:  # noqa: BLE001 - findfont 는 여러 예외를 던진다
            continue
        if path:
            plt.rcParams["font.family"] = name
            plt.rcParams["axes.unicode_minus"] = False
            return name
    return None


def forest_plot(verdicts: list[dict], out: Path, labels: dict[str, str], top: int = 15) -> Path | None:
    rows = [r for r in verdicts if r.get("rd") is not None and r["verdict"] != "기각"][:top]
    if not rows:
        return None
    fig, ax = plt.subplots(figsize=(9, max(3, 0.42 * len(rows))))
    ys = range(len(rows))
    colors = {"확정": "#1f77b4", "유력": "#5fa2dd", "참고": "#bbbbbb"}
    ax.barh(list(ys), [float(r["rd"]) for r in rows],
            color=[colors.get(r["verdict"], "#dddddd") for r in rows])
    ax.axvline(0, color="black", lw=0.8)
    ax.set_yticks(list(ys))
    ax.set_yticklabels([f"[{r['verdict']}] {labels.get(r['feature'], r['feature'])[:46]}" for r in rows], fontsize=8)
    ax.invert_yaxis()
    ax.set_xlabel("위험차 RD = Pass율(있음) − Pass율(없음)")
    ax.set_title("Pass를 가르는 요인 (효과크기)")
    fig.tight_layout()
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=140)
    plt.close(fig)
    return out


def weight_plot(weights: dict[str, float], out: Path, labels: dict[str, str], top: int = 15) -> Path | None:
    items = sorted(weights.items(), key=lambda kv: -abs(kv[1]))[:top]
    if not items:
        return None
    fig, ax = plt.subplots(figsize=(9, max(3, 0.42 * len(items))))
    ax.barh([labels.get(k, k)[:46] for k, _ in items], [v for _, v in items],
            color=["#1f77b4" if v > 0 else "#d62728" for _, v in items])
    ax.axvline(0, color="black", lw=0.8)
    ax.invert_yaxis()
    ax.set_xlabel("선형 SVM 가중치 w (양수 = Pass 쪽)")
    ax.set_title("선형 SVM 가중치")
    ax.tick_params(axis="y", labelsize=8)
    fig.tight_layout()
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=140)
    plt.close(fig)
    return out


def md(v) -> str:
    """마크다운 표 셀 안전화: `|` 와 줄바꿈이 표를 깨뜨리지 않게 한다."""
    t = "" if v is None else str(v)
    return t.replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ").strip()


def _is_binary_row(r: dict) -> bool:
    """단변량 결과 행이 이진 특징인가. `kind` 가 없으면 이진으로 본다(개념은 모두 이진)."""
    return str(r.get("kind", "binary")) == "binary"


def _rate(r: dict, which: str) -> str:
    """이진 특징은 Pass율(%), 수치형은 군 평균(수치)로 표시한다.

    수치형의 rate_with/rate_without 은 Pass율이 아니라 **Pass군/Fail군의 특징 평균**이다.
    이것을 %로 찍으면 "Pass율 159%" 같은 틀린 문장이 나온다.
    """
    v = r.get(f"rate_{which}")
    if v is None:
        return "—"
    return _fmt(v, pct=True) if _is_binary_row(r) else _fmt(v, nd=3)


def _effect_sentence(r: dict, label: str) -> str:
    """핵심 결론 한 줄."""
    if _is_binary_row(r):
        return (f"**{md(label)}** — 있을 때 Pass율 {_rate(r, 'with')} vs 없을 때 "
                f"{_rate(r, 'without')} (등급 {r['verdict']})")
    return (f"**{md(label)}** — Pass 쪽 평균 {_rate(r, 'with')} vs Fail 쪽 평균 "
            f"{_rate(r, 'without')} (수치형 특징, 등급 {r['verdict']})")


def _fmt(v, pct: bool = False, nd: int = 3) -> str:
    if v is None:
        return "—"
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    if f != f:
        return "—"
    return f"{f:.0%}" if pct else f"{f:.{nd}g}"


def render(
    s: Settings, g: Group, out_dir: Path, run_id: str, spec: TemplateSpec, results: dict,
    agreement: dict, ingest: dict, ocr_summary: dict, scoring: dict, counterfactual: dict | None,
    concept_labels: dict[str, str],
) -> Path:
    font = _font_setup()
    charts = out_dir / "charts"
    fp = forest_plot(results.get("verdicts", []), charts / "forest.png", concept_labels)
    wp = weight_plot(results.get("multivariate", {}).get("weights", {}), charts / "weights.png", concept_labels)

    mv = results.get("multivariate", {})
    perm = mv.get("permutation_test", {})
    L: list[str] = []
    A = L.append

    # 1. 요약
    A(f"# 분석 리포트 — {g.key}")
    A("")
    A(f"- run_id: `{run_id}`  |  백엔드: `{spec.meta.get('backend')}`  |  생성: {spec.meta.get('created_at')}")
    A(f"- 모델: FAST=`{spec.meta.get('model_fast')}`, PRO=`{spec.meta.get('model_pro')}`")
    A("")
    A("## 1. 요약")
    A("")
    A("| 항목 | 값 |")
    A("|---|---|")
    A(f"| 그룹 | {md(g.key)} |")
    A(f"| 문서 수 | {results['prepare']['n_docs']}건 (원본 PDF {ingest.get('n_pdfs', '—')}건) |")
    A(f"| 원본 형식 | {spec.doc_format} |")
    A(f"| 타깃 정의 | {md(spec.target_definition)} (`{results['target_column']}`) |")
    A(f"| Pass 문서 수 | {spec.n_target_pass}건 |")
    A(f"| 특징 수 | {results['prepare']['n_features']}개 |")
    A("")
    vc = results.get("verdict_counts", {})
    A("**핵심 결론 3줄**")
    A("")
    conf = [r for r in results.get("verdicts", []) if r["verdict"] in ("확정", "유력")]
    top3 = conf[:3]
    if top3:
        for i, r in enumerate(top3, start=1):
            A(f"{i}. " + _effect_sentence(r, concept_labels.get(r["feature"], r["feature"])))
    else:
        A("1. `확정`/`유력` 등급의 요인을 찾지 못했다. 데이터를 늘리거나 개념을 다시 검토해야 한다.")
    A("")
    if results.get("exploratory_only"):
        A(f"> ⚠ **{results.get('exploratory_reason')}**")
        A("")
    for w in agreement.get("warnings", []):
        A(f"> {w}")
        A("")

    # 2. 데이터 품질
    A("## 2. 데이터 품질")
    A("")
    A("| 항목 | 값 |")
    A("|---|---|")
    A(f"| PDF 수 / 처리된 문서 수 | {ingest.get('n_pdfs', '—')} / {ingest.get('n_docs', '—')} |")
    A(f"| 총 페이지 수 | {ingest.get('n_pages', '—')} |")
    A(f"| 원본 형식 판정 근거 | {ingest.get('format_sources', {})} |")
    A(f"| 텍스트 레이어 있는 문서 | {ingest.get('n_with_text_layer', '—')}건 (스캔본 전제이므로 무시) |")
    A(f"| 평균 판독성 | {_fmt(ocr_summary.get('mean_legibility'))} |")
    A(f"| 판독 불량 페이지 | {ocr_summary.get('n_low_legibility_pages', '—')}개 |")
    A(f"| 판독 불량 문서 | {', '.join(ocr_summary.get('docs_low_legibility', [])) or '없음'} |")
    A(f"| 매칭 실패(시트↔PDF) | {ingest.get('n_issues', 0)}건"
      + (" — `work/ingest_issues.csv`" if ingest.get("n_issues") else "") + " |")
    A(f"| 개념 채점 na 비율 | {_fmt(scoring.get('na_rate'), pct=True)} |")
    A(f"| 채점 불안정 개념 (일치율 < {s.analysis.scoring_min_agreement:.0%}) | "
      f"{', '.join(scoring.get('unstable_concepts', [])) or '없음'} |")
    A("")

    # 3. 평가자 일치도
    A("## 3. 평가자 일치도와 엄격도")
    A("")
    A(f"- Krippendorff α (nominal, 결측 허용): **{_fmt(agreement.get('krippendorff_alpha'))}**")
    A(f"- Fleiss κ (평가자 {agreement.get('fleiss_subset_n_raters', '—')}명인 문서 "
      f"{agreement.get('fleiss_subset_n_docs', '—')}건): **{_fmt(agreement.get('fleiss_kappa'))}**")
    A(f"- 총 평가 수: {agreement.get('n_ratings')} / 평가자 {agreement.get('n_raters')}명")
    A("")
    A("### 평가자별 엄격도 (Pass율이 낮을수록 엄격)")
    A("")
    A("| 평가자 | 평가 수 | Pass율 | P(Pass라 함 \\| 실제 Pass) | P(Pass라 함 \\| 실제 Fail) |")
    A("|---|---|---|---|---|")
    ds = agreement.get("dawid_skene", {}).get("confusion", {})
    for r, v in agreement.get("rater_strictness", {}).items():
        c = ds.get(r, {})
        A(f"| {md(r)} | {v.get('n')} | {_fmt(v.get('pass_rate'), pct=True)} | "
          f"{_fmt(c.get('P(say Pass | true Pass)'), pct=True)} | {_fmt(c.get('P(say Pass | true Fail)'), pct=True)} |")
    A("")
    A(f"- 가장 엄격: **{agreement.get('strictest_rater', '—')}**  |  "
      f"가장 관대: **{agreement.get('most_lenient_rater', '—')}**")
    A("")
    A("### 평가자 쌍별 Cohen κ")
    A("")
    pw = agreement.get("cohen_kappa_pairwise", {})
    if pw:
        rs = list(pw)
        A("| | " + " | ".join(rs) + " |")
        A("|---|" + "---|" * len(rs))
        for a in rs:
            A(f"| **{a}** | " + " | ".join(_fmt(pw[a].get(b)) for b in rs) + " |")
    A("")

    # 4. Pass를 가르는 요인
    A("## 4. Pass를 가르는 요인")
    A("")
    A("등급 기준 (SETUP.md §8.5-5): `확정` q<0.1 & 부호일관성≥90% & 검증셋 동일방향 / "
      "`유력` (q<0.25 **또는** 부호일관성≥80%) & 검증셋 동일방향 / `참고` |RD|>0.15 / `기각` 그 외")
    A("")
    A("등급 분포: " + ", ".join(f"**{k}** {vc.get(k, 0)}개" for k in VERDICT_ORDER))
    A("")
    for grade in VERDICT_ORDER:
        rows = [r for r in results.get("verdicts", []) if r["verdict"] == grade]
        if not rows:
            continue
        A(f"### {grade} ({len(rows)}개)")
        A("")
        A("> 이진 특징: `있음 쪽`/`없음 쪽` = 그 요소가 있을 때/없을 때의 **Pass율**, 효과 = 위험차 RD. "
          "수치 특징: Pass 쪽/Fail 쪽 문서의 **특징 평균**, 효과 = 평균차.")
        A("")
        A("| 특징 | 종류 | 설명 | n(있음/없음) | 있음 쪽 | 없음 쪽 | 효과(RD/평균차) | q | 부호안정성 | 검증셋 | SVM w | 근거 |")
        A("|---|---|---|---|---|---|---|---|---|---|---|---|")
        for r in rows[:25]:
            kind = "이진" if _is_binary_row(r) else "수치"
            A(f"| `{r['feature']}` | {kind} | {md(concept_labels.get(r['feature'], '')[:60])} | "
              f"{r.get('n_with')}/{r.get('n_without')} | {_rate(r, 'with')} | "
              f"{_rate(r, 'without')} | {_fmt(r.get('rd'))} | {_fmt(r.get('q_value'))} | "
              f"{_fmt(r.get('sign_stability'), pct=True)} | "
              f"{'동일' if r.get('same_direction') else ('반대' if r.get('same_direction') is False else '—')} | "
              f"{_fmt(r.get('svm_weight'))} | {md(r.get('verdict_basis', ''))} |")
        A("")
    if fp:
        A(f"![효과크기 forest plot](charts/{fp.name})")
        A("")
    if not font:
        A("> ⚠ 한글 글꼴을 찾지 못해 차트 라벨이 깨질 수 있다. "
          "`fonts-nanum`(Ubuntu) 또는 맑은 고딕(Windows)을 설치하면 해결된다.")
        A("")

    # 5. 모델 성능
    A("## 5. 모델 성능과 순열검정")
    A("")
    A(f"- 교차검증: {mv.get('cv', '—')}  |  대표 모델: `{mv.get('primary_model', '—')}`")
    A("")
    A("| 모델 | balanced accuracy | std | ROC-AUC |")
    A("|---|---|---|---|")
    for name, v in (mv.get("models") or {}).items():
        if "error" in v:
            A(f"| `{name}` | 오류: {md(v['error'][:60])} | | |")
        else:
            A(f"| `{name}` | {_fmt(v.get('balanced_accuracy'))} | {_fmt(v.get('std'))} | {_fmt(v.get('roc_auc'))} |")
    A("")
    if perm and "error" not in perm:
        A(f"- 순열검정: 실제 점수 **{_fmt(perm.get('score'))}** vs 순열 평균 {_fmt(perm.get('permutation_mean'))}, "
          f"p = **{_fmt(perm.get('p_value'))}** (n={perm.get('n_permutations')})")
        A(f"- 판정: **{'모델 신호 있음' if results.get('multivariate', {}).get('signal') else '⚠ 모델 신호 없음 (p ≥ 0.05)'}**")
    A("")
    if wp:
        A(f"![SVM 가중치](charts/{wp.name})")
        A("")

    # 6. 평가자별
    A("## 6. 평가자별 차이와 충돌")
    A("")
    pr = results.get("per_rater", {})
    A("**전원 통과 템플릿 = 각 평가자 요구사항의 합집합** (SETUP.md §8.5-6)")
    A("")
    A("| 평가자 | 분석 라벨 수 | Pass율 | 요구 항목(있을 때 Pass) | 역방향 항목 |")
    A("|---|---|---|---|---|")
    for r, v in (pr.get("raters") or {}).items():
        if v.get("skipped"):
            A(f"| {md(r)} | {v.get('n')} | — | (건너뜀: {md(v['skipped'])}) | |")
        else:
            A(f"| {md(r)} | {v.get('n')} | {_fmt(v.get('pass_rate'), pct=True)} | "
              f"{', '.join(f'`{x}`' for x in v.get('requires', [])) or '—'} | "
              f"{', '.join(f'`{x}`' for x in v.get('penalizes', [])) or '—'} |")
    A("")
    A(f"- 합집합 요구사항: {', '.join(f'`{x}`' for x in pr.get('union_requirements', [])) or '—'}")
    A("")
    if pr.get("conflicts"):
        A("**⚠ 평가자 간 요구 충돌**")
        A("")
        for c in pr["conflicts"]:
            A(f"- {c}")
    else:
        A("- 평가자 간 요구 충돌은 발견되지 않았다.")
    A("")

    # 7. 반사실
    A("## 7. 자주 필요한 보완 항목 (반사실 분석)")
    A("")
    if counterfactual:
        A(f"- Fail 쪽 문서 {counterfactual['n_fail_docs']}건 중 "
          f"{counterfactual['n_reached_pass_side']}건은 아래 항목만 보완하면 Pass 쪽으로 넘어간다.")
        A(f"- 문서당 평균 필요 변경 수: **{counterfactual['mean_changes_needed']}개**")
        A("")
        A("| 순위 | 조치 | 특징 | 해당 문서 수 | Fail 중 비율 | 내용 |")
        A("|---|---|---|---|---|---|")
        for i, r in enumerate(counterfactual.get("ranking", [])[:15], start=1):
            act = "추가" if r["action"] == "add" else "제거"
            A(f"| {i} | {act} | `{r['feature']}` | {r['n_docs']} | {r['share_of_fail']:.0%} | {md(r['label'][:64])} |")
    else:
        A("- 반사실 분석을 실행하지 않았다 (연속 타깃이거나 선형 모델이 없음).")
    A("")

    # 8. 한계
    A("## 8. 한계와 주의사항")
    A("")
    for c in spec.caveats:
        A(f"- {c}")
    A("")
    A("---")
    A("")
    A("### 산출물")
    A("")
    A("| 파일 | 내용 |")
    A("|---|---|")
    A("| `template_spec.json` | 기계가 읽는 템플릿 명세 (근거 ID 포함) |")
    A("| `template_spec.md` | 사람이 읽는 템플릿 명세 |")
    A(f"| `template.{'pptx' if spec.doc_format == 'slides' else 'docx'}` | 바로 쓰는 템플릿 파일 |")
    A("| `analysis_report.md` | 이 리포트 |")
    A("")

    out_dir.mkdir(parents=True, exist_ok=True)
    f = out_dir / "analysis_report.md"
    f.write_text("\n".join(L), encoding="utf-8")
    return f


def spec_markdown(spec: TemplateSpec, out: Path) -> Path:
    """template_spec.md — 사람이 읽는 템플릿 명세 (§8.6-3)."""
    L: list[str] = []
    A = L.append
    A(f"# 표준 템플릿 명세 — {spec.group}")
    A("")
    A(f"- 원본 형식: **{spec.doc_format}**  |  타깃: {spec.target_definition}")
    A(f"- 근거 문서: {spec.n_docs}건 (그중 Pass {spec.n_target_pass}건)  |  "
      f"평가자 일치도 α = {_fmt(spec.agreement_alpha)}")
    A(f"- run_id `{spec.meta.get('run_id')}` / 백엔드 `{spec.meta.get('backend')}` / "
      f"생성 {spec.meta.get('created_at')}")
    A("")
    A("> 이 문서는 **템플릿**이다. 실제 내용(수치, 원인, 대책)은 들어 있지 않다. "
      "플레이스홀더와 작성 가이드만 담겨 있다.")
    A("")
    if spec.global_rules:
        A("## 0. 문서 전체 규칙")
        A("")
        A("| 필수 | 종류 | 항목 | 작성 가이드 | 근거 |")
        A("|---|---|---|---|---|")
        for el in spec.global_rules:
            A(f"| {'☑' if el.required else '☐'} | {el.kind} | {md(el.title)} | {md(el.guidance)} | "
              f"{', '.join(f'`{e}`' for e in el.evidence_ids)} |")
        A("")
    A("## 섹션 구조")
    A("")
    A("| 순서 | 섹션 | 필수 | Pass 존재율 | 권장 분량 | 요소 수 |")
    A("|---|---|---|---|---|---|")
    for sec in spec.sections:
        amount = (f"{sec.slides_hint} 슬라이드" if spec.doc_format == "slides" and sec.slides_hint
                  else (f"{sec.pages_hint:.1f} 페이지" if sec.pages_hint else "—"))
        A(f"| {sec.order} | **{md(sec.title)}** (`{sec.section_id}`) | {'☑' if sec.required else '☐'} | "
          f"{sec.presence_in_pass:.0%} | {amount} | {len(sec.elements)} |")
    A("")
    for sec in spec.sections:
        A(f"### {sec.order}. {md(sec.title)} (`{sec.section_id}`)")
        A("")
        A(f"{sec.guidance}")
        A("")
        A("| 필수 | 종류 | 항목 | 작성 가이드 | 표 열 구성 | 근거 |")
        A("|---|---|---|---|---|---|")
        for el in sec.elements:
            A(f"| {'☑' if el.required else '☐'} | {el.kind} | {md(el.title)} | {md(el.guidance)} | "
              f"{md(', '.join(el.table_columns)) if el.table_columns else '—'} | "
              f"{', '.join(f'`{e}`' for e in el.evidence_ids)} |")
        A("")
    A("## 근거 통계")
    A("")
    A("| 개념 ID | 등급 | Pass율(있음) | Pass율(없음) | n(있음/없음) | q | SVM w | 부호안정성 |")
    A("|---|---|---|---|---|---|---|---|")
    for ev in spec.evidence:
        A(f"| `{ev.concept_id}` | {ev.verdict} | {ev.pass_rate_with:.0%} | {ev.pass_rate_without:.0%} | "
          f"{ev.n_with}/{ev.n_without} | {_fmt(ev.q_value)} | {_fmt(ev.svm_weight)} | "
          f"{_fmt(ev.sign_stability, pct=True)} |")
    A("")
    if spec.rater_conflicts:
        A("## 평가자 간 요구 충돌")
        A("")
        for c in spec.rater_conflicts:
            A(f"- {c}")
        A("")
    A("## 한계와 주의사항")
    A("")
    for c in spec.caveats:
        A(f"- {c}")
    A("")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(L), encoding="utf-8")
    return out


def save_json(obj, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    return path
