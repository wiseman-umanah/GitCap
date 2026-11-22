import pytest

from gitcap import groq
from gitcap.errors import GitCapError, PromptTooLarge

CHAT = "/openai/v1/chat/completions"
MODELS = "/openai/v1/models"


@pytest.fixture
def api(server, monkeypatch):
    monkeypatch.setattr(groq, "API_BASE", server.url + "/openai/v1")
    monkeypatch.setattr(groq.time, "sleep", lambda s: None)
    return server


def ok(text="feat: groq", model="openai/gpt-oss-120b"):
    return lambda body: (200, {
        "model": body.get("model", model),
        "choices": [{"message": {"content": text}}],
        "usage": {"prompt_tokens": 500, "completion_tokens": 12, "prompt_tokens_details": {"cached_tokens": 100}},
    })


def test_generate_success_and_request_shape(api):
    api.routes[f"POST {CHAT}"] = ok()
    gen = groq.generate("PROMPT", "gsk_test")
    assert gen.text == "feat: groq" and gen.source == "groq"
    assert (gen.usage.input_tokens, gen.usage.cache_read_tokens, gen.usage.output_tokens) == (400, 100, 12)
    [body] = api.bodies(CHAT)
    assert body["model"] == groq.DEFAULT_MODEL
    assert body["messages"][-1]["content"] == "PROMPT"
    assert body["max_completion_tokens"] == groq.MAX_OUTPUT_TOKENS
    assert body["reasoning_effort"] == "low"  # gpt-oss: keep reasoning short
    auth = [r for r in api.requests if r.path == CHAT]
    assert auth


def test_non_gpt_oss_models_get_no_reasoning_params(api):
    api.routes[f"POST {CHAT}"] = ok()
    groq.generate("p", "k", model="qwen/qwen3.8-27b")
    [body] = api.bodies(CHAT)
    assert "reasoning_effort" not in body and "include_reasoning" not in body


def test_invalid_key(api):
    api.routes[f"POST {CHAT}"] = lambda b: (401, {"error": {"message": "Invalid API Key", "code": "invalid_api_key"}})
    with pytest.raises(GitCapError, match="key is invalid"):
        groq.generate("p", "bad")


def test_request_too_large_reports_shrink_ratio(api):
    msg = "Request too large for model `openai/gpt-oss-120b` on tokens per minute (TPM): Limit 8000, Requested 10000, please reduce"
    api.routes[f"POST {CHAT}"] = lambda b: (413, {"error": {"message": msg, "code": "rate_limit_exceeded"}})
    with pytest.raises(PromptTooLarge) as info:
        groq.generate("p", "k")
    assert info.value.ratio == pytest.approx(8000 * 0.85 / 10000)


def test_request_too_large_without_numbers(api):
    api.routes[f"POST {CHAT}"] = lambda b: (413, {"error": {"message": "too big"}})
    with pytest.raises(PromptTooLarge) as info:
        groq.generate("p", "k")
    assert info.value.ratio is None


def test_short_rate_limit_is_waited_out(api):
    calls = []

    def route(body):
        calls.append(1)
        if len(calls) == 1:
            return 429, {"error": {"message": "slow down"}}, {"retry-after": "3"}
        return ok()(body)

    api.routes[f"POST {CHAT}"] = route
    assert groq.generate("p", "k").text == "feat: groq"
    assert len(calls) == 2


def test_long_rate_limit_fails_fast(api):
    api.routes[f"POST {CHAT}"] = lambda b: (429, {"error": {"message": "daily limit"}}, {"retry-after": "3600"})
    with pytest.raises(GitCapError, match="rate limit"):
        groq.generate("p", "k")


def test_retired_model_falls_back_to_live_one(api):
    def route(body):
        if body["model"] == "old/model":
            return 400, {"error": {"message": "decommissioned", "code": "model_decommissioned"}}
        return ok()(body)

    api.routes[f"POST {CHAT}"] = route
    api.routes[f"GET {MODELS}"] = lambda b: (200, {"data": [{"id": "qwen/qwen3.8-27b"}, {"id": "openai/gpt-oss-120b"}]})
    gen = groq.generate("p", "k", model="old/model")
    assert gen.model == "openai/gpt-oss-120b"
    assert any("old/model" in note for note in gen.attempts)


def test_retired_model_with_no_replacement(api):
    api.routes[f"POST {CHAT}"] = lambda b: (404, {"error": {"message": "nope", "code": "model_not_found"}})
    api.routes[f"GET {MODELS}"] = lambda b: (200, {"data": [{"id": "whisper-large-v3"}]})
    with pytest.raises(GitCapError, match="unavailable"):
        groq.generate("p", "k", model="old/model")


def test_unreachable(monkeypatch):
    monkeypatch.setattr(groq, "API_BASE", "http://127.0.0.1:9/openai/v1")
    with pytest.raises(GitCapError, match="Could not reach Groq"):
        groq.generate("p", "k")


def test_validate_key(api):
    api.routes[f"GET {MODELS}"] = lambda b: (200, {"data": []})
    groq.validate_key("k")
    api.routes[f"GET {MODELS}"] = lambda b: (401, {"error": {"message": "bad"}})
    with pytest.raises(GitCapError, match="invalid"):
        groq.validate_key("k")
