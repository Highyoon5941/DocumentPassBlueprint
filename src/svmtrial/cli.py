"""typer CLI — `python -m svmtrial <cmd>` (SETUP.md §12).

명령: ingest | ocr | labels | sections | concepts discover | concepts score
      | model | template | report | diagnose | all | approve | doctor
공통 옵션: --group G  --dry-run  --force
"""

from __future__ import annotations

import json
import shutil
import sys
import traceback
import warnings
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from svmtrial.config import Settings, check_interpreter_hygiene, load_settings
from svmtrial.gemini_client import GeminiClient, new_run_id
from svmtrial.groups import Group, discover_groups, parse_group

app = typer.Typer(add_completion=False, no_args_is_help=True,
                  help="문서 Pass 조건 역추적 → 표준 템플릿 생성 (SVM + Gemini 하이브리드)")
concepts_app = typer.Typer(no_args_is_help=True, help="S4b/S4c — 개념 가설 생성과 채점")
app.add_typer(concepts_app, name="concepts")
con = Console()

GroupOpt = Annotated[str | None, typer.Option("--group", "-g", help="분석 그룹 (예: 대책서__CUST_A). 생략하면 전부")]
DryOpt = Annotated[bool, typer.Option("--dry-run", help="Gemini 호출 없이 호출 수·예상 토큰만 출력")]
ForceOpt = Annotated[bool, typer.Option("--force", help="캐시·중간 산출물을 무시하고 다시 만든다")]
ConfigOpt = Annotated[Path | None, typer.Option("--config", help="config.yaml 경로")]


# ---------------------------------------------------------------- 공통


def _settings(config: Path | None = None) -> Settings:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        s = load_settings(config_path=config)
    for w in caught:
        con.print(f"[yellow]⚠ {w.message}[/yellow]")
    return s


def _groups(s: Settings, group: str | None) -> list[Group]:
    if group:
        return [parse_group(group)]
    gs = discover_groups(s)
    if not gs:
        con.print(f"[red]✗ 그룹을 찾지 못했습니다. {s.p('raw_pdfs')}/<doc_type>/<customer>/*.pdf 구조를 확인하세요.[/red]")
        raise typer.Exit(1)
    return gs


def _client(s: Settings, run_id: str, dry_run: bool = False, force: bool = False) -> GeminiClient:
    gc = GeminiClient(s, run_id=run_id, use_cache=not force, dry_run=dry_run)
    tag = "[yellow]offline (Gemini 호출 없음)[/yellow]" if s.backend == "offline" else f"[green]vertex ({s.project}/{s.location})[/green]"
    con.print(f"backend = {tag}   run_id = [cyan]{run_id}[/cyan]")
    if s.backend == "offline":
        con.print("[yellow]  → 실데이터 분석에는 쓸 수 없습니다. docs/migration_vertexAI.md 를 보세요.[/yellow]")
    return gc


def _label_ids(s: Settings, g: Group) -> set[str] | None:
    from svmtrial.labels import LabelError, filter_group, read_labels

    try:
        long = read_labels(s)
    except LabelError:
        return None
    return set(filter_group(long, g, s)["doc_id"].astype(str))


def _ids(s: Settings, g: Group) -> list[str]:
    f = s.work / "ingest" / f"{g.safe}.json"
    if not f.exists():
        con.print(f"[red]✗ {f} 가 없습니다 — 먼저 `svmtrial ingest --group {g.key}` 를 실행하세요.[/red]")
        raise typer.Exit(1)
    return json.loads(f.read_text(encoding="utf-8"))["doc_ids"]


def _target_col(s: Settings, g: Group) -> str:
    from svmtrial.labels import load_agreement

    a = load_agreement(s, g)
    if not a:
        con.print(f"[red]✗ 먼저 `svmtrial labels --group {g.key}` 를 실행하세요.[/red]")
        raise typer.Exit(1)
    return a["target"]["column"]


def _show(title: str, data: dict) -> None:
    t = Table(title=title, show_header=False, title_justify="left")
    t.add_column("키", style="cyan", no_wrap=True)
    t.add_column("값")
    for k, v in data.items():
        if isinstance(v, (dict, list)):
            v = json.dumps(v, ensure_ascii=False)[:160]
        t.add_row(str(k), str(v))
    con.print(t)


