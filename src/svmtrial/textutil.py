"""문자열 정규화 유틸 (섹션 제목 매칭, 개념 문구 매칭에 공통으로 쓴다)."""

from __future__ import annotations

import re
import unicodedata

_PUNCT = re.compile(r"[^\w가-힣]+", re.UNICODE)
_WS = re.compile(r"\s+")
_NUMBERING = re.compile(r"^\s*(?:[0-9]+[.)]|[IVXivx]+[.)]|[가-힣][.)]|[(][0-9]+[)])\s*")

# 의미가 거의 없는 토큰 (더미 문서의 채움 문장 포함)
STOPWORDS = {
    "the", "a", "an", "of", "and", "or", "to", "for", "in", "on", "at", "by", "with",
    "lorem", "ipsum", "dolor", "sit", "amet", "consectetur", "adipiscing", "elit",
    "및", "등", "것", "수", "이", "그", "저", "있다", "한다", "대한", "관한", "통해",
}


def nfkc(s: str) -> str:
    return unicodedata.normalize("NFKC", s or "")


def norm(s: str) -> str:
    """비교용 정규화: NFKC → 소문자 → 번호 접두 제거 → 구두점 제거 → 공백 정리."""
    s = nfkc(s).lower().strip()
    s = _NUMBERING.sub("", s)
    s = _PUNCT.sub(" ", s)
    return _WS.sub(" ", s).strip()


def tokens(s: str, min_len: int = 2) -> list[str]:
    return [t for t in norm(s).split() if len(t) >= min_len and t not in STOPWORDS]


def token_set(s: str) -> set[str]:
    return set(tokens(s))


def jaccard(a: str, b: str) -> float:
    ta, tb = token_set(a), token_set(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def contains_tokens(haystack: str, needle: str, ratio: float = 0.6) -> bool:
    """needle 의 내용 토큰이 haystack 에 ratio 이상 들어 있는가."""
    nt = tokens(needle)
    if not nt:
        return False
    hay = norm(haystack)
    hit = sum(1 for t in nt if t in hay)
    return hit / len(nt) >= ratio


def strip_numbering(s: str) -> str:
    return _NUMBERING.sub("", nfkc(s).strip()).strip()


# ---------------------------------------------------------------- 문서 식별자

_PDF_EXT = re.compile(r"\.pdf$", re.IGNORECASE)


def doc_id(name: object) -> str:
    """파일명 → doc_id. PDF 파일명과 평가 시트의 FileName 에 **같은 규칙**을 적용한다.

    NFC(맥에서 만든 NFD 한글 파일명 대응) → 앞뒤 공백 제거 → 경로 제거 → `.pdf` 제거.
    NFKC 는 쓰지 않는다. `㈜` 같은 글자가 `(주)` 로 바뀌어 폴더 이름이 원본과 달라진다.
    """
    s = unicodedata.normalize("NFC", str(name)).strip()
    s = s.replace("\\", "/").rsplit("/", 1)[-1]
    return _PDF_EXT.sub("", s).strip()


def _loose_id(s: str) -> str:
    return _WS.sub("", nfkc(s)).casefold()


def match_doc_ids(sheet_ids: set[str], pdf_ids: set[str]) -> tuple[dict[str, str], list[dict]]:
    """시트 ID → PDF ID 대응. 정확히 같으면 그대로, 아니면 대소문자·공백·전각 차이만 허용한다.

    느슨한 비교로 **하나에만** 맞을 때만 잇는다. 둘 이상에 맞으면 잇지 않는다(오매칭 방지).
    반환: (대응표, 느슨하게 이은 내역 — ingest_issues 에 남긴다)
    """
    out = {d: d for d in sheet_ids if d in pdf_ids}
    loose: dict[str, list[str]] = {}
    for p in pdf_ids - set(out.values()):
        loose.setdefault(_loose_id(p), []).append(p)
    notes: list[dict] = []
    for d in sorted(sheet_ids - set(out)):
        cands = loose.get(_loose_id(d), [])
        if len(cands) == 1:
            out[d] = cands.pop()            # 같은 PDF 에 시트 행 두 개가 붙지 않게 꺼낸다
            notes.append({"doc_id": out[d], "issue": f"시트 FileName '{d}' 와 대소문자·공백만 달라 같은 문서로 연결함",
                          "path": ""})
    return out, notes


QUOTED = re.compile(r"[\"'“”‘’「『]([^\"'“”‘’」』]{3,})[\"'“”‘’」』]")


def quoted_phrase(s: str) -> str | None:
    """개념 질문에서 인용된 핵심 문구를 꺼낸다."""
    m = QUOTED.search(nfkc(s))
    return m.group(1).strip() if m else None


def best_section(title: str, taxonomy: list[dict]) -> str:
    """제목을 분류체계 id 에 매핑한다. 동의어 포함 토큰 Jaccard 최대값, 임계 미달이면 OTHER.

    sections.py(동의어 사전 1차 매칭)와 offline_backend.py 가 공유한다.
    """
    best, best_score = "OTHER", 0.0
    nt = norm(title)
    for it in taxonomy:
        cands = [str(it.get("name", "")), str(it.get("id", "")), *[str(x) for x in it.get("synonyms", [])]]
        score = 0.0
        for c in cands:
            nc = norm(c)
            if not nc:
                continue
            if nc in nt or nt.startswith(nc):
                score = max(score, 0.95)
            score = max(score, jaccard(title, c))
        if score > best_score:
            best, best_score = str(it.get("id", "OTHER")), score
    return best if best_score >= 0.34 else "OTHER"
