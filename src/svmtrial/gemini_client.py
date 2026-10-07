"""유일한 Gemini 진입점 (SETUP.md §8.0, 규칙 R2).

- 재시도: tenacity 로 429/500/503 에 지수 백오프
- 동시성: ThreadPoolExecutor(max_workers=config.gemini.max_workers)
- 캐시: sha256(model + prompt_id + 입력 + extra)
- 토큰 로깅: logs/<run_id>/gemini_calls.jsonl
- temperature=0 고정 (R9)
- `--dry-run`: 호출 없이 호출 수/예상 토큰만 집계

백엔드는 `.env` 의 `SVMTRIAL_BACKEND` 로 고른다.
  vertex  : google-genai → Vertex AI (실업무)
  offline : offline_backend 의 결정론적 규칙 (이 개발 PC. docs/migration_vertexAI.md)
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from svmtrial.cache import JsonCache, cache_key
from svmtrial.config import Settings
from svmtrial.parts import Part, digest_inputs

T = TypeVar("T", bound=BaseModel)

RETRYABLE_SUBSTRINGS = ("429", "500", "503", "RESOURCE_EXHAUSTED", "UNAVAILABLE", "INTERNAL", "DEADLINE")


class GeminiCallError(RuntimeError):
    pass


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, ValidationError | GeminiCallError):
        return False
    return any(s in str(exc) for s in RETRYABLE_SUBSTRINGS) or isinstance(exc, TimeoutError | ConnectionError)


@dataclass
class Usage:
    prompt_tokens: int = 0
    output_tokens: int = 0
    thoughts_tokens: int = 0
    cache_read_tokens: int = 0     # 프롬프트 캐시에서 읽은 입력 토큰 (Claude)
    cache_write_tokens: int = 0    # 프롬프트 캐시에 쓴 입력 토큰 (Claude)


@dataclass
class DryRunStats:
    calls: int = 0
    by_prompt: dict[str, int] = field(default_factory=dict)
    est_tokens: int = 0

    def add(self, prompt_id: str, est_tokens: int) -> None:
        self.calls += 1
        self.by_prompt[prompt_id] = self.by_prompt.get(prompt_id, 0) + 1
        self.est_tokens += est_tokens


class GeminiClient:
    def __init__(
        self,
        settings: Settings,
        run_id: str = "adhoc",
        use_cache: bool = True,
        dry_run: bool = False,
    ):
        self.s = settings
        self.run_id = run_id
        self.dry_run = dry_run
        self.cache = JsonCache(settings.work / "cache", enabled=use_cache)
        self.dry = DryRunStats()
        self.usage_total = Usage()
        self._lock = threading.Lock()
        self._log_path = settings.logs / run_id / "gemini_calls.jsonl"
        self._backend: Any | None = None

    # ------------------------------------------------------------ 백엔드

    @property
    def backend(self) -> Any:
        if self._backend is None:
            if self.s.backend == "vertex":
                from svmtrial.vertex_backend import VertexBackend

                self._backend = VertexBackend(self.s)
            elif self.s.backend == "claude":
                from svmtrial.claude_backend import ClaudeBackend

                self._backend = ClaudeBackend(self.s)
            else:
                from svmtrial.offline_backend import OfflineBackend

                self._backend = OfflineBackend(self.s)
        return self._backend

    @property
    def backend_name(self) -> str:
        return self.s.backend

    def _cache_namespace(self) -> str:
        """캐시 분리 키. offline 은 스텁 규칙 버전까지 포함해 규칙 변경 시 자동 무효화한다."""
        if self.s.backend == "offline":
            from svmtrial.offline_backend import STUB_VERSION

            return f"offline/v{STUB_VERSION}"
        if self.s.backend == "claude":
            # effort 가 결과에 영향을 주므로 키에 포함한다
            return f"claude/{self.s.claude.effort_fast}/{self.s.claude.effort_pro}"
        return f"vertex/{self.s.project}/{self.s.location}"

    # ------------------------------------------------------------ 로깅

    def _log(self, record: dict) -> None:
        with self._lock:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            with self._log_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    # ------------------------------------------------------------ 본체

    def generate_json(
        self,
        *,
        model: str,
        prompt_id: str,
        parts: list[Part],
        schema: type[T],
        cache_key_extra: str = "",
        media_resolution: str | None = None,
    ) -> T:
        """구조화 JSON 응답을 pydantic 모델로 돌려준다.

        1) 캐시 키 = sha256(model + prompt_id(버전 포함) + 입력 + extra)
        2) 캐시 적중 → 즉시 반환 (호출 없음)
        3) 백엔드 호출 (temperature=0, response_schema=schema)
        4) 검증 실패 → 오류 메시지를 붙여 1회 재요청 → 그래도 실패하면 예외
        5) usage 를 logs/<run_id>/gemini_calls.jsonl 에 기록
        """
        # 캐시 키에 backend 를 반드시 포함한다. 그러지 않으면 offline 스텁 응답이
        # vertex 로 전환한 뒤에도 재사용되어 실제 호출이 일어나지 않는다.
        key = cache_key(
            self._cache_namespace(), model, prompt_id, schema.__name__, cache_key_extra, *digest_inputs(parts)
        )
        cached = self.cache.get(key)
        if cached is not None:
            return schema.model_validate(cached)

        if self.dry_run:
            self.dry.add(prompt_id, self.s.gemini.est_tokens_per_page)
            raise DryRunHit(prompt_id)

        t0 = time.perf_counter()
        payload, usage, attempts = self._call_with_validation(
            model=model, prompt_id=prompt_id, parts=parts, schema=schema, media_resolution=media_resolution
        )
        dt = time.perf_counter() - t0

        obj = schema.model_validate(payload)
        self.cache.put(
            key,
            obj.model_dump(mode="json"),
            meta={"model": model, "prompt_id": prompt_id, "backend": self.s.backend, "run_id": self.run_id},
        )
        with self._lock:
            self.usage_total.prompt_tokens += usage.prompt_tokens
            self.usage_total.output_tokens += usage.output_tokens
            self.usage_total.thoughts_tokens += usage.thoughts_tokens
            self.usage_total.cache_read_tokens += usage.cache_read_tokens
            self.usage_total.cache_write_tokens += usage.cache_write_tokens
        self._log(
            {
                "run_id": self.run_id,
                "backend": self.s.backend,
                "model": model,
                "prompt_id": prompt_id,
                "schema": schema.__name__,
                "cache_key": key,
                "attempts": attempts,
                "seconds": round(dt, 3),
                "prompt_tokens": usage.prompt_tokens,
                "output_tokens": usage.output_tokens,
                "thoughts_tokens": usage.thoughts_tokens,
                "cache_read_tokens": usage.cache_read_tokens,
                "cache_write_tokens": usage.cache_write_tokens,
            }
        )
        return obj

    def _call_with_validation(
        self,
        *,
        model: str,
        prompt_id: str,
        parts: list[Part],
        schema: type[T],
        media_resolution: str | None,
    ) -> tuple[dict, Usage, int]:
        attempt_parts = list(parts)
        last_err: Exception | None = None
        for attempt in (1, 2):
            raw, usage = self._call_backend(
                model=model, prompt_id=prompt_id, parts=attempt_parts, schema=schema, media_resolution=media_resolution
            )
            try:
                schema.model_validate(raw)
                return raw, usage, attempt
            except ValidationError as e:
                last_err = e
                if attempt == 2:
                    break
                from svmtrial import parts as P

                attempt_parts = list(parts) + [
                    P.text(
                        "앞선 응답이 JSON 스키마 검증에 실패했다. 아래 오류를 고쳐 스키마에 맞는 JSON만 다시 출력하라.\n"
                        f"오류: {e}"
                    )
                ]
        raise GeminiCallError(f"{prompt_id}: 스키마 검증 2회 실패 — {last_err}")

    def _call_backend(
        self,
        *,
        model: str,
        prompt_id: str,
        parts: list[Part],
        schema: type[T],
        media_resolution: str | None,
    ) -> tuple[dict, Usage]:
        @retry(
            retry=retry_if_exception(_is_retryable),
            wait=wait_exponential(multiplier=1, min=1, max=60),
            stop=stop_after_attempt(self.s.gemini.max_retries),
            reraise=True,
        )
        def _go() -> tuple[dict, Usage]:
            return self.backend.generate(
                model=model, prompt_id=prompt_id, parts=parts, schema=schema, media_resolution=media_resolution
            )

        return _go()

    # ------------------------------------------------------------ 동시 실행

    def map_parallel(self, fn: Callable[..., Any], jobs: Sequence[Any], desc: str = "") -> list[Any]:
        """jobs 각 원소에 fn 을 적용한다. DryRunHit 은 None 으로 흘려보낸다."""
        workers = max(1, int(self.s.gemini.max_workers))
        if workers == 1 or len(jobs) <= 1:
            return [self._safe(fn, j) for j in jobs]
        with ThreadPoolExecutor(max_workers=workers) as ex:
            return list(ex.map(lambda j: self._safe(fn, j), jobs))

    @staticmethod
    def _safe(fn: Callable[..., Any], job: Any) -> Any:
        try:
            return fn(job)
        except DryRunHit:
            return None

    # ------------------------------------------------------------ 요약

    def summary(self) -> dict:
        out: dict[str, Any] = {
            "backend": self.s.backend,
            "cache": self.cache.stats(),
            "tokens": {
                "prompt": self.usage_total.prompt_tokens,
                "output": self.usage_total.output_tokens,
                "thoughts": self.usage_total.thoughts_tokens,
                "cache_read": self.usage_total.cache_read_tokens,
                "cache_write": self.usage_total.cache_write_tokens,
            },
        }
        if self.dry_run:
            out["dry_run"] = {
                "calls": self.dry.calls,
                "by_prompt": self.dry.by_prompt,
                "est_tokens": self.dry.est_tokens,
            }
            price = self.s.gemini.cost_per_1m_tokens or {}
            # 단가는 하드코딩하지 않는다. config 에 비어 있으면 비용 추정을 생략한다 (§8.0).
            if price:
                unit = float(price.get("input", 0)) + float(price.get("output", 0))
                out["dry_run"]["est_cost"] = round(self.dry.est_tokens / 1_000_000 * unit, 4)
        return out


class DryRunHit(Exception):
    """--dry-run 에서 실제 호출을 막기 위한 제어 흐름 예외."""

    def __init__(self, prompt_id: str):
        super().__init__(f"dry-run: {prompt_id}")
        self.prompt_id = prompt_id


# ---------------------------------------------------------------- 프롬프트 로딩


def load_prompt(settings: Settings, prompt_id: str, **vars: Any) -> str:
    """`prompts/<prompt_id>.md` 를 Jinja2 로 렌더링한다. prompt_id 에 버전이 들어 있다 (R9).

    예: load_prompt(s, "ocr_page.v1", group="대책서__CUST_A")
    """
    from jinja2 import Environment, StrictUndefined

    f = settings.prompts_dir / f"{prompt_id}.md"
    if not f.exists():
        raise FileNotFoundError(f"프롬프트 파일이 없습니다: {f}")
    env = Environment(undefined=StrictUndefined, keep_trailing_newline=True, autoescape=False)
    return env.from_string(f.read_text(encoding="utf-8")).render(**vars)


def prompt_versions(settings: Settings) -> dict[str, str]:
    """리포트 meta 용. 프롬프트 파일명과 sha256 앞 12자."""
    import hashlib

    out = {}
    for f in sorted(settings.prompts_dir.glob("*.md")):
        out[f.stem] = hashlib.sha256(f.read_bytes()).hexdigest()[:12]
    return out


def new_run_id() -> str:
    from datetime import datetime

    return os.getenv("SVMTRIAL_RUN_ID") or datetime.now().strftime("%Y%m%d_%H%M%S")


def iter_chunks(seq: Iterable[Any], n: int) -> Iterable[list[Any]]:
    buf: list[Any] = []
    for x in seq:
        buf.append(x)
        if len(buf) >= n:
            yield buf
            buf = []
    if buf:
        yield buf


def ensure_dir(p: Path) -> Path:
    p.mkdir(parents=True, exist_ok=True)
    return p
