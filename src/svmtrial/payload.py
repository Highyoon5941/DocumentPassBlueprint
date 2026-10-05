"""프롬프트 안에 넣는 기계판독 데이터 블록.

모든 프롬프트는 사람이 읽는 지시문 + 아래 형식의 JSON 데이터 블록으로 구성된다.

    <<<SVMTRIAL_DATA
    { ... }
    SVMTRIAL_DATA>>>

- vertex 백엔드: Gemini 가 이 블록을 입력 컨텍스트로 읽는다(구조화된 입력이 정확도에 유리).
- offline 백엔드: 같은 블록을 프로그램이 파싱해 결정론적으로 응답을 만든다.

덕분에 호출부 코드가 백엔드에 따라 갈라지지 않는다.
"""

from __future__ import annotations

import json
from typing import Any

OPEN = "<<<SVMTRIAL_DATA"
CLOSE = "SVMTRIAL_DATA>>>"


def wrap(data: Any) -> str:
    return f"{OPEN}\n{json.dumps(data, ensure_ascii=False, indent=1, default=str)}\n{CLOSE}"


def extract(text: str) -> dict:
    """프롬프트 텍스트에서 데이터 블록을 꺼낸다. 없으면 빈 dict."""
    i = text.find(OPEN)
    if i < 0:
        return {}
    j = text.find(CLOSE, i)
    if j < 0:
        return {}
    body = text[i + len(OPEN) : j].strip()
    try:
        out = json.loads(body)
    except json.JSONDecodeError:
        return {}
    return out if isinstance(out, dict) else {"value": out}
