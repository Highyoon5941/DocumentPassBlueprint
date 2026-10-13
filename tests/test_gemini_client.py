"""gemini_client — 캐시, 검증 재요청, dry-run, 프롬프트 로딩. **실제 호출 없음 (R6).**"""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from svmtrial import parts as P
from svmtrial.gemini_client import (
    DryRunHit,
    GeminiCallError,
    GeminiClient,
    Usage,
    _is_retryable,
    load_prompt,
    prompt_versions,
)


class Toy(BaseModel):
    n: int
    name: str


class StubBackend:
    """호출 횟수를 세는 가짜 백엔드."""

    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.calls = 0

    def generate(self, *, model, prompt_id, parts, schema, media_resolution=None):
        self.calls += 1
        p = self.payloads[min(self.calls - 1, len(self.payloads) - 1)]
        return p, Usage(prompt_tokens=10, output_tokens=5)


def _client(settings, payloads, **kw):
    gc = GeminiClient(settings, run_id="t", **kw)
    gc._backend = StubBackend(payloads)
    return gc


def test_cache_prevents_second_call(settings):
    gc = _client(settings, [{"n": 1, "name": "a"}])
    args = dict(model="m", prompt_id="p.v1", parts=[P.text("x")], schema=Toy)
    a = gc.generate_json(**args)
    b = gc.generate_json(**args)
    assert a == b == Toy(n=1, name="a")
    assert gc._backend.calls == 1, "두 번째 호출은 캐시에서 와야 한다"
    assert gc.cache.stats()["hits"] == 1


def test_different_inputs_miss_cache(settings):
    gc = _client(settings, [{"n": 1, "name": "a"}])
    gc.generate_json(model="m", prompt_id="p.v1", parts=[P.text("x")], schema=Toy)
    gc.generate_json(model="m", prompt_id="p.v1", parts=[P.text("y")], schema=Toy)
    assert gc._backend.calls == 2


def test_prompt_version_changes_cache_key(settings):
    gc = _client(settings, [{"n": 1, "name": "a"}])
    gc.generate_json(model="m", prompt_id="p.v1", parts=[P.text("x")], schema=Toy)
    gc.generate_json(model="m", prompt_id="p.v2", parts=[P.text("x")], schema=Toy)
    assert gc._backend.calls == 2, "프롬프트 버전이 바뀌면 다시 호출해야 한다"


def test_image_bytes_affect_cache_key(settings):
    gc = _client(settings, [{"n": 1, "name": "a"}])
    gc.generate_json(model="m", prompt_id="p.v1", parts=[P.image(b"\x89PNG-1")], schema=Toy)
    gc.generate_json(model="m", prompt_id="p.v1", parts=[P.image(b"\x89PNG-2")], schema=Toy)
    assert gc._backend.calls == 2


def test_validation_failure_retries_once_then_succeeds(settings):
    gc = _client(settings, [{"n": "틀림"}, {"n": 2, "name": "b"}])
    out = gc.generate_json(model="m", prompt_id="p.v1", parts=[P.text("x")], schema=Toy)
    assert out == Toy(n=2, name="b")
    assert gc._backend.calls == 2


def test_validation_failure_twice_raises(settings):
    gc = _client(settings, [{"bad": 1}])
    with pytest.raises(GeminiCallError, match="스키마 검증 2회 실패"):
        gc.generate_json(model="m", prompt_id="p.v1", parts=[P.text("x")], schema=Toy)
    assert gc._backend.calls == 2


def test_dry_run_makes_no_call_and_counts(settings):
    gc = _client(settings, [{"n": 1, "name": "a"}], dry_run=True)
    with pytest.raises(DryRunHit):
        gc.generate_json(model="m", prompt_id="ocr_page.v1", parts=[P.text("x")], schema=Toy)
    assert gc._backend.calls == 0
    d = gc.summary()["dry_run"]
    assert d["calls"] == 1 and d["by_prompt"] == {"ocr_page.v1": 1} and d["est_tokens"] > 0


