"""문서 종류별 섹션 분류체계 seed (SETUP.md §8.4-3).

seed 는 **초기값**이다. Gemini 가 데이터에 맞게 수정할 수 있고, 최종 결정은 사람이 🔒에서 한다.
새 문서 종류(요구사양서, RMP, 외주 계산서 등)를 추가할 때 여기에 항목을 넣는다.
"""

from __future__ import annotations

from typing import Any

# 대책서 = 8D
SEED_8D: list[dict[str, Any]] = [
    {"id": "D0", "name": "긴급대응", "synonyms": ["emergency response", "d0", "초동대응"]},
    {"id": "D1", "name": "팀 구성", "synonyms": ["team", "d1", "팀"]},
    {"id": "D2", "name": "문제 정의 (5W2H)", "synonyms": ["problem description", "5w2h", "d2", "문제정의", "현상"]},
    {"id": "D3", "name": "임시조치 / 봉쇄", "synonyms": ["containment", "interim action", "d3", "봉쇄", "임시조치"]},
    {"id": "D4", "name": "근본원인 (발생/유출)", "synonyms": ["root cause", "d4", "근본원인", "원인분석"]},
    {"id": "D5", "name": "영구대책", "synonyms": ["corrective action", "permanent", "d5", "영구대책", "대책"]},
    {"id": "D6", "name": "실행 및 효과검증", "synonyms": ["effectiveness", "verification", "d6", "효과검증", "검증"]},
    {"id": "D7", "name": "재발방지 / 표준화·수평전개",
     "synonyms": ["prevent recurrence", "standardization", "horizontal deployment", "d7", "수평전개", "표준화"]},
    {"id": "D8", "name": "종결", "synonyms": ["closure", "d8", "종결", "승인"]},
]

SEEDS: dict[str, list[dict[str, Any]]] = {"대책서": SEED_8D}


def seed_for(doc_type: str) -> list[dict[str, Any]]:
    """문서 종류의 seed. 없으면 빈 목록(= 데이터에서만 분류체계를 만든다)."""
    return [dict(x) for x in SEEDS.get(doc_type, [])]
