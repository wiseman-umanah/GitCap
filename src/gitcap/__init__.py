"""gitcap command-line entry point."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable

import pyfiglet
from rich.console import Console
from rich.panel import Panel

from gitcap import git
from gitcap.config import (
    get_or_create_config,
    load_config,
    public_config_summary,
    reset_config,
    update_config,
)
from gitcap.errors import GitCapError
from gitcap.interactive import review_message, show_message
from gitcap.providers import get_provider

console = Console()


def print_banner(output: Console) -> None:
    """Print the gitcap ASCII banner."""
    banner = pyfiglet.figlet_format("gitcap", font="slant")
    output.print(f"[cyan]{banner}[/cyan]")


def install_excepthook() -> None:
    """Install a plain-English exception hook for expected failures."""
    def handle_exception(exc_type: type[BaseException], exc: BaseException, traceback: object) -> None:
        """Render expected exceptions without raw tracebacks."""
        if issubclass(exc_type, KeyboardInterrupt):
            console.print("\n[yellow]Aborted.[/yellow]")
            return
        if issubclass(exc_type, GitCapError):
            console.print(f"[red]{exc}[/red]")
            return
        console.print("[red]Something went wrong. Run again with a clean setup and retry.[/red]")

    sys.excepthook = handle_exception


def build_parser() -> argparse.ArgumentParser:
    """Build the gitcap argument parser."""
    parser = argparse.ArgumentParser(prog="gitcap", description="Generate AI-powered git commit messages.")
    subparsers = parser.add_subparsers(dest="command")

    commit_parser = subparsers.add_parser("commit", help="Generate and commit a staged git diff.")
    commit_parser.add_argument("path", help="Path to a git repository.")
    commit_parser.add_argument("--context", default="", help="Extra context for the AI prompt.")
    commit_parser.add_argument("--dry-run", action="store_true", help="Generate a message without committing.")
    commit_parser.add_argument("--auto", action="store_true", help="Commit immediately with the generated message.")

    config_parser = subparsers.add_parser("config", help="Show or update gitcap config.")
    config_parser.add_argument("--reset", action="store_true", help="Clear saved config.")
    config_parser.add_argument("--provider", help="Switch provider: groq, claude, chatgpt, openrouter.")
    config_parser.add_argument("--key", help="Update the API key for the configured provider.")
    config_parser.add_argument("--model", help="Update the preferred model.")
    return parser


def generate_with_config(diff: str, context: str = "") -> str:
    """Generate a commit message with the configured provider."""
    config = load_config()
    if config is None:
        raise GitCapError("No config found. Run `gitcap config` first.")
    provider = get_provider(config.provider)
    message = provider.generate_commit_message(diff, context)
    if not message:
        raise GitCapError("Provider returned an empty commit message. Try regenerating.")
    return message


def handle_commit(args: argparse.Namespace) -> None:
    """Handle the gitcap commit command."""
    repo = git.open_repo(args.path)
    diff = git.get_staged_diff(repo)
    get_or_create_config(console, show_banner=True)

    if args.auto:
        message = generate_with_config(diff, args.context)
        show_message(console, message)
        if args.dry_run:
            console.print("[yellow]Dry run enabled. No commit was created.[/yellow]")
            return
        output = git.commit(repo, message)
        console.print(f"[green]{output}[/green]")
        return

    review_message(
        console=console,
        repo=repo,
        diff=diff,
        context=args.context,
        generator=generate_with_config,
        dry_run=args.dry_run,
    )


def handle_config(args: argparse.Namespace) -> None:
    """Handle the gitcap config command."""
    if args.reset:
        reset_config()
        console.print("[green]Config cleared.[/green]")
        return

    if args.provider or args.key or args.model:
        config = update_config(console, provider=args.provider, api_key=args.key, preferred_model=args.model)
        console.print(f"[green]Config updated:[/green] {public_config_summary(config)}")
        return

    config = get_or_create_config(console, show_banner=True)
    console.print(Panel(public_config_summary(config), title="gitcap config", border_style="cyan"))


def route_command(args: argparse.Namespace) -> Callable[[argparse.Namespace], None]:
    """Return a handler for parsed CLI arguments."""
    if args.command == "commit":
        return handle_commit
    if args.command == "config":
        return handle_config
    raise GitCapError("Choose a command: `gitcap commit <path>` or `gitcap config`.")


def main() -> None:
    """Run the gitcap CLI."""
    install_excepthook()
    parser = build_parser()
    args = parser.parse_args()
    try:
        route_command(args)(args)
    except GitCapError as exc:
        console.print(f"[red]{exc}[/red]")
        raise SystemExit(1) from None
