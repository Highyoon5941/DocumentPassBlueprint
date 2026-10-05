"""offline 백엔드 스텁 — 실데이터에 쓸 수 없음을 포함한 동작 규약.

이 백엔드는 **개발 PC 전용**이다. 테스트는 vertex 전환 시 깨질 수 있는 지점을 고정한다.
"""

from __future__ import annotations

import io
import json

import pytest

from svmtrial import parts as P
from svmtrial.offline_backend import STUB_VERSION, OfflineBackend
from svmtrial.payload import wrap
from svmtrial.schemas import (
    ConceptAnswers,
    ConceptCandidates,
    DocFormatGuess,
    PageLayout,
    SectionTaxonomy,
    TitleMappings,
)


def _png(w: int, h: int) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (w, h), "white").save(buf, "PNG")
    return buf.getvalue()


def _gen(settings, prompt_id, schema, data=None, imgs=()):
    b = OfflineBackend(settings)
    parts = [P.text("지시문\n" + wrap(data or {}))] + [P.image(i) for i in imgs]
    payload, usage = b.generate(model="m", prompt_id=prompt_id, parts=parts, schema=schema)
    return schema.model_validate(payload), usage


def test_doc_format_from_aspect_ratio(settings):
    land, _ = _gen(settings, "doc_format.v1", DocFormatGuess, imgs=[_png(1600, 900), _png(1600, 900)])
    port, _ = _gen(settings, "doc_format.v1", DocFormatGuess, imgs=[_png(1240, 1754), _png(1240, 1754)])
    assert land.doc_format == "slides"
    assert port.doc_format == "docs"


def test_ocr_without_fixture_reports_unreadable(settings):
    """fixture 가 없으면 판독성 0 으로 돌려줘야 한다 — 조용히 그럴듯한 값을 만들지 않는다."""
    out, _ = _gen(settings, "ocr_page.v1", PageLayout,
                  data={"doc_id": "없는문서", "page": 1}, imgs=[_png(1240, 1754)])
    assert out.legibility == 0.0
    assert out.blocks == []
    assert out.looks_like == "other"


def test_ocr_with_fixture_parses_blocks(settings):
    fx = OfflineBackend.fixture_path(settings)
    fx.parent.mkdir(parents=True, exist_ok=True)
    fx.write_text(json.dumps({"DOC1/p001": {
        "doc_id": "DOC1", "page": 1, "page_role": "body", "legibility": 0.95,
        "lines": ["D4 Root cause",
                  "5-Why analysis table: Why1 | Why2 | Why3",
                  "[Chart] Defect rate trend graph",
                  "  lorem ipsum dolor sit amet "],
    }}, ensure_ascii=False), encoding="utf-8")
    out, _ = _gen(settings, "ocr_page.v1", PageLayout,
                  data={"doc_id": "DOC1", "page": 1}, imgs=[_png(1240, 1754)])
    kinds = [b.type for b in out.blocks]
    assert out.legibility == 0.95
    assert "heading" in kinds and "table" in kinds and "chart" in kinds
    tbl = next(b for b in out.blocks if b.type == "table")
    assert tbl.table.columns == ["Why1", "Why2", "Why3"]
    assert out.orientation == "portrait"


def test_section_taxonomy_merges_observed_titles(settings):
    out, _ = _gen(settings, "section_taxonomy.v1", SectionTaxonomy,
                  data={"titles": ["D4 Root cause", "근본원인 분석", "낯선 제목 X", "낯선 제목 X"]})
    ids = {i.id for i in out.items}
    assert "D4" in ids
    d4 = next(i for i in out.items if i.id == "D4")
    assert any("Root cause" in s for s in d4.synonyms)
    # seed 에 없고 2건 이상 관측된 제목은 새 후보로 올라온다
    assert any(i.id.startswith("X") for i in out.items)


def test_section_map_falls_back_to_other(settings):
    out, _ = _gen(settings, "section_map.v1", TitleMappings,
                  data={"titles": ["D6 효과검증", "완전히 무관한 문구 abcdef"],
                        "taxonomy": [{"id": "D6", "name": "실행 및 효과검증", "synonyms": ["효과검증"]}]})
    m = {x.title: x.section_id for x in out.mappings}
    assert m["D6 효과검증"] == "D6"
    assert m["완전히 무관한 문구 abcdef"] == "OTHER"


