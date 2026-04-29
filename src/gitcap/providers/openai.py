"""OpenAI ChatGPT provider support."""

from __future__ import annotations

from openai import OpenAI

from gitcap.config import load_config
from gitcap.errors import GitCapError
from gitcap.prompt import build_commit_prompt
from gitcap.providers._errors import provider_error


def _client(api_key: str) -> OpenAI:
    """Create an OpenAI client."""
    return OpenAI(api_key=api_key)


def validate_key(api_key: str, model: str) -> None:
    """Validate an OpenAI API key with a minimal chat completion."""
    try:
        _client(api_key).chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "Reply with ok."}],
            max_tokens=3,
            temperature=0,
        )
    except Exception as exc:
        raise provider_error("OpenAI", exc) from exc


def generate_commit_message(diff: str, context: str = "") -> str:
    """Generate a conventional commit message using OpenAI."""
    config = load_config()
    if config is None:
        raise GitCapError("No config found. Run `gitcap config` first.")
    try:
        response = _client(config.api_key).chat.completions.create(
            model=config.preferred_model,
            messages=[{"role": "user", "content": build_commit_prompt(diff, context)}],
            temperature=0.2,
        )
    except Exception as exc:
        raise provider_error("OpenAI", exc) from exc
    return response.choices[0].message.content.strip()
