"""모든 pydantic 스키마 (SETUP.md §9).

규칙:
- Gemini 구조화 출력(`response_schema`)으로 넘기는 스키마에는 자유형 `dict` 필드를 두지 않는다.
  자유형 값(`meta` 등)은 코드가 채운다.
- Gemini 구조화 출력의 최상위는 객체여야 하므로, 목록 응답은 래퍼 모델로 감싼다.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# ---------------------------------------------------------------- S2 ocr

BlockType = Literal[
    "heading", "paragraph", "bullet", "table", "image", "photo", "chart", "diagram",
    "signature", "stamp", "form_field", "other",
]


class TableInfo(BaseModel):
    columns: list[str] = []
    n_rows: int = 0
    caption: str | None = None


class Block(BaseModel):
    type: BlockType
    level: int | None = Field(None, description="heading일 때 1~3")
    text: str = ""                       # 표/그림이면 요약 설명
    table: TableInfo | None = None


class PageLayout(BaseModel):
    page_role: Literal["cover", "body", "appendix", "blank"]
    orientation: Literal["portrait", "landscape"]
    looks_like: Literal["document", "slide", "form", "spreadsheet", "other"]
    blocks: list[Block]
    legibility: float = Field(ge=0, le=1)


class DocFormatGuess(BaseModel):
    """S1 3단계: 첫 2페이지를 보고 원본 형식을 판정."""

    doc_format: Literal["docs", "slides"]
    confidence: float = Field(ge=0, le=1)
    reason: str = ""


# ---------------------------------------------------------------- S4a sections


class TaxonomyItem(BaseModel):
    id: str = Field(description="예: D4")
    name: str
    synonyms: list[str] = []
    description: str = ""


class SectionTaxonomy(BaseModel):
    items: list[TaxonomyItem]


class TitleMapping(BaseModel):
    title: str
    section_id: str = Field(description="분류체계의 id, 해당 없으면 'OTHER'")


class TitleMappings(BaseModel):
    mappings: list[TitleMapping]


# ---------------------------------------------------------------- S4b/S4c concepts


class Concept(BaseModel):
    id: str                               # 예: C012
    question: str                         # "D4에 5Why(또는 동등한 단계적 원인분석) 표가 있는가?"
    section_id: str | None = None
    type: Literal["structure", "content_completeness", "format", "quantitative"]
    actionable: bool
    rationale: str


class ConceptCandidates(BaseModel):
    concepts: list[Concept]


class ConceptAnswer(BaseModel):
    id: str
    value: Literal["yes", "no", "na"]
    page: int | None = None
    evidence_quote: str | None = None


class ConceptAnswers(BaseModel):
    answers: list[ConceptAnswer]


# ---------------------------------------------------------------- S5/S6


class Evidence(BaseModel):
    concept_id: str
    verdict: Literal["확정", "유력", "참고"]
    pass_rate_with: float
    pass_rate_without: float
    n_with: int
    n_without: int
    q_value: float | None = None
    svm_weight: float | None = None
    sign_stability: float | None = None


class Element(BaseModel):
    kind: Literal["text", "table", "figure", "photo", "chart", "signature", "checklist"]
    title: str
    required: bool
    guidance: str                         # 작성 가이드(플레이스홀더 수준, 실제 내용 금지)
    table_columns: list[str] | None = None
    evidence_ids: list[str]               # 비면 검증 실패


class Section(BaseModel):
    order: int
    section_id: str
    title: str
    required: bool
    presence_in_pass: float
    guidance: str
    elements: list[Element]
    slides_hint: int | None = None
    pages_hint: float | None = None
    evidence_ids: list[str]


class TemplateSpecLLM(BaseModel):
    """Gemini에 넘기는 축소 스키마 (SETUP.md §8.6-2).

    통계값(`presence_in_pass` 등)과 `meta`/`evidence`는 코드가 채우므로 LLM은 구조와 문구만 만든다.
    """

    sections: list[Section]
    global_rules: list[Element]


class TemplateSpec(BaseModel):
    group: str
    doc_format: Literal["docs", "slides"]
    target_definition: str
    n_docs: int
    n_target_pass: int
    agreement_alpha: float | None = None
    sections: list[Section]
    global_rules: list[Element]           # 문서 전체 규칙(분량, 서명란 등)
    rater_conflicts: list[str] = []
    caveats: list[str] = []
    evidence: list[Evidence] = []
    meta: dict = {}                       # run_id, 모델 ID, 프롬프트 버전, 생성시각
