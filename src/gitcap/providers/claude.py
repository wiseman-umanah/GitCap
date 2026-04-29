"""Anthropic Claude provider support."""

from __future__ import annotations

from anthropic import Anthropic

from gitcap.config import load_config
from gitcap.errors import GitCapError
from gitcap.prompt import build_commit_prompt
from gitcap.providers._errors import provider_error


def _client(api_key: str) -> Anthropic:
    """Create an Anthropic client."""
    return Anthropic(api_key=api_key)


def validate_key(api_key: str, model: str) -> None:
    """Validate an Anthropic API key with a minimal message call."""
    try:
        _client(api_key).messages.create(
            model=model,
            max_tokens=3,
            messages=[{"role": "user", "content": "Reply with ok."}],
        )
    except Exception as exc:
        raise provider_error("Claude", exc) from exc


def generate_commit_message(diff: str, context: str = "") -> str:
    """Generate a conventional commit message using Claude."""
    config = load_config()
    if config is None:
        raise GitCapError("No config found. Run `gitcap config` first.")
    try:
        response = _client(config.api_key).messages.create(
            model=config.preferred_model,
            max_tokens=500,
            temperature=0.2,
            messages=[{"role": "user", "content": build_commit_prompt(diff, context)}],
        )
    except Exception as exc:
        raise provider_error("Claude", exc) from exc

    parts = [block.text for block in response.content if getattr(block, "type", "") == "text"]
    return "\n".join(parts).strip()
