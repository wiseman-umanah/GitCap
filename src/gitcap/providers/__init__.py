"""Provider factory and key validation helpers."""

from __future__ import annotations

from importlib import import_module
from types import ModuleType

from gitcap.config import DEFAULT_MODELS, normalize_provider
from gitcap.errors import GitCapError


def get_provider(name: str) -> ModuleType:
    """Return a provider module by name."""
    provider = normalize_provider(name)
    module_name = "openai" if provider == "chatgpt" else provider
    return import_module(f"gitcap.providers.{module_name}")


def validate_provider_key(provider: str, api_key: str, model: str | None = None) -> None:
    """Validate a provider key with a small live API call."""
    normalized = normalize_provider(provider)
    provider_module = get_provider(normalized)
    validator = getattr(provider_module, "validate_key", None)
    if validator is None:
        raise GitCapError(f"Provider `{provider}` cannot validate API keys.")
    validator(api_key=api_key, model=model or DEFAULT_MODELS[normalized])
