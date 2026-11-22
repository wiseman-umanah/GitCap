"""gitcap command-line interface."""

from __future__ import annotations

import argparse
import sys

from gitcap import __version__
from gitcap.errors import GitCapError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gitcap",
        description="Write commit messages with the coding agent you're already logged in to.",
    )
    parser.add_argument("--version", action="version", version=f"gitcap {__version__}")
    sub = parser.add_subparsers(dest="command")

    commit = sub.add_parser("commit", help="Generate a message for staged changes and commit.")
    commit.add_argument("path", nargs="?", default=".", help="Any path inside the repository (default: .).")
    commit.add_argument("-a", "--agent", help="Use this backend only: an agent name (see `gitcap agents`), `local` or `groq`.")
    commit.add_argument("-m", "--model", help="Model for the chosen backend, e.g. `sonnet`, `qwen3:8b`, `openai/gpt-oss-120b`.")
    commit.add_argument("-c", "--context", default="", help="Extra context for the message.")
    commit.add_argument("-n", "--dry-run", action="store_true", help="Print the message without committing.")
    commit.add_argument("-y", "--yes", "--auto", dest="auto", action="store_true", help="Commit without review.")

    agents_cmd = sub.add_parser("agents", help="Show detected coding agents and the order they're tried in.")
    agents_cmd.add_argument("--retry", action="store_true", help="Clear pauses on agents that failed (expired plan, credits, login).")

    uninstall = sub.add_parser("uninstall", help="Remove gitcap, its settings (including the Groq key) and caches.")
    uninstall.add_argument("-y", "--yes", action="store_true", help="Don't ask first.")
    uninstall.add_argument("--keep-config", action="store_true", help="Keep settings and caches; remove only the package.")

    upgrade = sub.add_parser("upgrade", help="Update gitcap to the latest release.")
    upgrade.add_argument("--check", action="store_true", help="Only report whether an update is available.")

    config = sub.add_parser("config", help="Show or change settings.")
    config.add_argument("--agent", dest="backend", help="Default backend: `auto`, an agent name, `local`, or `groq`.")
    config.add_argument("--model", help="Model for the default backend (`default` to clear).")
    config.add_argument("--groq-key", help="Groq API key (or set GROQ_API_KEY).")
    config.add_argument("--local-url", help="Local model server URL (Ollama or OpenAI-compatible); `default` to clear.")
    config.add_argument("--reset", action="store_true", help="Delete saved settings.")
    return parser


def setup_groq(reason: str) -> bool:
    """Walk the user through adding a free Groq key. Returns True once a valid key is saved."""
    from gitcap import groq, ui
    from gitcap.config import load_config, save_config
    from gitcap.generate import GROQ_KEYS_URL

    ui.groq_onboarding(reason, GROQ_KEYS_URL)
    for _ in range(3):
        key = ui.ask_secret("Groq API key")
        if not key:
            return False
        try:
            with ui.spinner("Checking key…"):
                groq.validate_key(key)
        except GitCapError as exc:
            ui.error(str(exc).split(" Run ")[0] + " Try again, or press Enter to cancel.")
            continue
        config = load_config()
        config.groq_api_key = key
        save_config(config)
        ui.print("[green]✓[/green] Groq key saved. Using Groq from now on whenever no agent is available.")
        return True
    return False


def recovery_hint(groq_key: str | None, one_off_agent: str | None) -> str:
    """What to do when every backend failed (expired plans, no credits, logged out)."""
    lines = ["", "To fix it:"]
    if one_off_agent:
        lines.append(f"  • drop `-a {one_off_agent}` to let gitcap try your other agents")
    lines.append("  • log back in to one of your agents (`gitcap agents` shows them all)")
    if not groq_key:
        lines.append("  • or add a free Groq key: https://console.groq.com/keys, then `gitcap config --groq-key <key>`")
    return "\n".join(lines)


