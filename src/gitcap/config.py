"""Configuration storage and first-run setup for gitcap."""

from __future__ import annotations

import pickle
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from platformdirs import user_config_dir
from rich.console import Console
from rich.prompt import Prompt

from gitcap.errors import GitCapError

APP_NAME: Final[str] = "gitcap"
CONFIG_FILENAME: Final[str] = "config.pkl"

DEFAULT_MODELS: Final[dict[str, str]] = {
    "groq": "llama3-8b-8192",
    "claude": "claude-3-haiku-20240307",
    "chatgpt": "gpt-4o-mini",
    "openrouter": "openai/gpt-4o-mini",
}

PROVIDER_LABELS: Final[dict[str, str]] = {
    "groq": "Groq (free, fast)",
    "claude": "Claude (Anthropic)",
    "chatgpt": "ChatGPT (OpenAI)",
    "openrouter": "OpenRouter (multiple models)",
}


@dataclass(slots=True)
class GitCapConfig:
    """Persisted gitcap provider settings."""

    provider: str
    api_key: str
    preferred_model: str


def get_config_file() -> Path:
    """Return the pickle config file path."""
    return Path(user_config_dir(appname=APP_NAME)) / CONFIG_FILENAME


def load_config() -> GitCapConfig | None:
    """Load saved gitcap config, returning None when it does not exist."""
    config_file = get_config_file()
    if not config_file.exists():
        return None

    try:
        with config_file.open("rb") as handle:
            config = pickle.load(handle)
    except (OSError, pickle.PickleError, EOFError) as exc:
        raise GitCapError("Saved config could not be read. Run `gitcap config --reset`.") from exc

    if isinstance(config, GitCapConfig):
        return config

    if isinstance(config, dict):
        try:
            return GitCapConfig(
                provider=str(config["provider"]),
                api_key=str(config["api_key"]),
                preferred_model=str(config["preferred_model"]),
            )
        except KeyError as exc:
            raise GitCapError("Saved config is incomplete. Run `gitcap config --reset`.") from exc

    raise GitCapError("Saved config has an unknown format. Run `gitcap config --reset`.")


def save_config(config: GitCapConfig) -> None:
    """Persist gitcap config as a pickle file."""
    config_file = get_config_file()
    config_file.parent.mkdir(parents=True, exist_ok=True)
    with config_file.open("wb") as handle:
        pickle.dump(config, handle)


def reset_config() -> None:
    """Remove saved gitcap config if present."""
    config_file = get_config_file()
    try:
        config_file.unlink(missing_ok=True)
    except OSError as exc:
        raise GitCapError("Could not clear saved config. Check file permissions and try again.") from exc


def normalize_provider(provider: str) -> str:
    """Normalize and validate a provider name."""
    normalized = provider.strip().lower()
    aliases = {"openai": "chatgpt", "anthropic": "claude"}
    normalized = aliases.get(normalized, normalized)
    if normalized not in DEFAULT_MODELS:
        choices = ", ".join(DEFAULT_MODELS)
        raise GitCapError(f"Unknown provider `{provider}`. Choose one of: {choices}.")
    return normalized


def prompt_for_provider(console: Console) -> str:
    """Ask the user to choose an AI provider."""
    console.print("[bold]Choose an AI provider[/bold]")
    providers = list(PROVIDER_LABELS)
    for index, provider in enumerate(providers, start=1):
        console.print(f"  [cyan]{index}[/cyan]. {PROVIDER_LABELS[provider]}")

    choice = Prompt.ask("Provider", choices=[str(index) for index in range(1, len(providers) + 1)])
    return providers[int(choice) - 1]


def prompt_for_key(provider: str) -> str:
    """Ask for an API key without echoing it back."""
    label = PROVIDER_LABELS[provider].split(" (", 1)[0]
    return Prompt.ask(f"{label} API key", password=True).strip()


def get_or_create_config(console: Console, show_banner: bool = False) -> GitCapConfig:
    """Load existing config or run first-time provider setup."""
    from gitcap.providers import validate_provider_key
    from gitcap import print_banner

    config = load_config()
    if config is not None:
        return config

    if show_banner:
        print_banner(console)

    provider = prompt_for_provider(console)
    api_key = prompt_for_key(provider)
    model = DEFAULT_MODELS[provider]
    console.print("[dim]Validating API key...[/dim]")
    validate_provider_key(provider=provider, api_key=api_key, model=model)

    config = GitCapConfig(provider=provider, api_key=api_key, preferred_model=model)
    save_config(config)
    console.print("[green]Config saved.[/green]")
    return config


def update_config(
    console: Console,
    provider: str | None = None,
    api_key: str | None = None,
    preferred_model: str | None = None,
) -> GitCapConfig:
    """Update saved config fields and validate the resulting provider key."""
    from gitcap.providers import validate_provider_key

    current = load_config()
    normalized_provider = normalize_provider(provider) if provider else current.provider if current else None
    if normalized_provider is None:
        normalized_provider = prompt_for_provider(console)

    model = preferred_model or (current.preferred_model if current and current.provider == normalized_provider else DEFAULT_MODELS[normalized_provider])
    key = api_key or (current.api_key if current and current.provider == normalized_provider else None)
    if key is None:
        key = prompt_for_key(normalized_provider)

    console.print("[dim]Validating API key...[/dim]")
    validate_provider_key(provider=normalized_provider, api_key=key, model=model)

    config = GitCapConfig(provider=normalized_provider, api_key=key, preferred_model=model)
    save_config(config)
    return config


def public_config_summary(config: GitCapConfig) -> str:
    """Return a key-safe display summary for saved config."""
    return f"provider={config.provider}, model={config.preferred_model}"
