"""Terminal rendering. rich is imported here only, so it stays off the fast paths."""

from __future__ import annotations

import time
from contextlib import contextmanager
from functools import lru_cache
from typing import TYPE_CHECKING

from gitcap.result import Generation, Usage

if TYPE_CHECKING:
    from rich.console import Console

@lru_cache(maxsize=1)
def console() -> Console:
    from rich.console import Console

    return Console(highlight=False)


def print(*args: object, **kwargs: object) -> None:  # noqa: A001 - thin wrapper on purpose
    console().print(*args, **kwargs)


def error(message: str) -> None:
    console().print(f"[red]✗[/red] {escape(message)}")


def escape(text: str) -> str:
    from rich.markup import escape as rich_escape

    return rich_escape(text)


def human(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


def ago(timestamp: float | None) -> str:
    if not timestamp:
        return "never"
    seconds = max(time.time() - timestamp, 0)
    for unit, size in (("d", 86_400), ("h", 3_600), ("m", 60)):
        if seconds >= size:
            return f"{int(seconds // size)}{unit} ago"
    return "just now"


def duration(seconds: float | None) -> str:
    seconds = max(int(seconds or 0), 0)
    if seconds >= 3_600:
        hours, rest = divmod(seconds, 3_600)
        return f"{hours}h" + (f"{rest // 60}m" if rest >= 60 and hours < 3 else "")
    return f"{max(seconds // 60, 1)}m"


def usage_line(usage: Usage | None) -> str:
    if usage is None:
        return "[dim]tokens not reported[/dim]"
    parts = [f"[bold]{human(usage.input_tokens)}[/bold] in", f"[bold]{human(usage.output_tokens)}[/bold] out"]
    if usage.cache_read_tokens:
        parts.append(f"{human(usage.cache_read_tokens)} cache read")
    if usage.cache_write_tokens:
        parts.append(f"{human(usage.cache_write_tokens)} cache write")
    if usage.cost_usd:
        parts.append(f"${usage.cost_usd:.4f}")
    return " · ".join(parts)


def show_generation(generation: Generation) -> None:
    from rich.panel import Panel

    for note in generation.attempts:
        console().print(f"[dim]↷ {escape(note)}[/dim]")
    meta = f"[cyan]{generation.source}[/cyan]"
    if generation.model:
        meta += f" · {escape(generation.model)}"
    meta += f" · {generation.seconds:.1f}s"
    console().print(Panel(escape(generation.text), title="commit message", title_align="left", border_style="cyan", padding=(1, 2)))
    console().print(f"  {meta}   {usage_line(generation.usage)}")


@contextmanager
def spinner(text: str):
    """Live status line; yields a function to update its text."""
    status = console().status(text, spinner="dots")
    with status:
        yield status.update


def agents_table(detections: list, order: list[str], groq_ready: bool, server=None, paused: dict | None = None) -> None:
    """order: backend names in the order a commit will try them. paused: {name: (until, reason)}."""
    from rich.table import Table

    paused = paused or {}
    now = time.time()

    def position(name: str) -> str:
        if name not in order:
            return "[dim]·[/dim]"
        number = order.index(name) + 1
        if name in paused:
            return f"[yellow]{number}[/yellow]"
        return f"[bold green]{number}[/bold green]" if number == 1 else f"[cyan]{number}[/cyan]"

    def status(name: str, normal: str) -> str:
        if name in paused:
            until, reason = paused[name]
            short = escape(reason[:40] + ("…" if len(reason) > 40 else ""))
            return f"[yellow]paused {duration(until - now)}[/yellow] [dim]{short}[/dim]"
        return normal

    table = Table(title="Coding agents on this machine", title_justify="left", box=None, pad_edge=False, header_style="bold dim")
    table.add_column("#", justify="right")
    table.add_column("agent")
    table.add_column("binary")
    table.add_column("status")
    table.add_column("last used", justify="right")
    for d in detections:
        login = {True: "[green]logged in[/green]", False: "[red]logged out[/red]", None: "[yellow]login ?[/yellow]"}[d.logged_in]
        binary = escape(d.binary) if d.binary else "[dim]not installed[/dim]"
        name = d.agent.name
        table.add_row(position(name), d.agent.label, binary, status(name, login) if d.binary else "[dim]-[/dim]", ago(d.last_used))
    if server:
        models = ", ".join(server.models[:3]) + (f" +{len(server.models) - 3}" if len(server.models) > 3 else "")
        table.add_row(position("local"), f"{server.label} (local)", escape(server.base_url), status("local", "[green]running[/green]"), escape(models))
    else:
        table.add_row(position("local"), "Local model", "[dim]Ollama :11434 / LM Studio :1234[/dim]", "[dim]not running[/dim]", "")
    groq_status = status("groq", "[green]key set[/green]") if groq_ready else "[dim]no key[/dim]"
    table.add_row(position("groq"), "Groq API", "[dim]built in[/dim]", groq_status, "")
    console().print(table)
    notes = "[dim]# = order a commit tries them in; each failure moves on to the next"
    if paused:
        notes += "\n[yellow]paused[/yellow] = failed for an account reason (plan, credits, login), tried last until then."
        notes += " Clear with `gitcap agents --retry`"
    console().print(notes + "[/dim]")


def groq_onboarding(reason: str, keys_url: str) -> None:
    """Explain why Groq is being offered and where to get a free key."""
    from rich.panel import Panel

    body = (
        f"{escape(reason)}\n\n"
        "gitcap can use [bold]Groq[/bold] instead. It's free and fast, and it only needs an API key:\n\n"
        f"  1. Open [link={keys_url}][cyan underline]{keys_url}[/cyan underline][/link] and sign in\n"
        "  2. Click [bold]Create API Key[/bold] and copy it\n"
        "  3. Paste it below (your typing is hidden; press Enter on an empty line to cancel)\n\n"
        "[dim]The key is saved to ~/.config/gitcap/config.json, readable only by you.[/dim]"
    )
    console().print(Panel(body, title="no coding agent available", title_align="left", border_style="yellow", padding=(1, 2)))


def ask_secret(label: str) -> str:
    from rich.prompt import Prompt

    return Prompt.ask(label, password=True, console=console(), default="", show_default=False).strip()
