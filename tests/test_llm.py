import json
from enum import Enum
from typing import Literal, Optional

import pytest
from pydantic import BaseModel

from llm.cache import DiskCache, cache_key
from llm.errors import LLMError, LLMParseError, LLMRateLimited
from llm.factory import read_setting
from llm.gemini import GeminiClient
from llm.schema import to_gemini_schema


# ---------- schema conversion ----------
class Op(str, Enum):
    LTE = "<="
    GTE = ">="


class Step(BaseModel):
    value: float
    effective_from: Optional[str] = None


class Item(BaseModel):
    operator: Op
    kind: Literal["a", "b"]
    first: Step
    steps: list[Step]
    note: Optional[str] = None


def test_schema_inlines_refs_enums_and_nullable():
    out = to_gemini_schema(Item)
    props = out["properties"]
    assert out["type"] == "OBJECT" and "$defs" not in json.dumps(out) and "title" not in json.dumps(out)
    assert props["operator"] == {"type": "STRING", "enum": ["<=", ">="]}
    assert props["kind"]["enum"] == ["a", "b"]
    assert props["first"]["properties"]["effective_from"] == {"type": "STRING", "nullable": True}
    assert props["steps"]["type"] == "ARRAY" and props["steps"]["items"]["type"] == "OBJECT"
    assert props["note"]["nullable"] is True
    assert set(out["required"]) == {"operator", "kind", "first", "steps"}


def test_schema_rejects_unsupported_shapes():
    with pytest.raises(ValueError):
        to_gemini_schema({"type": "object", "properties": {"x": {"anyOf": [{"type": "string"}, {"type": "number"}]}}})
    with pytest.raises(ValueError):
        to_gemini_schema({"type": "array", "items": {"type": "string"}})


# ---------- cache ----------
def test_cache_roundtrip_and_key_sensitivity(tmp_path):
    cache = DiskCache(tmp_path)
    key = cache_key("gemini", "m", "sys", "prompt", {"a": 1}, 0.0)
    assert cache.get(key) is None
    cache.put(key, {"data": {"x": 1}, "text": "{}"})
    assert cache.get(key)["data"] == {"x": 1}
    assert key != cache_key("gemini", "other-model", "sys", "prompt", {"a": 1}, 0.0)
    assert key != cache_key("gemini", "m", "sys", "prompt2", {"a": 1}, 0.0)
    assert key != cache_key("gemini", "m", "sys", "prompt", {"a": 2}, 0.0)
    (tmp_path / key[:2] / f"{key}.json").write_text("not json", encoding="utf-8")
    assert cache.get(key) is None                      # corrupt file = miss


# ---------- client ----------
def ok(text, usage=None):
    return 200, {"candidates": [{"content": {"parts": [{"text": text}]}, "finishReason": "STOP"}],
                 "usageMetadata": usage or {"promptTokenCount": 5}}, {}


class FakeTransport:
    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []

    def __call__(self, url, headers, body, timeout):
        self.calls.append((url, headers, body, timeout))
        return self.responses.pop(0)


def client(transport, tmp_path=None, **kw):
    return GeminiClient("secret-key", "gemini-test", cache=DiskCache(tmp_path) if tmp_path else None,
                        transport=transport, sleep=lambda s: None, **kw)


def test_success_sends_schema_system_and_header_key(tmp_path):
    t = FakeTransport(ok('{"value": 1.5}'))
    result = client(t, tmp_path).generate_json("hello", Step, system="be exact")
    assert result.data == {"value": 1.5} and not result.cached and result.usage == {"promptTokenCount": 5}
    url, headers, body, _ = t.calls[0]
    assert url.endswith("/models/gemini-test:generateContent") and "secret-key" not in url
    assert headers == {"x-goog-api-key": "secret-key"}
    assert body["systemInstruction"]["parts"][0]["text"] == "be exact"
    assert body["generationConfig"]["responseMimeType"] == "application/json"
    assert body["generationConfig"]["temperature"] == 0.0
    assert body["generationConfig"]["responseSchema"]["properties"]["value"]["type"] == "NUMBER"


def test_cache_hit_makes_no_api_call(tmp_path):
    t = FakeTransport(ok('{"value": 2}'))
    c = client(t, tmp_path)
    first = c.generate_json("same prompt", Step)
    second = c.generate_json("same prompt", Step)
    assert second.cached and second.data == first.data == {"value": 2}
    assert len(t.calls) == 1 and c.calls == 1
    t2 = FakeTransport(ok('{"value": 3}'))
    assert client(t2, tmp_path).generate_json("different prompt", Step).data == {"value": 3}


def test_retries_429_then_succeeds():
    t = FakeTransport((429, {"error": {"message": "slow down"}}, {"Retry-After": "1"}), (503, {}, {}), ok('{"value": 1}'))
    assert client(t).generate_json("p", Step).data == {"value": 1} and len(t.calls) == 3


def test_persistent_429_raises_rate_limited():
    t = FakeTransport(*[(429, {"error": {"message": "quota"}}, {})] * 4)
    with pytest.raises(LLMRateLimited) as err:
        client(t).generate_json("p", Step)
    assert err.value.status == 429 and len(t.calls) == 4 and "secret-key" not in str(err.value)


def test_non_retryable_error_fails_fast_with_google_message():
    t = FakeTransport((404, {"error": {"message": "model is no longer available"}}, {}))
    with pytest.raises(LLMError, match="no longer available"):
        client(t).generate_json("p", Step)
    assert len(t.calls) == 1


def test_timeout_is_retried_once_only():
    t = FakeTransport((0, {"error": "timed out"}, {}), (0, {"error": "timed out"}, {}), ok('{"value": 1}'))
    with pytest.raises(LLMError, match="HTTP 0"):
        client(t).generate_json("p", Step)
    assert len(t.calls) == 2


def test_bad_answers_are_errors_and_not_cached(tmp_path):
    cache_dir = tmp_path
    with pytest.raises(LLMParseError):
        client(FakeTransport(ok("not json")), cache_dir).generate_json("p", Step)
    cut = (200, {"candidates": [{"content": {"parts": [{"text": '{"val'}]}, "finishReason": "MAX_TOKENS"}]}, {})
    with pytest.raises(LLMParseError, match="cut off"):
        client(FakeTransport(cut), cache_dir).generate_json("p", Step)
    with pytest.raises(LLMError, match="blocked"):
        client(FakeTransport((200, {"promptFeedback": {"blockReason": "SAFETY"}}, {})), cache_dir).generate_json("p", Step)
    assert not list(cache_dir.rglob("*.json"))


def test_thought_parts_are_ignored():
    payload = (200, {"candidates": [{"content": {"parts": [{"text": "thinking...", "thought": True}, {"text": '{"value": 4}'}]},
                                     "finishReason": "STOP"}]}, {})
    assert client(FakeTransport(payload)).generate_json("p", Step).data == {"value": 4}


def test_missing_key_or_model_is_a_clear_error():
    with pytest.raises(LLMError, match="GEMINI_API_KEY"):
        GeminiClient("", "m")
    with pytest.raises(LLMError, match="LLM_MODEL"):
        GeminiClient("k", "")


def test_read_setting_env_then_file(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("LLM_MODEL=from-file\nEMPTY=\n", encoding="utf-8")
    monkeypatch.delenv("LLM_MODEL", raising=False)
    assert read_setting("LLM_MODEL", env_file=env) == "from-file"
    monkeypatch.setenv("LLM_MODEL", "from-env")
    assert read_setting("LLM_MODEL", env_file=env) == "from-env"
    assert read_setting("EMPTY", "dflt", env_file=env) == "dflt"
