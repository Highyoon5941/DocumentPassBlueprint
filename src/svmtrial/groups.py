"""분석 단위(그룹)와 입력 문서 목록.

- inputdata 레이아웃 (기본, 2026-10-14 확정): `InputData/Documents/*.pdf` 전체가 **그룹 1개**다.
  그룹 이름은 `config.input.doc_type` (비우면 `InputData`).
- legacy 레이아웃: 분석 단위 = (doc_type, customer) 그룹 (SETUP.md §7.3).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from svmtrial import textutil as tu
from svmtrial.config import Settings

SEP = "__"
_SAFE = re.compile(r"[^\w가-힣.\-]+", re.UNICODE)
INPUT_GROUP = "InputData"


@dataclass(frozen=True)
class Group:
    doc_type: str
    customer: str | None = None  # pool_customers 이면 None

    @property
    def key(self) -> str:
        return f"{self.doc_type}{SEP}{self.customer}" if self.customer else self.doc_type

    @property
    def safe(self) -> str:
        """파일 시스템에 쓰는 이름. 한글은 유지하고 구분자만 정리한다."""
        return _SAFE.sub("_", self.key)

    def __str__(self) -> str:
        return self.key


def parse_group(key: str) -> Group:
    if SEP in key:
        dt, cu = key.split(SEP, 1)
        return Group(dt, cu or None)
    return Group(key)


def input_group(s: Settings) -> Group:
    """inputdata 레이아웃의 단일 그룹."""
    return Group((s.input.doc_type or "").strip() or INPUT_GROUP)


def list_documents(s: Settings) -> tuple[list[Path], list[dict]]:
    """`InputData/Documents` 아래 PDF 목록과 입력 이슈.

    - 하위 폴더까지 찾는다. 확장자는 대소문자를 가리지 않는다(`.PDF`).
    - PDF 가 아닌 파일, 이름이 겹치는 PDF(doc_id 기준)는 이슈로 남기고 제외한다.
    - Office 잠금파일(`~$…`)과 숨김파일은 조용히 건너뛴다.
    """
    root = s.documents_dir
    if not root.exists():
        return [], []
    pdfs: list[Path] = []
    issues: list[dict] = []
    seen: dict[str, Path] = {}
    for f in sorted(p for p in root.rglob("*") if p.is_file()):
        if f.name.startswith(("~$", ".")):
            continue
        did = tu.doc_id(f.name)
        if f.suffix.lower() != ".pdf":
            issues.append({"doc_id": did, "issue": "PDF 가 아니어서 제외", "path": str(f)})
            continue
        if did in seen:
            issues.append({"doc_id": did, "issue": f"파일명 중복 — {seen[did]} 만 사용", "path": str(f)})
            continue
        seen[did] = f
        pdfs.append(f)
    return pdfs, issues


def discover_groups(s: Settings) -> list[Group]:
    """inputdata: Documents 에 PDF 가 있으면 그룹 1개. legacy: data/raw/pdfs/<doc_type>/<customer>/*.pdf."""
    if s.inputdata:
        return [input_group(s)] if list_documents(s)[0] else []
    root = s.p("raw_pdfs")
    out: list[Group] = []
    if not root.exists():
        return out
    for dt_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        if s.analysis.pool_customers:
            if any(dt_dir.rglob("*.pdf")):
                out.append(Group(dt_dir.name))
            continue
        for cu_dir in sorted(p for p in dt_dir.iterdir() if p.is_dir()):
            if any(cu_dir.glob("*.pdf")):
                out.append(Group(dt_dir.name, cu_dir.name))
    return out


def pdfs_of(s: Settings, g: Group) -> list[Path]:
    if s.inputdata:
        return list_documents(s)[0]
    base = s.p("raw_pdfs", g.doc_type)
    if g.customer:
        base = base / g.customer
    return sorted(base.rglob("*.pdf")) if base.exists() else []


def input_hint(s: Settings) -> str:
    """사람에게 보여줄 입력 위치 안내."""
    if s.inputdata:
        return f"{s.documents_dir}/*.pdf + {s.judgement_path}"
    return f"{s.p('raw_pdfs')}/<doc_type>/<customer>/*.pdf + {s.p('labels')}/*.xlsx"


def work_dir(s: Settings, stage: str, g: Group | None = None) -> Path:
    p = s.work / stage
    if g is not None:
        p = p / g.safe
    p.mkdir(parents=True, exist_ok=True)
    return p