def cmd_commit(args: argparse.Namespace) -> None:
    from gitcap import git, ui
    from gitcap.config import load_config
    from gitcap.generate import AgentsFailed, NeedsGroqKey, candidates, generate, groq_only
    from gitcap.prompt import build_commit_prompt

    root = git.repo_root(args.path)
    changes = git.staged_changes(root)
    recent = git.recent_subjects(root)

    def make_prompt(budget: int) -> str:
        return build_commit_prompt(changes.render(budget), args.context, recent)
    interactive = sys.stdin.isatty()
    # Offer Groq whenever gitcap is choosing backends itself; a one-off `-a <agent>` means only that agent.
    may_offer_groq = interactive and (args.agent is None or args.agent.lower() == "groq")

    from gitcap import health
    from gitcap.generate import order_by_health

    paused = health.paused()
    try:
        chain = candidates(load_config(), backend=args.agent, model=args.model)
    except NeedsGroqKey as exc:
        if not may_offer_groq or not setup_groq(str(exc).splitlines()[0]):
            raise
        chain = groq_only(load_config(), args.model if args.agent == "groq" else None)
    if args.agent is None:
        chain = order_by_health(chain, paused)

    def on_failure(candidate, why: str, current: list) -> None:
        seconds = health.record_failure(candidate.name, why)
        if len(current) == 1:
            return  # the final error says it all
        pause = f" (skipping it for {ui.duration(seconds)})" if seconds else ""
        nxt = " — trying next" if candidate is not current[-1] else ""
        ui.print(f"[dim]↷ {candidate.label} failed: {ui.escape(why)}{pause}{nxt}[/dim]")

    def on_attempt(candidate, update) -> None:
        if candidate.name in paused:
            update(f"Every other option failed, retrying [cyan]{candidate.label}[/cyan]…")
        else:
            update(f"[cyan]{candidate.label}[/cyan] is writing your commit message…")

    def attempt(current: list):
        with ui.spinner("Starting…") as update:
            return generate(
                current,
                make_prompt,
                on_shrink=lambda c, budget: ui.print(
                    f"[dim]↷ {c.label}: diff too large, retrying with ~{ui.human(budget)} characters[/dim]"
                ),
                on_attempt=lambda c: on_attempt(c, update),
                on_failure=lambda c, why: on_failure(c, why, current),
                on_success=lambda c: health.clear(c.name),
            )

    def run():
        nonlocal chain
        try:
            return attempt(chain)
        except AgentsFailed as exc:
            if may_offer_groq and not load_config().groq_key:
                if setup_groq("None of your coding agents could write the message:\n" + str(exc)):
                    chain = groq_only(load_config())
                    return attempt(chain)
            raise GitCapError(str(exc) + recovery_hint(load_config().groq_key, args.agent)) from None

    generation = run()
    if args.dry_run or args.auto:
        ui.show_generation(generation)
        if args.dry_run:
            ui.print("[yellow]Dry run: nothing was committed.[/yellow]")
        else:
            ui.print(f"[green]✓[/green] {ui.escape(git.commit(root, generation.text))}")
        return

    if not interactive:
        raise GitCapError("Not a terminal, so the review prompt can't run. Use --yes or --dry-run.")
    from gitcap.interactive import review

    review(root, run, generation)


def cmd_agents(args: argparse.Namespace) -> None:
    from gitcap import agents, health, local, ui
    from gitcap.config import load_config
    from gitcap.generate import NeedsGroqKey, candidates, order_by_health

    if args.retry:
        health.clear()
        ui.print("[green]✓[/green] Cleared. Every agent will be tried in its normal order again.")
    config = load_config()
    paused = health.paused()
    try:
        order = [c.name for c in order_by_health(candidates(config), paused)]
    except NeedsGroqKey:
        order = []
    ui.agents_table(agents.detect_all(), order, bool(config.groq_key), local.detect(config.local_url), paused)


def cmd_config(args: argparse.Namespace) -> None:
    from gitcap import ui
    from gitcap.config import AUTO, load_config, reset_config, save_config, summary

    if args.reset:
        reset_config()
        ui.print("[green]✓[/green] Settings cleared.")
        return

    config = load_config()
    changed = False
    if args.backend:
        backend = args.backend.strip().lower()
        if backend not in {AUTO, "groq", "local"}:
            from gitcap.agents import get_agent

            backend = get_agent(backend).name
        if backend != config.backend:
            config.model = None  # a model belongs to its backend
        config.backend, changed = backend, True
    if args.model:
        config.model, changed = (None if args.model == "default" else args.model), True
    if args.local_url:
        config.local_url, changed = (None if args.local_url == "default" else args.local_url.rstrip("/")), True
    if args.groq_key:
        from gitcap import groq

        with ui.spinner("Checking Groq key…"):
            groq.validate_key(args.groq_key.strip())
        config.groq_api_key, changed = args.groq_key.strip(), True

    if changed:
        save_config(config)
        ui.print(f"[green]✓[/green] Saved: {summary(config)}")
    else:
        ui.print(summary(config))


def cmd_uninstall(args: argparse.Namespace) -> None:
    from gitcap.uninstall import uninstall

    uninstall(yes=args.yes, keep_config=args.keep_config)


def cmd_upgrade(args: argparse.Namespace) -> None:
    from gitcap.update import upgrade

    upgrade(check_only=args.check)


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    handlers = {"commit": cmd_commit, "agents": cmd_agents, "config": cmd_config, "upgrade": cmd_upgrade, "uninstall": cmd_uninstall}
    if args.command is None:
        parser.print_help()
        raise SystemExit(1)

    from gitcap import update

    # Checks PyPI in the background while the command runs; `upgrade` does its own check.
    check = update.start_check() if args.command not in {"upgrade", "uninstall"} else None
    try:
        handlers[args.command](args)
    except GitCapError as exc:
        from gitcap import ui

        ui.error(str(exc))
        update.notify(check)
        raise SystemExit(1) from None
    except KeyboardInterrupt:
        print("\nAborted. Nothing was committed.", file=sys.stderr)
        raise SystemExit(130) from None
    update.notify(check)