def _dry_note(gc: GeminiClient) -> None:
    d = gc.summary().get("dry_run")
    if d:
        _show("--dry-run 추정", d)
        if not gc.s.gemini.cost_per_1m_tokens:
            con.print("[dim]비용 추정은 config.gemini.cost_per_1m_tokens 가 비어 있어 생략했습니다.[/dim]")


# ---------------------------------------------------------------- 단계별 명령


@app.command()
def doctor(config: ConfigOpt = None) -> None:
    """환경과 설정을 점검한다 (Gemini 호출 없음)."""
    s = _settings(config)
    rows: dict[str, str] = {
        "python": sys.version.split()[0],
        "프로젝트 루트": str(s.root),
        "backend": s.backend,
        "GOOGLE_CLOUD_PROJECT": s.project or "(없음)",
        "GOOGLE_CLOUD_LOCATION": s.location,
        "GEMINI_MODEL_FAST": s.gemini.model_fast or "(없음)",
        "GEMINI_MODEL_PRO": s.gemini.model_pro or "(없음)",
        "PDF 경로": f"{s.p('raw_pdfs')} ({'있음' if s.p('raw_pdfs').exists() else '없음'})",
        "평가 시트 경로": f"{s.p('labels')} ({'있음' if s.p('labels').exists() else '없음'})",
        "타깃": s.analysis.target,
    }
    hy = check_interpreter_hygiene()
    rows["PYTHONPATH 위생"] = "OK" if not hy else hy[0][:90]
    gs = discover_groups(s)
    rows["발견된 그룹"] = ", ".join(g.key for g in gs) or "(없음)"
    _show("환경 점검", rows)
    if s.backend == "vertex" and not s.project:
        con.print("[red]✗ vertex 백엔드인데 GOOGLE_CLOUD_PROJECT 가 없습니다.[/red]")
        raise typer.Exit(1)
    if s.backend == "offline":
        from svmtrial.offline_backend import OfflineBackend

        fx = OfflineBackend.fixture_path(s)
        con.print(f"offline fixture: {fx} ({'있음' if fx.exists() else '[red]없음 — scripts/make_dummy_data.py 를 먼저 실행[/red]'})")
    con.print("[green]✓ doctor 완료[/green]")


@app.command()
def ingest(group: GroupOpt = None, dry_run: DryOpt = False, force: ForceOpt = False, config: ConfigOpt = None) -> None:
    """S1 — PDF → 페이지 PNG + 메타, 원본 형식(docs/slides) 판정."""
    s = _settings(config)
    run_id = new_run_id()
    gc = _client(s, run_id, dry_run, force)
    from svmtrial import ingest as mod

    for g in _groups(s, group):
        out = mod.run(s, g, gc, force=force, label_doc_ids=_label_ids(s, g))
        _show(f"ingest — {g.key}", {k: v for k, v in out.items() if k != "doc_ids"})
    _dry_note(gc)


@app.command()
def ocr(group: GroupOpt = None, dry_run: DryOpt = False, force: ForceOpt = False,
        limit: Annotated[int | None, typer.Option("--limit", help="앞 N개 문서만 (시험용)")] = None,
        config: ConfigOpt = None) -> None:
    """S2 — Gemini 비전으로 페이지 구조 추출."""
    s = _settings(config)
    gc = _client(s, new_run_id(), dry_run, force)
    from svmtrial import ocr as mod

    for g in _groups(s, group):
        out = mod.run(s, g, gc, force=force, limit=limit)
        _show(f"ocr — {g.key}", out)
    _dry_note(gc)


@app.command()
def labels(group: GroupOpt = None, config: ConfigOpt = None) -> None:
    """S3 — 평가자 일치도(κ/α), Dawid-Skene, 타깃 결정."""
    s = _settings(config)
    from svmtrial import labels as mod

    for g in _groups(s, group):
        try:
            out = mod.run(s, g, doc_ids=set(_ids(s, g)))
        except mod.LabelError as e:
            con.print(f"[red]✗ {g.key}: {e}[/red]")
            raise typer.Exit(1) from e
        _show(f"labels — {g.key}", {k: v for k, v in out.items() if k != "warnings"})
        for w in out["warnings"]:
            con.print(f"[yellow]⚠ {w}[/yellow]")