def test_concept_discovery_prefers_pass_only_lines(settings):
    pass_docs = [{"doc_id": f"P{i}", "outline_text": "# D4 근본원인\n5Why 단계적 원인분석 표 Why1 Why2\n(본문)"}
                 for i in range(4)]
    fail_docs = [{"doc_id": f"F{i}", "outline_text": "# D4 근본원인\n(본문)"} for i in range(4)]
    out, _ = _gen(settings, "concept_discovery.v1", ConceptCandidates,
                  data={"pass_docs": pass_docs, "fail_docs": fail_docs, "max_hypotheses": 5,
                        "taxonomy": [{"id": "D4", "name": "근본원인", "synonyms": ["근본원인"]}]})
    assert out.concepts, "Pass 쪽에만 있는 줄이 가설이 되어야 한다"
    c = out.concepts[0]
    assert "5Why" in c.question or "Why1" in c.question
    assert c.section_id == "D4", "줄이 들어 있던 상위 제목으로 섹션이 잡혀야 한다"
    assert '"' in c.question, "핵심 문구가 인용되어야 한다(채점이 이 문구를 쓴다)"


def test_concept_discovery_ignores_boilerplate(settings):
    pass_docs = [{"doc_id": f"P{i}", "outline_text": "# D2\n  lorem ipsum dolor sit amet \n(본문)"}
                 for i in range(4)]
    fail_docs = [{"doc_id": f"F{i}", "outline_text": "# D2\n"} for i in range(4)]
    out, _ = _gen(settings, "concept_discovery.v1", ConceptCandidates,
                  data={"pass_docs": pass_docs, "fail_docs": fail_docs, "max_hypotheses": 5})
    assert not any("lorem" in c.question.lower() for c in out.concepts)


def test_concept_merge_dedupes(settings):
    cs = [{"id": f"C{i:03d}", "question": 'D4 에 "5Why 표" 가 있는가?', "section_id": "D4",
           "type": "structure", "actionable": True, "rationale": "r"} for i in range(1, 4)]
    cs.append({"id": "C009", "question": 'D6 에 "효과 그래프" 가 있는가?', "section_id": "D6",
               "type": "quantitative", "actionable": True, "rationale": "r"})
    out, _ = _gen(settings, "concept_merge.v1", ConceptCandidates, data={"concepts": cs})
    assert len(out.concepts) == 2
    assert [c.id for c in out.concepts] == ["C001", "C002"], "id 를 다시 매겨야 한다"


def test_concept_scoring_matches_quoted_phrase(settings):
    data = {
        "doc_id": "D1",
        "outline_text": "# D4 근본원인\n5Why 단계적 원인분석 표 Why1 Why2 Why3",
        "pages": [{"page": 1, "text": "5Why 단계적 원인분석 표 Why1 Why2 Why3"}],
        "concepts": [
            {"id": "C001", "question": 'D4 에 "5Why 단계적 원인분석 표" 가 있는가?', "type": "structure"},
            {"id": "C002", "question": 'D6 에 "대책 전후 불량률 추이 그래프" 가 있는가?', "type": "quantitative"},
        ],
    }
    out, _ = _gen(settings, "concept_scoring.v1", ConceptAnswers, data=data)
    got = {a.id: a.value for a in out.answers}
    assert got == {"C001": "yes", "C002": "no"}
    yes = next(a for a in out.answers if a.value == "yes")
    assert yes.page == 1 and yes.evidence_quote


def test_scoring_answers_every_concept(settings):
    """질문 목록의 모든 id 에 답이 하나씩 와야 한다 (빠뜨리거나 추가하지 않는다)."""
    ids = [f"C{i:03d}" for i in range(1, 11)]
    data = {"doc_id": "D", "outline_text": "", "pages": [],
            "concepts": [{"id": i, "question": f'"{i} 문구"', "type": "structure"} for i in ids]}
    out, _ = _gen(settings, "concept_scoring.v1", ConceptAnswers, data=data)
    assert [a.id for a in out.answers] == ids


def test_unknown_prompt_id_raises(settings):
    b = OfflineBackend(settings)
    with pytest.raises(RuntimeError, match="처리기가 없습니다"):
        b.generate(model="m", prompt_id="made_up.v1", parts=[P.text("x")], schema=PageLayout)


def test_stub_version_is_in_cache_namespace(settings):
    from svmtrial.gemini_client import GeminiClient

    settings.backend = "offline"
    ns = GeminiClient(settings, run_id="t")._cache_namespace()
    assert STUB_VERSION in ns, "스텁 규칙 버전이 캐시 키에 들어가야 규칙 변경 시 자동 무효화된다"
