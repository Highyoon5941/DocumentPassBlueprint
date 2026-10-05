"""Vertex AI (Gemini) 백엔드 — 실업무 환경의 유일한 외부 호출 지점 (R1, R2).

이 모듈 밖에서는 google-genai 를 import 하지 않는다.
설정은 `.env`(GOOGLE_CLOUD_PROJECT / GOOGLE_CLOUD_LOCATION / GEMINI_MODEL_*)에서만 읽는다.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel

from svmtrial.config import Settings
from svmtrial.parts import Part

_MEDIA_RES = {
    "LOW": "MEDIA_RESOLUTION_LOW",
    "MEDIUM": "MEDIA_RESOLUTION_MEDIUM",
    "HIGH": "MEDIA_RESOLUTION_HIGH",
}


class VertexBackend:
    def __init__(self, settings: Settings):
        from google import genai
        from google.genai import types

        self.s = settings
        self.types = types
        if not settings.project:
            raise RuntimeError(
                ".env 에 GOOGLE_CLOUD_PROJECT 가 없습니다. docs/migration_vertexAI.md §3 을 보세요."
            )
        # vertexai=True 는 신 명칭 enterprise=True 와 같은 뜻이다 (SETUP.md §3).
        self.client = genai.Client(
            vertexai=True,
            project=settings.project,
            location=settings.location,
            http_options=types.HttpOptions(
                retry_options=types.HttpRetryOptions(attempts=settings.gemini.max_retries)
            ),
        )

    # ------------------------------------------------------------------

    def _to_contents(self, parts: list[Part]) -> list[Any]:
        t = self.types
        out: list[Any] = []
        for p in parts:
            if "text" in p:
                out.append(t.Part.from_text(text=p["text"]))
            else:
                out.append(t.Part.from_bytes(data=p["bytes"], mime_type=p["mime_type"]))
        return out

    def generate(
        self,
        *,
        model: str,
        prompt_id: str,
        parts: list[Part],
        schema: type[BaseModel],
        media_resolution: str | None = None,
    ) -> tuple[dict, Any]:
        from svmtrial.gemini_client import Usage

        t = self.types
        cfg_kwargs: dict[str, Any] = {
            "temperature": 0,  # R9 재현성
            "response_mime_type": "application/json",
            "response_schema": schema,
        }
        if media_resolution:
            key = _MEDIA_RES.get(media_resolution.upper())
            if key:
                cfg_kwargs["media_resolution"] = getattr(t.MediaResolution, key)

        resp = self.client.models.generate_content(
            model=model,
            contents=self._to_contents(parts),
            config=t.GenerateContentConfig(**cfg_kwargs),
        )

        payload = self._payload(resp, schema)
        u = getattr(resp, "usage_metadata", None)
        usage = Usage(
            prompt_tokens=int(getattr(u, "prompt_token_count", 0) or 0),
            output_tokens=int(getattr(u, "candidates_token_count", 0) or 0),
            thoughts_tokens=int(getattr(u, "thoughts_token_count", 0) or 0),
        )
        return payload, usage

    @staticmethod
    def _payload(resp: Any, schema: type[BaseModel]) -> dict:
        """resp.parsed 를 우선 쓰고, 없으면 resp.text 를 JSON 으로 읽는다."""
        parsed = getattr(resp, "parsed", None)
        if isinstance(parsed, BaseModel):
            return parsed.model_dump(mode="json")
        if isinstance(parsed, dict):
            return parsed
        txt = (getattr(resp, "text", None) or "").strip()
        if not txt:
            raise RuntimeError(f"{schema.__name__}: 빈 응답 (안전 필터 차단 가능성). resp={resp!r:.300}")
        if txt.startswith("```"):
            txt = txt.strip("`").split("\n", 1)[-1]
            txt = txt.rsplit("```", 1)[0]
        return json.loads(txt)

    # ------------------------------------------------------------------

    def list_models(self) -> list[str]:
        names = set()
        for m in self.client.models.list(config={"query_base": True, "page_size": 200}):
            if m.name and "gemini" in m.name:
                names.add(m.name.split("/")[-1])
        return sorted(names)
