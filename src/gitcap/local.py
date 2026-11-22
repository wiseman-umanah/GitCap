"""Local model servers: Ollama, LM Studio, or any OpenAI-compatible endpoint (llama.cpp, vLLM, ...).

Detection is a quick probe of localhost ports, so a server that isn't running costs about nothing
(connection refused returns immediately).
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from gitcap.errors import GitCapError, PromptTooLarge
from gitcap.result import Generation, Usage

PROBE_TIMEOUT = 0.25
GENERATE_TIMEOUT = 300
CHARS_PER_TOKEN = 3
RESERVED_TOKENS = 1_500  # instructions + the reply
MAX_NUM_CTX = 32_768  # don't ask Ollama to allocate more KV cache than a commit message needs
DEFAULT_CTX = 4_096  # what servers typically load a model with when we can't find out
SYSTEM_PROMPT = "You write git commit messages. Reply with the commit message only."
SKIP_MODEL_HINTS = ("embed", "rerank", "whisper", "tts", "clip")


@dataclass(slots=True)
class LocalServer:
    kind: str  # "ollama" | "openai"
    label: str
    base_url: str
    models: list[str] = field(default_factory=list)

    @property
    def name(self) -> str:
        return "local"


def _get_json(url: str, timeout: float = PROBE_TIMEOUT, body: dict | None = None) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json", "User-Agent": "gitcap"},
        method="POST" if body is not None else "GET",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read())


def _chat_models(names: list[str]) -> list[str]:
    return [n for n in names if not any(hint in n.lower() for hint in SKIP_MODEL_HINTS)]


def _probe_ollama(base: str) -> LocalServer | None:
    try:
        data = _get_json(f"{base}/api/tags")
    except (OSError, ValueError):
        return None
    entries = sorted(data.get("models") or [], key=lambda m: m.get("modified_at") or "", reverse=True)
    return LocalServer("ollama", "Ollama", base, _chat_models([m.get("name") or m.get("model") for m in entries]))


def _probe_openai(base: str, label: str) -> LocalServer | None:
    try:
        data = _get_json(f"{base}/v1/models")
    except (OSError, ValueError):
        return None
    if not isinstance(data.get("data"), list):
        return None  # something else is listening on this port
    return LocalServer("openai", label, base, _chat_models([m.get("id") for m in data["data"] if m.get("id")]))


def detect(url: str | None = None) -> LocalServer | None:
    """First reachable local server with at least one chat model.

    An explicit url (config `local_url` or GITCAP_LOCAL_URL) is tried as Ollama first, then OpenAI-compatible.
    """
    url = (url or os.environ.get("GITCAP_LOCAL_URL") or "").rstrip("/").removesuffix("/v1")
    probes = (
        [lambda: _probe_ollama(url), lambda: _probe_openai(url, "Local server")]
        if url
        else [
            lambda: _probe_ollama(os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")),
            lambda: _probe_openai("http://127.0.0.1:1234", "LM Studio"),
        ]
    )
    for probe in probes:
        server = probe()
        if server and server.models:
            return server
    return None


# --------------------------------------------------------------------------- context size


def context_tokens(server: LocalServer, model: str) -> int:
    """The context window the model will actually run with (best effort)."""
    try:
        if server.kind == "ollama":
            info = _get_json(f"{server.base_url}/api/show", timeout=2, body={"model": model})
            native = next((int(v) for k, v in (info.get("model_info") or {}).items() if k.endswith(".context_length")), 0)
            return min(native or DEFAULT_CTX, MAX_NUM_CTX)
        # LM Studio's REST API reports the length the model is loaded with.
        data = _get_json(f"{server.base_url}/api/v0/models/{model}", timeout=2)
        return int(data.get("loaded_context_length") or data.get("max_context_length") or DEFAULT_CTX)
    except (OSError, ValueError, TypeError):
        return DEFAULT_CTX


def budget_chars(context: int) -> int:
    return max((context - RESERVED_TOKENS) * CHARS_PER_TOKEN, 2_000)


def pick_model(server: LocalServer, model: str | None) -> str:
    if model:
        return model
    if not server.models:
        raise GitCapError(f"{server.label} has no chat models. Pull or load one first.")
    return server.models[0]


# --------------------------------------------------------------------------- generation


def generate(server: LocalServer, prompt: str, model: str, context: int) -> Generation:
    started = time.monotonic()
    messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}]
    try:
        if server.kind == "ollama":
            # Native API so num_ctx can be set: Ollama otherwise loads models with a small window
            # and silently drops the *start* of longer prompts, which is where the instructions are.
            data = _get_json(
                f"{server.base_url}/api/chat",
                timeout=GENERATE_TIMEOUT,
                body={"model": model, "messages": messages, "stream": False, "options": {"num_ctx": context, "temperature": 0.2}},
            )
            text = (data.get("message") or {}).get("content") or ""
            usage = Usage(input_tokens=int(data.get("prompt_eval_count") or 0), output_tokens=int(data.get("eval_count") or 0))
        else:
            data = _get_json(
                f"{server.base_url}/v1/chat/completions",
                timeout=GENERATE_TIMEOUT,
                body={"model": model, "messages": messages, "temperature": 0.2, "stream": False},
            )
            text = ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
            raw = data.get("usage") or {}
            usage = Usage(input_tokens=int(raw.get("prompt_tokens") or 0), output_tokens=int(raw.get("completion_tokens") or 0))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:300]
        if any(hint in detail.lower() for hint in ("context", "too long", "exceed", "n_ctx")):
            raise PromptTooLarge(f"Prompt is larger than {model}'s context window.") from None
        if exc.code == 404:
            raise GitCapError(f"{server.label} has no model `{model}`.") from None
        raise GitCapError(f"{server.label} request failed ({exc.code}): {detail}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise GitCapError(f"{server.label} at {server.base_url} could not be reached: {exc}") from None
    return Generation(text=text, model=model, usage=usage, source=f"local/{server.kind}", seconds=time.monotonic() - started)
