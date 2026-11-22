"""Groq API backend, using only the standard library (no SDK import cost)."""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request

from gitcap.errors import GitCapError, PromptTooLarge
from gitcap.result import Generation, Usage

# GROQ_BASE_URL is the same override the official Groq SDK honours (proxies, gateways, tests).
API_BASE = os.environ.get("GROQ_BASE_URL", "https://api.groq.com").rstrip("/") + "/openai/v1"
DEFAULT_MODEL = "openai/gpt-oss-120b"
# Tried in order when the configured model has been retired.
FALLBACK_MODELS = ("openai/gpt-oss-120b", "qwen/qwen3.8-27b", "openai/gpt-oss-20b")
TIMEOUT_SECONDS = 60
MAX_OUTPUT_TOKENS = 800
# Free tier allows 8K tokens per minute, and one request may not exceed that. Diffs run about
# 3 characters per token, so ~16k characters plus instructions and output stays under it.
# Paid tiers are larger; a 413 tells us the real limit and we shrink to fit.
DIFF_BUDGET_CHARS = 16_000
MAX_RETRY_AFTER_SECONDS = 20
_LIMIT = re.compile(r"Limit\s+(\d+),\s*Requested\s+(\d+)", re.IGNORECASE)
SYSTEM_PROMPT = "You write git commit messages. Reply with the commit message only."


class _ModelUnavailable(GitCapError):
    pass


class _RetryAfter(GitCapError):
    def __init__(self, message: str, seconds: float) -> None:
        super().__init__(message)
        self.seconds = seconds


def _request(method: str, path: str, api_key: str, body: dict | None = None) -> dict:
    request = urllib.request.Request(
        f"{API_BASE}{path}",
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "gitcap",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        try:
            error = json.loads(exc.read()).get("error") or {}
        except ValueError:
            error = {}
        code, message = error.get("code") or "", str(error.get("message") or exc.reason)
        if exc.code == 401:
            raise GitCapError("Groq API key is invalid. Run `gitcap config --groq-key <key>`.") from None
        if exc.code == 413 or "request too large" in message.lower():
            limit = _LIMIT.search(message)
            # Aim 15% under the reported limit to leave room for estimation error.
            ratio = (int(limit.group(1)) * 0.85) / int(limit.group(2)) if limit else None
            raise PromptTooLarge("Diff is larger than Groq allows in one request.", ratio) from None
        if exc.code == 429:
            retry_after = exc.headers.get("retry-after")
            try:
                seconds = float(retry_after) if retry_after else None
            except ValueError:
                seconds = None
            if seconds is not None and seconds <= MAX_RETRY_AFTER_SECONDS:
                raise _RetryAfter(message, seconds) from None
            raise GitCapError("Groq rate limit reached. Wait a bit and retry.") from None
        if exc.code == 404 or code in {"model_not_found", "model_decommissioned"}:
            raise _ModelUnavailable(message) from None
        raise GitCapError(f"Groq request failed ({exc.code}): {message}") from None
    except (urllib.error.URLError, TimeoutError) as exc:
        raise GitCapError("Could not reach Groq. Check your internet connection.") from exc


def available_models(api_key: str) -> list[str]:
    return [model["id"] for model in _request("GET", "/models", api_key).get("data", []) if model.get("active", True)]


def validate_key(api_key: str) -> None:
    available_models(api_key)


def _complete(prompt: str, api_key: str, model: str) -> Generation:
    body: dict = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
        "max_completion_tokens": MAX_OUTPUT_TOKENS,
    }
    if model.startswith("openai/gpt-oss"):
        body["reasoning_effort"] = "low"
        body["include_reasoning"] = False
    try:
        data = _request("POST", "/chat/completions", api_key, body)
    except _RetryAfter as wait:
        time.sleep(wait.seconds)
        data = _request("POST", "/chat/completions", api_key, body)
    choice = (data.get("choices") or [{}])[0]
    raw = data.get("usage") or {}
    cached = int((raw.get("prompt_tokens_details") or {}).get("cached_tokens") or 0)
    return Generation(
        text=(choice.get("message") or {}).get("content") or "",
        model=data.get("model") or model,
        usage=Usage(
            input_tokens=int(raw.get("prompt_tokens") or 0) - cached,
            output_tokens=int(raw.get("completion_tokens") or 0),
            cache_read_tokens=cached,
        ),
    )


def generate(prompt: str, api_key: str, model: str | None = None) -> Generation:
    """Generate with Groq, falling back to a live model if the chosen one was retired."""
    started = time.monotonic()
    chosen = model or DEFAULT_MODEL
    try:
        generation = _complete(prompt, api_key, chosen)
    except _ModelUnavailable:
        live = set(available_models(api_key))
        replacement = next((m for m in FALLBACK_MODELS if m in live and m != chosen), None)
        if replacement is None:
            raise GitCapError(f"Groq model `{chosen}` is unavailable. Pick one with `gitcap config --model`.") from None
        generation = _complete(prompt, api_key, replacement)
        generation.attempts.append(f"groq: `{chosen}` unavailable, used `{replacement}`")
    generation.source = "groq"
    generation.seconds = time.monotonic() - started
    return generation