@app.command()
def sections(group: GroupOpt = None, dry_run: DryOpt = False, force: ForceOpt = False,
             config: ConfigOpt = None) -> None:
    """S4a — 표준 섹션 분류체계 후보 생성 (🔒) 과 문서별 섹션 매핑."""
    s = _settings(config)
    gc = _client(s, new_run_id(), dry_run, force)
    from svmtrial import sections as mod

    for g in _groups(s, group):
        out = mod.run(s, g, gc, _ids(s, g), force=force)
        _show(f"sections — {g.key}", {k: v for k, v in out.items() if k != "message"})
        if out.get("stage") == "awaiting_approval":
            con.print(f"[yellow]{out['message']}[/yellow]")
    _dry_note(gc)


@concepts_app.command("discover")
def concepts_discover(group: GroupOpt = None, dry_run: DryOpt = False, force: ForceOpt = False,
                      config: ConfigOpt = None) -> None:
    """S4b — Pass/Fail 대조로 개념 가설 생성 (발견셋만, 🔒)."""
    s = _settings(config)
    gc = _client(s, new_run_id(), dry_run, force)
    from svmtrial import concepts as mod
    from svmtrial import labels as lmod

    for g in _groups(s, group):
        tcol = _target_col(s, g)
        out = mod.discover(s, g, gc, lmod.load_targets(s, g), tcol, force=force)
        if not out:
            continue
        _show(f"concepts discover — {g.key}", {
            "발견셋 문서": out["n_discovery_docs"],
            "발견셋 Pass/Fail": f"{out['n_pass_in_discovery']}/{out['n_fail_in_discovery']}",
            "라운드별 가설 수": [r["n_concepts"] for r in out["rounds"]],
            "합친 개념 수": len(out["concepts"]),
        })
        t = Table(title="개념 후보 (🔒 사람 검토 필요)", title_justify="left")
        for c in ("ID", "type", "섹션", "질문"):
            t.add_column(c, overflow="fold")
        for c in out["concepts"]:
            t.add_row(c["id"], c["type"], str(c.get("section_id") or "—"), c["question"])
        con.print(t)
        con.print(f"[yellow]🔒 {mod.work_dir(s, 'concepts', g) / mod.CANDIDATES} 를 검토한 뒤"
                  f" `svmtrial approve --group {g.key} --gate concepts` 또는 직접 "
                  f"`{mod.APPROVED}` 로 저장하세요.[/yellow]")
    _dry_note(gc)


@concepts_app.command("score")
def concepts_score(group: GroupOpt = None, dry_run: DryOpt = False, force: ForceOpt = False,
                   config: ConfigOpt = None) -> None:
    """S4c — 문서 × 개념 0/1 행렬 + 구조 특징."""
    s = _settings(config)
    gc = _client(s, new_run_id(), dry_run, force)
    from svmtrial import concepts as mod
    from svmtrial.sections import GateNotPassed

    for g in _groups(s, group):
        try:
            out = mod.score(s, g, gc, _ids(s, g), force=force)
        except GateNotPassed as e:
            con.print(f"[yellow]{e}[/yellow]")
            raise typer.Exit(2) from e
        _show(f"concepts score — {g.key}", {k: v for k, v in out.items() if k not in ("yes_rate", "scoring_stability")})
        if out.get("unstable_concepts"):
            con.print(f"[yellow]⚠ 채점 불안정 개념(모델에서 제외): {out['unstable_concepts']}[/yellow]")
    _dry_note(gc)


