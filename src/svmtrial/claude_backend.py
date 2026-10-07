"""Claude (Anthropic API) 백엔드 — 실업무용 LLM 경로 (R1, R2).

이 모듈 밖에서는 `anthropic` 을 import 하지 않는다. `vertex_backend.VertexBackend` 와
완전히 같은 `generate()` 인터페이스를 제공하므로, 호출부·스키마·캐시·프롬프트는 바뀌지 않는다.

설계 결정
- **구조화 출력**: `client.messages.parse(output_format=<pydantic 클래스>)` 를 쓴다.
  우리 파이프라인이 이미 pydantic 스키마 기반이므로 SDK 가 검증까지 해준다.
- **프롬프트 캐시**: 프롬프트를 `payload.OPEN` 마커에서 둘로 쪼개,
  **고정 지시문은 `system`(cache_control)** 으로, **가변 데이터 블록과 이미지는 `messages`** 로 보낸다.
  OCR 은 페이지마다 같은 지시문을 반복하므로 캐시 적중률이 높다.
- **tier 별 effort**: 대량 호출(FAST: OCR·채점)은 낮은 effort, 추론(PRO: 가설·템플릿)은 높은 effort.
- **거부 처리**: 안전 분류기 거부는 HTTP 200 + `stop_reason == "refusal"` 로 온다. 조용히 넘기지 않는다.
"""

from __future__ import annotations

import base64
from typing import Any

from pydantic import BaseModel

from svmtrial.config import Settings
from svmtrial.parts import Part, blobs, joined_text
from svmtrial.payload import OPEN


class ClaudeBackend:
    def __init__(self, settings: Settings):
        import anthropic

        self.s = settings
        self.anthropic = anthropic
        # 자격증명 해석 순서: ANTHROPIC_API_KEY → ANTHROPIC_AUTH_TOKEN → `ant auth login` 프로필
        self.client = anthropic.Anthropic(max_retries=settings.claude.sdk_max_retries)
        self._pro_model = settings.claude.model_pro

    # ------------------------------------------------------------------ 입력 조립

    @staticmethod
    def _split_prompt(parts: list[Part]) -> tuple[str | None, str]:
        """프롬프트를 (고정 지시문, 가변 데이터) 로 쪼갠다.

        고정부를 `system` 에 넣고 캐시를 걸면 같은 단계의 반복 호출에서 입력 비용이 줄어든다.
        마커가 없으면 전체를 가변부로 둔다(캐시 없음).
        """
        text = joined_text(parts)
        i = text.find(OPEN)
        if i <= 0:
            return None, text
        return text[:i].rstrip(), text[i:]

    def _image_blocks(self, parts: list[Part]) -> list[dict[str, Any]]:
        return [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": b["mime_type"],
                    "data": base64.standard_b64encode(b["bytes"]).decode("ascii"),
                },
            }
            for b in blobs(parts)
        ]

    def _effort(self, model: str) -> str:
        c = self.s.claude
        return c.effort_pro if model == self._pro_model else c.effort_fast

    # ------------------------------------------------------------------ 본체

    def generate(
        self,
        *,
        model: str,
        prompt_id: str,
        parts: list[Part],
        schema: type[BaseModel],
        media_resolution: str | None = None,   # Claude 에는 해당 개념이 없다 (서명만 맞춘다)
    ) -> tuple[dict, Any]:
        from svmtrial.gemini_client import Usage

        system_text, user_text = self._split_prompt(parts)
        content: list[dict[str, Any]] = [{"type": "text", "text": user_text}]
        content += self._image_blocks(parts)

        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": self.s.claude.max_tokens,
            "messages": [{"role": "user", "content": content}],
            "output_format": schema,
            # 4.6+ 모델은 adaptive 가 유일한 on-mode 이고, 깊이는 effort 로 조절한다.
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": self._effort(model)},
        }
        if system_text:
            block: dict[str, Any] = {"type": "text", "text": system_text}
            if self.s.claude.prompt_cache:
                block["cache_control"] = {"type": "ephemeral"}
            kwargs["system"] = [block]

        resp = self.client.messages.parse(**kwargs)

        # 안전 분류기 거부는 예외가 아니라 200 응답으로 온다.
        if resp.stop_reason == "refusal":
            raise RuntimeError(
                f"{prompt_id}: Claude 안전 분류기가 요청을 거부했습니다 "
                f"(stop_details={getattr(resp, 'stop_details', None)}). "
                "해당 문서/페이지를 확인하세요."
            )
        if resp.stop_reason == "max_tokens":
            raise RuntimeError(
                f"{prompt_id}: 출력이 max_tokens({self.s.claude.max_tokens})에서 잘렸습니다. "
                "config 의 claude.max_tokens 를 올리거나 analysis.concepts_per_scoring_call 을 낮추세요."
            )

        obj = resp.parsed_output
        if obj is None:
            raise RuntimeError(f"{prompt_id}: 구조화 출력이 비었습니다 (stop_reason={resp.stop_reason}).")

        u = resp.usage
        usage = Usage(
            prompt_tokens=int(getattr(u, "input_tokens", 0) or 0),
            output_tokens=int(getattr(u, "output_tokens", 0) or 0),
            cache_read_tokens=int(getattr(u, "cache_read_input_tokens", 0) or 0),
            cache_write_tokens=int(getattr(u, "cache_creation_input_tokens", 0) or 0),
        )
        payload = obj.model_dump(mode="json") if isinstance(obj, BaseModel) else dict(obj)
        return payload, usage

    # ------------------------------------------------------------------ 점검용

    def ping(self) -> dict[str, Any]:
        """연결·인증·구조화 출력을 한 번에 확인한다 (scripts/check_claude.py)."""

        class Probe(BaseModel):
            ok: bool
            seen: str

        resp = self.client.messages.parse(
            model=self.s.claude.model_fast,
            max_tokens=1024,
            messages=[{"role": "user", "content": "ok 를 true 로, seen 을 'hello' 로 돌려줘."}],
            output_format=Probe,
        )
        return {
            "model": resp.model,
            "parsed": resp.parsed_output.model_dump() if resp.parsed_output else None,
            "stop_reason": resp.stop_reason,
            "input_tokens": resp.usage.input_tokens,
            "output_tokens": resp.usage.output_tokens,
        }
