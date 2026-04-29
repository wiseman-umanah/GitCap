"""Interactive commit message review flow."""

from __future__ import annotations

import os
import subprocess
import tempfile
from collections.abc import Callable

import readchar
from git import Repo
from rich.console import Console
from rich.panel import Panel

from gitcap import git
from gitcap.errors import GitCapError


def show_message(console: Console, message: str) -> None:
    """Display a generated commit message in a bordered panel."""
    console.print(Panel(message, title="Generated commit message", border_style="cyan"))


def edit_message(message: str) -> str:
    """Open the generated commit message in the user's editor."""
    editor = os.environ.get("EDITOR") or os.environ.get("VISUAL") or "nano"
    with tempfile.NamedTemporaryFile("w+", suffix=".txt") as handle:
        handle.write(message)
        handle.flush()
        try:
            subprocess.run([editor, handle.name], check=True)
        except FileNotFoundError as exc:
            raise GitCapError(f"Editor `{editor}` was not found. Set EDITOR and try again.") from exc
        except subprocess.CalledProcessError as exc:
            raise GitCapError("Editor closed with an error. Commit was aborted.") from exc
        handle.seek(0)
        edited = handle.read().strip()
    if not edited:
        raise GitCapError("Commit message cannot be empty.")
    return edited


def read_action(console: Console) -> str:
    """Read a single-key commit action."""
    console.print("[bold][C][/bold]ommit  [bold][E][/bold]dit  [bold][R][/bold]egenerate  [bold][A][/bold]bort")
    while True:
        key = readchar.readkey().lower()
        if key in {"c", "e", "r", "a"}:
            return key


def review_message(
    console: Console,
    repo: Repo,
    diff: str,
    context: str,
    generator: Callable[[str, str], str],
    dry_run: bool = False,
) -> None:
    """Run the generated message review loop."""
    message = generator(diff, context)
    while True:
        show_message(console, message)
        if dry_run:
            console.print("[yellow]Dry run enabled. No commit was created.[/yellow]")
            return

        action = read_action(console)
        if action == "c":
            output = git.commit(repo, message)
            console.print(f"[green]{output}[/green]")
            return
        if action == "e":
            message = edit_message(message)
            continue
        if action == "r":
            console.print("[dim]Regenerating...[/dim]")
            message = generator(diff, context)
            continue
        console.print("[yellow]Aborted. No commit was created.[/yellow]")
        return
