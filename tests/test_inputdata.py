"""2026-10-14 확정 입력 — InputData/Documents + Judgement.xlsx (p / f / f(사유)), 이미지 기반 통일.

Gemini 는 호출하지 않는다 (R6). LLM 이 필요한 곳은 offline 핸들러나 FakeGemini 를 쓴다.
"""

from __future__ import annotations

import json
import unicodedata

import numpy as np
import pandas as pd
import pymupdf
import pytest

from svmtrial import labels as L
from svmtrial import textutil as tu
from svmtrial.groups import Group, discover_groups, input_group, list_documents

# ---------------------------------------------------------------- 보조


def _pdf(path, pages=((595, 842),), text=None):
    """페이지 크기 목록으로 PDF 를 만든다. text 가 있으면 텍스트 레이어를 넣는다(디지털 PDF 흉내)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    d = pymupdf.open()
    for w, h in pages:
        p = d.new_page(width=w, height=h)
        if text:
            p.insert_text((72, 72), text, fontsize=14)
    d.save(path)
    d.close()
    return path


def _screenshot_sheet(settings, extra_rows=()):
    """사용자가 보여준 예시 시트와 같은 모양: FileName | Person1 | Person2 | Person3(빈) | .. | 메모 칸 + '..' 행."""
    root = settings.input_root
    root.mkdir(parents=True, exist_ok=True)
    rows = [
        ("a", "p", "f(cause)"), ("b", "p", "p"), ("c", "f(cause)", "p"), ("d", "p", "p"),
        ("e", "f", "f(cause)"), ("f", "p", "p"), ("g", "f(cause)", "f(cause)"), *extra_rows,
    ]
    df = pd.DataFrame(rows, columns=["FileName", "Person1", "Person2"])
    df["Person3"] = None
    df[".."] = None
    df[""] = None
    note = ["1. 향후 계획 미기입", "2. 향후 일정 미기입", "3. 사진 부적합", "4. etc.."] + [None] * (len(df) - 4)
    df["examples of cause:"] = note
    tail = dict.fromkeys(df.columns)
    tail["FileName"] = ".."
    df = pd.concat([df, pd.DataFrame([tail])], ignore_index=True)
    f = root / "Judgement.xlsx"
    df.to_excel(f, index=False)
    return f


# ---------------------------------------------------------------- 값 해석


@pytest.mark.parametrize("raw,y,causes", [
    ("p", 1.0, []), ("P", 1.0, []), ("Pass", 1.0, []), ("합격", 1.0, []),
    ("f", 0.0, []), ("F", 0.0, []), ("불합격", 0.0, []),
    ("f(향후 계획 미기입)", 0.0, ["향후 계획 미기입"]),
    ("F (사진 부적합)", 0.0, ["사진 부적합"]),
    ("f（향후 일정 미기입）", 0.0, ["향후 일정 미기입"]),            # 전각 괄호
    ("f(향후 계획 미기입, 사진 부적합)", 0.0, ["향후 계획 미기입", "사진 부적합"]),
    ("f(1. 향후 계획 미기입 2. 사진 부적합)", 0.0, ["향후 계획 미기입", "사진 부적합"]),
    ("f(대책 전/후 그래프 없음)", 0.0, ["대책 전/후 그래프 없음"]),    # '/' 는 나누지 않는다
    ("f()", 0.0, []),
    ("f(사진 부적합", 0.0, ["사진 부적합"]),                          # 닫는 괄호 누락
    ("p(사진 보완 권고)", 1.0, []),                                   # Pass 의 코멘트는 사유가 아니다
])
def test_parse_judgement(settings, raw, y, causes):
    j = L.parse_judgement(raw, L._value_map(settings))
    assert j.y == y
    assert j.causes == causes


def test_parse_judgement_blank_and_unknown(settings):
    vm = L._value_map(settings)
    assert np.isnan(L.parse_judgement("", vm).y)
    assert np.isnan(L.parse_judgement(None, vm).y)
    assert L.parse_judgement("보류", vm).y is None
    assert L.parse_judgement("보류(검토중)", vm).y is None


def test_cause_key_ignores_spacing_and_symbols():
    assert L.cause_key("향후계획 미기입") == L.cause_key("향후 계획  미기입.")
    assert L.cause_key("향후 계획 미기입") != L.cause_key("향후 일정 미기입")


# ---------------------------------------------------------------- Judgement 시트


def test_read_judgement_like_screenshot(settings):
    _screenshot_sheet(settings)
    notes: dict = {}
    long = L.read_labels(settings, notes)
    assert notes["raters"] == ["Person1", "Person2"]
    ignored = {x["column"]: x["reason"] for x in notes["ignored_columns"]}
    assert any("Person3" in c for c in ignored), "빈 평가자 열은 제외돼야 한다"
    assert any("examples of cause" in c and "메모" in r for c, r in ignored.items()), "메모 칸은 제외돼야 한다"
    assert notes["skipped_rows"] == 1, "'..' 행은 건너뛴다"
    assert sorted(long["doc_id"].unique()) == list("abcdefg")
    assert len(long) == 14
    m = {(r.doc_id, r.rater): r.y for r in long.itertuples()}
    assert m[("a", "Person2")] == 0.0 and m[("b", "Person1")] == 1.0 and m[("e", "Person1")] == 0.0
    c = {(r.doc_id, r.rater): r.cause for r in long.itertuples()}
    assert c[("a", "Person2")] == "cause" and c[("e", "Person1")] == ""


def test_note_column_with_a_valid_looking_cell_is_still_a_note(settings):
    """메모 칸에 우연히 'p' 하나가 있어도 절반 미만이면 평가자 열이 아니다."""
    root = settings.input_root
    root.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"FileName": ["a", "b", "c", "d"], "Person1": ["p", "f", "p", "f(x)"],
                  "비고": ["p", "재평가 필요", "담당 변경", "확인중"]}).to_excel(root / "Judgement.xlsx", index=False)
    notes: dict = {}
    L.read_labels(settings, notes)
    assert notes["raters"] == ["Person1"]


def test_typo_inside_rater_column_stops_with_error_list(settings):
    root = settings.input_root
    root.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"FileName": ["a", "b", "c"], "Person1": ["p", "pp", "f"]}).to_excel(
        root / "Judgement.xlsx", index=False)
    with pytest.raises(L.LabelError, match="p / f / f\\(사유\\)"):
        L.read_labels(settings)
    assert (settings.work / "labels" / "label_value_errors.csv").exists()


def test_missing_file_column_is_clear_error(settings):
    root = settings.input_root
    root.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"파일": ["a"], "Person1": ["p"]}).to_excel(root / "Judgement.xlsx", index=False)
    with pytest.raises(L.LabelError, match="FileName"):
        L.read_labels(settings)


def test_judgement_found_with_other_extension(settings):
    root = settings.input_root
    root.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"FileName": ["a"], "Person1": ["p"]}).to_csv(root / "judgement.csv", index=False)
    assert L.judgement_file(settings).name == "judgement.csv"


def test_duplicate_file_names_keep_first(settings):
    root = settings.input_root
    root.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"FileName": ["a", "a", "b"], "Person1": ["p", "f", "f"]}).to_excel(
        root / "Judgement.xlsx", index=False)
    notes: dict = {}
    long = L.read_labels(settings, notes)
    assert notes["duplicate_file_names"] == ["a"]
    assert long.loc[long["doc_id"] == "a", "y"].tolist() == [1.0]


def test_labels_run_writes_causes(settings):
    _screenshot_sheet(settings, extra_rows=[("h", "f(향후 계획 미기입, 사진 부적합)", "f(향후계획 미기입)")])
    out = L.run(settings, input_group(settings), doc_ids=set("abcdefgh"))
    wd = settings.work / "labels" / "InputData"
    causes = pd.read_csv(wd / "causes_long.csv")
    assert set(causes["cause"]) == {"cause", "향후 계획 미기입", "사진 부적합", "향후계획 미기입"}
    summ = json.loads((wd / "cause_summary.json").read_text(encoding="utf-8"))
    top = {c["key"]: c for c in summ["causes"]}
    assert top[L.cause_key("향후 계획 미기입")]["n_ratings"] == 2, "띄어쓰기만 다른 사유는 같은 사유다"
    assert summ["n_fail_ratings"] == 8 and summ["n_fail_with_cause"] == 7
    t = pd.read_csv(wd / "doc_targets.csv").set_index("doc_id")
    assert "Person1: 향후 계획 미기입; 사진 부적합" in t.loc["h", "causes"]
    agree = json.loads((wd / "agreement.json").read_text(encoding="utf-8"))
    assert agree["input_notes"]["raters"] == ["Person1", "Person2"]
    assert out["raters"] == ["Person1", "Person2"]


def test_labels_run_maps_case_only_differences(settings):
    """시트 FileName 과 PDF 이름이 대소문자만 다르면 PDF 쪽 ID 로 맞춘다."""
    root = settings.input_root
    root.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"FileName": ["Doc1", "doc2", "doc3"], "Person1": ["p", "f", "p"],
                  "Person2": ["p", "f", "f"], "Person3": ["p", "p", "f"]}).to_excel(
        root / "Judgement.xlsx", index=False)
    L.run(settings, input_group(settings), doc_ids={"doc1", "doc2", "doc3"})
    t = pd.read_csv(settings.work / "labels" / "InputData" / "doc_targets.csv")
    assert sorted(t["doc_id"]) == ["doc1", "doc2", "doc3"]


# ---------------------------------------------------------------- 문서 식별자


def test_doc_id_normalizes_nfd_extension_and_path():
    nfd = unicodedata.normalize("NFD", "대책서_가.pdf")
    assert tu.doc_id(nfd) == "대책서_가"
    assert tu.doc_id("  x.PDF ") == "x"
    assert tu.doc_id("Documents/sub/y.pdf") == "y"
    assert tu.doc_id("C:\\in\\z.pdf") == "z"
    assert tu.doc_id("㈜보고서") == "㈜보고서", "NFKC 로 바꾸면 폴더 이름이 원본과 달라진다"


def test_match_doc_ids_loose_but_unambiguous():
    m, notes = tu.match_doc_ids({"Doc1 ", "a", "XY"}, {"doc1", "a", "x y", "X Y"})
    assert m["a"] == "a" and m["Doc1 "] == "doc1"
    assert "XY" not in m, "두 개에 맞으면 잇지 않는다"
    assert len(notes) == 1


# ---------------------------------------------------------------- 입력 폴더


def test_list_documents_and_groups(settings):
    docs = settings.documents_dir
    _pdf(docs / "a.pdf")
    _pdf(docs / "b.PDF")
    _pdf(docs / "sub" / "c.pdf")
    _pdf(docs / "sub" / "a.pdf")                       # 이름 중복
    (docs / "memo.txt").write_text("x", encoding="utf-8")
    (docs / "~$a.pdf").write_text("lock", encoding="utf-8")
    pdfs, issues = list_documents(settings)
    assert [tu.doc_id(p.name) for p in pdfs] == ["a", "b", "c"]
    kinds = sorted(i["issue"].split(" ")[0] for i in issues)
    assert kinds == ["PDF", "파일명"]
    assert discover_groups(settings) == [Group("InputData")]
    settings.input.doc_type = "대책서"
    assert discover_groups(settings) == [Group("대책서")]


def test_no_documents_no_group(settings):
    assert discover_groups(settings) == []


# ---------------------------------------------------------------- 이미지 기반 통일


def test_render_doc_is_image_first_and_caps_size(settings):
    from svmtrial.ingest import render_doc

    settings.ingest.max_long_edge_px = 1200
    pdf = _pdf(settings.documents_dir / "mix.pdf", pages=((595, 842), (842, 474), (2384, 3370)),
               text="digital pdf with text layer")
    meta = render_doc(settings, pdf, settings.work / "pages")
    assert meta.n_pages == 3 and meta.has_text_layer, "텍스트 레이어는 참고값으로만 기록한다"
    assert all((settings.work / "pages" / "mix" / n).exists() for n in meta.pages)
    assert max(meta.page_px[2]) <= 1200, "A0 페이지도 긴 변 제한 안으로 렌더링한다"
    assert meta.page_ratios[1] > 1.25 > meta.page_ratios[0]


def test_doc_format_ignores_pdf_creator(settings):
    """creator 가 PowerPoint 여도 페이지 이미지가 세로형이면 docs — 이미지로만 판정한다."""
    from svmtrial.ingest import DocMeta, decide_doc_format

    meta = DocMeta(doc_id="x", pdf_path="", sha256="", n_pages=2, page_ratios=[0.707, 0.707],
                   creator="Microsoft PowerPoint", creator_hint="slides")
    out = decide_doc_format(settings, meta, None, settings.work / "pages")
    assert out.doc_format == "docs" and out.doc_format_source == "ratio"
    assert out.creator_hint == "slides"


def test_ingest_run_reports_input_issues(settings):
    from svmtrial import ingest

    _pdf(settings.documents_dir / "a.pdf")
    _pdf(settings.documents_dir / "b.pdf")
    (settings.documents_dir / "readme.docx").write_bytes(b"x")
    (settings.documents_dir / "broken.pdf").write_bytes(b"not a pdf")
    out = ingest.run(settings, input_group(settings), None, label_doc_ids={"a", "zz"})
    assert out["doc_ids"] == ["a", "b"] and out["image_based"] is True
    issues = pd.read_csv(settings.work / "ingest_issues.csv")
    got = {(r.doc_id, r.issue.split(" (")[0]) for r in issues.itertuples()}
    assert ("zz", "평가 시트에는 있으나 PDF 없음") in got
    assert ("b", "PDF는 있으나 평가 시트에 없음") in got
    assert ("readme.docx", "PDF 가 아니어서 제외") in got, "PDF 가 아닌 파일은 확장자까지 보여준다"
    assert ("broken", "PDF 를 이미지로 바꾸지 못함") in got


# ---------------------------------------------------------------- 사유 → 기준(차원)


def test_offline_cause_criteria_groups_by_object(settings):
    from svmtrial.offline_backend import OfflineBackend
    from svmtrial.payload import wrap
    from svmtrial.schemas import ConceptCandidates

    data = {"id_prefix": "K", "taxonomy": [], "causes": [
        {"cause": "향후 계획 미기입", "variants": ["향후 계획 미기입"], "n_ratings": 3, "n_docs": 2, "n_raters": 1},
        {"cause": "향후 계획 누락", "variants": ["향후 계획 누락"], "n_ratings": 1, "n_docs": 1, "n_raters": 1},
        {"cause": "사진 부적합", "variants": ["사진 부적합"], "n_ratings": 2, "n_docs": 2, "n_raters": 1},
        {"cause": "누락", "variants": ["누락"], "n_ratings": 1, "n_docs": 1, "n_raters": 1},
    ]}
    payload, _ = OfflineBackend(settings).generate(
        model="m", prompt_id="cause_criteria.v1", parts=[{"text": wrap(data)}], schema=ConceptCandidates)
    cs = ConceptCandidates.model_validate(payload).concepts
    assert [c.id for c in cs] == ["K001", "K002"]
    plan = next(c for c in cs if "향후 계획" in c.question)
    assert sorted(plan.cause_refs) == ["향후 계획 누락", "향후 계획 미기입"] and plan.source == "cause"
    photo = next(c for c in cs if "사진" in c.question)
    assert photo.type == "format" and "요건" in photo.question


def _discover_world(settings, monkeypatch):
    """발견/검증 분할을 고정한 작은 세계. 사유는 문서마다 다르게 둔다."""
    from svmtrial import concepts as C

    g = input_group(settings)
    targets = pd.DataFrame({"doc_id": [f"d{i}" for i in range(10)],
                            "y_majority": [1, 1, 1, 1, 0, 0, 0, 0, 0, 0]})
    split = pd.DataFrame({"doc_id": [f"d{i}" for i in range(10)],
                          "split": ["discovery"] * 6 + ["validation"] * 4})
    wd = settings.work / "concepts" / g.safe
    wd.mkdir(parents=True, exist_ok=True)
    split.to_csv(wd / "split.csv", index=False)
    lab = settings.work / "labels" / g.safe
    lab.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([
        {"doc_id": "d4", "rater": "P1", "cause": "향후 계획 미기입"},
        {"doc_id": "d5", "rater": "P2", "cause": "향후계획 미기입"},
        {"doc_id": "d5", "rater": "P1", "cause": "사진 부적합"},
        {"doc_id": "d8", "rater": "P1", "cause": "서명 누락"},          # 검증셋에만 있는 사유
    ]).assign(cause_key=lambda d: d["cause"].map(L.cause_key)).to_csv(lab / "causes_long.csv", index=False)
    for i in range(10):
        o = settings.work / "ocr" / f"d{i}"
        o.mkdir(parents=True, exist_ok=True)
        (o / "doc_outline.json").write_text(json.dumps({
            "doc_id": f"d{i}", "n_pages": 1, "headings": [{"page": 1, "level": 1, "text": "1. 향후 계획"}],
            "tables": [], "figures": [], "n_signatures": 0, "outline_text": "# 향후 계획", "pages": []},
            ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(C, "make_split", lambda *a, **k: split)
    return C, g, targets


def test_cause_criteria_use_discovery_set_only(settings, fake_gemini, monkeypatch):
    """누수 방지: 검증셋 문서의 사유는 기준을 만드는 LLM 입력에 들어가지 않는다."""
    from svmtrial.payload import extract

    C, g, targets = _discover_world(settings, monkeypatch)
    seen = {}

    def cause_resp(parts):
        seen.update(extract(parts[0]["text"]))
        return {"concepts": [{"id": "Z9", "question": '"향후 계획" 이 기입되어 있는가?', "section_id": None,
                              "type": "content_completeness", "actionable": True, "rationale": "r",
                              "source": "hypothesis",                       # 코드가 cause 로 고친다
                              "cause_refs": ["향후 계획 미기입", "향후계획  미기입"]}]}

    fg = fake_gemini({
        "cause_criteria": cause_resp,
        "concept_discovery": {"concepts": [
            {"id": "C1", "question": '"향후 계획" 항목이 있는가?', "section_id": "S1", "type": "structure",
             "actionable": True, "rationale": "r"},
            {"id": "C2", "question": '"결재란 서명" 이 있는가?', "section_id": None, "type": "format",
             "actionable": True, "rationale": "r"}]},
        "concept_merge": lambda parts: {"concepts": extract(parts[0]["text"])["concepts"][:2]},
    })
    out = C.discover(settings, g, fg, targets, "y_majority")
    sent = {c["cause"] for c in seen["causes"]}
    assert sent == {"향후 계획 미기입", "사진 부적합"}, f"발견셋 사유만 보내야 한다: {sent}"
    blob = json.dumps(seen, ensure_ascii=False)
    assert "P1" not in blob and "P2" not in blob and "d4" not in blob, "평가자 이름·문서명은 LLM 에 보내지 않는다"
    assert all(v in {"향후 계획 미기입", "향후계획 미기입"} for c in seen["causes"] for v in c["variants"]
               if "계획" in v)
    cc = out["cause_criteria"]
    assert cc["validation_only"] == ["서명 누락"]
    assert cc["unmapped"] == ["사진 부적합"], "기준에 안 들어간 사유는 따로 보고한다"
    k = out["concepts"][0]
    assert k["id"] == "K001" and k["source"] == "cause"
    assert sorted(k["cause_refs"]) == ["향후 계획 미기입", "향후계획 미기입"], "LLM 이 바꿔 적은 원문을 되돌린다"
    assert k["section_id"] == "S1", "겹친 가설의 섹션을 물려받는다"
    assert [d["id"] for d in out["dropped_as_duplicate_of_cause"]] == ["C1"]
    assert [c["id"] for c in out["concepts"]] == ["K001", "C2"]


def test_use_causes_off_skips_cause_step(settings, fake_gemini, monkeypatch):
    C, g, targets = _discover_world(settings, monkeypatch)
    settings.analysis.use_causes = False
    one = {"concepts": [{"id": "C1", "question": '"향후 계획" 항목이 있는가?', "section_id": None,
                         "type": "structure", "actionable": True, "rationale": "r"}]}
    fg = fake_gemini({"concept_discovery": one, "concept_merge": one})
    out = C.discover(settings, g, fg, targets, "y_majority")
    assert not any(c["prompt_id"].startswith("cause_criteria") for c in fg.calls)
    assert out["concepts"][0]["source"] == "hypothesis"


def test_cause_consistency():
    from svmtrial.concepts import cause_consistency
    from svmtrial.schemas import Concept

    cs = [Concept(id="K001", question="q", type="structure", actionable=True, rationale="r",
                  source="cause", cause_refs=["계획 미기입"]),
          Concept(id="K002", question="q", type="format", actionable=True, rationale="r",
                  source="cause", cause_refs=["사진 부적합"]),
          Concept(id="C001", question="q", type="format", actionable=True, rationale="r")]
    causes = pd.DataFrame({"doc_id": ["a", "b", "a", "c", "d"], "rater": ["P1"] * 5,
                           "cause": ["계획 미기입", "계획 미기입", "사진 부적합", "사진 부적합", "사진 부적합"]})
    causes["cause_key"] = causes["cause"].map(L.cause_key)
    answers = {"a": {"K001": "no", "K002": "yes"}, "b": {"K001": "no"}, "c": {"K002": "yes"}, "d": {"K002": "no"}}
    out = cause_consistency(cs, answers, causes)
    assert out["per_concept"]["K001"]["agreement"] == 1.0
    assert out["per_concept"]["K002"]["agreement"] == pytest.approx(1 / 3, abs=1e-3)
    assert out["low_agreement"] == ["K002"]
    assert "C001" not in out["per_concept"]


# ---------------------------------------------------------------- 회귀


def test_objective_json_with_svm_weights(settings):
    """method=svm 에서 numpy 스칼라가 objective.json 저장을 깨뜨리던 버그 (2026-10-14 발견)."""
    from svmtrial import modeling as M
    from svmtrial import objective as O
    from svmtrial.schemas import Concept

    rng = np.random.default_rng(1)
    X = pd.DataFrame(rng.integers(0, 2, (40, 3)).astype(float), columns=["K001", "K002", "C001"],
                     index=[f"d{i}" for i in range(40)])
    y = pd.Series(((X["K001"] + X["K002"] + rng.random(40)) > 1.4).astype(float), index=X.index)
    settings.analysis.method = "svm"
    fit = M.fit_primary(settings, X, y)
    assert all(type(v) is float for v in fit["weights"].values())
    cs = [Concept(id=c, question=c, type="structure", actionable=True, rationale="r") for c in X.columns]
    O.run(settings, input_group(settings), fit, X, y, cs)
    assert json.loads((settings.work / "models" / "InputData" / "objective.json").read_text(encoding="utf-8"))
