"""Application-specific exceptions for gitcap."""

from __future__ import annotations


class GitCapError(Exception):
    """Raise for expected user-facing gitcap errors."""


class PromptTooLarge(GitCapError):
    """The backend rejected the prompt for size; retry with a smaller diff budget.

    `ratio` is how much to shrink the budget by, when the backend told us its limit.
    """

    def __init__(self, message: str, ratio: float | None = None) -> None:
        super().__init__(message)
        self.ratio = ratio
