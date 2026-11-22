"""`gitcap uninstall`: remove gitcap's own files, then the package itself."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from gitcap.config import config_dir
from gitcap.errors import GitCapError
from gitcap.health import state_file
from gitcap.update import PACKAGE, install_method


def leftover_paths() -> list[Path]:
    """Everything gitcap created outside the package: settings (with the Groq key) and caches."""
    paths = [config_dir(), state_file().parent]
    if sys.platform == "darwin":  # where gitcap 0.1.x kept its config.pkl
        paths.append(Path.home() / "Library" / "Application Support" / PACKAGE)
    unique = list(dict.fromkeys(paths))
    return [p for p in unique if p.exists()]


def uninstall_command(method: str) -> list[str] | None:
    if method == "uv":
        return ["uv", "tool", "uninstall", PACKAGE]
    if method == "pipx":
        return ["pipx", "uninstall", PACKAGE]
    if method == "pip":
        return [sys.executable, "-m", "pip", "uninstall", "-y", PACKAGE]
    return None  # a source checkout: nothing to uninstall


def _confirm(question: str) -> bool:
    from rich.prompt import Confirm

    from gitcap import ui

    return Confirm.ask(question, default=False, console=ui.console())


def uninstall(yes: bool = False, keep_config: bool = False) -> None:
    from gitcap import ui

    paths = [] if keep_config else leftover_paths()
    method = install_method()
    command = uninstall_command(method)

    ui.print("[bold]This will remove:[/bold]")
    for path in paths:
        ui.print(f"  • {ui.escape(str(path))}")
    if command:
        ui.print(f"  • the gitcap package [dim]({' '.join(command)})[/dim]")
    else:
        ui.print("  • [dim]not the package: this gitcap runs from a source checkout[/dim]")
    if not paths and not command:
        ui.print("Nothing to remove.")
        return

    if not yes:
        if not sys.stdin.isatty():
            raise GitCapError("Not a terminal, so gitcap can't ask first. Run `gitcap uninstall --yes`.")
        if not _confirm("Continue?"):
            ui.print("Nothing was removed.")
            return

    for path in paths:
        try:
            shutil.rmtree(path)
        except OSError as exc:
            raise GitCapError(f"Could not remove {path}: {exc.strerror or exc}") from None
        ui.print(f"[green]✓[/green] Removed {ui.escape(str(path))}")

    if command is None:
        ui.print("[yellow]Settings removed. Delete the source checkout yourself to finish.[/yellow]")
        return
    ui.print(f"[dim]$ {' '.join(command)}[/dim]")
    try:
        result = subprocess.run(command)
    except FileNotFoundError:
        raise GitCapError(f"`{command[0]}` was not found. Finish with: {' '.join(command)}") from None
    if result.returncode != 0:
        raise GitCapError(f"Package removal failed (exit {result.returncode}). Finish with: {' '.join(command)}")
    ui.print("[green]✓[/green] gitcap is uninstalled.")
