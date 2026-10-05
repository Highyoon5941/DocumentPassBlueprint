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
