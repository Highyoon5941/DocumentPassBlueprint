"""분석 단위 = (doc_type, customer) 그룹 (SETUP.md §7.3)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from svmtrial.config import Settings

SEP = "__"
_SAFE = re.compile(r"[^\w가-힣.\-]+", re.UNICODE)


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


def discover_groups(s: Settings) -> list[Group]:
    """data/raw/pdfs/<doc_type>/<customer>/*.pdf 구조에서 그룹을 찾는다."""
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
    base = s.p("raw_pdfs", g.doc_type)
    if g.customer:
        base = base / g.customer
    return sorted(base.rglob("*.pdf")) if base.exists() else []


def work_dir(s: Settings, stage: str, g: Group | None = None) -> Path:
    p = s.work / stage
    if g is not None:
        p = p / g.safe
    p.mkdir(parents=True, exist_ok=True)
    return p
