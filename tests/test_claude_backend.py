"""Claude 백엔드 — **실제 호출 없음 (R6).** anthropic 클라이언트를 mock 으로 대체한다."""

from __future__ import annotations

import base64
import types

import pytest
from pydantic import BaseModel

from svmtrial import parts as P
from svmtrial.payload import OPEN, wrap


class Toy(BaseModel):
    n: int
    name: str


def _fake_resp(parsed=None, stop_reason="end_turn", stop_details=None,
               input_tokens=100, output_tokens=20, cache_read=0, cache_write=0):
    return types.SimpleNamespace(
        parsed_output=parsed,
        stop_reason=stop_reason,
        stop_details=stop_details,
        model="claude-sonnet-5-5",
        usage=types.SimpleNamespace(
            input_tokens=input_tokens, output_tokens=output_tokens,
            cache_read_input_tokens=cache_read, cache_creation_input_tokens=cache_write,
        ),
    )


def _backend(settings, resp=None, capture=None):
    """ClaudeBackend 를 만들고 anthropic 클라이언트만 가짜로 바꾼다."""
    from svmtrial.claude_backend import ClaudeBackend

    settings.backend = "claude"
    b = ClaudeBackend.__new__(ClaudeBackend)        # __init__ 의 anthropic.Anthropic() 우회
    b.s = settings
    b._pro_model = settings.claude.model_pro

    def parse(**kwargs):
        if capture is not None:
            capture.update(kwargs)
        return resp if resp is not None else _fake_resp(Toy(n=1, name="a"))

    b.client = types.SimpleNamespace(messages=types.SimpleNamespace(parse=parse))
    return b


# ---------------------------------------------------------------- 프롬프트 분할 / 캐시


def test_prompt_split_puts_stable_part_in_system(settings):
    """고정 지시문은 system 으로, 데이터 블록은 messages 로 가야 캐시가 걸린다."""
    cap: dict = {}
    b = _backend(settings, capture=cap)
    prompt = "고정 지시문입니다. 이 부분은 매 호출 같다.\n\n" + wrap({"doc_id": "D1", "page": 1})
    b.generate(model="claude-sonnet-5-5", prompt_id="ocr_page.v1",
               parts=[P.text(prompt)], schema=Toy)

    assert "system" in cap, "system 이 설정되지 않았다 → 캐시가 걸리지 않는다"
    sys_block = cap["system"][0]
    assert "고정 지시문" in sys_block["text"]
    assert OPEN not in sys_block["text"], "데이터 블록이 system(캐시 대상)에 섞였다"
    assert sys_block["cache_control"] == {"type": "ephemeral"}

    user_text = cap["messages"][0]["content"][0]["text"]
    assert OPEN in user_text and "고정 지시문" not in user_text


def test_prompt_cache_can_be_disabled(settings):
    cap: dict = {}
    settings.claude.prompt_cache = False
    b = _backend(settings, capture=cap)
    b.generate(model="claude-sonnet-5-5", prompt_id="p.v1",
               parts=[P.text("지시문\n\n" + wrap({"x": 1}))], schema=Toy)
    assert "cache_control" not in cap["system"][0]


def test_no_marker_means_no_system_block(settings):
    """데이터 블록 마커가 없으면 전체를 messages 로 보낸다(캐시 없음)."""
    cap: dict = {}
    b = _backend(settings, capture=cap)
    b.generate(model="claude-sonnet-5-5", prompt_id="p.v1", parts=[P.text("마커 없는 평문")], schema=Toy)
    assert "system" not in cap
    assert cap["messages"][0]["content"][0]["text"] == "마커 없는 평문"


# ---------------------------------------------------------------- 이미지 / effort


def test_images_are_base64_image_blocks(settings):
    cap: dict = {}
    b = _backend(settings, capture=cap)
    raw = b"\x89PNG\r\n\x1a\nFAKE"
    b.generate(model="claude-sonnet-5-5", prompt_id="ocr_page.v1",
               parts=[P.text("지시문\n\n" + wrap({})), P.image(raw)], schema=Toy)
    blocks = cap["messages"][0]["content"]
    img = [x for x in blocks if x["type"] == "image"]
    assert len(img) == 1
    assert img[0]["source"]["media_type"] == "image/png"
    assert base64.standard_b64decode(img[0]["source"]["data"]) == raw


