"""공통 픽스처. **테스트는 Gemini 를 호출하지 않는다 (R6).**

offline 백엔드조차 쓰지 않고, `GeminiClient.generate_json` 을 mock 으로 바꾼다.
실제 호출이 필요한 점검은 `scripts/check_gemini.py` 로만 한다.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """테스트 중 실수로 네트워크를 쓰면 즉시 실패시킨다."""
    import socket

    def guard(*a, **k):
        raise AssertionError("테스트에서 네트워크를 쓰려고 했습니다 (R6 위반).")

    monkeypatch.setattr(socket.socket, "connect", guard)
    monkeypatch.setenv("SVMTRIAL_BACKEND", "offline")
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)


@pytest.fixture
def settings(tmp_path, monkeypatch):
    from svmtrial.config import load_settings

    monkeypatch.setenv("SVMTRIAL_BACKEND", "offline")
    s = load_settings(ROOT)
    s.paths.work = str(tmp_path / "work")
    s.paths.outputs = str(tmp_path / "outputs")
    s.root = ROOT
    (tmp_path / "work").mkdir(parents=True, exist_ok=True)
    # work/outputs 를 tmp 로 돌리기 위해 root 를 tmp 로 두고 prompts 만 실제 경로를 쓴다
    object.__setattr__(s, "root", tmp_path)
    monkeypatch.setattr(type(s), "prompts_dir", property(lambda self: ROOT / "prompts"))
    s.paths.work = "work"
    s.paths.outputs = "outputs"
    return s


class FakeGemini:
    """generate_json 을 고정 응답으로 대체하는 mock. 호출 기록을 남긴다."""

    def __init__(self, responses: dict[str, object] | None = None, backend: str = "mock"):
        self.responses = responses or {}
        self.calls: list[dict] = []
        self.backend_name = backend
        self.dry_run = False
        self.s = None

    def generate_json(self, *, model, prompt_id, parts, schema, cache_key_extra="", media_resolution=None):
        self.calls.append({"model": model, "prompt_id": prompt_id, "schema": schema.__name__,
                           "cache_key_extra": cache_key_extra, "n_parts": len(parts)})
        key = prompt_id.split(".")[0]
        if key not in self.responses:
            raise AssertionError(f"FakeGemini 에 {key} 응답이 등록되지 않았습니다")
        payload = self.responses[key]
        if callable(payload):
            payload = payload(parts)
        return schema.model_validate(payload)

    def map_parallel(self, fn, jobs, desc=""):
        return [fn(j) for j in jobs]

    def summary(self):
        return {"backend": self.backend_name, "cache": {"hits": 0, "misses": len(self.calls)},
                "tokens": {"prompt": 0, "output": 0, "thoughts": 0}}


@pytest.fixture
def fake_gemini():
    return FakeGemini
