import pytest

from gitcap import local
from gitcap.errors import GitCapError, PromptTooLarge


@pytest.fixture
def ollama(server):
    server.routes["GET /api/tags"] = lambda b: (200, {"models": [
        {"name": "nomic-embed-text:latest", "modified_at": "2026-11-12T00:00:00Z"},
        {"name": "llama3.2:3b", "modified_at": "2026-10-01T00:00:00Z"},
        {"name": "qwen3:8b", "modified_at": "2026-11-01T00:00:00Z"},
    ]})
    server.routes["POST /api/show"] = lambda b: (200, {"model_info": {"qwen3.context_length": 40960}})
    server.routes["POST /api/chat"] = lambda b: (200, {
        "message": {"content": "<think>hmm</think>\nfeat: local"}, "prompt_eval_count": 321, "eval_count": 7,
    })
    return server


@pytest.fixture
def lmstudio(server):
    server.routes["GET /v1/models"] = lambda b: (200, {"data": [{"id": "text-embedding-nomic"}, {"id": "qwen2.5-coder-7b"}]})
    server.routes["GET /api/v0/models/qwen2.5-coder-7b"] = lambda b: (200, {"loaded_context_length": 8192, "max_context_length": 32768})
    server.routes["POST /v1/chat/completions"] = lambda b: (200, {
        "choices": [{"message": {"content": "fix: lmstudio"}}], "usage": {"prompt_tokens": 100, "completion_tokens": 5},
    })
    return server


def test_detect_ollama_skips_embedding_models_and_orders_by_recent(ollama):
    srv = local.detect(ollama.url)
    assert srv.kind == "ollama"
    assert srv.models == ["qwen3:8b", "llama3.2:3b"]
    assert local.pick_model(srv, None) == "qwen3:8b"
    assert local.pick_model(srv, "llama3.2:3b") == "llama3.2:3b"


def test_detect_openai_compatible(lmstudio):
    srv = local.detect(lmstudio.url + "/v1")  # a trailing /v1 is accepted
    assert srv.kind == "openai"
    assert srv.models == ["qwen2.5-coder-7b"]


def test_detect_nothing_running(env):
    assert local.detect("http://127.0.0.1:9") is None


def test_detect_ignores_unrelated_server(server):
    server.routes["GET /v1/models"] = lambda b: (200, {"hello": "world"})
    assert local.detect(server.url) is None


def test_ollama_context_is_capped(ollama):
    srv = local.detect(ollama.url)
    assert local.context_tokens(srv, "qwen3:8b") == local.MAX_NUM_CTX


def test_ollama_context_falls_back_when_unknown(ollama):
    ollama.routes["POST /api/show"] = lambda b: (500, {})
    assert local.context_tokens(local.detect(ollama.url), "qwen3:8b") == local.DEFAULT_CTX


def test_lmstudio_uses_loaded_context(lmstudio):
    assert local.context_tokens(local.detect(lmstudio.url), "qwen2.5-coder-7b") == 8192


def test_budget_from_context():
    assert local.budget_chars(8192) == (8192 - local.RESERVED_TOKENS) * local.CHARS_PER_TOKEN
    assert local.budget_chars(1024) == 2_000  # floor


def test_ollama_generate_sets_num_ctx(ollama):
    srv = local.detect(ollama.url)
    gen = local.generate(srv, "PROMPT", "qwen3:8b", 16384)
    assert (gen.usage.input_tokens, gen.usage.output_tokens) == (321, 7)
    assert gen.source == "local/ollama"
    [body] = ollama.bodies("/api/chat")
    assert body["options"]["num_ctx"] == 16384
    assert body["stream"] is False
    assert body["messages"][-1]["content"] == "PROMPT"


def test_openai_compatible_generate(lmstudio):
    gen = local.generate(local.detect(lmstudio.url), "p", "qwen2.5-coder-7b", 8192)
    assert gen.text == "fix: lmstudio"
    assert gen.usage.output_tokens == 5


def test_context_overflow_is_prompt_too_large(ollama):
    ollama.routes["POST /api/chat"] = lambda b: (400, {"error": "input length exceeds the context length"})
    with pytest.raises(PromptTooLarge):
        local.generate(local.detect(ollama.url), "p", "qwen3:8b", 4096)


def test_missing_model(ollama):
    ollama.routes["POST /api/chat"] = lambda b: (404, {"error": "model 'x' not found"})
    with pytest.raises(GitCapError, match="no model `x`"):
        local.generate(local.detect(ollama.url), "p", "x", 4096)


def test_no_chat_models(server):
    server.routes["GET /api/tags"] = lambda b: (200, {"models": [{"name": "nomic-embed-text"}]})
    assert local.detect(server.url) is None
