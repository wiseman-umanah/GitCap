"""Interactive commit message review loop."""

from __future__ import annotations

import os
import shlex
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path

from gitcap import git, ui
from gitcap.errors import GitCapError
from gitcap.result import Generation


def edit_message(message: str) -> str:
    """Open the message in $VISUAL/$EDITOR (falls back to git's configured editor)."""
    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR") or os.environ.get("GIT_EDITOR") or "vi"
    with tempfile.NamedTemporaryFile("w", suffix=".gitcap-COMMIT_EDITMSG", delete=False) as handle:
        handle.write(message + "\n")
        path = Path(handle.name)
    try:
        subprocess.run([*shlex.split(editor), str(path)], check=True)
        lines = [line for line in path.read_text().splitlines() if not line.startswith("#")]
    except FileNotFoundError as exc:
        raise GitCapError(f"Editor `{editor}` was not found. Set $EDITOR and try again.") from exc
    except subprocess.CalledProcessError as exc:
        raise GitCapError("Editor exited with an error. Nothing was committed.") from exc
    finally:
        path.unlink(missing_ok=True)
    edited = "\n".join(lines).strip()
    if not edited:
        raise GitCapError("Commit message is empty. Nothing was committed.")
    return edited


def read_action() -> str:
    import readchar

    ui.print("[bold cyan]c[/bold cyan] commit  [bold cyan]e[/bold cyan] edit  [bold cyan]r[/bold cyan] regenerate  [bold cyan]q[/bold cyan] quit")
    while True:
        key = readchar.readkey().lower()
        if key in {"c", "\r", "\n"}:
            return "c"
        if key in {"e", "r"}:
            return key
        if key in {"q", "a", readchar.key.ESC, readchar.key.CTRL_C}:
            return "q"


def review(root: Path, generate: Callable[[], Generation], first: Generation) -> None:
    generation = first
    while True:
        ui.show_generation(generation)
        action = read_action()
        if action == "c":
            ui.print(f"[green]✓[/green] {ui.escape(git.commit(root, generation.text))}")
            return
        if action == "e":
            generation.text = edit_message(generation.text)
            continue
        if action == "r":
            generation = generate()
            continue
        ui.print("[yellow]Nothing was committed.[/yellow]")
        return