@app.command()
def model(group: GroupOpt = None, config: ConfigOpt = None) -> None:
    """S5 — 단변량 + 다변량 + 안정성 + 검증셋 + 등급 + 평가자별 + 반사실."""
    s = _settings(config)
    from svmtrial import concepts as cmod
    from svmtrial import counterfactual as cfmod
    from svmtrial import labels as lmod
    from svmtrial import modeling as mod

    for g in _groups(s, group):
        tcol = _target_col(s, g)
        X = cmod.load_X(s, g)
        meta = cmod.load_scoring_meta(s, g)
        res = mod.run(s, g, X, lmod.load_targets(s, g), tcol, cmod.load_split(s, g),
                      lmod.load_long(s, g), unstable=meta.get("unstable_concepts"))
        mv = res["multivariate"]
        _show(f"model — {g.key}", {
            "타깃": f"{tcol} ({res['prepare']['n_docs']}건, 특징 {res['prepare']['n_features']}개)",
            "탐색적 표기": res["exploratory_only"],
            "대표 모델": mv.get("primary_model"),
            "교차검증": mv.get("cv"),
            "순열검정 p": mv.get("permutation_test", {}).get("p_value"),
            "모델 신호": mv.get("signal"),
            "등급 분포": res["verdict_counts"],
        })
        t = Table(title="개념 판정", title_justify="left")
        for c in ("특징", "등급", "RD", "q", "부호안정성", "검증셋", "SVM w", "근거"):
            t.add_column(c, overflow="fold")
        for r in res["verdicts"][:20]:
            t.add_row(r["feature"], r["verdict"],
                      f"{r['rd']:+.3f}" if r.get("rd") is not None else "—",
                      f"{r['q_value']:.3g}" if r.get("q_value") is not None else "—",
                      f"{r['sign_stability']:.0%}" if r.get("sign_stability") is not None else "—",
                      {True: "동일", False: "반대"}.get(r.get("same_direction"), "—"),
                      f"{r['svm_weight']:+.4f}" if r.get("svm_weight") is not None else "—",
                      str(r.get("verdict_basis", ""))[:40])
        con.print(t)
        if res["per_rater"]["conflicts"]:
            con.print("[yellow]⚠ 평가자 간 요구 충돌:[/yellow]")
            for c in res["per_rater"]["conflicts"]:
                con.print(f"  - {c}")

        # 반사실
        if not res["continuous_target"]:
            Xi, y, _ = mod.prepare(X, lmod.load_targets(s, g), tcol, meta.get("unstable_concepts"))
            fit = mod.fit_svm_weights(s, Xi, y, mv.get("primary_model", "svm_C0.1"))
            cf = cfmod.run(s, g, Xi, y, fit["weights"], fit["intercept"], cmod.load_approved_concepts(s, g))
            _show(f"counterfactual — {g.key}", {
                "Fail 문서": cf["n_fail_docs"], "Pass 쪽 도달": cf["n_reached_pass_side"],
                "평균 변경 수": cf["mean_changes_needed"],
                "상위 보완": [f"{r['action']}:{r['feature']}({r['share_of_fail']:.0%})" for r in cf["ranking"][:5]],
            })


@app.command()
def template(group: GroupOpt = None, force: ForceOpt = False, config: ConfigOpt = None) -> None:
    """S6 — TemplateSpec(json/md) + template.docx|pptx 생성 (🔒 최종 검토 대상)."""
    s = _settings(config)
    run_id = new_run_id()
    gc = _client(s, run_id, False, force)
    _build_outputs(s, gc, run_id, _groups(s, group), with_report=False)


@app.command()
def report(group: GroupOpt = None, config: ConfigOpt = None) -> None:
    """analysis_report.md 와 차트 생성 (template 이후)."""
    s = _settings(config)
    run_id = new_run_id()
    gc = _client(s, run_id, False, False)
    _build_outputs(s, gc, run_id, _groups(s, group), with_report=True, spec_only_if_missing=True)


def _build_outputs(s: Settings, gc: GeminiClient, run_id: str, groups: list[Group],
                   with_report: bool, spec_only_if_missing: bool = False) -> None:
    from svmtrial import concepts as cmod
    from svmtrial import labels as lmod
    from svmtrial import modeling as mod
    from svmtrial import render_docx, render_pptx
    from svmtrial import report as rmod
    from svmtrial import template_spec as tmod

    for g in groups:
        res = mod.load_results(s, g)
        targets = lmod.load_targets(s, g)
        agree = lmod.load_agreement(s, g)
        concepts = cmod.load_approved_concepts(s, g)
        cf_f = (s.work / "models" / g.safe / "counterfactual.json")
        cf = json.loads(cf_f.read_text(encoding="utf-8")) if cf_f.exists() else None

        spec_f = s.work / "models" / g.safe / "template_spec.json"
        if spec_only_if_missing and spec_f.exists():
            spec = tmod.load_spec(s, g)
        else:
            spec = tmod.run(s, g, gc, run_id, res, targets, agree, concepts, cf)

        out_dir = s.outputs / run_id / g.safe
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "template_spec.json").write_text(spec.model_dump_json(indent=1), encoding="utf-8")
        rmod.spec_markdown(spec, out_dir / "template_spec.md")

        if spec.doc_format == "slides":
            doc = render_pptx.render(spec, out_dir / "template.pptx", font=s.template.font)
        else:
            doc = render_docx.render(spec, out_dir / "template.docx", font=s.template.font)

        made = {"template_spec.json": "✓", "template_spec.md": "✓", doc.name: "✓"}
        if with_report:
            ing = json.loads((s.work / "ingest" / f"{g.safe}.json").read_text(encoding="utf-8"))
            ocr_sum_f = s.work / "ocr" / f"_summary_{g.safe}.json"
            ocr_sum = json.loads(ocr_sum_f.read_text(encoding="utf-8")) if ocr_sum_f.exists() else {}
            labels_map = {c.id: c.question for c in concepts}
            labels_map.update({f: f for f in res["features"] if f not in labels_map})
            rmod.render(s, g, out_dir, run_id, spec, res, agree, ing, ocr_sum,
                        cmod.load_scoring_meta(s, g), cf, labels_map)
            made["analysis_report.md"] = "✓"

        n_missing = sum(1 for sec in spec.sections for el in sec.elements if not el.evidence_ids)
        _show(f"outputs — {g.key}", {
            "출력 폴더": str(out_dir.relative_to(s.root)),
            "생성 파일": ", ".join(made),
            "섹션 수": len(spec.sections),
            "요소 수": sum(len(sec.elements) for sec in spec.sections),
            "근거 없는 요소": n_missing,
            "근거 통계 항목": len(spec.evidence),
        })
        con.print(f"[yellow]🔒 {out_dir / doc.name} 를 사람이 최종 검토한 뒤 배포하세요 (SETUP.md §8.6-4).[/yellow]")


