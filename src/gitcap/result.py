"""Shared result types for every generation backend."""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(slots=True)
class Usage:
    """Token counts as reported by the provider. Nothing here is estimated."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float | None = None

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens + self.cache_read_tokens + self.cache_write_tokens


@dataclass(slots=True)
class Generation:
    """One generated commit message plus where it came from and what it cost."""

    text: str
    usage: Usage | None = None
    model: str | None = None
    source: str = ""
    seconds: float = 0.0
    attempts: list[str] = field(default_factory=list)


_THINK = re.compile(r"<think(?:ing)?>.*?</think(?:ing)?>", re.DOTALL | re.IGNORECASE)
_FENCE = re.compile(r"^```[\w-]*\s*\n(.*?)\n```\s*$", re.DOTALL)


def clean_message(text: str) -> str:
    """Strip reasoning blocks, markdown fences and wrapping quotes from model output."""
    text = _THINK.sub("", text).strip()
    fenced = _FENCE.match(text)
    if fenced:
        text = fenced.group(1).strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {'"', "'", "`"}:
        text = text[1:-1].strip()
    return text
