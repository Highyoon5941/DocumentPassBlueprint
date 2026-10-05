"""sha256 키 기반 JSON 캐시 (SETUP.md §8.0).

Gemini 응답을 디스크에 남겨, 중단 후 재실행이나 429 재시도에서 같은 호출을 반복하지 않는다 (R9).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def cache_key(*parts: Any) -> str:
    """순서가 있는 입력들로 안정적인 sha256 키를 만든다.

    bytes 는 그대로, 그 외는 repr 가 아닌 str/json 으로 정규화해 플랫폼 간 동일하게 만든다.
    """
    h = hashlib.sha256()
    for p in parts:
        if isinstance(p, bytes | bytearray):
            h.update(b"\x00b")
            h.update(bytes(p))
        elif isinstance(p, dict | list | tuple):
            h.update(b"\x00j")
            h.update(json.dumps(p, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8"))
        else:
            h.update(b"\x00s")
            h.update(str(p).encode("utf-8"))
    return h.hexdigest()


class JsonCache:
    """`<work>/cache/<키 앞 2자>/<키>.json` 에 저장한다."""

    def __init__(self, root: Path, enabled: bool = True):
        self.root = Path(root)
        self.enabled = enabled
        self.hits = 0
        self.misses = 0

    def path(self, key: str) -> Path:
        return self.root / key[:2] / f"{key}.json"

    def get(self, key: str) -> Any | None:
        if not self.enabled:
            return None
        f = self.path(key)
        if not f.exists():
            self.misses += 1
            return None
        try:
            payload = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            self.misses += 1
            return None
        self.hits += 1
        return payload.get("value")

    def put(self, key: str, value: Any, meta: dict | None = None) -> None:
        if not self.enabled:
            return
        f = self.path(key)
        f.parent.mkdir(parents=True, exist_ok=True)
        tmp = f.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps({"meta": meta or {}, "value": value}, ensure_ascii=False, indent=1),
            encoding="utf-8",
        )
        tmp.replace(f)

    def stats(self) -> dict[str, int]:
        return {"hits": self.hits, "misses": self.misses}
