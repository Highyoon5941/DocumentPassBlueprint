"""Gemini 호출 입력 조각(part)의 백엔드 중립 표현.

`gemini_client` 밖에서는 google-genai 타입을 직접 쓰지 않는다 (R2).
백엔드가 vertex 인지 offline 인지에 관계없이 호출부는 이 두 함수만 쓴다.
"""

from __future__ import annotations

from typing import Any, TypedDict


class TextPart(TypedDict):
    text: str


class BlobPart(TypedDict):
    bytes: bytes
    mime_type: str


Part = TextPart | BlobPart


def text(s: str) -> TextPart:
    return {"text": s}


def image(data: bytes, mime_type: str = "image/png") -> BlobPart:
    return {"bytes": data, "mime_type": mime_type}


def digest_inputs(parts: list[Part]) -> list[Any]:
    """캐시 키 재료. 바이트는 그대로 넣어 sha256에 반영한다."""
    out: list[Any] = []
    for p in parts:
        if "text" in p:
            out.append(("t", p["text"]))
        else:
            out.append(("b", p["mime_type"]))
            out.append(p["bytes"])
    return out


def joined_text(parts: list[Part]) -> str:
    return "\n".join(p["text"] for p in parts if "text" in p)


def blobs(parts: list[Part]) -> list[BlobPart]:
    return [p for p in parts if "bytes" in p]  # type: ignore[misc]