@app.command()
def diagnose(
    pdf: Annotated[Path, typer.Option("--pdf", help="진단할 새 문서 PDF")],
    group: Annotated[str, typer.Option("--group", "-g", help="기준 그룹 (예: 대책서__CUST_A)")],
    force: ForceOpt = False, config: ConfigOpt = None,
) -> None:
    """S7 — 새 문서 1건이 Pass 쪽으로 가려면 무엇을 보완해야 하는지 진단."""
    s = _settings(config)
    run_id = new_run_id()
    gc = _client(s, run_id, False, force)
    from svmtrial import diagnose as mod

    g = parse_group(group)
    out_dir = s.outputs / run_id / g.safe
    out = mod.run(s, g, gc, pdf, out_dir, force=force)
    for r in out.get("unreliable_reasons", []):
        con.print(f"[red]⚠ {r}[/red]")
    _show(f"diagnose — {out['doc_id']}", {
        "판정": f"{out['side']} (f = {out['decision_value']:+.4f})",
        "신뢰 가능": out.get("reliable"),
        "페이지/형식": f"{out['n_pages']} / {out['doc_format']}",
        "빠진 섹션": out["missing_sections"] or "없음",
        "필요 보완 수": out["counterfactual"].get("n_changes", 0),
        "리포트": out["report"],
    })
    for i, ch in enumerate(out["counterfactual"].get("changes", []), start=1):
        con.print(f"  {i}. {'추가' if ch['action'] == 'add' else '제거'} `{ch['feature']}` (이득 {ch['gain']:+.4f})")


@app.command()
def approve(
    group: Annotated[str, typer.Option("--group", "-g")],
    gate: Annotated[str, typer.Option("--gate", help="sections | concepts")],
    config: ConfigOpt = None,
) -> None:
    """🔒 후보 파일을 승인 파일로 복사한다. **사람이 검토한 뒤 직접 실행하는 명령이다.**

    `all` 파이프라인은 이 명령을 자동으로 호출하지 않는다 (SETUP.md R7).
    """
    s = _settings(config)
    g = parse_group(group)
    pairs = {
        "sections": ("sections", "taxonomy_candidates.yaml", "taxonomy_approved.yaml"),
        "concepts": ("concepts", "concepts_candidates.yaml", "concepts_approved.yaml"),
    }
    if gate not in pairs:
        con.print(f"[red]✗ --gate 는 {list(pairs)} 중 하나여야 합니다.[/red]")
        raise typer.Exit(1)
    stage, cand_name, appr_name = pairs[gate]
    from svmtrial.groups import work_dir

    wd = work_dir(s, stage, g)
    cand, appr = wd / cand_name, wd / appr_name
    if not cand.exists():
        con.print(f"[red]✗ 후보 파일이 없습니다: {cand}[/red]")
        raise typer.Exit(1)
    if appr.exists():
        con.print(f"[yellow]⚠ 이미 승인 파일이 있습니다: {appr} (덮어씁니다)[/yellow]")
    shutil.copy2(cand, appr)
    con.print(f"[green]✓ 승인: {appr.relative_to(s.root)}[/green]")
    con.print("[dim]검토 없이 복사만 한 것이라면, 파일을 열어 내용을 확인하세요.[/dim]")


