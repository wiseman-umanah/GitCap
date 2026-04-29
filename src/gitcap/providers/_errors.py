"""Shared provider error helpers."""

from __future__ import annotations

from requests import RequestException

from gitcap.errors import GitCapError


def provider_error(provider: str, exc: Exception) -> GitCapError:
    """Convert SDK exceptions into clear user-facing errors."""
    message = str(exc).lower()
    if "unauthorized" in message or "invalid api key" in message or "401" in message:
        return GitCapError(f"{provider} API key is invalid. Run `gitcap config --reset`.")
    if "rate limit" in message or "429" in message:
        return GitCapError(f"{provider} rate limit reached. Wait a bit and retry.")
    if isinstance(exc, RequestException) or "connection" in message or "network" in message or "timeout" in message:
        return GitCapError("No internet connection or the provider could not be reached.")
    return GitCapError(f"{provider} request failed. Check your config and try again.")