def test_dry_run_cost_omitted_without_price(settings):
    settings.gemini.cost_per_1m_tokens = {}
    gc = _client(settings, [{"n": 1, "name": "a"}], dry_run=True)
    with pytest.raises(DryRunHit):
        gc.generate_json(model="m", prompt_id="p.v1", parts=[P.text("x")], schema=Toy)
    assert "est_cost" not in gc.summary()["dry_run"]

    settings.gemini.cost_per_1m_tokens = {"input": 0.3, "output": 2.5}
    gc2 = _client(settings, [{"n": 1, "name": "a"}], dry_run=True)
    with pytest.raises(DryRunHit):
        gc2.generate_json(model="m", prompt_id="p.v1", parts=[P.text("x")], schema=Toy)
    assert "est_cost" in gc2.summary()["dry_run"]


def test_token_logging(settings):
    gc = _client(settings, [{"n": 1, "name": "a"}])
    gc.generate_json(model="m", prompt_id="p.v1", parts=[P.text("x")], schema=Toy)
    log = settings.logs / "t" / "gemini_calls.jsonl"
    assert log.exists()
    import json

    rec = json.loads(log.read_text(encoding="utf-8").strip())
    assert rec["prompt_tokens"] == 10 and rec["output_tokens"] == 5 and rec["prompt_id"] == "p.v1"
    assert gc.summary()["tokens"]["prompt"] == 10


def test_force_disables_cache(settings):
    gc = GeminiClient(settings, run_id="t", use_cache=False)
    gc._backend = StubBackend([{"n": 1, "name": "a"}])
    args = dict(model="m", prompt_id="p.v1", parts=[P.text("x")], schema=Toy)
    gc.generate_json(**args)
    gc.generate_json(**args)
    assert gc._backend.calls == 2


@pytest.mark.parametrize("msg,expected", [
    ("429 RESOURCE_EXHAUSTED", True),
    ("503 UNAVAILABLE", True),
    ("500 INTERNAL", True),
    ("404 model not found", False),
    ("403 PERMISSION_DENIED", False),
])
def test_retryable_classification(msg, expected):
    assert _is_retryable(RuntimeError(msg)) is expected


def test_all_prompts_render(settings):
    """프롬프트 디렉토리의 **모든** 파일이 렌더링되어야 한다 (변수 누락 조기 발견).

    고정 목록이 아니라 디렉토리를 스캔한다 — 프롬프트를 추가하면 자동으로 포함된다.
    """
    common = {"data_block": "{}", "max_hypotheses": 15, "id_prefix": "C",
              "min_keep": 30, "max_keep": 60}
    files = sorted(settings.prompts_dir.glob("*.md"))
    assert files, "프롬프트 파일이 없다"
    for f in files:
        text = load_prompt(settings, f.stem, **common)
        assert len(text) > 100, f.stem
        assert "{{" not in text, f"{f.stem}: 치환되지 않은 변수가 남았다"
    assert len(prompt_versions(settings)) == len(files)


def test_prompt_v2_variants_are_domain_neutral(settings):
    """자율 모드 v2 프롬프트에는 특정 도메인 용어가 없어야 한다.

    v1 은 '5Why' 같은 실제 기법 이름을 예시로 들어 LLM 을 그쪽으로 유도한다.
    자율 모드의 전제(사람 의견 배제)를 지키려면 v2 에는 그런 용어가 없어야 한다.
    """
    banned = ["5why", "5-why", "수평전개", "근본원인", "8d", "효과검증", "5w2h"]
    for stem in ("concept_discovery.v2", "concept_scoring.v2", "section_taxonomy.v2"):
        text = load_prompt(settings, stem, data_block="{}", max_hypotheses=15,
                           id_prefix="C", min_keep=30, max_keep=60).lower()
        hits = [b for b in banned if b in text]
        assert not hits, f"{stem} 에 도메인 용어가 남아 있다: {hits}"


def test_prompt_version_switches_with_autonomous(settings):
    from svmtrial.sections import prompt_version

    settings.analysis.autonomous = False
    assert prompt_version(settings, "concept_discovery") == "concept_discovery.v1"
    settings.analysis.autonomous = True
    assert prompt_version(settings, "concept_discovery") == "concept_discovery.v2"


def test_missing_prompt_raises(settings):
    with pytest.raises(FileNotFoundError):
        load_prompt(settings, "does_not_exist.v9", data_block="{}")