def test_effort_differs_by_tier(settings):
    """대량 호출(FAST)은 낮은 effort, 추론(PRO)은 높은 effort."""
    cap_f, cap_p = {}, {}
    _backend(settings, capture=cap_f).generate(
        model=settings.claude.model_fast, prompt_id="ocr_page.v1",
        parts=[P.text("x\n\n" + wrap({}))], schema=Toy)
    _backend(settings, capture=cap_p).generate(
        model=settings.claude.model_pro, prompt_id="concept_discovery.v1",
        parts=[P.text("x\n\n" + wrap({}))], schema=Toy)
    assert cap_f["output_config"]["effort"] == settings.claude.effort_fast == "low"
    assert cap_p["output_config"]["effort"] == settings.claude.effort_pro == "high"


def test_adaptive_thinking_and_schema_passed(settings):
    cap: dict = {}
    b = _backend(settings, capture=cap)
    b.generate(model="claude-sonnet-5-5", prompt_id="p.v1",
               parts=[P.text("x\n\n" + wrap({}))], schema=Toy)
    assert cap["thinking"] == {"type": "adaptive"}
    assert cap["output_format"] is Toy, "pydantic 스키마를 그대로 넘겨야 SDK 가 검증한다"
    assert cap["max_tokens"] == settings.claude.max_tokens


# ---------------------------------------------------------------- 실패 경로


def test_refusal_raises_not_silently_passes(settings):
    """안전 분류기 거부는 200 응답으로 온다. 조용히 넘기면 빈 결과가 통계로 흘러간다."""
    b = _backend(settings, resp=_fake_resp(None, stop_reason="refusal",
                                           stop_details={"category": "bio"}))
    with pytest.raises(RuntimeError, match="거부"):
        b.generate(model="claude-sonnet-5-5", prompt_id="ocr_page.v1",
                   parts=[P.text("x\n\n" + wrap({}))], schema=Toy)


def test_max_tokens_truncation_raises(settings):
    b = _backend(settings, resp=_fake_resp(None, stop_reason="max_tokens"))
    with pytest.raises(RuntimeError, match="max_tokens"):
        b.generate(model="claude-sonnet-5-5", prompt_id="concept_scoring.v1",
                   parts=[P.text("x\n\n" + wrap({}))], schema=Toy)


def test_empty_parsed_output_raises(settings):
    b = _backend(settings, resp=_fake_resp(None, stop_reason="end_turn"))
    with pytest.raises(RuntimeError, match="구조화 출력이 비었"):
        b.generate(model="claude-sonnet-5-5", prompt_id="p.v1",
                   parts=[P.text("x\n\n" + wrap({}))], schema=Toy)


# ---------------------------------------------------------------- 사용량 / 캐시 키


def test_usage_includes_cache_tokens(settings):
    b = _backend(settings, resp=_fake_resp(Toy(n=2, name="b"), input_tokens=500,
                                           output_tokens=40, cache_read=450, cache_write=0))
    payload, usage = b.generate(model="claude-sonnet-5-5", prompt_id="p.v1",
                                parts=[P.text("x\n\n" + wrap({}))], schema=Toy)
    assert payload == {"n": 2, "name": "b"}
    assert usage.prompt_tokens == 500
    assert usage.cache_read_tokens == 450, "캐시 적중 토큰이 기록되지 않으면 비용 검증을 못 한다"


def test_cache_namespace_separates_claude_from_others(settings):
    """백엔드별 캐시 분리 — vertex/offline 응답이 claude 로 재사용되면 안 된다."""
    from svmtrial.gemini_client import GeminiClient

    ns = {}
    for be in ("offline", "vertex", "claude"):
        settings.backend = be
        settings.project = "proj"
        ns[be] = GeminiClient(settings, run_id="t")._cache_namespace()
    assert len(set(ns.values())) == 3, f"캐시 네임스페이스가 겹친다: {ns}"
    assert ns["claude"].startswith("claude/")


def test_effort_is_in_cache_namespace(settings):
    """effort 가 결과에 영향을 주므로 캐시 키에 들어가야 한다."""
    from svmtrial.gemini_client import GeminiClient

    settings.backend = "claude"
    a = GeminiClient(settings, run_id="t")._cache_namespace()
    settings.claude.effort_pro = "max"
    b = GeminiClient(settings, run_id="t")._cache_namespace()
    assert a != b


def test_model_for_uses_claude_models(settings):
    settings.backend = "claude"
    assert settings.model_for("fast") == "claude-sonnet-5-5"
    assert settings.model_for("pro") == "claude-opus-5-5"


def test_backend_validation_accepts_claude(monkeypatch):
    from svmtrial.config import load_settings

    monkeypatch.setenv("SVMTRIAL_BACKEND", "claude")
    assert load_settings().backend == "claude"
    monkeypatch.setenv("SVMTRIAL_BACKEND", "nonsense")
    with pytest.raises(RuntimeError, match="offline \\| vertex \\| claude"):
        load_settings()
