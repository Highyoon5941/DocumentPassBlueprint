"""설정 로드, 환경변수 치환, 캐시, 캐시 네임스페이스."""

from __future__ import annotations

import pytest

from svmtrial.cache import JsonCache, cache_key
from svmtrial.config import _subst_env, check_interpreter_hygiene, load_settings
from svmtrial.payload import extract, wrap


def test_env_substitution(monkeypatch):
    monkeypatch.setenv("MY_MODEL", "gemini-x-flash")
    assert _subst_env({"a": "${MY_MODEL}"}) == {"a": "gemini-x-flash"}
    monkeypatch.delenv("MY_MODEL", raising=False)
    assert _subst_env({"a": "${MY_MODEL}"}) == {"a": None}
    assert _subst_env({"a": "literal ${X} text"}) == {"a": "literal ${X} text"}


def test_backend_must_be_valid(monkeypatch):
    monkeypatch.setenv("SVMTRIAL_BACKEND", "nonsense")
    with pytest.raises(RuntimeError, match="SVMTRIAL_BACKEND"):
        load_settings()


def test_model_for_requires_id_on_vertex(monkeypatch, settings):
    settings.backend = "vertex"
    settings.gemini.model_fast = None
    with pytest.raises(RuntimeError, match="GEMINI_MODEL_FAST"):
        settings.model_for("fast")
    settings.backend = "offline"
    assert settings.model_for("fast") == "offline-fast"


def test_cache_roundtrip(tmp_path):
    c = JsonCache(tmp_path / "cache")
    k = cache_key("m", "p.v1", b"\x00\x01")
    assert c.get(k) is None
    c.put(k, {"x": 1}, meta={"m": "x"})
    assert c.get(k) == {"x": 1}
    assert c.stats() == {"hits": 1, "misses": 1}


def test_cache_key_is_stable_and_order_sensitive():
    assert cache_key("a", "b") == cache_key("a", "b")
    assert cache_key("a", "b") != cache_key("b", "a")
    assert cache_key(b"\x01") != cache_key("\x01")


def test_cache_disabled(tmp_path):
    c = JsonCache(tmp_path / "c", enabled=False)
    c.put("k", {"x": 1})
    assert c.get("k") is None


def test_cache_corrupt_file_is_miss(tmp_path):
    c = JsonCache(tmp_path / "c")
    k = cache_key("x")
    c.path(k).parent.mkdir(parents=True, exist_ok=True)
    c.path(k).write_text("{not json", encoding="utf-8")
    assert c.get(k) is None


def test_cache_namespace_separates_backends(settings, monkeypatch):
    """offline 스텁 응답이 vertex 전환 후 재사용되면 안 된다 (중요)."""
    from svmtrial.gemini_client import GeminiClient

    settings.backend = "offline"
    off = GeminiClient(settings, run_id="t")._cache_namespace()
    settings.backend = "vertex"
    settings.project = "proj-1"
    ver = GeminiClient(settings, run_id="t")._cache_namespace()
    assert off != ver
    assert off.startswith("offline/") and ver.startswith("vertex/")


def test_payload_roundtrip():
    data = {"group": "대책서__CUST_A", "titles": ["D4 근본원인", "D6 효과검증"], "n": 3}
    text = "지시문\n" + wrap(data) + "\n꼬리말"
    assert extract(text) == data


def test_payload_missing_block():
    assert extract("데이터 블록 없음") == {}
    assert extract("<<<SVMTRIAL_DATA\n{bad json\nSVMTRIAL_DATA>>>") == {}


def test_interpreter_hygiene_returns_list():
    out = check_interpreter_hygiene()
    assert isinstance(out, list)