@app.command("all")
def run_all(group: GroupOpt = None, dry_run: DryOpt = False, force: ForceOpt = False,
            config: ConfigOpt = None) -> None:
    """S1~S6 전체 실행. 🔒 관문에서 승인 파일이 없으면 그 지점에서 멈추고 안내한다."""
    s = _settings(config)
    run_id = new_run_id()
    gc = _client(s, run_id, dry_run, force)
    from svmtrial import concepts as cmod
    from svmtrial import counterfactual as cfmod
    from svmtrial import ingest as imod
    from svmtrial import labels as lmod
    from svmtrial import modeling as mmod
    from svmtrial import ocr as omod
    from svmtrial import sections as smod

    for g in _groups(s, group):
        con.rule(f"[bold]{g.key}")
        ing = imod.run(s, g, gc, force=force, label_doc_ids=_label_ids(s, g))
        con.print(f"S1 ingest: 문서 {ing['n_docs']}건 / 페이지 {ing['n_pages']}장 / 형식 {ing['group_doc_format']}")
        ids = ing["doc_ids"]

        oc = omod.run(s, g, gc, force=force)
        if dry_run:
            _dry_note(gc)
            continue
        con.print(f"S2 ocr: 페이지 {oc['n_pages']}장 / 평균 판독성 {oc['mean_legibility']}")

        lb = lmod.run(s, g, doc_ids=set(ids))
        con.print(f"S3 labels: α={lb['krippendorff_alpha']} / 타깃 {lb['target']['used']} ({lb['target']['column']})")
        for w in lb["warnings"]:
            con.print(f"[yellow]⚠ {w}[/yellow]")
        tcol = lb["target"]["column"]

        sec = smod.run(s, g, gc, ids, force=force)
        if sec.get("stage") == "awaiting_approval":
            con.print("[yellow]🔒 여기서 멈춥니다 — 섹션 분류체계 승인이 필요합니다.[/yellow]")
            con.print(f"[yellow]{sec['message']}[/yellow]")
            continue
        con.print(f"S4a sections: 분류체계 {len(sec['taxonomy'])}개 / OTHER 제목 {sec['n_titles_other']}개")

        cand = cmod.discover(s, g, gc, lmod.load_targets(s, g), tcol, force=force)
        con.print(f"S4b concepts discover: 개념 후보 {len(cand.get('concepts', []))}개")
        try:
            cmod.load_approved_concepts(s, g)
        except smod.GateNotPassed as e:
            con.print("[yellow]🔒 여기서 멈춥니다 — 개념 승인이 필요합니다.[/yellow]")
            con.print(f"[yellow]{e}[/yellow]")
            continue

        sc = cmod.score(s, g, gc, ids, force=force)
        con.print(f"S4c concepts score: 개념 {sc['n_concepts']}개 / 구조특징 {sc['n_structural']}개 "
                  f"/ 불안정 {len(sc['unstable_concepts'])}개")

        X = cmod.load_X(s, g)
        res = mmod.run(s, g, X, lmod.load_targets(s, g), tcol, cmod.load_split(s, g),
                       lmod.load_long(s, g), unstable=sc.get("unstable_concepts"))
        con.print(f"S5 model: 등급 {res['verdict_counts']} / 순열검정 p="
                  f"{res['multivariate'].get('permutation_test', {}).get('p_value')}")

        if not res["continuous_target"]:
            Xi, y, _ = mmod.prepare(X, lmod.load_targets(s, g), tcol, sc.get("unstable_concepts"))
            fit = mmod.fit_svm_weights(s, Xi, y, res["multivariate"].get("primary_model", "svm_C0.1"))
            cfmod.run(s, g, Xi, y, fit["weights"], fit["intercept"], cmod.load_approved_concepts(s, g))

        _build_outputs(s, gc, run_id, [g], with_report=True)

    _show("Gemini 사용량", gc.summary())


def main() -> None:
    try:
        app()
    except KeyboardInterrupt:
        con.print("\n[yellow]중단됨[/yellow]")
        sys.exit(130)
    except Exception as e:  # noqa: BLE001 - CLI 최상단에서 친절한 메시지로 바꾼다
        con.print(f"[red]✗ {type(e).__name__}: {e}[/red]")
        if __debug__ and "--traceback" in sys.argv:
            traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
